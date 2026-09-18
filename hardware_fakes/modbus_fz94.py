"""Fake Modbus RTU slave(s) standing in for a Coffee-Tech FZ-94 (plain,
USB/RTU -- not the EVO, which is Modbus TCP over Ethernet, a different
connection method entirely; see ``modbus_bridge/engine.py``'s docstring).

Serves the register/slave map ``modbus_bridge/engine.py`` expects,
confirmed against Artisan's own shipped FZ-94 preset/source (see that
module's docstring for the full citations):

    - BT:      slave 11, register 0    (function code 3, read),  x10 int
    - ET:      slave 13, register 0    (function code 3, read),  x10 int
    - DT:      slave 12, register 0    (function code 3, read),  x10 int
    - Burner:  slave 12, register 5    (function code 3 read *and* code 6
               write), x10 int, a *setpoint temperature* (bang-bang PID),
               not a power % -- readable from the same register it's
               written to, so this also answers "what's it actually set
               to right now" for a device connected to mid-roast
    - Drum:    slave 1,  registers 8192 (run/stop, write) + 8193 (freq,
               write, x100) + 8451 (actual speed, read, x100)
    - Air:     slave 2,  same three registers, its own slave

One connection handles all of it by default (19200 baud, 8N2 -- Artisan's
own shipped preset's exact settings), matching the real engine's default
of sharing one connection for BT/ET/DT/Burner *and* Air/Drum. Passing
``--drive-port`` instead runs a second bus concurrently (on a background
thread) for the uncommon case of wiring that genuinely needs two
connections -- matching the real engine's optional ``control_port``
override. Either way both buses (if two are used) share one
``ThermalDriver``, so a command from either affects the same simulated
roast.

BT/ET/DT readings and reactions to Burner/Air/Drum writes are driven by
the same thermal model as the app's own Simulator mode (see
``_thermal.py``), so this behaves like a real roaster, not a static
register dump. DT (drum space temperature) isn't something that shared
model computes on its own -- approximated here as ET plus a fixed offset
(the drum space reads hotter than the ET probe), not from any real
measurement, since none of the sources found for this feature described
its actual thermal relationship to BT/ET. Burner is written as an SV
temperature (matching the real protocol) but fed into the shared model
as an equivalent heater_pct via the same burner_sv_range_c mapping the
real engine uses in reverse -- see ``_sv_to_heater_pct`` below.

Hand-rolled RTU framing (CRC16, function codes 3 and 6 only) rather than
built on pymodbus's server-side datastore classes: as of pymodbus 3.15
those are mid-migration to a new SimData/SimDevice model and several are
already deprecated, so a small amount of protocol code we fully control
is more reliable here than chasing a moving internal API. The client
side (``modbus_bridge/engine.py``) is unaffected -- it uses pymodbus's
stable ``ModbusSerialClient``, which this responds to like a real slave.

Usage::

    # BT/ET/DT/Burner + Air/Drum, all on one connection (the normal case)
    python -m hardware_fakes.modbus_fz94 --port /tmp/ttyFAKE_ROASTER

    # only if your own wiring genuinely needs a second connection for
    # Air/Drum -- matches the real engine's control_port override
    python -m hardware_fakes.modbus_fz94 --port /tmp/ttyFAKE_ROASTER \\
        --drive-port /tmp/ttyFAKE_ROASTER_DRIVES

Then point the app's "Direct Modbus" ``modbus_port`` field (and, only if
using --drive-port, ``modbus_control_port`` too) at the other end of each
virtual serial pair (see hardware_fakes/README.md for the ``socat`` setup).
"""
from __future__ import annotations

import argparse
import sys
import threading

import serial as pyserial

from ._thermal import ThermalDriver

READ_HOLDING_REGISTERS = 0x03
WRITE_SINGLE_REGISTER = 0x06
FRAME_LEN = 8  # both function 3 and function 6 *requests* are 8 bytes

TEMP_REGISTER = 0  # every temperature slave only exposes this one register
DT_OFFSET_C = 12.0  # DT approximation -- see module docstring
BURNER_REGISTER = 5
BURNER_DIVISOR = 10.0
BURNER_SV_RANGE_C = (100.0, 250.0)  # must match ModbusEngine's own default

