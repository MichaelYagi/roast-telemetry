"""ModbusEngine.from_profile -- the profile-driven construction path
added alongside the existing flat-constructor one (see
tests/test_modbus_engine.py, which covers that path and is itself proof
the compat shim didn't change behavior). Covers each ModbusControlKind
plus an extra temperature channel, against the same fake pymodbus client
pattern test_modbus_engine.py already uses.
"""
from __future__ import annotations

import pytest

from backend.app.models import (
    DeviceProfile,
    ModbusChannelRole,
    ModbusControlChannel,
    ModbusControlKind,
    ModbusTempChannel,
)
from modbus_bridge.device_profiles import BUILT_IN_PROFILES
from modbus_bridge.engine import ModbusEngine
from tests.test_modbus_engine import _make_fake_client_cls


def test_built_in_fz94_profile_via_from_profile_matches_flat_defaults():
    """The built-in profile is meant to be a pure re-expression of
    ModbusEngine's own flat defaults -- this proves it, by comparing a
    from_profile()-built engine's reads/writes against known FZ-94
    register numbers rather than re-deriving them from the profile
    itself (which would just be tautological)."""
    client_cls, instances = _make_fake_client_cls()
    profile = BUILT_IN_PROFILES[0]
    engine = ModbusEngine.from_profile(profile, "PRIMARY", client_cls=client_cls)
    primary = instances["PRIMARY"]
    primary.register_values[(11, 0)] = 960  # BT slave 11 -> 96.0C

    sample = engine.tick(1.0)
    assert sample["bt"] == pytest.approx(96.0)

    engine.apply_command({"heater_pct": 50.0})
    # 50% across the default 100-260C range -> 180.0C -> raw 1800
    assert (5, 1800, 12) in primary.writes


def test_direct_register_control_kind_writes_a_plain_percentage():
    client_cls, instances = _make_fake_client_cls()
    profile = DeviceProfile(
        id="test-direct", name="Test Direct-Register Roaster", created_at="2026-01-01T00:00:00+00:00",
        temp_channels=[ModbusTempChannel(role=ModbusChannelRole.BT, slave_id=1, register_address=0, divisor=10.0)],
        control_channels=[
            ModbusControlChannel(
                maps_to="fan_pct", kind=ModbusControlKind.DIRECT_REGISTER, slave_id=2,
                write_register=100, write_scale=1.0, feedback_register=101, feedback_divisor=1.0,
                value_range=(0.0, 100.0),
            ),
        ],
    )
    engine = ModbusEngine.from_profile(profile, "PRIMARY", client_cls=client_cls)
    primary = instances["PRIMARY"]

    engine.apply_command({"fan_pct": 42.0})
    # No run/stop word -- a single plain write, unlike vfd_drive.
    assert (100, 42, 2) in primary.writes
    assert not any(addr == 8192 for addr, _, _ in primary.writes)

    primary.register_values[(2, 101)] = 37  # feedback register reports the drive's real current value
    sample = engine.tick(1.0)
    assert sample["fan_pct"] == pytest.approx(37.0)  # genuine readback, not an echo of the 42 just sent


def test_vfd_drive_control_kind_writes_run_stop_and_frequency():
    client_cls, instances = _make_fake_client_cls()
    profile = DeviceProfile(
        id="test-vfd", name="Test VFD Roaster", created_at="2026-01-01T00:00:00+00:00",
        temp_channels=[ModbusTempChannel(role=ModbusChannelRole.BT, slave_id=1, register_address=0, divisor=10.0)],
        control_channels=[
            ModbusControlChannel(
                maps_to="drum_speed_pct", kind=ModbusControlKind.VFD_DRIVE, slave_id=2,
                control_register=200, frequency_register=201, frequency_scale=100.0, value_range=(0.0, 70.0),
            ),
        ],
    )
    engine = ModbusEngine.from_profile(profile, "PRIMARY", client_cls=client_cls)
    primary = instances["PRIMARY"]

    engine.apply_command({"drum_speed_pct": 35.0})
    assert (200, 2, 2) in primary.writes  # run
    assert (201, 3500, 2) in primary.writes  # 35% * scale 100


def test_sv_temperature_control_kind_maps_onto_any_slot_not_just_heater():
    """maps_to is independent of kind -- an sv_temperature channel bound
    to fan_pct (an unusual but valid profile) still works the same way
    heater_pct's built-in one does."""
    client_cls, instances = _make_fake_client_cls()
    profile = DeviceProfile(
        id="test-sv-fan", name="Test SV-as-Fan Roaster", created_at="2026-01-01T00:00:00+00:00",
        temp_channels=[ModbusTempChannel(role=ModbusChannelRole.BT, slave_id=1, register_address=0, divisor=10.0)],
        control_channels=[
            ModbusControlChannel(
                maps_to="fan_pct", kind=ModbusControlKind.SV_TEMPERATURE, slave_id=9,
                register_address=50, divisor=10.0, sv_range_c=(0.0, 100.0),
            ),
        ],
    )
    engine = ModbusEngine.from_profile(profile, "PRIMARY", client_cls=client_cls)
    primary = instances["PRIMARY"]

    engine.apply_command({"fan_pct": 60.0})
    assert (50, 600, 9) in primary.writes  # 60% of a 0-100C range -> 60.0C -> raw 600


def test_extra_temperature_channel_lands_in_sample_extra():
    client_cls, instances = _make_fake_client_cls()
    profile = DeviceProfile(
        id="test-extra", name="Test Roaster With Flue Probe", created_at="2026-01-01T00:00:00+00:00",
        temp_channels=[
            ModbusTempChannel(role=ModbusChannelRole.BT, slave_id=1, register_address=0, divisor=10.0),
            ModbusTempChannel(role=ModbusChannelRole.EXTRA, label="Flue", slave_id=3, register_address=0, divisor=10.0),
        ],
    )
    engine = ModbusEngine.from_profile(profile, "PRIMARY", client_cls=client_cls)
    primary = instances["PRIMARY"]
    primary.register_values[(1, 0)] = 900  # BT
    primary.register_values[(3, 0)] = 1450  # Flue -> 145.0C

    sample = engine.tick(1.0)
    assert sample["extra"] == {"Flue": pytest.approx(145.0)}


def test_from_profile_connection_settings_default_to_the_profiles_own():
    client_cls, instances = _make_fake_client_cls()
    profile = DeviceProfile(
        id="test-baud", name="Test Roaster", created_at="2026-01-01T00:00:00+00:00", baudrate=9600,
        temp_channels=[ModbusTempChannel(role=ModbusChannelRole.BT, slave_id=1, register_address=0, divisor=10.0)],
    )
    ModbusEngine.from_profile(profile, "PRIMARY", client_cls=client_cls)
    assert instances["PRIMARY"].baudrate == 9600


def test_from_profile_baudrate_override_beats_the_profiles_own():
    client_cls, instances = _make_fake_client_cls()
    profile = DeviceProfile(
        id="test-baud2", name="Test Roaster", created_at="2026-01-01T00:00:00+00:00", baudrate=9600,
        temp_channels=[ModbusTempChannel(role=ModbusChannelRole.BT, slave_id=1, register_address=0, divisor=10.0)],
    )
    ModbusEngine.from_profile(profile, "PRIMARY", baudrate=19200, client_cls=client_cls)
    assert instances["PRIMARY"].baudrate == 19200
