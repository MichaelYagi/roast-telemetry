"""Fake Mastech MS6514 dual-thermocouple meter standing in for the real
USB meter, for testing ``ms6514_bridge`` without one on hand.

Frame layout (see ``ms6514_bridge/engine.py``'s docstring for the full
sourcing notes): 18 bytes, streamed continuously
and unsolicited (no request needed) --

    byte0-1:   sync, 0x65 0x14
    byte5-6:   T1 raw, big-endian, value * 10
    byte7-8:   T2 raw, big-endian, value * 10
    byte11:    mode byte -- 0x08 = "Display T1", fields already in
               canonical T1,T2 order (what this fake always sends)
    byte12:    secondary-channel OK(0x08)/NC(0x40) flag
    byte16-17: terminator, 0x0D 0x0A

BT (T1) / ET (T2) come from the same thermal model as the app's own
Simulator mode (see ``_thermal.py``). This is a read-only meter in real
life too, so this fake never reads anything back -- it just streams.

Usage::

    python -m hardware_fakes.ms6514_device --port /tmp/ttyFAKE_METER

Then point the app's "Direct USB (thermocouple meter)" port field at the
other end of a virtual serial pair (see hardware_fakes/README.md).
"""
from __future__ import annotations

import argparse
import sys
import threading
import time

import serial as pyserial

from ._thermal import ThermalDriver

SYNC0, SYNC1 = 0x65, 0x14
TERM0, TERM1 = 0x0D, 0x0A
MODE_DISPLAY_T1_OK = 0x08
SECONDARY_OK = 0x08
FRAME_INTERVAL_S = 0.5


def _build_frame(t1_c: float, t2_c: float) -> bytes:
    raw1 = max(0, min(0xFFFF, round(t1_c * 10)))
    raw2 = max(0, min(0xFFFF, round(t2_c * 10)))
    frame = bytearray(18)
    frame[0], frame[1] = SYNC0, SYNC1
    frame[2:5] = b"\x00\x00\x00"  # unused by the parser
    frame[5], frame[6] = raw1 >> 8, raw1 & 0xFF
    frame[7], frame[8] = raw2 >> 8, raw2 & 0xFF
    frame[9], frame[10] = 0, 0  # unused
    frame[11] = MODE_DISPLAY_T1_OK
    frame[12] = SECONDARY_OK
    frame[13:16] = b"\x00\x00\x00"  # unused
    frame[16], frame[17] = TERM0, TERM1
    return bytes(frame)


def stream(ser, driver: ThermalDriver, quiet: bool, stop: threading.Event | None = None) -> None:
    """Streams frames on `ser` until `stop` is set (or forever)."""
    while stop is None or not stop.is_set():
        snap = driver.snapshot()
        bt, et = snap["bt"], snap["et"]
        if bt is not None and et is not None:
            ser.write(_build_frame(bt, et))
            if not quiet:
                print(f"t={snap['time_s']:.0f}s T1(BT)={bt:.1f} T2(ET)={et:.1f}", file=sys.stderr)
        if stop is not None:
            stop.wait(FRAME_INTERVAL_S)
        else:
            time.sleep(FRAME_INTERVAL_S)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", required=True, help="serial port to write on, e.g. /tmp/ttyFAKE_METER")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    driver = ThermalDriver()
    driver.start()
    ser = pyserial.Serial(port=args.port, baudrate=9600, bytesize=8, parity="N", stopbits=1)
    print(f"MS6514 fake streaming on {ser.port}", file=sys.stderr)

    try:
        stream(ser, driver, args.quiet)
    except KeyboardInterrupt:
        pass
    finally:
        driver.stop()
        ser.close()


if __name__ == "__main__":
    main()