DRIVE_CONTROL_REGISTER = 8192
DRIVE_FREQUENCY_REGISTER = 8193
DRIVE_RUN, DRIVE_STOP = 2, 1
DRIVE_FEEDBACK_REGISTER = 8451  # actual speed readback, same x100 convention as the frequency write


def _sv_to_heater_pct(sv_c: float) -> float:
    """Inverse of the real engine's heater_pct -> SV-temperature mapping
    -- lets this fake feed a written setpoint back into the shared
    thermal model (which only understands heater_pct) as an equivalent
    percentage, rather than not reacting to Burner writes at all."""
    lo, hi = BURNER_SV_RANGE_C
    if hi == lo:
        return 0.0
    pct = (sv_c - lo) / (hi - lo) * 100.0
    return max(0.0, min(100.0, pct))


def _heater_pct_to_sv(pct: float) -> float:
    """Forward direction of the same mapping -- lets a Burner SV *read*
    report back the thermal model's current heater_pct as the setpoint a
    real PLC would hold in that same holding register, mirroring
    ModbusEngine.tick()'s own new SV readback."""
    lo, hi = BURNER_SV_RANGE_C
    return lo + (max(0.0, min(100.0, pct)) / 100.0) * (hi - lo)


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


class _SerialBus:
    """One physical Modbus RTU connection. The FZ-94's temperature/burner
    slaves and its Air/Drum drives run at different baud rates/framing in
    every real setup documented for this machine (see module docstring),
    so they're always at least potentially two of these, not one."""

    def __init__(self, port: str, baudrate: int, stopbits: int, name: str, log):
        self.name = name
        self._log = log
        self.ser = pyserial.Serial(
            port=port, baudrate=baudrate, bytesize=8, parity="N", stopbits=stopbits, timeout=0.2,
        )

    def run(self, on_frame) -> None:
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
                        self._log(f"[{self.name}] bad CRC, resyncing: {frame.hex()}")
                        continue
                    on_frame(self, frame)
        except KeyboardInterrupt:
            pass
        finally:
            self.ser.close()


