# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Acknowledgement: this module's default Modbus register map and serial
# settings for the Coffee-Tech FZ-94 were worked out with reference to the
# machine settings and Modbus handling published by the Artisan project
# (https://github.com/artisan-roaster-scope/artisan; Copyright (C) 2010-2026
# The Artisan team, licensed AGPL-3.0-or-later). This file is licensed under
# the same terms, like Roast Telemetry as a whole (see LICENSE).
"""Direct Modbus RTU telemetry + control for a real roaster over USB --
talks straight to the roaster's own PLC via ``pymodbus``.

Register map default is Coffee-Tech Engineering's plain FZ-94 (USB/RTU --
*not* the FZ-94 EVO, which connects over Modbus TCP/Ethernet, a different
connection method entirely). Live-tested against a real FZ-94 (see below).

**One connection for everything.** The FZ-94 uses a single Modbus RTU
serial connection (one port, one baud rate/framing) for every device
below -- BT/ET/DT/Burner *and* Air/Drum -- as one multi-drop bus with
different slave IDs, not two separate connections. (An earlier version of
this module assumed the temperature probes and the drives needed
genuinely separate connections at different baud rates, based on one
write-up's account of a setup from *before* it was unified onto one bus;
that was wrong.) Default communication: **19200 baud, 8 data bits, no
parity, 2 stop bits.**

**Temperature probes + Burner:** each is its *own* Modbus slave device (a
separate PID controller per probe/setpoint), all at register 0 for
reading (function 3):

- BT: slave 11, register 0
- ET: slave 13, register 0
- DT: slave 12, register 0 -- drum *space* temperature, a genuine third
  probe, not the same reading as ET and not just a relabeling of it
- Burner: same slave as DT (12) -- it's that PID controller -- register 5,
  not a power
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
  Confirmed on a real FZ-94, not just inferred.

**Air/Drum drives:** Delta VFD-L frequency drives, not simple registers --
each needs a run/stop word written before a frequency command means
anything. The slave-ID assignment below (Air=2, Drum=1) is confirmed
against a real, live, independently control-tested FZ-94 (Drum
writes go to slave 1, Fan/Air writes go to slave 2).

This *replaces* an earlier default that had them the other way around
(Air=1, Drum=2), which was NOT simply wrong -- it came from a real,
working installation too: the published write-up this app's register
*numbers* (8192/8193/8451) and value conventions come from
explicitly documents setting the VFD's own slaveID parameter (Delta
VFD-L parameter 9-00, itself a configurable setting, not a fixed
constant) to "d1 (Air Flow Controller)" and "d2 (Drum Speed Controller)"
on that author's own unit -- the opposite of what's used as the default
now. Since this wiring is an aftermarket addition (the FZ-94 doesn't ship
with it -- see below) that two different real, working installations
have configured oppositely, there may genuinely be no single universal
default -- whoever wires a given unit picks these slave IDs themselves.
Air=2/Drum=1 is used as the default only because it's the more recent,
more directly relevant confirmation (verified by control tests on a real
unit, not just a register write that happens to work), not because that
write-up's account was somehow mistaken. Test Connection's read+write check and the
device profile override fields exist precisely for this -- don't assume
either default without confirming against your own unit.

- Drum: slave 1; Air: slave 2 -- same VFD model, same registers, different
  slave ID each.
- Control register 8192 (2000H; 1=Stop, 2=Run), then frequency register
  8193 (2001H; value = percent x100, so 100% -> 10000, factor confirmed
  as "we send 10.000 to indicate 100% speed") -- register numbers and
  this convention from the published control write-up.
- Feedback register 8451 -- reads the drive's *actual* current speed
  (divide raw by 100, same x100 convention as the write side), not an
  echo of the last command. Same register on both slave IDs. From
  the published wiring write-up specifically (it covers this read
  register; the control/frequency *write* registers above come from a
  different write-up in the series -- don't assume one covers both
  directions).
- Air range 0-100%, Drum range 0-70% (per the published drive limits)
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
``roast_heuristics.LiveRoastDetector``, since a PLC's raw registers carry
temperatures only -- no roast events.

Mutually exclusive with any other program using the same connection: a
serial Modbus RTU port only accepts one client at a time. If another
program is also running against this same roaster, pick one owner of the port --
this engine can't share it.

Live-tested against a real FZ-94: read-only and read+write Test
Connection checks, plus independent Air and Drum control tests, all
passed on real hardware (see the Air/Drum slave-ID correction above,
which came directly out of that same session). The temperature/Burner
slave IDs/registers/multiplier and the single-bus 19200/8N2
communication settings are also confirmed against the machine's stock
configuration; the Air/Drum register *numbers* (not the slave
IDs, now live-confirmed) remain sourced from published write-ups only.

For comparison, not application: the FZ94 EVO's published configuration
has real Air/Drum/Burner single-register write commands (register 20 for
Drum, 16 for Air, 35 for Burner, all on a single slave/device ID 1, sent
over Modbus *TCP* since the EVO connects over Ethernet, not serial RTU).
That's a completely different register scheme and connection method from
this module's serial-RTU/8192/8193 defaults -- confirms the two models
genuinely don't share a register map (different hardware generations),
it does not corroborate or refute 8192/8193 for the plain FZ94 either
way. Do not apply the EVO's registers here.
"""
from __future__ import annotations

