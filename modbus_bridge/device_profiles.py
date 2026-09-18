"""Built-in DeviceProfiles -- see backend/app/models.py's DeviceProfile
and api/device_profiles.py. Shipped/seeded once at startup (main.py's
lifespan), `built_in=True`, not editable/deletable via the API.

The one entry here is a pure re-expression of ModbusEngine's own
long-standing hardcoded FZ-94 defaults as data instead of constructor
defaults -- see modbus_bridge/engine.py's module docstring for the full
sourcing/confidence notes on every register below (Artisan's own
shipped FZ94.aset for BT/ET/DT/Burner; Air/Drum slave IDs now confirmed
against a real, live, independently control-tested FZ-94, superseding
an earlier default that had them swapped based only on an unverified
blog post). Not a behavior change from today's default (profile-less)
ModbusEngine construction -- both were updated together.
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
            feedback_register=8451,
            feedback_divisor=100.0,
            value_range=(0.0, 70.0),
        ),
    ],
    built_in=True,
)

BUILT_IN_PROFILES: list[DeviceProfile] = [COFFEETECH_FZ94]