class FZ94Simulator:
    def __init__(
        self,
        port: str,
        bt_slave_id: int,
        et_slave_id: int,
        dt_slave_id: int,
        burner_slave_id: int,
        baudrate: int,
        drive_port: str | None,
        air_slave_id: int,
        drum_slave_id: int,
        drive_baudrate: int,
        verbose: bool,
    ):
        self.temp_slaves = {bt_slave_id: "bt", et_slave_id: "et", dt_slave_id: "dt"}
        self.burner_slave_id = burner_slave_id
        self.drive_slaves = {air_slave_id: "fan_pct", drum_slave_id: "drum_speed_pct"}
        self.verbose = verbose
        self.driver = ThermalDriver()
        self._driver_started = False
        self._start_lock = threading.Lock()

        self.temp_bus = _SerialBus(port, baudrate, stopbits=2, name="temp/burner", log=self._log)
        self.drive_bus = (
            _SerialBus(drive_port, drive_baudrate, stopbits=2, name="drives", log=self._log)
            if drive_port
            else None
        )

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg, file=sys.stderr)

    def run(self) -> None:
        # Deliberately doesn't start the thermal clock here -- it starts
        # lazily on the first request this fake actually receives on
        # *either* bus (see _handle_frame), so "Charge" happens exactly
        # when the app first connects, not whenever this process happened
        # to launch. Without this, leaving the fake running for a few
        # minutes before starting a roast in the app (easy to do -- e.g.
        # while setting up socat, configuring the form) means the app's
        # recording starts mid-roast instead of at a fresh Charge.
        self._log(
            f"FZ-94 fake: temp/burner bus on {self.temp_bus.ser.port} "
            f"(BT={[k for k, v in self.temp_slaves.items() if v == 'bt'][0]}, "
            f"ET={[k for k, v in self.temp_slaves.items() if v == 'et'][0]}, "
            f"DT={[k for k, v in self.temp_slaves.items() if v == 'dt'][0]}, "
            f"Burner=slave {self.burner_slave_id}) -- clock starts on first request"
        )
        drive_thread = None
        if self.drive_bus is not None:
            self._log(
                f"FZ-94 fake: drive bus on {self.drive_bus.ser.port} "
                f"(Air={[k for k, v in self.drive_slaves.items() if v == 'fan_pct'][0]}, "
                f"Drum={[k for k, v in self.drive_slaves.items() if v == 'drum_speed_pct'][0]})"
            )
            drive_thread = threading.Thread(target=self.drive_bus.run, args=(self._handle_frame,), daemon=True)
            drive_thread.start()
        try:
            self.temp_bus.run(self._handle_frame)
        finally:
            self.driver.stop()
            if drive_thread is not None:
                drive_thread.join(timeout=1)

    def _handle_frame(self, bus: _SerialBus, frame: bytes) -> None:
        if not self._driver_started:
            with self._start_lock:
                if not self._driver_started:
                    self.driver.start()
                    self._driver_started = True
                    self._log("first request received (either bus) -- starting thermal clock (Charge)")
        slave_id, function = frame[0], frame[1]
        address = int.from_bytes(frame[2:4], "big")

        if function == READ_HOLDING_REGISTERS:
            count = int.from_bytes(frame[4:6], "big")
            # Checked before temp_slaves: burner_slave_id defaults to the
            # same slave as DT (12, same physical PID controller), so a
            # read for *this* register has to be distinguished by address,
            # not slave ID alone, before falling into the DT-shaped read.
            if slave_id == self.burner_slave_id and address == BURNER_REGISTER:
                self._handle_burner_read(bus, address, count)
                return
            temp_key = self.temp_slaves.get(slave_id)
            if temp_key is not None:
                self._handle_temp_read(bus, slave_id, temp_key, address, count)
                return
            drive_key = self.drive_slaves.get(slave_id)
            if drive_key is not None:
                self._handle_drive_read(bus, slave_id, drive_key, address, count)
                return
            # else: not one of our slaves -- ignore.
        elif function == WRITE_SINGLE_REGISTER:
            value = int.from_bytes(frame[4:6], "big")
            if slave_id == self.burner_slave_id:
                self._handle_burner_write(bus, address, value)
            elif slave_id in self.drive_slaves:
                self._handle_drive_write(bus, slave_id, self.drive_slaves[slave_id], address, value)
            # else: not one of our slaves -- ignore.
        # else: unrecognized function code -- nothing to reply as
        # meaningfully (a real device would send an illegal-function
        # exception, but that needs a canonical slave_id this fake
        # doesn't have just one of anymore), so just ignore it.

    def _handle_temp_read(self, bus: _SerialBus, slave_id: int, key: str, address: int, count: int) -> None:
        snap = self.driver.snapshot()
        # DT isn't something the shared thermal model computes -- see
        # module docstring for why this offset-from-ET approximation.
        temp_c = (snap["et"] + DT_OFFSET_C) if key == "dt" and snap.get("et") is not None else snap.get(key)
        values = []
        for offset in range(count):
            if address + offset == TEMP_REGISTER and temp_c is not None:
                values.append(max(0, min(65535, int(round(temp_c * 10)))))
            else:
                values.append(0)  # unmapped register -- real device would 0-fill or error
        body = bytes([slave_id, READ_HOLDING_REGISTERS, count * 2])
        for v in values:
            body += v.to_bytes(2, "big")
        bus.ser.write(body + _crc16(body))
        self._log(f"[{bus.name}] read slave={slave_id} ({key}) addr={address} count={count} -> {values}")

    def _handle_drive_read(self, bus: _SerialBus, slave_id: int, channel_key: str, address: int, count: int) -> None:
        # Real speed readback (register 8451), not the last-commanded value
        # -- this fake's thermal model applies commands instantaneously
        # (no ramp/lag), so in practice it'll match what was last written,
        # but it's read from the driver's own current state, the same way
        # a real VFD would report *its* actual state, not just echo.
        pct = self.driver.snapshot().get(channel_key)
        values = []
        for offset in range(count):
            if address + offset == DRIVE_FEEDBACK_REGISTER and pct is not None:
                values.append(max(0, min(65535, int(round(pct * 100)))))
            else:
                values.append(0)  # unmapped register -- real device would 0-fill or error
        body = bytes([slave_id, READ_HOLDING_REGISTERS, count * 2])
        for v in values:
            body += v.to_bytes(2, "big")
        bus.ser.write(body + _crc16(body))
        self._log(f"[{bus.name}] read slave={slave_id} ({channel_key}) addr={address} count={count} -> {values}")

    def _handle_burner_read(self, bus: _SerialBus, address: int, count: int) -> None:
        heater_pct = self.driver.snapshot().get("heater_pct")
        values = []
        for offset in range(count):
            if address + offset == BURNER_REGISTER and heater_pct is not None:
                sv_c = _heater_pct_to_sv(heater_pct)
                values.append(max(0, min(65535, int(round(sv_c * BURNER_DIVISOR)))))
            else:
                values.append(0)
        body = bytes([self.burner_slave_id, READ_HOLDING_REGISTERS, count * 2])
        for v in values:
            body += v.to_bytes(2, "big")
        bus.ser.write(body + _crc16(body))
        self._log(f"[{bus.name}] read slave={self.burner_slave_id} (Burner SV) addr={address} count={count} -> {values}")

    def _handle_burner_write(self, bus: _SerialBus, address: int, value: int) -> None:
        if address != BURNER_REGISTER:
            bus.ser.write(_exception_response(self.burner_slave_id, WRITE_SINGLE_REGISTER, 2))  # illegal address
            return
        sv_c = value / BURNER_DIVISOR
        heater_pct = _sv_to_heater_pct(sv_c)
        self.driver.apply_command({"heater_pct": heater_pct})
        self._echo_write(bus, self.burner_slave_id, address, value)
        self._log(f"[{bus.name}] write slave={self.burner_slave_id} (Burner SV) addr={address} value={value} -> {sv_c:.1f}C (heater_pct={heater_pct:.0f})")

    def _handle_drive_write(self, bus: _SerialBus, slave_id: int, channel_key: str, address: int, value: int) -> None:
        if address == DRIVE_CONTROL_REGISTER:
            # Run/stop word -- nothing to apply to the thermal model on
            # its own; the frequency write (below) carries the actual
            # value, same as the real engine only sends a meaningful
            # command via _write_drive's pair of writes together.
            self._echo_write(bus, slave_id, address, value)
            self._log(f"[{bus.name}] write slave={slave_id} (run/stop) addr={address} value={value}")
            return
        if address == DRIVE_FREQUENCY_REGISTER:
            pct = value / 100.0
            self.driver.apply_command({channel_key: pct})
            self._echo_write(bus, slave_id, address, value)
            self._log(f"[{bus.name}] write slave={slave_id} (frequency) addr={address} value={value} -> {channel_key}={pct:.0f}")
            return
        bus.ser.write(_exception_response(slave_id, WRITE_SINGLE_REGISTER, 2))  # illegal address

    def _echo_write(self, bus: _SerialBus, slave_id: int, address: int, value: int) -> None:
        # Standard Modbus write-single-register response is an echo of the request.
        body = bytes([slave_id, WRITE_SINGLE_REGISTER]) + address.to_bytes(2, "big") + value.to_bytes(2, "big")
        bus.ser.write(body + _crc16(body))


