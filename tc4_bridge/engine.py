"""Direct USB-serial reader/controller for a TC4+ shield running the
aArtisanQ (PID) firmware -- the same idea as
modbus_bridge/ms6514_bridge for their own devices.

Protocol confirmed against the real spec (not guessed): the firmware's
published documentation and
github.com/greencardigan/TC4-shield's own commands.txt. Plain
newline-terminated ASCII commands, comma-delimited CSV responses --
genuinely simpler than every other live-bridge protocol this app
speaks: no register maps (Modbus), no raw-USB descriptor work (Aillio),
just write a line, read a line.

Baud rate 115200 (the aArtisanQ/aArtisan firmware default -- older TC4
firmware used 19200, not supported by this engine).

Commands used:
  UNITS,C   -- sent once at connect, so READ always comes back Celsius
              (this app is internally always-Celsius, same convention
              every other engine already follows).
  READ      -- request a reading. Response: "ambient,chan1,chan2,chan3,chan4"
              (CSV, 5 fields). Channel 1 = BT, channel 2 = ET (the TC4
              shield's default wiring convention) -- channel 3
              mapped to this app's own "third probe" DT slot (same
              convention modbus_bridge's DT already uses) if the field
              parses; channel 4 and ambient aren't used by this app.
  OT1,duty  -- heater output, 0-100. Already the same 0-100% scale this
              app's own heater_pct slider uses -- no SV-range remapping
              needed, unlike Modbus's burner_sv_range_c.
  DCFAN,duty -- fan output, 0-100. The firmware enforces its own ramp
              limit on this; nothing for this engine to replicate.

No OT2 (secondary output) or drum support -- TC4 has no drum channel at
all, so apply_command silently ignores drum_speed_pct, same as every
other engine already does for a channel that isn't actually wired up
on a given device.
"""
from __future__ import annotations

from typing import Optional

import serial as pyserial
from roast_heuristics import LiveRoastDetector


class TC4EngineError(RuntimeError):
    pass


def _parse_read_response(line: str) -> Optional[dict]:
    fields = line.strip().split(",")
    if len(fields) < 3:
        return None
    try:
        values = [float(f) for f in fields[:5]]
    except ValueError:
        return None
    bt = values[1] if len(values) > 1 else None
    et = values[2] if len(values) > 2 else None
    dt = values[3] if len(values) > 3 else None
    return {"bt": bt, "et": et, "dt": dt}


class TC4Engine:
    def __init__(
        self,
        port: str,
        baudrate: int = 115200,
        timeout: float = 0.7,
        dry_end_c: Optional[float] = 160.0,
        fc_start_c: Optional[float] = 196.0,
        # Opt-in -- off by default. See ModbusEngine's identical parameter
        # for the full rationale.
        detect_milestones: bool = False,
        serial_cls=pyserial.Serial,  # injectable for testing without real hardware
    ):
        if not port:
            raise ValueError("port (e.g. 'COM5') is required to connect to the TC4+")
        self.port = port
        self._dry_end_c = dry_end_c
        self._fc_start_c = fc_start_c
        self._detect_milestones = detect_milestones
        self._detector = LiveRoastDetector(dry_end_c=dry_end_c, fc_start_c=fc_start_c, detect_milestones=detect_milestones)
        self._last_time_s = 0.0
        self._last_bt: Optional[float] = None
        self._last_et: Optional[float] = None
        self._last_dt: Optional[float] = None
        self._connected = False
        self._last_error: Optional[str] = None

        try:
            self._serial = serial_cls(port=port, baudrate=baudrate, bytesize=8, parity="N", stopbits=1, timeout=timeout)
            self._connected = bool(self._serial.is_open)
            if self._connected:
                self._write_line("UNITS,C")
        except Exception as exc:  # pragma: no cover - depends on local hardware/OS
            self._serial = None
            self._connected = False
            self._last_error = str(exc)

    # -- wire I/O -----------------------------------------------------
    def _write_line(self, command: str) -> None:
        if self._serial is None:
            return
        try:
            self._serial.write((command + "\n").encode("ascii"))
        except Exception as exc:  # pragma: no cover - depends on local hardware/OS
            self._last_error = str(exc)
            self._connected = False

    def _read_reading(self) -> Optional[dict]:
        if self._serial is None:
            return None
        try:
            self._serial.write(b"READ\n")
            raw = self._serial.readline()
            if not raw:
                self._last_error = "no response to READ (device not connected / wrong port?)"
                return None
            parsed = _parse_read_response(raw.decode("ascii", errors="replace"))
            if parsed is None:
                self._last_error = f"unparseable READ response: {raw!r}"
                return None
            self._last_error = None
            self._connected = True
            return parsed
        except Exception as exc:  # pragma: no cover - depends on local hardware/OS
            self._last_error = str(exc)
            self._connected = False
            return None

    # -- engine contract (matches simulator.SimulatorEngine / ModbusEngine / MS6514Engine) --
    def tick(self, dt: float) -> dict:
        self._last_time_s += dt
        time_s = self._last_time_s

        parsed = self._read_reading()
        if parsed is not None:
            if parsed["bt"] is not None:
                self._last_bt = parsed["bt"]
            if parsed["et"] is not None:
                self._last_et = parsed["et"]
            if parsed["dt"] is not None:
                self._last_dt = parsed["dt"]

        sample = self._detector.observe(time_s, self._last_bt, self._last_et)
        sample["dt"] = self._last_dt
        sample["heater_pct"] = None
        sample["fan_pct"] = None
        sample["drum_speed_pct"] = None
        return sample

    def get_new_events(self) -> list:
        return self._detector.get_new_events()

    def apply_command(self, cmd: dict) -> None:
        if "heater_pct" in cmd and cmd["heater_pct"] is not None:
            duty = max(0, min(100, int(round(cmd["heater_pct"]))))
            self._write_line(f"OT1,{duty}")
        if "fan_pct" in cmd and cmd["fan_pct"] is not None:
            duty = max(0, min(100, int(round(cmd["fan_pct"]))))
            self._write_line(f"DCFAN,{duty}")
        # drum_speed_pct: no-op -- TC4 has no drum output to write to.

    def is_finished(self) -> bool:
        return False  # no end-of-roast signal from the board -- stop manually from the UI

    def status(self) -> dict:
        return {
            "mode": "tc4_live",
            "port": self.port,
            "connected": self._connected,
            "last_error": self._last_error,
        }

    def reset_detection(self) -> None:
        """See ModbusEngine.reset_detection() -- same reasoning."""
        self._detector = LiveRoastDetector(dry_end_c=self._dry_end_c, fc_start_c=self._fc_start_c, detect_milestones=self._detect_milestones)
        self._last_time_s = 0.0

    def mark_milestone_fired(self, event_type: str) -> None:
        """See ModbusEngine.mark_milestone_fired -- same forwarding."""
        self._detector.mark_milestone_fired(event_type)

    def notify_manual_charge(self, time_s: float, bt: float) -> None:
        """See ModbusEngine.notify_manual_charge -- same forwarding."""
        self._detector.notify_manual_charge(time_s, bt)

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:  # pragma: no cover - best-effort cleanup
                pass
