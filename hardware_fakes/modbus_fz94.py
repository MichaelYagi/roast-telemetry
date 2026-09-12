"""Fake Modbus RTU slave standing in for a Coffee-Tech FZ94 EVO.

Serves the exact register map ``modbus_bridge/engine.py`` already expects
(taken from Artisan's own FZ94_EVO.aset preset, see that module's
docstring for the citation):

    - BT:      holding register 100 (function code 3, read)
    - ET/DT:   holding register 80  (function code 3, read)
    - Air:     holding register 20  (function code 6, write, 30-70)
    - Drum:    holding register 16  (function code 6, write, 30-70)
    - Burner:  holding register 35  (function code 6, write, 30-100)

BT/ET readings and reactions to Air/Drum/Burner writes are driven by the
same thermal model as the app's own Simulator mode (see ``_thermal.py``),
so this behaves like a real roaster, not a static register dump.

Hand-rolled RTU framing (CRC16, function codes 3 and 6 only) rather than
built on pymodbus's server-side datastore classes: as of pymodbus 3.15
those are mid-migration to a new SimData/SimDevice model and several are
already deprecated, so a small amount of protocol code we fully control
is more reliable here than chasing a moving internal API. The client
side (``modbus_bridge/engine.py``) is unaffected -- it uses pymodbus's
stable ``ModbusSerialClient``, which this responds to like a real slave.

Usage::

    python -m hardware_fakes.modbus_fz94 --port /tmp/ttyFAKE_ROASTER

Then point the app's "Direct Modbus" ``modbus_port`` field at the other
end of a virtual serial pair (see hardware_fakes/README.md for the
``socat`` setup) -- e.g. /tmp/ttyFAKE_BRIDGE.
"""
from __future__ import annotations

import argparse
import sys

import serial as pyserial

from ._thermal import ThermalDriver

READ_HOLDING_REGISTERS = 0x03
WRITE_SINGLE_REGISTER = 0x06
FRAME_LEN = 8  # both function 3 and function 6 *requests* are 8 bytes

# address -> (thermal snapshot key, clamp range or None for read-only)
WRITABLE_REGISTERS = {
    20: ("fan_pct", (30, 70)),
    16: ("drum_speed_pct", (30, 70)),
    35: ("heater_pct", (30, 100)),
}
READABLE_REGISTERS = {
    100: "bt",
    80: "et",
}


def _crc16(data: bytes) -> bytes:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc.to_bytes(2, "little")


def _exception_response(slave_id: int, function: int, code: int) -> bytes:
    body = bytes([slave_id, function | 0x80, code])
    return body + _crc16(body)


class FZ94Simulator:
    def __init__(self, port: str, slave_id: int, baudrate: int, verbose: bool):
        self.slave_id = slave_id
        self.verbose = verbose
        self.driver = ThermalDriver()
        self._driver_started = False
        self.ser = pyserial.Serial(
            port=port, baudrate=baudrate, bytesize=8, parity="N", stopbits=2, timeout=0.2,
        )

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg, file=sys.stderr)

    def run(self) -> None:
        # Deliberately doesn't start the thermal clock here -- it starts
        # lazily on the first request this fake actually receives (see
        # _handle_frame), so "Charge" happens exactly when the app first
        # connects, not whenever this process happened to launch. Without
        # this, leaving the fake running for a few minutes before starting
        # a roast in the app (easy to do -- e.g. while setting up socat,
        # configuring the form) means the app's recording starts mid-roast
        # instead of at a fresh Charge.
        self._log(f"FZ94 EVO fake listening on {self.ser.port} (slave_id={self.slave_id}) -- clock starts on first request")
        buf = b""
        try:
            while True:
                chunk = self.ser.read(FRAME_LEN)
                if not chunk:
                    continue
                buf += chunk
                # Slide a window until we find a byte offset whose 8 bytes
                # pass CRC -- robust against reading mid-frame after startup.
                while len(buf) >= FRAME_LEN:
                    frame, buf = buf[:FRAME_LEN], buf[FRAME_LEN:]
                    if _crc16(frame[:-2]) != frame[-2:]:
                        self._log(f"bad CRC, resyncing: {frame.hex()}")
                        continue
                    self._handle_frame(frame)
        except KeyboardInterrupt:
            pass
        finally:
            self.driver.stop()
            self.ser.close()

    def _handle_frame(self, frame: bytes) -> None:
        if not self._driver_started:
            self.driver.start()
            self._driver_started = True
            self._log("first request received -- starting thermal clock (Charge)")
        slave_id, function = frame[0], frame[1]
        if slave_id != self.slave_id:
            return  # not addressed to us
        address = int.from_bytes(frame[2:4], "big")

        if function == READ_HOLDING_REGISTERS:
            count = int.from_bytes(frame[4:6], "big")
            self._handle_read(address, count)
        elif function == WRITE_SINGLE_REGISTER:
            value = int.from_bytes(frame[4:6], "big")
            self._handle_write(address, value)
        else:
            self.ser.write(_exception_response(self.slave_id, function, 1))  # illegal function

    def _handle_read(self, address: int, count: int) -> None:
        snap = self.driver.snapshot()
        values = []
        for offset in range(count):
            reg = address + offset
            key = READABLE_REGISTERS.get(reg)
            if key is not None and snap.get(key) is not None:
                values.append(max(0, min(65535, int(round(snap[key])))))
            else:
                values.append(0)  # unmapped register -- real device would 0-fill or error
        body = bytes([self.slave_id, READ_HOLDING_REGISTERS, count * 2])
        for v in values:
            body += v.to_bytes(2, "big")
        self.ser.write(body + _crc16(body))
        self._log(f"read addr={address} count={count} -> {values}")

    def _handle_write(self, address: int, value: int) -> None:
        entry = WRITABLE_REGISTERS.get(address)
        if entry is None:
            self.ser.write(_exception_response(self.slave_id, WRITE_SINGLE_REGISTER, 2))  # illegal address
            return
        channel_key, (lo, hi) = entry
        clamped = max(lo, min(hi, value))
        self.driver.apply_command({channel_key: clamped})
        # Standard Modbus write-single-register response is an echo of the request.
        body = bytes([self.slave_id, WRITE_SINGLE_REGISTER]) + address.to_bytes(2, "big") + value.to_bytes(2, "big")
        self.ser.write(body + _crc16(body))
        self._log(f"write addr={address} value={value} -> {channel_key}={clamped}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", required=True, help="serial port to listen on, e.g. /tmp/ttyFAKE_ROASTER")
    parser.add_argument("--slave-id", type=int, default=1)
    parser.add_argument("--baudrate", type=int, default=57600)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    FZ94Simulator(args.port, args.slave_id, args.baudrate, verbose=not args.quiet).run()


if __name__ == "__main__":
    main()