def _exception_response(slave_id: int, function: int, code: int) -> bytes:
    body = bytes([slave_id, function | 0x80, code])
    return body + _crc16(body)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", required=True, help="temperature/burner bus serial port, e.g. /tmp/ttyFAKE_ROASTER")
    parser.add_argument("--baudrate", type=int, default=19200)
    parser.add_argument("--bt-slave-id", type=int, default=11)
    parser.add_argument("--et-slave-id", type=int, default=13)
    parser.add_argument("--dt-slave-id", type=int, default=12)
    parser.add_argument("--burner-slave-id", type=int, default=12)
    parser.add_argument("--drive-port", default=None, help="optional: Air/Drum drive bus serial port, e.g. /tmp/ttyFAKE_ROASTER_DRIVES")
    parser.add_argument("--drive-baudrate", type=int, default=19200)
    parser.add_argument("--air-slave-id", type=int, default=2)
    parser.add_argument("--drum-slave-id", type=int, default=1)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    FZ94Simulator(
        args.port,
        args.bt_slave_id,
        args.et_slave_id,
        args.dt_slave_id,
        args.burner_slave_id,
        args.baudrate,
        args.drive_port,
        args.air_slave_id,
        args.drum_slave_id,
        args.drive_baudrate,
        verbose=not args.quiet,
    ).run()


if __name__ == "__main__":
    main()
