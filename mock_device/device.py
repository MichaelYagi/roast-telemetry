"""Mock USB/serial hardware layer.

Pretends to be a physical roaster's serial/USB interface. Callers
(``roast_session``) talk to this exactly as they would to a real port:
``connect()`` / ``disconnect()`` / ``read()`` / ``write()`` / ``status()``.
Underneath, a ``MockDevice`` doesn't know or care whether its readings
come from the Artisan-style simulator or from an ``.alog`` playback
engine -- both satisfy the same small structural contract (``RoastEngine``
below), so either can be plugged in transparently.
"""
from __future__ import annotations

import random
import time
from enum import Enum
from typing import Optional, Protocol, runtime_checkable


class DeviceState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    STREAMING = "streaming"
    ERROR = "error"


@runtime_checkable
class RoastEngine(Protocol):
    """Structural contract shared by SimulatorEngine and AlogPlayer."""

    def tick(self, dt: float) -> dict: ...
    def get_new_events(self) -> list: ...
    def apply_command(self, cmd: dict) -> None: ...
    def is_finished(self) -> bool: ...
    def status(self) -> dict: ...


class MockDeviceError(RuntimeError):
    pass


class MockDevice:
    """Fakes a USB/serial connection to a roaster.

    ``engine`` supplies the actual data (simulator or .alog playback);
    this class only adds the hardware-shaped lifecycle and framing that
    a real serial device would have (connect handshake, read/write,
    connection-state errors).
    """

    def __init__(self, device_id: str, engine: RoastEngine, *, connect_latency_s: float = 0.15):
        self.device_id = device_id
        self.engine = engine
        self.connect_latency_s = connect_latency_s
        self.state = DeviceState.DISCONNECTED
        self.connected_at: Optional[float] = None
        self.last_error: Optional[str] = None

    def connect(self) -> None:
        self.state = DeviceState.CONNECTING
        time.sleep(min(self.connect_latency_s, 0.5))  # simulate handshake latency
        self.state = DeviceState.CONNECTED
        self.connected_at = time.time()
        self.last_error = None

    def disconnect(self) -> None:
        self.state = DeviceState.DISCONNECTED
        self.connected_at = None

    def read(self, dt: float = 1.0) -> dict:
        if self.state not in (DeviceState.CONNECTED, DeviceState.STREAMING):
            raise MockDeviceError(f"device {self.device_id} is not connected (state={self.state.value})")
        self.state = DeviceState.STREAMING
        try:
            sample = self.engine.tick(dt)
        except Exception as exc:  # pragma: no cover - defensive
            self.state = DeviceState.ERROR
            self.last_error = str(exc)
            raise MockDeviceError(str(exc)) from exc
        sample["events"] = self.engine.get_new_events()
        sample["finished"] = self.engine.is_finished()
        return sample

    def write(self, command: dict) -> dict:
        if self.state not in (DeviceState.CONNECTED, DeviceState.STREAMING):
            raise MockDeviceError(f"device {self.device_id} is not connected (state={self.state.value})")
        self.engine.apply_command(command)
        return {"ok": True, "applied": command}

    def status(self) -> dict:
        return {
            "device_id": self.device_id,
            "state": self.state.value,
            "connected_at": self.connected_at,
            "last_error": self.last_error,
            "engine_status": self.engine.status() if self.state != DeviceState.DISCONNECTED else None,
        }

    def simulate_fault(self, message: str = "simulated hardware fault") -> None:
        """Testing hook: force the device into an ERROR state."""
        self.state = DeviceState.ERROR
        self.last_error = message
