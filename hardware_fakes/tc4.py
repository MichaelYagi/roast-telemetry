"""Fake TC4+ (aArtisanQ/PID firmware) board standing in for the real
Arduino shield, for testing ``tc4_bridge`` without one on hand.

Unlike the MS6514 fake (continuous unsolicited streaming), TC4 is a
request/response protocol -- this reads whatever command line arrives
and responds accordingly (see ``tc4_bridge/engine.py``'s own docstring
for the full protocol citation):

    READ        -> "ambient,chan1,chan2,chan3,chan4\\n" (chan1=BT,
                   chan2=ET, chan3=DT; ambient/chan4 are flat 0.0
                   placeholders, unused by this app either direction)
    UNITS,x     -> no response (real firmware doesn't ack this either)
    OT1,duty    -> sets the shared ThermalDriver's heater_pct
    DCFAN,duty  -> sets the shared ThermalDriver's fan_pct
    anything else -> ignored

BT/ET/heater/fan all come from the same shared thermal model as the
app's own Simulator mode (see ``_thermal.py``) -- this fake's roast
reacts to OT1/DCFAN writes exactly like the Modbus fake already does,
not a static read-only stream the way the MS6514 fake is (that's a
genuinely read-only meter in real life too).

Usage::

    python -m hardware_fakes.tc4 --port /tmp/ttyFAKE_TC4

Then point the app's "TC4+ (USB, PID firmware)" port field at the other
end of a virtual serial pair (see hardware_fakes/README.md).
"""
from __future__ import annotations

import argparse
import sys

import serial as pyserial

from ._thermal import ThermalDriver


def _handle_command(line: str, driver: ThermalDriver, ser, quiet: bool) -> None:
    parts = line.strip().split(",")
    if not parts or not parts[0]:
        return
    cmd = parts[0].upper()

    if cmd == "READ":
        snap = driver.snapshot()
        bt, et = snap["bt"], snap["et"]
        if bt is None or et is None:
            return
        dt = et + 15.0  # same fixed drum-space-reads-hotter approximation modbus_fz94.py's fake already uses
        response = f"0.0,{bt:.1f},{et:.1f},{dt:.1f},0.0\n"
        ser.write(response.encode("ascii"))
        if not quiet:
            print(f"t={snap['time_s']:.0f}s READ -> BT={bt:.1f} ET={et:.1f} DT={dt:.1f}", file=sys.stderr)
    elif cmd == "OT1" and len(parts) > 1:
        try:
            driver.apply_command({"heater_pct": float(parts[1])})
        except ValueError:
            pass
    elif cmd == "DCFAN" and len(parts) > 1:
        try:
            driver.apply_command({"fan_pct": float(parts[1])})
        except ValueError:
            pass
    # UNITS/OT2/everything else -- no response needed, silently ignored


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", required=True, help="serial port to serve on, e.g. /tmp/ttyFAKE_TC4")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    driver = ThermalDriver()
    driver.start()
    ser = pyserial.Serial(port=args.port, baudrate=115200, bytesize=8, parity="N", stopbits=1, timeout=1.0)
    print(f"TC4+ fake listening on {ser.port}", file=sys.stderr)

    try:
        while True:
            raw = ser.readline()
            if not raw:
                continue  # read timeout, no command arrived -- loop and wait again
            _handle_command(raw.decode("ascii", errors="replace"), driver, ser, args.quiet)
    except KeyboardInterrupt:
        pass
    finally:
        driver.stop()
        ser.close()


if __name__ == "__main__":
    main()
