"""Simulated devices the server can start by itself, so a machine can be
tried with no hardware and no setup (no ``socat``, no WSL, no second
terminal). The port picker offers them as ``sim://<kind>`` entries; when a
roast connects to one, the server calls ``start()``, points the ordinary
connection code at the returned handle, and calls ``stop()`` when the roast
ends.

Each simulated device is the same fake the standalone scripts run
(``modbus_fz94``, ``modbus_fz94_evo``, ``ms6514_device``, ``tc4``), served
over a local TCP listener on 127.0.0.1 -- so the real engine code (framing,
parsing, control writes) runs unchanged.

Adding a device model means adding its fake, its entry in ``KINDS`` and its
starter below (see also the "fake hardware for every model" project rule).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Optional

from . import ms6514_device, tc4
from ._thermal import ThermalDriver
from .modbus_fz94 import FZ94Simulator
from .modbus_fz94_evo import FZ94EvoSimulator
from .tcp_serial import TcpServerSerial

PREFIX = "sim://"
LOCALHOST = "127.0.0.1"


@dataclass(frozen=True)
class SimKind:
    key: str
    label: str  # shown in the port picker
    mode: str  # the roast mode it belongs to (RoastMode value)
    transport: str  # "serial" (reached via a socket:// URL) or "tcp" (host + port)


KINDS: dict[str, SimKind] = {
    "fz94": SimKind("fz94", "Simulated FZ-94 (no hardware needed)", "modbus_live", "serial"),
    "fz94_evo": SimKind("fz94_evo", "Simulated FZ-94 Evo (no hardware needed)", "modbus_live", "tcp"),
    "ms6514": SimKind("ms6514", "Simulated MS6514 meter (no hardware needed)", "ms6514_live", "serial"),
    "tc4": SimKind("tc4", "Simulated TC4+ (no hardware needed)", "tc4_live", "serial"),
}


def is_simulated(value: object) -> bool:
    """True for a ``sim://...`` value in a port or host field."""
    return isinstance(value, str) and value.startswith(PREFIX)


def kind_of(value: str) -> SimKind:
    key = value[len(PREFIX):] if value.startswith(PREFIX) else value
    try:
        return KINDS[key]
    except KeyError:
        raise ValueError(f"Unknown simulated device {value!r}") from None


@dataclass
class SimHandle:
    kind: SimKind
    host: str
    port: int  # TCP port the fake listens on
    serial_url: Optional[str]  # socket://host:port for serial kinds, else None
    _stop: Callable[[], None]

    def stop(self) -> None:
        self._stop()


def _thread(target: Callable[[], None], name: str) -> None:
    threading.Thread(target=target, daemon=True, name=name).start()


def _start_fz94(kind: SimKind) -> SimHandle:
    ser = TcpServerSerial(LOCALHOST, timeout=0.2)
    sim = FZ94Simulator.with_defaults(ser)
    _thread(sim.run, "sim-fz94")
    return SimHandle(kind, ser.host, ser.tcp_port, ser.url, sim.stop)


def _start_fz94_evo(kind: SimKind) -> SimHandle:
    sim = FZ94EvoSimulator(LOCALHOST, 0, 1, verbose=False)
    _thread(sim.run, "sim-fz94-evo")
    return SimHandle(kind, LOCALHOST, sim.port, None, sim.stop)


def _start_streaming(kind: SimKind, run: Callable, timeout: float) -> SimHandle:
    """ms6514 / tc4: a thermal model driving a serve/stream loop on a TCP 'serial port'."""
    ser = TcpServerSerial(LOCALHOST, timeout=timeout)
    driver = ThermalDriver()
    driver.start()
    stop = threading.Event()

    def target() -> None:
        try:
            run(ser, driver, True, stop)
        finally:
            driver.stop()

    _thread(target, f"sim-{kind.key}")

    def shutdown() -> None:
        stop.set()
        ser.close()

    return SimHandle(kind, ser.host, ser.tcp_port, ser.url, shutdown)


def start(value: str) -> SimHandle:
    """Starts the simulated device named by a ``sim://<kind>`` value."""
    kind = kind_of(value)
    if kind.key == "fz94":
        return _start_fz94(kind)
    if kind.key == "fz94_evo":
        return _start_fz94_evo(kind)
    if kind.key == "ms6514":
        return _start_streaming(kind, ms6514_device.stream, timeout=0.2)
    if kind.key == "tc4":
        return _start_streaming(kind, tc4.serve, timeout=1.0)
    raise ValueError(f"No starter for simulated device {kind.key!r}")  # KINDS and this function out of sync
