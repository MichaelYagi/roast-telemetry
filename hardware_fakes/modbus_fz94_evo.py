"""Fake Modbus TCP slave standing in for a Coffee-Tech FZ-94 Evo --
genuinely different from the plain FZ-94 (``modbus_fz94.py``, Modbus
RTU/USB): this one speaks real Modbus TCP (MBAP framing, no CRC) over a
plain network socket, no virtual serial port or ``socat`` needed at all
-- point the app's Configure Roast form straight at this process's own
host/port.

Serves the register map ``modbus_bridge/device_profiles.py``'s
``COFFEETECH_FZ94_EVO`` profile expects (see that module's docstring for
the full sourcing/confidence notes -- the manufacturer's own user manual
plus the manufacturer's shipped roaster-scope config for this model,
independently corroborated against a real, live device's own
configuration screens):

    - BT:      device 1, register 100  (function code 3, read),  x10 int
    - ET:      device 1, register 80   (function code 3, read),  x10 int
    - Burner:  device 1, register 35   (function code 6, write), plain
               0-100 percentage -- unlike the plain FZ-94's Burner, this
               is *not* a temperature setpoint register, just a direct
               power %, matching the real device's confirmed write
               (ModbusControlKind.DIRECT_REGISTER, write_scale=1.0)
    - Air:     device 1, register 20   (function code 6, write), 30-70
    - Drum:    device 1, register 16   (function code 6, write), 30-70

No run/stop word for Air/Drum (unlike the plain FZ-94's VFD drives) --
each is a single direct register holding the target value, so "off" is
just a write of 0 the same as any other value, clamped into range like
everything else here.

BT/ET readings and reactions to Burner/Air/Drum writes are driven by the
same thermal model as the app's own Simulator mode and the plain FZ-94
fake (see ``_thermal.py``), so this behaves like a real roaster, not a
static register dump.

Hand-rolled MBAP framing rather than pymodbus's server-side classes --
same reasoning as ``modbus_fz94.py``'s own docstring (those are
mid-migration to a new internal model as of pymodbus 3.15). The client
side (``modbus_bridge/engine.py``'s ``transport="tcp"`` path) is
unaffected -- it uses pymodbus's stable ``ModbusTcpClient``, which this
responds to like a real TCP slave.

Usage::

    python -m hardware_fakes.modbus_fz94_evo --port 5020

Then point the app's Configure Roast form at Data Source "Direct Modbus
(Ethernet)", Host `127.0.0.1`, TCP port `5020` (whatever --port was),
Device profile "Coffee-Tech FZ-94 Evo (built-in)".
"""
from __future__ import annotations

import argparse
import socket
import sys
import threading

from ._thermal import ThermalDriver

READ_HOLDING_REGISTERS = 0x03
WRITE_SINGLE_REGISTER = 0x06
MBAP_HEADER_LEN = 7  # transaction(2) + protocol(2) + length(2) + unit_id(1)

BT_REGISTER = 100
ET_REGISTER = 80
TEMP_DIVISOR = 10.0

BURNER_REGISTER = 35
BURNER_RANGE = (0.0, 100.0)

AIR_REGISTER = 20
DRUM_REGISTER = 16
DRIVE_RANGE = (30.0, 70.0)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _read_frame(sock: socket.socket, buf: bytearray) -> bytes | None:
    """Buffers a TCP stream into whole MBAP frames -- unlike RTU there's
    no CRC/resync dance needed, just the header's own Length field
    telling us exactly how many more bytes make up this request."""
    while len(buf) < MBAP_HEADER_LEN - 1:  # need through the Length field (bytes 4-5)
        chunk = sock.recv(4096)
        if not chunk:
            return None
        buf.extend(chunk)
    length = int.from_bytes(buf[4:6], "big")
    total = 6 + length  # 6 header bytes before Length's own payload count, + that many more
    while len(buf) < total:
        chunk = sock.recv(4096)
        if not chunk:
            return None
        buf.extend(chunk)
    frame, rest = bytes(buf[:total]), buf[total:]
    buf[:] = rest
    return frame


def _mbap_response(txn_id: bytes, unit_id: int, pdu: bytes) -> bytes:
    length = len(pdu) + 1  # + the unit_id byte, same convention as a real request's Length field
    return txn_id + b"\x00\x00" + length.to_bytes(2, "big") + bytes([unit_id]) + pdu


def _exception_pdu(function: int, code: int) -> bytes:
    return bytes([function | 0x80, code])


