"""Direct Modbus RTU telemetry + control for a real roaster over USB --
bypasses Artisan entirely. Talks straight to the roaster's own PLC via
``pymodbus`` (the same library Artisan itself depends on for its Modbus
device support).

Register map default is Coffee-Tech Engineering's plain FZ-94 (USB/RTU --
*not* the FZ-94 EVO, which connects over Modbus TCP/Ethernet, a different
connection method entirely; artisan-scope.org/machines/coffeetech/).
Confirmed against Artisan's own shipped machine preset for this exact
model -- ``src/includes/Machines/Coffee-Tech/FZ94.aset`` in
https://github.com/artisan-roaster-scope/artisan -- and its
Modbus-handling source, ``src/artisanlib/modbusport.py``, not just
paraphrased from blog write-ups (though those independently corroborate
it: https://artisan-roasterscope.blogspot.com/2015/01/connecting-artisan-to-coffee-tech-fz-94.html,
.../2016/08/fz-94-2-pushing-drum-heat-limit.html,
.../2016/08/fz-94-3-connecting-drives.html, .../2016/08/fz-94-4-taking-control.html).

**One connection for everything.** Artisan's own shipped preset uses a
single Modbus RTU serial connection (one port, one baud rate/framing) for
every device below -- BT/ET/DT/Burner *and* Air/Drum -- as one multi-drop
bus with different slave IDs, not two separate connections. (An earlier
version of this module assumed the temperature probes and the drives
needed genuinely separate connections at different baud rates, based on
one blog's account of upgrading their setup mid-series; that was wrong --
it was a snapshot of that author's setup *before* they'd unified
everything onto one bus, not the final/shipped configuration.) Default
communication, straight from the .aset: **19200 baud, 8 data bits, no
parity, 2 stop bits.**

**Temperature probes + Burner:** each is its *own* Modbus slave device (a
separate PID controller per probe/setpoint), all at register 0 for
reading (function 3), confirmed identical in the .aset's `[Modbus]`
`inputN`/`deviceId` fields:

- BT: slave 11, register 0
- ET: slave 13, register 0
- DT: slave 12, register 0 -- drum *space* temperature, a genuine third
  probe, not the same reading as ET and not just a relabeling of it
- Burner: same slave as DT (12) -- it's that PID controller -- register 5
  (`PID_SV_register=5`, `PID_device_ID=12` in the .aset), not a power
  percentage at all. It's a bang-bang (on/off + hysteresis) PID that
  switches the 3 heating elements around a drum-temperature *limit* (the
  FZ-94's own user manual independently confirms 3 separately-switched
  1000W elements plus one "Drum heat limit controller" with its own
  setpoint -- there's no per-element percentage control on this hardware
  at all). This app's `heater_pct` (0-100%) is mapped onto a configurable
  SV temperature range (`burner_sv_range_c`, default 100-250C, comfortably
  spanning Coffee-Tech's own factory-recommended 190C starting point) to
  keep the existing Controls UI slider working unmodified -- that mapping
  itself isn't documented anywhere, it's this app's own approximation to
  fit a percentage-based UI onto a setpoint-based control. Since it's a
  holding register, `tick()` also reads it back (same address as the
  write) and reports the inverse mapping as `heater_pct` -- the PLC's
  actual current setpoint, not just an echo of this app's own last write.
  Matters on connecting to a device that's already running (an operator's
  own manual setting, or a previous session) -- without this it would
  read blank/0 until this app happened to write it itself.
- Raw register value is temperature x10 as an integer (e.g. `1807` ->
  180.7C) -- hence bt_divisor/et_divisor/dt_divisor/burner_divisor below.
  Confirmed in source, not just inferred: `modbusport.py`'s `setTarget()`
  maps the .aset's `SVmultiplier=1` to an actual x10 multiplier internally
  (1/2 mean x10/x100 respectively -- an enum, not a literal multiplier).

**Air/Drum drives:** Delta VFD-L frequency drives, not simple registers --
each needs a run/stop word written before a frequency command means
anything. Precisely attributed (the .aset's own `[Sliders]` block ships
with empty `slidercommands`, i.e. not pre-wired the way the temperature
side is, so none of this is Artisan-preset-confirmed the way BT/ET/DT/
Burner are -- it's all from one person's FZ-94/Delta VFD-L installation,
written up across a 5-part blog series, not independently corroborated by
a second source):

- Air: slave 1; Drum: slave 2 -- same VFD model, same registers, different
  slave ID each.
- Control register 8192 (2000H; 1=Stop, 2=Run), then frequency register
  8193 (2001H; value = percent x100, so 100% -> 10000, factor confirmed
  as "we send 10.000 to indicate 100% speed") -- from
  .../2016/08/fz-94-4-taking-control.html specifically.
- Feedback register 8451 -- reads the drive's *actual* current speed
  (divide raw by 100, same x100 convention as the write side), not an
  echo of the last command. Same register on both slave IDs. From
  .../2016/08/fz-94-3-connecting-drives.html specifically (that post is
  about wiring + this read register; the control/frequency *write*
  registers above are a different post in the series -- don't assume one
  post covers both directions).
- Air range 0-100%, Drum range 0-70% (per the blog's own drive limits)
- `control_port`/`control_baudrate` below exist only as an override for
  wiring that genuinely needs a second physical connection (uncommon) --
  by default (`control_port=None`) drive writes go out on the same
  primary connection as everything else, matching the confirmed single-bus
  architecture above.

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
environment) -- verified against a mocked pymodbus client instead. The
temperature/Burner slave IDs/registers/multiplier and the single-bus
19200/8N2 communication settings are confirmed against Artisan's own
shipped preset and source code (see above), the most authoritative
source available without the hardware itself; the Air/Drum register
numbers are still only blog-sourced; and the actual wire-level RTU
behavior against your specific unit is unverified either way.

For comparison, not application: the FZ94 EVO's own shipped preset
(``FZ94_EVO.aset``) is *not* empty the way the plain FZ94's is -- it has
real Air/Drum/Burner ``writeSingle`` slider commands (register 20 for
Drum, 16 for Air, 35 for Burner, all on a single slave/device ID 1, sent
over Modbus *TCP* since the EVO connects over Ethernet, not serial RTU).
That's a completely different register scheme and connection method from
this module's serial-RTU/8192/8193 defaults -- confirms the two models
genuinely don't share a register map (different hardware generations),
it does not corroborate or refute 8192/8193 for the plain FZ94 either
way. Do not apply the EVO's registers here.
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
        baudrate: int = 19200,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: int = 2,
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
        # Air/Drum drives -- share the primary connection above by
        # default (confirmed single-bus architecture; see module
        # docstring). control_port is an override for wiring that
        # genuinely needs a second physical connection, not the norm.
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
        # Real speed readback, not an echo of the last command -- same
        # register on both drives (same Delta VFD-L model, different
        # slave ID each). None disables it, falling back to the command
        # echo below (see module docstring for the source/confidence).
        air_feedback_register: Optional[int] = 8451,
        air_feedback_divisor: float = 100.0,
        drum_slave_id: int = 2,
        drum_control_register: Optional[int] = 8192,
        drum_frequency_register: Optional[int] = 8193,
        drum_range: tuple[float, float] = (0, 70),
        drum_feedback_register: Optional[int] = 8451,
        drum_feedback_divisor: float = 100.0,
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
        self.air_feedback_register = air_feedback_register
        self.air_feedback_divisor = air_feedback_divisor or 1.0
        self.drum_slave_id = drum_slave_id
        self.drum_control_register = drum_control_register
        self.drum_frequency_register = drum_frequency_register
        self.drum_range = drum_range
        self.drum_feedback_register = drum_feedback_register
        self.drum_feedback_divisor = drum_feedback_divisor or 1.0

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

        # Drives share the primary connection by default (confirmed
        # single-bus architecture -- see module docstring). control_port
        # is only for the uncommon case of wiring that genuinely needs a
        # second physical connection; self._has_separate_control tracks
        # which situation this is, for status() reporting.
        self._has_separate_control = bool(control_port)
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
        else:
            self._control_client = self._client
            self._control_connected = self._connected

    # -- Modbus I/O -----------------------------------------------------
    def _read_register(
        self, address: int, slave_id: int, *, is_heartbeat: bool = False, client=None
    ) -> Optional[int]:
        """``is_heartbeat`` gates whether this read's outcome updates
        overall connected/last_error state. BT is the heartbeat (always
        required); ET/DT are optional and shouldn't be able to mask a BT
        failure by succeeding afterward in the same tick, nor clear a
        real BT error just because it happened to work. Each temperature
        channel is its own Modbus slave device, not a register on a
        shared one -- ``slave_id`` is passed per call, not fixed on self.
        ``client`` defaults to the primary connection; Air/Drum feedback
        reads pass ``self._control_client`` instead, since that's where
        those slave IDs actually live when control_port is set."""
        client = client or self._client
        try:
            result = client.read_holding_registers(address, count=1, device_id=slave_id)
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

        # Burner SV is a holding register -- readable from the same address
        # it's written to (register 5), so this reports the PLC's actual
        # current setpoint, not just an echo of the last command this app
        # itself sent. Matters on connecting to a device an operator (or a
        # previous session) already has running: without this, heater_pct
        # would read blank/0 until this app happened to write it, which is
        # both misleading and, if the UI ever auto-sent a "starting value"
        # on top of that, actively risked clobbering real state instead of
        # reflecting it. See burner_sv_range_c for the inverse of the same
        # mapping _write_burner_sv uses.
        heater_fb = None
        if self.burner_register is not None:
            sv_raw = self._read_register(self.burner_register, self.burner_slave_id)
            if sv_raw is not None:
                sv_c = sv_raw / self.burner_divisor
                sv_lo, sv_hi = self.burner_sv_range_c
                heater_fb = max(0.0, min(100.0, (sv_c - sv_lo) / (sv_hi - sv_lo) * 100.0))
        sample["heater_pct"] = heater_fb if heater_fb is not None else self._last_values.get("burner")

        # Prefer a genuine feedback read over echoing the last command --
        # confirms the drive actually took the write, not just that pymodbus
        # didn't error. Falls back to the echo if no feedback register is
        # configured (or its read fails), so this degrades to the old
        # behavior rather than going blank.
        air_fb = None
        if self.air_feedback_register is not None:
            air_raw = self._read_register(self.air_feedback_register, self.air_slave_id, client=self._control_client)
            air_fb = (air_raw / self.air_feedback_divisor) if air_raw is not None else None
        sample["fan_pct"] = air_fb if air_fb is not None else self._last_values.get("air")

        drum_fb = None
        if self.drum_feedback_register is not None:
            drum_raw = self._read_register(
                self.drum_feedback_register, self.drum_slave_id, client=self._control_client
            )
            drum_fb = (drum_raw / self.drum_feedback_divisor) if drum_raw is not None else None
        sample["drum_speed_pct"] = drum_fb if drum_fb is not None else self._last_values.get("drum")
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
        if control_register is None or frequency_register is None:
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
            # Only meaningfully distinct from the above when a separate
            # control_port was actually configured -- otherwise drives
            # share the primary connection's own status.
            "control_port": self.control_port,
            "control_connected": self._control_connected,
            "control_last_error": self._control_last_error,
        }

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass
        # Only a distinct object (and needs its own close()) when a
        # separate control_port was configured -- otherwise it's the same
        # client as self._client, already closed above.
        if self._has_separate_control:
            try:
                self._control_client.close()
            except Exception:  # pragma: no cover - best-effort cleanup
                pass
