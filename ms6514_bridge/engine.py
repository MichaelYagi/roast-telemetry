"""Direct USB-serial reader for the Mastech MS6514 dual K-type
thermocouple meter -- bypasses Artisan entirely. Protocol traced
directly from Artisan's own driver (`MS6514temperature()` in
`artisanlib/comm.py`, and `hex2int()` in `artisanlib/util.py`), not
reverse-engineered guesswork.

Serial: 9600 baud, 8 data bits, no parity, 1 stop bit. Unlike the
PerfectPrime TC0301 (request/response), the MS6514 continuously streams
18-byte frames on its own -- no command needs to be sent.

Frame layout (18 bytes, 0-indexed), when synced at offset 0:
  byte0-1:   sync bytes, 0x65 0x14
  byte5-6:   raw 16-bit big-endian value (hex2int: byte5*256+byte6) / 10
  byte7-8:   second raw 16-bit big-endian value / 10
  byte11:    status/mode byte (see below)
  byte12:    secondary channel's OK(0x08)/NC(0x40) flag
  byte16-17: terminator, 0x0D 0x0A

byte11 says which display mode the meter is in and disambiguates the
two raw fields (verified by tracing Artisan's own reassignment logic
line by line, not just reading the docstring table):
  0x08 ("Display T1", OK):  field[5-6]=T1, field[7-8]=T2 -- already in
                             canonical order, no correction needed.
  0x40 ("Display T1", T1 NC): same layout, T1 marked not-connected.
  0x09 ("Display T2", OK):  field[5-6]=T2, field[7-8]=T1 -- swapped;
                             this engine un-swaps them.
  0x41 ("Display T2", T2 NC): same layout, swapped, T2 marked NC.
  anything else (0x0A/0x8A/0x0B/0x8B/0x42/0x43/0x66/0xC2/0xC3/...):
      the meter is in "T1-T2" (delta) display mode. One of the two
      raw fields is a computed T1-T2 delta, not a direct reading, and
      Artisan's own reconstruction logic for this mode is convoluted
      enough that tracing it turned up what looks like a real bug on
      one branch (a value that always evaluates to zero). Rather than
      faithfully reproduce logic we can't verify is even correct in
      the original, this engine just skips frames caught in this mode
      and keeps the last known-good T1/T2 -- **keep the meter's display
      set to "T1" or "T2" (not "T1-T2") for reliable dual-channel
      reading.** Every frame carries both channels regardless of which
      single one is shown on the LCD, so this doesn't cost us anything
      as long as the T1-T2 mode is avoided.

No RS232 command exists to control this device -- it's a read-only
meter, so ``apply_command`` is a no-op, same as the other live bridges.
RoR and CHARGE/TURNING_POINT/DRY_END/FC_START auto-detection reuse
``roast_heuristics.LiveRoastDetector``.

Not tested against real MS6514 hardware -- none available in this
environment. The byte layout and decoding are Artisan's own verified
logic (traced, not guessed); the "T1-T2 mode is ambiguous" scope
decision is a deliberate simplification, documented above, not a gap.
"""
from __future__ import annotations

from typing import Optional

import serial as pyserial
from roast_heuristics import LiveRoastDetector

FRAME_LEN = 18
SYNC0, SYNC1 = 0x65, 0x14
TERM0, TERM1 = 0x0D, 0x0A


class MS6514EngineError(RuntimeError):
    pass


