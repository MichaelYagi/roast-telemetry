"""Direct Modbus RTU telemetry + control for a real roaster over USB --
bypasses Artisan entirely. Talks straight to the roaster's own PLC via
``pymodbus`` (the same library Artisan itself depends on for its Modbus
device support).

Register map default is Coffee-Tech Engineering's plain FZ-94 (USB/RTU --
*not* the FZ-94 EVO, which connects over Modbus TCP/Ethernet, a different
connection method entirely; artisan-scope.org/machines/coffeetech/).
Confirmed against real users' own working Artisan setup guides for this
exact machine, not guessed -- see the two blog series linked below.

**Temperature probes** (https://artisan-roasterscope.blogspot.com/2015/01/connecting-artisan-to-coffee-tech-fz-94.html):
each is its *own* Modbus slave device (a separate PID controller per
probe), all at register 0, not three registers on one shared slave:

- BT: slave 11, register 0
- ET: slave 13, register 0
- DT: slave 12, register 0 -- drum *space* temperature, a genuine third
  probe, not the same reading as ET and not just a relabeling of it (an
  earlier version of this module assumed that; it was wrong)
- Communication: 2400 baud, 8N1 (8 data bits, no parity, 1 stop bit),
  Modbus function 3 (read holding register)
- Raw register value is temperature x10 as an integer (e.g. `1807` ->
  180.7C) -- hence bt_divisor/et_divisor/dt_divisor below

**Air/Drum drives** (https://artisan-roasterscope.blogspot.com/2016/08/fz-94-3-connecting-drives.html
and .../2016/08/fz-94-4-taking-control.html): VFDs, not simple
registers -- each needs a run/stop word written before a frequency
command means anything, and run at a *different* baud rate/framing than
the temperature probes above:

- Air: slave 1; Drum: slave 2
- Control register 8192 (1=Stop, 2=Run), then frequency register 8193
  (value = percent x100, so 100% -> 10000)
- Air range 0-100%, Drum range 0-70% (per the blog's own drive limits)
- Communication: 19200 baud, 8N2 (2 stop bits) -- confirmed different
  from the temperature probes' 2400/8N1. A single Modbus RTU connection
  is one shared baud rate/framing for every device on it, so on hardware
  wired exactly as these posts describe, the drives cannot share a
  serial connection with the temperature probes -- hence `control_port`
  below being a genuinely separate connection, not just different
  registers on the same one.

**Burner** (https://artisan-roasterscope.blogspot.com/2016/08/fz-94-2-pushing-drum-heat-limit.html):
not a power percentage at all -- there's no "burner %" on this hardware.
It's a bang-bang (on/off + hysteresis) PID that switches the 3 heating
elements around a drum-temperature *limit*:

- Slave 12 (the same physical device as the DT probe above -- it's that
  PID controller), register 5 (0x0005), value x10 (same convention as
  the temperature reads) -- so this rides the *temperature* connection
  (2400/8N1), not the drives' 19200/8N2 one.
- This app's `heater_pct` (0-100%) is mapped onto a configurable SV
  temperature range (`burner_sv_range_c`, default 100-250C) to keep the
  existing Controls UI slider working unmodified -- that mapping itself
  isn't documented anywhere, it's this app's own approximation to fit a
  percentage-based UI onto a setpoint-based control.

A different Modbus roaster model will have a completely different
register map. None of this is hardcoded to the FZ-94 specifically --
every address, slave ID, and range is a constructor argument; the
defaults just happen to be this machine's.

RoR and CHARGE/TURNING_POINT/DRY_END/FC_START auto-detection reuse
``roast_heuristics.LiveRoastDetector``, the same logic ``artisan_bridge``
uses, since a PLC's raw registers carry temperatures only -- no roast
events, same situation as WebLCDs.

Mutually exclusive with a running Artisan on the same connection: a
serial Modbus RTU port only accepts one client at a time. If your
operator is running Artisan against this same roaster, use
``artisan_bridge`` (mirrors Artisan, view-only, no port conflict)
instead of this engine (owns the port, but can control).

Not tested against real FZ-94 hardware (none available in this
environment) -- verified against a mocked pymodbus client instead. Every
slave ID/register/baud rate above is from real users' own setup guides
for this exact machine (linked above); the actual wire-level RTU
behavior against your specific unit is still unverified, and the
heater_pct-to-SV-temperature mapping is this app's own invention, not
documented anywhere.
"""
from __future__ import annotations

from typing import Optional

from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException
from roast_heuristics import LiveRoastDetector


class ModbusEngineError(RuntimeError):
    pass