import logging
from typing import Optional

from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException

logger = logging.getLogger(__name__)

from backend.app.models import (
    DeviceProfile,
    ModbusChannelRole,
    ModbusControlChannel,
    ModbusControlKind,
    ModbusTempChannel,
)
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
        burner_sv_range_c: tuple[float, float] = (100.0, 260.0),
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
        air_slave_id: int = 2,
        air_control_register: Optional[int] = 8192,
        air_frequency_register: Optional[int] = 8193,
        air_range: tuple[float, float] = (0, 100),
        # Real speed readback, not an echo of the last command -- same
        # register on both drives (same Delta VFD-L model, different
        # slave ID each). None disables it, falling back to the command
        # echo below (see module docstring for the source/confidence).
        air_feedback_register: Optional[int] = 8451,
        air_feedback_divisor: float = 100.0,
        # raw = pct * frequency_scale + frequency_offset, written to
        # air/drum_frequency_register (see _write_vfd_drive below). 100/0
        # on the FZ-94's Delta VFD-L; exposed for a different VFD that
        # needs a genuinely different linear mapping.
        air_frequency_scale: float = 100.0,
        air_frequency_offset: float = 0.0,
        drum_slave_id: int = 1,
        drum_control_register: Optional[int] = 8192,
        drum_frequency_register: Optional[int] = 8193,
        drum_range: tuple[float, float] = (0, 70),
        drum_feedback_register: Optional[int] = 8451,
        drum_feedback_divisor: float = 100.0,
        drum_frequency_scale: float = 100.0,
        drum_frequency_offset: float = 0.0,
        dry_end_c: Optional[float] = 160.0,
        fc_start_c: Optional[float] = 196.0,
        # Opt-in -- off by default, matching real hardware meaning a real
        # operator marking milestones by hand. When on, Charge/Dry End/FC
        # Start auto-fire from the BT curve same as simulator/alog_playback
        # do, but manual clicks still work as an override (see
        # RoastSession.add_event's notify_manual_charge/mark_milestone_fired
        # calls, which keep the detector's own state in sync either way so
        # a manual override never also produces a duplicate auto-fired copy).
        detect_milestones: bool = False,
        client_cls=ModbusSerialClient,  # injectable for testing without real hardware
    ):
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
        self.air_frequency_scale = air_frequency_scale
        self.air_frequency_offset = air_frequency_offset
        self.drum_slave_id = drum_slave_id
        self.drum_control_register = drum_control_register
        self.drum_frequency_register = drum_frequency_register
        self.drum_range = drum_range
        self.drum_feedback_register = drum_feedback_register
        self.drum_feedback_divisor = drum_feedback_divisor or 1.0
        self.drum_frequency_scale = drum_frequency_scale
        self.drum_frequency_offset = drum_frequency_offset

        # Everything above is kept exactly as before (including every
        # individual attribute -- tests assert on e.g. engine.bt_slave_id
        # directly) purely for backward compatibility; tick()/
        # apply_command() below now actually run off these two generic
        # lists instead, built from those same flat args. See
        # DeviceProfile/ModbusTempChannel/ModbusControlChannel in
        # backend/app/models.py -- this constructor is the compatibility
        # shim for the FZ-94's own (or an overridden) flat register map;
        # from_profile() below is the other way in, building the same
        # two lists directly from a saved/built-in DeviceProfile instead.
        self.temp_channels, self.control_channels = self._channels_from_flat_args(
            bt_slave_id=self.bt_slave_id, bt_register=self.bt_register, bt_divisor=self.bt_divisor,
            et_slave_id=self.et_slave_id, et_register=self.et_register, et_divisor=self.et_divisor,
            dt_slave_id=self.dt_slave_id, dt_register=self.dt_register, dt_divisor=self.dt_divisor,
            burner_slave_id=self.burner_slave_id, burner_register=self.burner_register,
            burner_divisor=self.burner_divisor, burner_sv_range_c=self.burner_sv_range_c,
            air_slave_id=self.air_slave_id, air_control_register=self.air_control_register,
            air_frequency_register=self.air_frequency_register, air_range=self.air_range,
            air_feedback_register=self.air_feedback_register, air_feedback_divisor=self.air_feedback_divisor,
            air_frequency_scale=self.air_frequency_scale, air_frequency_offset=self.air_frequency_offset,
            drum_slave_id=self.drum_slave_id, drum_control_register=self.drum_control_register,
            drum_frequency_register=self.drum_frequency_register, drum_range=self.drum_range,
            drum_feedback_register=self.drum_feedback_register, drum_feedback_divisor=self.drum_feedback_divisor,
            drum_frequency_scale=self.drum_frequency_scale, drum_frequency_offset=self.drum_frequency_offset,
        )

        self._dry_end_c = dry_end_c
        self._fc_start_c = fc_start_c
        # Real hardware means a real operator standing at the machine --
        # milestones are marked by hand by default (this platform's own
        # event buttons), not guessed from the temperature curve. Opt-in
        # per roast (detect_milestones above) to auto-fire from the BT
        # curve instead, same as simulator/alog_playback do. See
        # roast_heuristics.LiveRoastDetector's own docstring.
        self._detect_milestones = detect_milestones
        self._detector = LiveRoastDetector(dry_end_c=dry_end_c, fc_start_c=fc_start_c, detect_milestones=detect_milestones)
        self._last_time_s = 0.0
        self._connected = False
        self._last_error: Optional[str] = None
        self._control_last_error: Optional[str] = None
        self._last_values: dict[str, float] = {}

        self._open_connections(
            port, baudrate, bytesize, parity, stopbits, timeout,
            control_port, control_baudrate, control_bytesize, control_parity, control_stopbits, control_timeout,
            client_cls,
        )

    @staticmethod
    def _channels_from_flat_args(
        *, bt_slave_id, bt_register, bt_divisor, et_slave_id, et_register, et_divisor,
        dt_slave_id, dt_register, dt_divisor, burner_slave_id, burner_register, burner_divisor, burner_sv_range_c,
        air_slave_id, air_control_register, air_frequency_register, air_range, air_feedback_register, air_feedback_divisor,
        air_frequency_scale, air_frequency_offset,
        drum_slave_id, drum_control_register, drum_frequency_register, drum_range, drum_feedback_register, drum_feedback_divisor,
        drum_frequency_scale, drum_frequency_offset,
    ) -> tuple[list[ModbusTempChannel], list[ModbusControlChannel]]:
        """Translates the legacy flat FZ-94-shaped constructor args into
        the same two generic lists from_profile() builds directly from a
        DeviceProfile -- see the constructor's own comment for why both
        paths exist. `None` on an optional register disables that channel
        entirely, same meaning it's always had."""
        temp_channels = [ModbusTempChannel(role=ModbusChannelRole.BT, slave_id=bt_slave_id, register_address=bt_register, divisor=bt_divisor)]
        if et_register is not None:
            temp_channels.append(ModbusTempChannel(role=ModbusChannelRole.ET, slave_id=et_slave_id, register_address=et_register, divisor=et_divisor))
        if dt_register is not None:
            temp_channels.append(ModbusTempChannel(role=ModbusChannelRole.DT, slave_id=dt_slave_id, register_address=dt_register, divisor=dt_divisor))

        control_channels: list[ModbusControlChannel] = []
        if burner_register is not None:
            control_channels.append(ModbusControlChannel(
                maps_to="heater_pct", kind=ModbusControlKind.SV_TEMPERATURE, slave_id=burner_slave_id,
                register_address=burner_register, divisor=burner_divisor, sv_range_c=burner_sv_range_c,
            ))
        if air_control_register is not None and air_frequency_register is not None:
            control_channels.append(ModbusControlChannel(
                maps_to="fan_pct", kind=ModbusControlKind.VFD_DRIVE, slave_id=air_slave_id,
                control_register=air_control_register, frequency_register=air_frequency_register,
                frequency_scale=air_frequency_scale, frequency_offset=air_frequency_offset,
                feedback_register=air_feedback_register, feedback_divisor=air_feedback_divisor, value_range=air_range,
            ))
        if drum_control_register is not None and drum_frequency_register is not None:
            control_channels.append(ModbusControlChannel(
                maps_to="drum_speed_pct", kind=ModbusControlKind.VFD_DRIVE, slave_id=drum_slave_id,
                control_register=drum_control_register, frequency_register=drum_frequency_register,
                frequency_scale=drum_frequency_scale, frequency_offset=drum_frequency_offset,
                feedback_register=drum_feedback_register, feedback_divisor=drum_feedback_divisor, value_range=drum_range,
            ))
        return temp_channels, control_channels

    @classmethod
    def from_profile(
        cls,
        profile: DeviceProfile,
        port: Optional[str] = None,
        *,
        transport: str = "serial",
        host: Optional[str] = None,
        tcp_port: int = 502,
        baudrate: Optional[int] = None,
        bytesize: Optional[int] = None,
        parity: Optional[str] = None,
        stopbits: Optional[int] = None,
        timeout: float = 0.4,
        control_port: Optional[str] = None,
        control_baudrate: Optional[int] = None,
        control_bytesize: Optional[int] = None,
        control_parity: Optional[str] = None,
        control_stopbits: Optional[int] = None,
        control_timeout: float = 0.4,
        dry_end_c: Optional[float] = 160.0,
        fc_start_c: Optional[float] = 196.0,
        detect_milestones: bool = False,
        client_cls=ModbusSerialClient,
    ) -> "ModbusEngine":
        """The profile-driven way in -- builds the same two generic
        channel lists the flat constructor above builds internally, but
        straight from a saved/built-in DeviceProfile instead of 26 flat
        override fields. Connection settings default to the profile's
        own (baudrate/bytesize/parity/stopbits) but can still be
        overridden per-roast, same as modbus_baudrate already can today.
        Every named flat attribute (self.bt_slave_id etc.) that only the
        legacy constructor's own tests/internals ever read is left unset
        here -- nothing in tick()/apply_command() touches them; they only
        exist for the flat-constructor compatibility path above."""
        self = cls.__new__(cls)
        self.port = port if transport != "tcp" else f"{host}:{tcp_port}"
        self.control_port = control_port
        self.temp_channels = list(profile.temp_channels)
        self.control_channels = list(profile.control_channels)

        self._dry_end_c = dry_end_c
        self._fc_start_c = fc_start_c
        self._detect_milestones = detect_milestones
        self._detector = LiveRoastDetector(dry_end_c=dry_end_c, fc_start_c=fc_start_c, detect_milestones=detect_milestones)
        self._last_time_s = 0.0
        self._connected = False
        self._last_error = None
        self._control_last_error = None
        self._last_values = {}

        self._open_connections(
            port,
            baudrate if baudrate is not None else profile.baudrate,
            bytesize if bytesize is not None else profile.bytesize,
            parity if parity is not None else profile.parity,
            stopbits if stopbits is not None else profile.stopbits,
            timeout,
            control_port,
            control_baudrate if control_baudrate is not None else profile.baudrate,
            control_bytesize if control_bytesize is not None else profile.bytesize,
            control_parity if control_parity is not None else profile.parity,
            control_stopbits if control_stopbits is not None else profile.stopbits,
            control_timeout,
            client_cls,
            transport=transport,
            host=host,
            tcp_port=tcp_port,
        )
        return self

    def _open_connections(
        self, port, baudrate, bytesize, parity, stopbits, timeout,
        control_port, control_baudrate, control_bytesize, control_parity, control_stopbits, control_timeout,
        client_cls,
        *,
        transport: str = "serial",
        host: Optional[str] = None,
        tcp_port: int = 502,
    ) -> None:
        """Shared by both constructors above -- opens the primary
        connection (and, only if control_port is actually set, a second
        one for Air/Drum -- see the module docstring's confirmed
        single-bus architecture). ``transport="tcp"`` is the Modbus
        TCP/Ethernet path (e.g. the Coffee-Tech FZ-94 Evo) -- a genuinely
        different wire protocol from RTU (MBAP framing, no CRC), not
        just a different port string, so it gets its own client
        construction here rather than reusing pyserial's socket:// URL
        trick (that still frames traffic as RTU over a raw socket,
        which a true Modbus TCP device won't understand)."""
        if transport == "tcp":
            if not host:
                raise ValueError("host is required to connect via Modbus TCP")
            self._client = client_cls(host=host, port=tcp_port, timeout=timeout)
        else:
            if not port:
                raise ValueError("port (e.g. 'COM3') is required to connect via Modbus RTU")
            self._client = client_cls(
                port=port, baudrate=baudrate, bytesize=bytesize, parity=parity, stopbits=stopbits, timeout=timeout,
            )
        try:
            self._connected = bool(self._client.connect())
            if not self._connected:
                if transport == "tcp":
                    self._last_error = f"could not open TCP host {host}:{tcp_port}"
                else:
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
                logger.debug("read slave=%s reg=%s -> error: %s", slave_id, address, result)
                if is_heartbeat:
                    self._last_error = str(result)
                    self._connected = False
                return None
            if is_heartbeat:
                self._last_error = None
                self._connected = True
            logger.debug("read slave=%s reg=%s -> %s", slave_id, address, result.registers[0])
            return result.registers[0]
        except ModbusException as exc:
            logger.debug("read slave=%s reg=%s -> exception: %s", slave_id, address, exc)
            if is_heartbeat:
                self._last_error = str(exc)
                self._connected = False
            return None

    def _read_temp_channel(self, ch: ModbusTempChannel, *, is_heartbeat: bool = False) -> Optional[float]:
        raw = self._read_register(ch.register_address, ch.slave_id, is_heartbeat=is_heartbeat)
        return (raw / (ch.divisor or 1.0)) if raw is not None else None

    def _read_control_feedback(self, ch: ModbusControlChannel) -> tuple[Optional[float], Optional[float]]:
        """Returns (pct_feedback, raw_sv_c). raw_sv_c is only ever
        non-None for kind=SV_TEMPERATURE (the roaster's own PID setpoint
        in its native °C -- see RoastProfilePoint.burner_sv_c); every
        other kind returns None there, no equivalent concept."""
        if ch.kind == ModbusControlKind.SV_TEMPERATURE:
            # Readable from the same address it's written to -- reports
            # the PLC's actual current setpoint, not just an echo of the
            # last command this app itself sent. Matters on connecting to
            # a device an operator (or a previous session) already has
            # running: without this, heater_pct would read blank/0 until
            # this app happened to write it, which is both misleading and
            # risks clobbering real state if the UI ever auto-sent a
            # "starting value" on top of that instead of reflecting it.
            if ch.register_address is None:
                return None, None
            raw = self._read_register(ch.register_address, ch.slave_id)
            if raw is None:
                return None, None
            sv_c = raw / (ch.divisor or 1.0)
            sv_lo, sv_hi = ch.sv_range_c or (0.0, 100.0)
            pct = max(0.0, min(100.0, (sv_c - sv_lo) / (sv_hi - sv_lo) * 100.0)) if sv_hi != sv_lo else None
            return pct, sv_c
        # vfd_drive / direct_register share the same optional feedback-
        # register shape -- a genuine readback of the drive/register's
        # actual current value, not an echo of the last write. None here
        # (no feedback register configured, or the read failed) falls
        # back to the last commanded value in tick() below, same
        # degrade-gracefully behavior as always.
        if ch.feedback_register is None:
            return None, None
        raw = self._read_register(ch.feedback_register, ch.slave_id, client=self._control_client)
        return ((raw / (ch.feedback_divisor or 1.0)) if raw is not None else None), None

    # -- engine contract (matches simulator.SimulatorEngine / AlogPlayer / MS6514Engine) --
    def tick(self, dt: float) -> dict:
        self._last_time_s += dt
        time_s = self._last_time_s

        bt = et = dt_val = None
        extra: dict[str, float] = {}
        for ch in self.temp_channels:
            value = self._read_temp_channel(ch, is_heartbeat=(ch.role == ModbusChannelRole.BT))
            if ch.role == ModbusChannelRole.BT:
                bt = value
            elif ch.role == ModbusChannelRole.ET:
                et = value
            elif ch.role == ModbusChannelRole.DT:
                dt_val = value
            elif ch.role == ModbusChannelRole.EXTRA and value is not None and ch.label:
                extra[ch.label] = value

        sample = self._detector.observe(time_s, bt, et)
        sample["dt"] = dt_val
        sample["extra"] = extra

        # Explicit None for every one of the three known slots up front --
        # a profile that doesn't map a control channel onto one (or the
        # flat-constructor path with that channel disabled) still reports
        # it as present-and-null, not simply absent from the dict, same
        # as this engine has always done.
        sample["heater_pct"] = sample["fan_pct"] = sample["drum_speed_pct"] = None
        sv_c = None
        for ch in self.control_channels:
            pct, maybe_sv_c = self._read_control_feedback(ch)
            sample[ch.maps_to] = pct if pct is not None else self._last_values.get(ch.maps_to)
            if maybe_sv_c is not None:
                sv_c = maybe_sv_c
        # The raw SV in its native unit (°C), not the 0-100% UI mapping --
        # lets an operator sanity-check sv_range_c against the roaster's
        # own real setpoint instead of trusting the % blindly. None on a
        # profile with no SV_TEMPERATURE control channel at all.
        sample["burner_sv_c"] = sv_c
        return sample

    def get_new_events(self) -> list:
        return self._detector.get_new_events()

    def apply_command(self, cmd: dict) -> None:
        """Reuses the platform's existing heater_pct/fan_pct/drum_speed_pct
        control command shape so the existing Controls UI works
        unmodified regardless of which physical channel/mechanism a
        DeviceProfile actually maps each slot onto -- dispatches to one
        of the three write helpers below by that channel's own `kind`.
        See ModbusControlKind for why these don't share one simple
        "write a percentage to a register" shape."""
        matched_keys = set()
        for ch in self.control_channels:
            value = cmd.get(ch.maps_to)
            if value is not None:
                matched_keys.add(ch.maps_to)
                if ch.kind == ModbusControlKind.SV_TEMPERATURE:
                    self._write_sv_temperature(ch, float(value))
                elif ch.kind == ModbusControlKind.VFD_DRIVE:
                    self._write_vfd_drive(ch, float(value))
                elif ch.kind == ModbusControlKind.DIRECT_REGISTER:
                    self._write_direct_register(ch, float(value))
            # Second, independent write path onto the exact same burner
            # channel -- native °C instead of heater_pct's 0-100% mapping.
            # Not a separate ModbusControlChannel/maps_to slot: both are
            # unit representations of the one PID setpoint this channel's
            # register actually holds (see ControlCommand.burner_sv_c).
            if ch.kind == ModbusControlKind.SV_TEMPERATURE:
                sv_c = cmd.get("burner_sv_c")
                if sv_c is not None:
                    self._write_sv_temperature_c(ch, float(sv_c))
        # A commanded key with no matching channel on this profile is a
        # silent no-op otherwise -- often means the device is in the
        # wrong control mode for what's being asked of it (e.g. Damper
        # commanded on a profile that never assigned it a channel).
        for key in ("heater_pct", "fan_pct", "drum_speed_pct"):
            if cmd.get(key) is not None and key not in matched_keys:
                logger.warning(
                    "commanded %s but this device profile has no matching control channel -- ignored", key
                )

    def _write_sv_temperature(self, ch: ModbusControlChannel, pct: float) -> None:
        if ch.register_address is None:
            return
        clamped_pct = max(0.0, min(100.0, pct))
        if clamped_pct != pct:
            logger.warning("requested %s=%.2f%% out of range 0-100%%, clamped to %.2f%%", ch.maps_to, pct, clamped_pct)
        sv_lo, sv_hi = ch.sv_range_c or (0.0, 100.0)
        sv_c = sv_lo + (clamped_pct / 100.0) * (sv_hi - sv_lo)
        self._write_sv_raw(ch, sv_c, clamped_pct)

    def _write_sv_temperature_c(self, ch: ModbusControlChannel, sv_c: float) -> None:
        if ch.register_address is None:
            return
        sv_lo, sv_hi = ch.sv_range_c or (0.0, 100.0)
        lo, hi = min(sv_lo, sv_hi), max(sv_lo, sv_hi)
        clamped = max(lo, min(hi, sv_c))
        if clamped != sv_c:
            logger.warning("requested burner_sv_c=%.2f out of range %.2f-%.2f, clamped to %.2f", sv_c, lo, hi, clamped)
        pct = max(0.0, min(100.0, (clamped - sv_lo) / (sv_hi - sv_lo) * 100.0)) if sv_hi != sv_lo else None
        self._write_sv_raw(ch, clamped, pct)

    def _write_sv_raw(self, ch: ModbusControlChannel, sv_c: float, pct: Optional[float]) -> None:
        """Shared by both SV write paths above -- same register, same
        divisor-scaled integer write, only how `sv_c` got computed
        differs. `pct` is stashed in _last_values so tick()'s heater_pct
        feedback (and the other write path) both stay in sync regardless
        of which unit was actually written."""
        raw = int(round(sv_c * (ch.divisor or 1.0)))
        logger.debug("write slave=%s reg=%s <- %s (sv_c=%.2f)", ch.slave_id, ch.register_address, raw, sv_c)
        try:
            result = self._client.write_register(ch.register_address, raw, device_id=ch.slave_id)
            if result.isError():
                self._last_error = str(result)
                logger.warning(
                    "write rejected by device (slave=%s reg=%s): %s -- device may not be in remote/PC control mode",
                    ch.slave_id, ch.register_address, result,
                )
            else:
                self._last_error = None
                self._connected = True
                if pct is not None:
                    self._last_values[ch.maps_to] = pct
        except ModbusException as exc:
            self._last_error = str(exc)
            self._connected = False

    def _write_vfd_drive(self, ch: ModbusControlChannel, value: float) -> None:
        if ch.control_register is None or ch.frequency_register is None:
            return
        lo, hi = ch.value_range
        clamped = max(lo, min(hi, value))
        if clamped != value:
            logger.warning("requested %s=%.2f out of range %.2f-%.2f, clamped to %.2f", ch.maps_to, value, lo, hi, clamped)
        try:
            run_state = 2 if clamped > 0 else 1  # 2=Run, 1=Stop
            logger.debug("write slave=%s reg=%s <- %s (run_state)", ch.slave_id, ch.control_register, run_state)
            run_result = self._control_client.write_register(ch.control_register, run_state, device_id=ch.slave_id)
            if clamped > 0:
                freq_raw = int(round(clamped * ch.frequency_scale + ch.frequency_offset))
                logger.debug("write slave=%s reg=%s <- %s (frequency)", ch.slave_id, ch.frequency_register, freq_raw)
                freq_result = self._control_client.write_register(
                    ch.frequency_register, freq_raw, device_id=ch.slave_id
                )
            else:
                # Off: only touch the control (run/stop) register, same as
                # a real button-based control (confirmed against a live
                # FZ-94 -- its Off button sends just write(2,8192,1),
                # leaving the frequency register alone). Zeroing the
                # frequency register here too would forget the last speed,
                # so turning back on would always resume at 0 instead of
                # wherever the drive was left.
                freq_result = run_result
            if run_result.isError() or freq_result.isError():
                error_result = run_result if run_result.isError() else freq_result
                self._control_last_error = str(error_result)
                logger.warning(
                    "write rejected by device (slave=%s reg=%s): %s -- device may not be in remote/PC control mode",
                    ch.slave_id, ch.control_register, error_result,
                )
            else:
                self._control_last_error = None
                self._control_connected = True
                self._last_values[ch.maps_to] = clamped
        except ModbusException as exc:
            self._control_last_error = str(exc)
            self._control_connected = False

    def _write_direct_register(self, ch: ModbusControlChannel, value: float) -> None:
        if ch.write_register is None:
            return
        lo, hi = ch.value_range
        clamped = max(lo, min(hi, value))
        if clamped != value:
            logger.warning("requested %s=%.2f out of range %.2f-%.2f, clamped to %.2f", ch.maps_to, value, lo, hi, clamped)
        raw = int(round(clamped * ch.write_scale))
        logger.debug("write slave=%s reg=%s <- %s", ch.slave_id, ch.write_register, raw)
        try:
            result = self._control_client.write_register(ch.write_register, raw, device_id=ch.slave_id)
            if result.isError():
                self._control_last_error = str(result)
                logger.warning(
                    "write rejected by device (slave=%s reg=%s): %s -- device may not be in remote/PC control mode",
                    ch.slave_id, ch.write_register, result,
                )
            else:
                self._control_last_error = None
                self._control_connected = True
                self._last_values[ch.maps_to] = clamped
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

    def reset_detection(self) -> None:
        """Called right as a roast actually starts recording (see
        RoastSession.begin_recording()) to throw away everything the
        engine accumulated during an earlier preview/verification window
        (see RoastSession.connect()) and start the recorded roast
        genuinely fresh, in two ways:

        - Discards whatever CHARGE/TURNING_POINT/etc. state the milestone
          detector has built up -- not a pause, a clean restart.
          LiveRoastDetector's own state (_phase, _events_fired) is
          one-shot and irreversible, so without this a milestone
          detected-and-discarded during preview could never fire again.
        - Resets the elapsed-time clock (_last_time_s) back to 0. Without
          this, a roast that only started recording after e.g. 5 minutes
          of connection testing would have its very first recorded sample
          land at time_s=300 instead of 0 -- not just a preview-display
          quirk, an actually wrong time axis on the persisted roast."""
        self._detector = LiveRoastDetector(dry_end_c=self._dry_end_c, fc_start_c=self._fc_start_c, detect_milestones=self._detect_milestones)
        self._last_time_s = 0.0

    def mark_milestone_fired(self, event_type: str) -> None:
        """Forwards to the detector -- see its own mark_milestone_fired
        docstring. Called from RoastSession.add_event() when DRY_END/
        FC_START is marked manually, so a later auto-fire (if
        detect_milestones is on) doesn't produce a duplicate."""
        self._detector.mark_milestone_fired(event_type)

    def notify_manual_charge(self, time_s: float, bt: float) -> None:
        """Forwards to the detector -- see its own notify_manual_charge
        docstring. Called from RoastSession.add_event() right after a
        manual CHARGE is recorded, so Turning Point still gets tracked
        and auto-plotted even though CHARGE itself was a manual click."""
        self._detector.notify_manual_charge(time_s, bt)

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