class FZ94EvoSimulator:
    def __init__(self, host: str, port: int, device_id: int, verbose: bool):
        self.host = host
        self.port = port
        self.device_id = device_id
        self.verbose = verbose
        self.driver = ThermalDriver()
        self._driver_started = False
        self._start_lock = threading.Lock()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen(1)
        # accept() wakes regularly to notice stop() -- closing a socket from another
        # thread doesn't reliably interrupt a blocked accept() on every OS.
        self._sock.settimeout(0.3)
        self.port = self._sock.getsockname()[1]  # the real port when 0 (pick any free one) was asked for
        self._stopping = False

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg, file=sys.stderr)

    def run(self) -> None:
        self._log(
            f"FZ-94 Evo fake: Modbus TCP on {self.host}:{self.port} (device id {self.device_id}) "
            f"-- clock starts on first request"
        )
        try:
            while True:
                try:
                    conn, addr = self._sock.accept()
                except socket.timeout:
                    if self._stopping:
                        break
                    continue
                except OSError:
                    if self._stopping:
                        break
                    raise
                conn.settimeout(None)
                self._log(f"client connected: {addr}")
                threading.Thread(target=self._serve_client, args=(conn,), daemon=True).start()
        except KeyboardInterrupt:
            pass
        finally:
            self.driver.stop()
            self._sock.close()

    def stop(self) -> None:
        """Ends run() (used when the server starts this fake itself)."""
        self._stopping = True
        try:
            self._sock.close()
        except OSError:
            pass

    def _serve_client(self, conn: socket.socket) -> None:
        buf = bytearray()
        try:
            while True:
                frame = _read_frame(conn, buf)
                if frame is None:
                    break
                self._handle_frame(conn, frame)
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
        finally:
            conn.close()
            self._log("client disconnected")

    def _handle_frame(self, conn: socket.socket, frame: bytes) -> None:
        if not self._driver_started:
            with self._start_lock:
                if not self._driver_started:
                    self.driver.start()
                    self._driver_started = True
                    self._log("first request received -- starting thermal clock (Charge)")

        txn_id = frame[0:2]
        unit_id = frame[6]
        function = frame[7]
        if unit_id != self.device_id:
            return  # not addressed to us -- a real device would just never see it either

        if function == READ_HOLDING_REGISTERS:
            address = int.from_bytes(frame[8:10], "big")
            count = int.from_bytes(frame[10:12], "big")
            self._handle_read(conn, txn_id, address, count)
        elif function == WRITE_SINGLE_REGISTER:
            address = int.from_bytes(frame[8:10], "big")
            value = int.from_bytes(frame[10:12], "big")
            self._handle_write(conn, txn_id, address, value)
        # else: unrecognized function code -- ignore, same as the RTU fake.

    def _handle_read(self, conn: socket.socket, txn_id: bytes, address: int, count: int) -> None:
        snap = self.driver.snapshot()
        values = []
        for offset in range(count):
            reg = address + offset
            if reg == BT_REGISTER and snap.get("bt") is not None:
                values.append(max(0, min(65535, int(round(snap["bt"] * TEMP_DIVISOR)))))
            elif reg == ET_REGISTER and snap.get("et") is not None:
                values.append(max(0, min(65535, int(round(snap["et"] * TEMP_DIVISOR)))))
            else:
                values.append(0)  # unmapped register -- real device would 0-fill or error
        pdu = bytes([READ_HOLDING_REGISTERS, count * 2])
        for v in values:
            pdu += v.to_bytes(2, "big")
        conn.sendall(_mbap_response(txn_id, self.device_id, pdu))
        self._log(f"read addr={address} count={count} -> {values}")

    def _handle_write(self, conn: socket.socket, txn_id: bytes, address: int, value: int) -> None:
        if address == BURNER_REGISTER:
            pct = _clamp(float(value), *BURNER_RANGE)
            self.driver.apply_command({"heater_pct": pct})
            self._echo_write(conn, txn_id, address, value)
            self._log(f"write Burner addr={address} value={value} -> heater_pct={pct:.0f}")
        elif address == AIR_REGISTER:
            pct = _clamp(float(value), *DRIVE_RANGE) if value > 0 else 0.0
            self.driver.apply_command({"fan_pct": pct})
            self._echo_write(conn, txn_id, address, value)
            self._log(f"write Air addr={address} value={value} -> fan_pct={pct:.0f}")
        elif address == DRUM_REGISTER:
            pct = _clamp(float(value), *DRIVE_RANGE) if value > 0 else 0.0
            self.driver.apply_command({"drum_speed_pct": pct})
            self._echo_write(conn, txn_id, address, value)
            self._log(f"write Drum addr={address} value={value} -> drum_speed_pct={pct:.0f}")
        else:
            conn.sendall(_mbap_response(txn_id, self.device_id, _exception_pdu(WRITE_SINGLE_REGISTER, 2)))  # illegal address

    def _echo_write(self, conn: socket.socket, txn_id: bytes, address: int, value: int) -> None:
        # Standard Modbus write-single-register response is an echo of the request.
        pdu = bytes([WRITE_SINGLE_REGISTER]) + address.to_bytes(2, "big") + value.to_bytes(2, "big")
        conn.sendall(_mbap_response(txn_id, self.device_id, pdu))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1", help="interface to listen on (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5020, help="TCP port (default 5020 -- the real device's own default, 502, needs root on most systems)")
    parser.add_argument("--device-id", type=int, default=1)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    FZ94EvoSimulator(args.host, args.port, args.device_id, verbose=not args.quiet).run()


if __name__ == "__main__":
    main()
