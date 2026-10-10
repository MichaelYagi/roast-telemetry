"""Example device plugin: a read-only serial thermometer that streams plain
ASCII lines shaped "BT=<float> ET=<float>\\n". This line format is made up
for this example -- it isn't a real device's protocol -- and exists purely
so a plugin author has a complete, working file to copy and adapt. See
device_plugins/README.md for what's required of a real one.

Not auto-loaded: device_plugins/base.py's load_installed() only scans
device_plugins/installed/, not examples/. Copy this file there (and give it
a real kind name) to actually use it.
"""
from __future__ import annotations

from typing import Optional

import serial as pyserial
from roast_heuristics import LiveRoastDetector

from ..base import PluginSpec, register


def _open_serial(port: str, **kwargs):
    """Accepts a plain device path (COM3, /dev/ttyUSB0) and also pyserial
    URLs like socket://host:port -- how a remote or simulated device is
    reached in tests, same as every built-in bridge's own _open_serial."""
    return pyserial.serial_for_url(port, **kwargs)


class ExampleSerialMeterEngine:
    """Matches device_plugins.base.DeviceEngine. ms6514_bridge/engine.py's
    MS6514Engine is the real-hardware version of this exact shape -- read
    that one alongside this file for how a genuine wire protocol looks."""

    def __init__(
        self,
        port: str,
        *,
        dry_end_c: Optional[float] = 160.0,
        fc_start_c: Optional[float] = 196.0,
        detect_milestones: bool = False,
        serial_cls=None,  # injectable for testing without real hardware
    ):
        if not port:
            raise ValueError("port is required to connect to this meter")
        self.port = port
        self._dry_end_c = dry_end_c
        self._fc_start_c = fc_start_c
        self._detect_milestones = detect_milestones
        self._detector = LiveRoastDetector(dry_end_c=dry_end_c, fc_start_c=fc_start_c, detect_milestones=detect_milestones)
        self._last_time_s = 0.0
        self._last_bt: Optional[float] = None
        self._last_et: Optional[float] = None
        self._connected = False
        self._last_error: Optional[str] = None
        try:
            self._serial = (serial_cls or _open_serial)(port=port, baudrate=9600, timeout=0.7)
            self._connected = bool(self._serial.is_open)
        except Exception as exc:  # pragma: no cover - depends on local hardware/OS
            self._serial = None
            self._connected = False
            self._last_error = str(exc)

    def _read_line(self) -> Optional[dict]:
        if self._serial is None:
            return None
        try:
            raw = self._serial.readline()
            if not raw:
                self._last_error = "no data received (device not streaming / wrong port?)"
                return None
            text = raw.decode("ascii", errors="ignore").strip()
            fields = dict(part.split("=", 1) for part in text.split() if "=" in part)
            bt = float(fields["BT"]) if "BT" in fields else None
            et = float(fields["ET"]) if "ET" in fields else None
            self._connected = True
            self._last_error = None
            return {"bt": bt, "et": et}
        except Exception as exc:  # pragma: no cover - depends on local hardware/OS
            self._last_error = str(exc)
            return None

    # -- DeviceEngine contract -----------------------------------------
    def tick(self, dt: float) -> dict:
        self._last_time_s += dt
        reading = self._read_line()
        if reading is not None:
            if reading["bt"] is not None:
                self._last_bt = reading["bt"]
            if reading["et"] is not None:
                self._last_et = reading["et"]
        sample = self._detector.observe(self._last_time_s, self._last_bt, self._last_et)
        # Read-only (PluginSpec.read_only=True below) -- no controls to report.
        sample["heater_pct"] = None
        sample["fan_pct"] = None
        sample["drum_speed_pct"] = None
        return sample

    def get_new_events(self) -> list:
        return self._detector.get_new_events()

    def apply_command(self, cmd: dict) -> None:
        pass  # read-only meter -- nothing to write

    def is_finished(self) -> bool:
        return False

    def status(self) -> dict:
        return {"mode": "plugin_live", "port": self.port, "connected": self._connected, "last_error": self._last_error}

    def reset_detection(self) -> None:
        self._detector = LiveRoastDetector(dry_end_c=self._dry_end_c, fc_start_c=self._fc_start_c, detect_milestones=self._detect_milestones)
        self._last_time_s = 0.0

    def mark_milestone_fired(self, event_type: str) -> None:
        self._detector.mark_milestone_fired(event_type)

    def notify_manual_charge(self, time_s: float, bt: float) -> None:
        self._detector.notify_manual_charge(time_s, bt)

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:  # pragma: no cover - best-effort cleanup
                pass


def _connect(port, *, dry_end_c, fc_start_c, detect_milestones):
    return ExampleSerialMeterEngine(port, dry_end_c=dry_end_c, fc_start_c=fc_start_c, detect_milestones=detect_milestones)


register(PluginSpec(
    kind="example_serial_meter",
    label="Example Serial Meter (copy this file to build a real plugin)",
    connect=_connect,
    needs_port=True,
    read_only=True,
    port_hint="Serial port",
))