class ModbusEngine:
    def __init__(
        self,
        port: str,
        baudrate: int = 2400,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: int = 1,
        timeout: float = 0.4,
        # Temperature reads (and the Burner SV write, same physical PID
        # device as DT) -- all on this primary connection. Confirmed.
        bt_slave_id: int = 11,
        bt_register: int = 0,
        bt_divisor: float = 10.0,
        et_slave_id: int = 13,
        et_register: Optional[int] = 0,
        et_divisor: float = 10.0,
        dt_slave_id: int = 12,
        dt_register: Optional[int] = 0,
        dt_divisor: float = 10.0,
        burner_slave_id: int = 12,
        burner_register: Optional[int] = 5,
        burner_divisor: float = 10.0,
        burner_sv_range_c: tuple[float, float] = (100.0, 250.0),
        # Air/Drum drives -- a genuinely separate connection (different
        # baud/framing than the temperature bus above; see module
        # docstring). None (default) means "not configured" -- drive
        # control commands are just dropped, same as any other engine
        # missing an optional channel, rather than erroring.
        control_port: Optional[str] = None,
        control_baudrate: int = 19200,
        control_bytesize: int = 8,
        control_parity: str = "N",
        control_stopbits: int = 2,
        control_timeout: float = 0.4,
        air_slave_id: int = 1,
        air_control_register: Optional[int] = 8192,
        air_frequency_register: Optional[int] = 8193,
        air_range: tuple[float, float] = (0, 100),
        drum_slave_id: int = 2,
        drum_control_register: Optional[int] = 8192,
        drum_frequency_register: Optional[int] = 8193,
        drum_range: tuple[float, float] = (0, 70),
        dry_end_c: Optional[float] = 160.0,
        fc_start_c: Optional[float] = 196.0,
        client_cls=ModbusSerialClient,  # injectable for testing without real hardware
    ):
        if not port:
            raise ValueError("port (e.g. 'COM3') is required to connect via Modbus RTU")
        self.port = port
        self.control_port = control_port
        self.bt_slave_id = bt_slave_id
        self.bt_register = bt_register
        self.bt_divisor = bt_divisor or 1.0
        self.et_slave_id = et_slave_id
        self.et_register = et_register
        self.et_divisor = et_divisor or 1.0
        self.dt_slave_id = dt_slave_id
        self.dt_register = dt_register
        self.dt_divisor = dt_divisor or 1.0
        self.burner_slave_id = burner_slave_id
        self.burner_register = burner_register
        self.burner_divisor = burner_divisor or 1.0
        self.burner_sv_range_c = burner_sv_range_c
        self.air_slave_id = air_slave_id
        self.air_control_register = air_control_register
        self.air_frequency_register = air_frequency_register
        self.air_range = air_range
        self.drum_slave_id = drum_slave_id
        self.drum_control_register = drum_control_register
        self.drum_frequency_register = drum_frequency_register
        self.drum_range = drum_range

        self._detector = LiveRoastDetector(dry_end_c=dry_end_c, fc_start_c=fc_start_c)
        self._last_time_s = 0.0
        self._connected = False
        self._last_error: Optional[str] = None
        self._control_last_error: Optional[str] = None
        self._last_values: dict[str, float] = {}

        self._client = client_cls(
            port=port, baudrate=baudrate, bytesize=bytesize, parity=parity, stopbits=stopbits, timeout=timeout,
        )
        try:
            self._connected = bool(self._client.connect())
            if not self._connected:
                self._last_error = f"could not open serial port {port!r}"
        except Exception as exc:  # pragma: no cover - depends on local hardware/OS
            self._last_error = str(exc)
            self._connected = False

        # Drives only -- Burner rides the primary connection above (it's
        # the DT probe's own PID device). If control_port isn't given,
        # self._control_client stays None and drive commands are no-ops
        # (see _write_drive) rather than silently trying to reuse the
        # temperature connection's mismatched baud rate/framing.
        self._control_client = None
        self._control_connected = False
        if control_port:
            self._control_client = client_cls(
                port=control_port, baudrate=control_baudrate, bytesize=control_bytesize,
                parity=control_parity, stopbits=control_stopbits, timeout=control_timeout,
            )
            try:
                self._control_connected = bool(self._control_client.connect())
                if not self._control_connected:
                    self._control_last_error = f"could not open control serial port {control_port!r}"
            except Exception as exc:  # pragma: no cover - depends on local hardware/OS
                self._control_last_error = str(exc)
                self._control_connected = False

    # -- Modbus I/O -----------------------------------------------------
    def _read_register(self, address: int, slave_id: int, *, is_heartbeat: bool = False) -> Optional[int]:
        """``is_heartbeat`` gates whether this read's outcome updates
        overall connected/last_error state. BT is the heartbeat (always
        required); ET/DT are optional and shouldn't be able to mask a BT
        failure by succeeding afterward in the same tick, nor clear a
        real BT error just because it happened to work. Each temperature
        channel is its own Modbus slave device, not a register on a
        shared one -- ``slave_id`` is passed per call, not fixed on self."""
        try:
            result = self._client.read_holding_registers(address, count=1, device_id=slave_id)
            if result.isError():
                if is_heartbeat:
                    self._last_error = str(result)
                    self._connected = False
                return None
            if is_heartbeat:
                self._last_error = None
                self._connected = True
            return result.registers[0]
        except ModbusException as exc:
            if is_heartbeat:
                self._last_error = str(exc)
                self._connected = False
            return None

    # -- engine contract (matches simulator.SimulatorEngine / AlogPlayer / ArtisanBridgeEngine) --
    def tick(self, dt: float) -> dict:
        self._last_time_s += dt
        time_s = self._last_time_s

        bt_raw = self._read_register(self.bt_register, self.bt_slave_id, is_heartbeat=True)
        bt = (bt_raw / self.bt_divisor) if bt_raw is not None else None

        et = None
        if self.et_register is not None:
            et_raw = self._read_register(self.et_register, self.et_slave_id)
            et = (et_raw / self.et_divisor) if et_raw is not None else None

        dt = None
        if self.dt_register is not None:
            dt_raw = self._read_register(self.dt_register, self.dt_slave_id)
            dt = (dt_raw / self.dt_divisor) if dt_raw is not None else None

        sample = self._detector.observe(time_s, bt, et)
        sample["dt"] = dt
        sample["heater_pct"] = self._last_values.get("burner")
        sample["fan_pct"] = self._last_values.get("air")
        sample["drum_speed_pct"] = self._last_values.get("drum")
        return sample

    def get_new_events(self) -> list:
        return self._detector.get_new_events()

    def apply_command(self, cmd: dict) -> None:
        """Reuses the platform's existing heater_pct/fan_pct/drum_speed_pct
        control command shape so the existing Controls UI works
        unmodified for this engine too -- heater_pct becomes a Burner SV
        temperature write on the primary connection, fan_pct/drum_speed_pct
        become two-register VFD drive writes on the (separate) control
        connection. See module docstring for why these three don't share
        one simple "write a percentage to a register" shape."""
        if cmd.get("heater_pct") is not None:
            self._write_burner_sv(float(cmd["heater_pct"]))
        if cmd.get("fan_pct") is not None:
            self._write_drive(
                "air", self.air_slave_id, self.air_control_register, self.air_frequency_register,
                float(cmd["fan_pct"]), self.air_range,
            )
        if cmd.get("drum_speed_pct") is not None:
            self._write_drive(
                "drum", self.drum_slave_id, self.drum_control_register, self.drum_frequency_register,
                float(cmd["drum_speed_pct"]), self.drum_range,
            )

    def _write_burner_sv(self, heater_pct: float) -> None:
        if self.burner_register is None:
            return
        pct = max(0.0, min(100.0, heater_pct))
        sv_lo, sv_hi = self.burner_sv_range_c
        sv_c = sv_lo + (pct / 100.0) * (sv_hi - sv_lo)
        raw = int(round(sv_c * self.burner_divisor))
        try:
            result = self._client.write_register(self.burner_register, raw, device_id=self.burner_slave_id)
            if result.isError():
                self._last_error = str(result)
            else:
                self._last_error = None
                self._connected = True
                self._last_values["burner"] = pct
        except ModbusException as exc:
            self._last_error = str(exc)
            self._connected = False

    def _write_drive(
        self, name: str, slave_id: int, control_register: Optional[int], frequency_register: Optional[int],
        value: float, value_range: tuple[float, float],
    ) -> None:
        # No control_port configured -- nothing to write to (see
        # __init__). Not an error: same as any other engine silently
        # ignoring a control command for a channel it doesn't have.
        if self._control_client is None or control_register is None or frequency_register is None:
            return
        lo, hi = value_range
        clamped = max(lo, min(hi, value))
        try:
            run_state = 2 if clamped > 0 else 1  # 2=Run, 1=Stop
            run_result = self._control_client.write_register(control_register, run_state, device_id=slave_id)
            freq_result = self._control_client.write_register(
                frequency_register, int(round(clamped * 100)), device_id=slave_id
            )
            if run_result.isError() or freq_result.isError():
                self._control_last_error = str(run_result) if run_result.isError() else str(freq_result)
            else:
                self._control_last_error = None
                self._control_connected = True
                self._last_values[name] = clamped
        except ModbusException as exc:
            self._control_last_error = str(exc)
            self._control_connected = False

    def is_finished(self) -> bool:
        return False  # no end-of-roast signal from the PLC; stop manually from the UI

    def status(self) -> dict:
        return {
            "mode": "modbus_live",
            "port": self.port,
            "connected": self._connected,
            "last_error": self._last_error,
            "control_port": self.control_port,
            "control_connected": self._control_connected if self._control_client is not None else None,
            "control_last_error": self._control_last_error,
        }

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass
        if self._control_client is not None:
            try:
                self._control_client.close()
            except Exception:  # pragma: no cover - best-effort cleanup
                pass
