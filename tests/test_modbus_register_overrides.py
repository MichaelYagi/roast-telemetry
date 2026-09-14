"""RoastCreateRequest's Air/Drum/Burner-SV-range overrides -- confirms
they actually reach ModbusEngine's constructor via RoastSession, not just
that the request model accepts them. Uses a nonexistent serial port
throughout: ModbusEngine.__init__ catches the resulting connect failure
internally (never raises), so this never touches real/virtual hardware
and stays fast.
"""
from __future__ import annotations

from backend.app.models import RoastCreateRequest, RoastMode
from backend.app.roast_session.session import RoastSession

BOGUS_PORT = "/dev/nonexistent-for-tests"


def test_modbus_live_session_applies_register_overrides():
    request = RoastCreateRequest(
        title="Test Roast",
        mode=RoastMode.MODBUS_LIVE,
        modbus_port=BOGUS_PORT,
        modbus_bt_slave_id=21,
        modbus_bt_register=1,
        modbus_bt_divisor=100.0,
        modbus_et_slave_id=23,
        modbus_dt_register=2,
        modbus_burner_slave_id=22,
        modbus_burner_register=6,
        modbus_air_slave_id=9,
        modbus_air_control_register=100,
        modbus_air_frequency_register=101,
        modbus_air_feedback_register=102,
        modbus_air_min_pct=5.0,
        modbus_air_max_pct=95.0,
        modbus_drum_slave_id=8,
        modbus_burner_sv_min_c=120.0,
        modbus_burner_sv_max_c=200.0,
    )
    engine = RoastSession("test-roast-id", request)._engine

    assert engine.bt_slave_id == 21
    assert engine.bt_register == 1
    assert engine.bt_divisor == 100.0
    assert engine.et_slave_id == 23
    assert engine.dt_register == 2
    assert engine.burner_slave_id == 22
    assert engine.burner_register == 6
    assert engine.air_slave_id == 9
    assert engine.air_control_register == 100
    assert engine.air_frequency_register == 101
    assert engine.air_feedback_register == 102
    assert engine.air_range == (5.0, 95.0)
    assert engine.drum_slave_id == 8
    assert engine.burner_sv_range_c == (120.0, 200.0)

    # Untouched fields keep ModbusEngine's own defaults.
    assert engine.et_register == 0
    assert engine.dt_slave_id == 12
    assert engine.drum_control_register == 8192
    assert engine.drum_range == (0, 70)


def test_modbus_live_session_ignores_a_lone_half_of_a_range_pair():
    # Only the min was given -- shouldn't guess the max from ModbusEngine's
    # own default and silently bake a copy of it into this override path.
    request = RoastCreateRequest(
        title="Test Roast", mode=RoastMode.MODBUS_LIVE, modbus_port=BOGUS_PORT, modbus_air_min_pct=5.0,
    )
    engine = RoastSession("test-roast-id", request)._engine

    assert engine.air_range == (0, 100)  # default, not (5.0, 100)


def test_modbus_live_session_defaults_match_modbus_engine_when_nothing_overridden():
    request = RoastCreateRequest(title="Test Roast", mode=RoastMode.MODBUS_LIVE, modbus_port=BOGUS_PORT)
    engine = RoastSession("test-roast-id", request)._engine

    assert engine.bt_slave_id == 11
    assert engine.et_slave_id == 13
    assert engine.dt_slave_id == 12
    assert engine.burner_slave_id == 12
    assert engine.burner_register == 5
    assert engine.air_slave_id == 1
    assert engine.drum_slave_id == 2
    assert engine.burner_sv_range_c == (100.0, 250.0)
