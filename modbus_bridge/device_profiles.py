"""Built-in DeviceProfiles -- see backend/app/models.py's DeviceProfile
and api/device_profiles.py. Shipped/seeded once at startup (main.py's
lifespan), `built_in=True`, not editable/deletable via the API.

COFFEETECH_FZ94 is a pure re-expression of ModbusEngine's own
long-standing hardcoded FZ-94 defaults as data instead of constructor
defaults -- see modbus_bridge/engine.py's module docstring for the full
sourcing/confidence notes on every register below (the stock
FZ-94 configuration for BT/ET/DT/Burner; Air/Drum slave IDs default to a
real, live, independently control-tested FZ-94's assignment, which is
the *opposite* of an earlier default -- both are real, working
installations that wired this aftermarket feature's slave IDs
differently, not a case of one simply being wrong; see engine.py for the
full explanation). Not a behavior change from today's default
(profile-less) ModbusEngine construction -- both were updated together.

COFFEETECH_FZ94_EVO is a genuinely different roaster: Modbus TCP over
Ethernet (default 192.168.1.2:502), not RTU/USB -- see
ModbusEngine._open_connections' transport="tcp" path. Write-side
register map (Air=20, Drum=16, Burner=35, all slave 1) is confirmed
with high confidence via the manufacturer's own user manual plus the
manufacturer's shipped roaster-scope config for this model, independently
corroborated against a real, live device's own configuration screens.
Two known open questions, deliberately not guessed at: which of read
registers 47/46 is Air vs. Drum feedback (left unconfigured -- falls
back to command-echo display), and the purpose of read register 37
(not used by anything here).
"""
from __future__ import annotations

from backend.app.models import DeviceProfile, ModbusChannelRole, ModbusControlChannel, ModbusControlKind, ModbusTempChannel

COFFEETECH_FZ94 = DeviceProfile(
    id="coffeetech-fz94",
    name="Coffee-Tech FZ-94 (built-in)",
    created_at="1970-01-01T00:00:00+00:00",  # overwritten by the real seed timestamp at insert time
    baudrate=19200,
    bytesize=8,
    parity="N",
    stopbits=2,
    temp_channels=[
        ModbusTempChannel(role=ModbusChannelRole.BT, slave_id=11, register_address=0, divisor=10.0),
        ModbusTempChannel(role=ModbusChannelRole.ET, slave_id=13, register_address=0, divisor=10.0),
        ModbusTempChannel(role=ModbusChannelRole.DT, slave_id=12, register_address=0, divisor=10.0),
    ],
    control_channels=[
        ModbusControlChannel(
            maps_to="heater_pct",
            kind=ModbusControlKind.SV_TEMPERATURE,
            slave_id=12,
            register_address=5,
            divisor=10.0,
            sv_range_c=(100.0, 260.0),
        ),
        ModbusControlChannel(
            maps_to="fan_pct",
            kind=ModbusControlKind.VFD_DRIVE,
            slave_id=2,
            control_register=8192,
            frequency_register=8193,
            frequency_scale=100.0,
            frequency_offset=0.0,
            feedback_register=8451,
            feedback_divisor=100.0,
            value_range=(0.0, 100.0),
        ),
        ModbusControlChannel(
            maps_to="drum_speed_pct",
            kind=ModbusControlKind.VFD_DRIVE,
            slave_id=1,
            control_register=8192,
            frequency_register=8193,
            frequency_scale=100.0,
            frequency_offset=0.0,
            feedback_register=8451,
            feedback_divisor=100.0,
            value_range=(0.0, 70.0),
        ),
    ],
    built_in=True,
)

COFFEETECH_FZ94_EVO = DeviceProfile(
    id="coffeetech-fz94-evo",
    name="Coffee-Tech FZ-94 Evo (built-in)",
    created_at="1970-01-01T00:00:00+00:00",  # overwritten by the real seed timestamp at insert time
    # Modbus TCP, not RTU -- these framing fields are simply unused for
    # this profile (see ModbusEngine._open_connections' transport="tcp"
    # path); left at their harmless defaults rather than repurposed.
    baudrate=19200,
    bytesize=8,
    parity="N",
    stopbits=2,
    temp_channels=[
        ModbusTempChannel(role=ModbusChannelRole.BT, slave_id=1, register_address=100, divisor=10.0),
        # The manufacturer's own manual documents this as an *exhaust*
        # probe (part of the chaff collector assembly), not a drum
        # probe -- kept as ET here to match what it actually measures.
        # The device's own shipped roaster-scope config for this model
        # relabels its display name to "DT" despite that; a real,
        # deliberately-not-carried-forward discrepancy.
        ModbusTempChannel(role=ModbusChannelRole.ET, slave_id=1, register_address=80, divisor=10.0),
    ],
    control_channels=[
        # All three are single direct-register writes on one device (slave
        # 1) over one TCP connection -- no separate run/stop word, unlike
        # the plain FZ-94's VFD drives. Confirmed against the user's own
        # live device config (Events/Sliders + Events/Buttons screens,
        # independently corroborating the manufacturer's shipped
        # roaster-scope config for this model).
        ModbusControlChannel(
            maps_to="heater_pct",
            kind=ModbusControlKind.DIRECT_REGISTER,
            slave_id=1,
            write_register=35,
            write_scale=1.0,
            value_range=(0.0, 100.0),
        ),
        # Range is 30-70, matching the user's own live-configured slider
        # -- not the manual's printed 30-100 spec-sheet figure for
        # Airflow, a real, flagged discrepancy (live config wins).
        ModbusControlChannel(
            maps_to="fan_pct",
            kind=ModbusControlKind.DIRECT_REGISTER,
            slave_id=1,
            write_register=20,
            write_scale=1.0,
            value_range=(30.0, 70.0),
            # Feedback register unconfirmed (reg 47 or 46, unclear which
            # of Air/Drum each belongs to) -- deliberately left unset
            # rather than guessed. Falls back to the existing command-echo
            # display, same safe degrade-gracefully path any control
            # channel without a configured feedback register already has.
        ),
        ModbusControlChannel(
            maps_to="drum_speed_pct",
            kind=ModbusControlKind.DIRECT_REGISTER,
            slave_id=1,
            write_register=16,
            write_scale=1.0,
            value_range=(30.0, 70.0),
        ),
    ],
    built_in=True,
)

BUILT_IN_PROFILES: list[DeviceProfile] = [COFFEETECH_FZ94, COFFEETECH_FZ94_EVO]