def _parse_frame(frame: bytes) -> Optional[dict]:
    if (
        len(frame) != FRAME_LEN
        or frame[0] != SYNC0
        or frame[1] != SYNC1
        or frame[16] != TERM0
        or frame[17] != TERM1
    ):
        return None

    raw1 = frame[5] * 256 + frame[6]
    raw2 = frame[7] * 256 + frame[8]
    b11 = frame[11]
    b12 = frame[12]

    s1: Optional[float] = raw1 / 10.0
    s2: Optional[float] = raw2 / 10.0

    # NC (probe not connected) flags, checked in raw field position --
    # matches the order of operations in Artisan's own driver.
    if (64 <= b11 <= 67) or (194 <= b11 <= 195):
        s1 = None
    if b12 == 64:
        s2 = None

    if b11 in (9, 65):
        s1, s2 = s2, s1  # "Display T2" mode: raw fields are swapped -- restore order
    elif b11 in (8, 64):
        pass  # "Display T1" mode: already in canonical T1, T2 order
    else:
        return None  # "T1-T2" delta mode -- ambiguous, see module docstring

    return {"t1": s1, "t2": s2}


def _find_frame(buf: bytes) -> Optional[bytes]:
    for i in range(len(buf) - FRAME_LEN + 1):
        if buf[i] == SYNC0 and buf[i + 1] == SYNC1 and buf[i + 16] == TERM0 and buf[i + 17] == TERM1:
            return buf[i : i + FRAME_LEN]
    return None


class MS6514Engine:
    def __init__(
        self,
        port: str,
        baudrate: int = 9600,
        timeout: float = 0.7,
        dry_end_c: Optional[float] = 160.0,
        fc_start_c: Optional[float] = 196.0,
        serial_cls=pyserial.Serial,  # injectable for testing without real hardware
    ):
        if not port:
            raise ValueError("port (e.g. 'COM5') is required to connect to the MS6514")
        self.port = port
        self._detector = LiveRoastDetector(dry_end_c=dry_end_c, fc_start_c=fc_start_c)
        self._last_time_s = 0.0
        self._last_bt: Optional[float] = None
        self._last_et: Optional[float] = None
        self._connected = False
        self._last_error: Optional[str] = None

        try:
            self._serial = serial_cls(port=port, baudrate=baudrate, bytesize=8, parity="N", stopbits=1, timeout=timeout)
            self._connected = bool(self._serial.is_open)
        except Exception as exc:  # pragma: no cover - depends on local hardware/OS
            self._serial = None
            self._connected = False
            self._last_error = str(exc)

    # -- wire I/O -----------------------------------------------------
    def _read_reading(self) -> Optional[dict]:
        if self._serial is None:
            return None
        try:
            buf = self._serial.read(64)  # continuous stream -- grab whatever's arrived
            if not buf:
                self._last_error = "no data received (device not streaming / wrong port?)"
                return None
            frame = _find_frame(buf)
            if frame is None:
                self._last_error = f"no valid frame found in {len(buf)} bytes read"
                return None
            parsed = _parse_frame(frame)
            self._last_error = None
            self._connected = True
            return parsed  # None here just means "T1-T2 mode active", not a comms error
        except Exception as exc:  # pragma: no cover - depends on local hardware/OS
            self._last_error = str(exc)
            self._connected = False
            return None

    # -- engine contract (matches simulator.SimulatorEngine / ArtisanBridgeEngine / ModbusEngine) --
    def tick(self, dt: float) -> dict:
        self._last_time_s += dt
        time_s = self._last_time_s

        parsed = self._read_reading()
        if parsed is not None:
            if parsed["t1"] is not None:
                self._last_bt = parsed["t1"]
            if parsed["t2"] is not None:
                self._last_et = parsed["t2"]

        sample = self._detector.observe(time_s, self._last_bt, self._last_et)
        sample["heater_pct"] = None
        sample["fan_pct"] = None
        sample["drum_speed_pct"] = None
        return sample

    def get_new_events(self) -> list:
        return self._detector.get_new_events()

    def apply_command(self, cmd: dict) -> None:
        pass  # read-only meter -- no command exists to control anything on it

    def is_finished(self) -> bool:
        return False  # no end-of-roast signal from the meter; stop manually from the UI

    def status(self) -> dict:
        return {
            "mode": "ms6514_live",
            "port": self.port,
            "connected": self._connected,
            "last_error": self._last_error,
        }

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:  # pragma: no cover - best-effort cleanup
                pass
