"""modbus_bridge/engine.py -- ModbusEngine's register/slave map for the
Coffee-Tech FZ-94 (BT/ET/DT reads, Burner-as-setpoint, Air/Drum drives on
a separate connection). Exercised against a fake pymodbus client (no
real/virtual serial port needed) so these run in plain CI, not just the
hardware_fakes-based manual smoke test described in the README."""
from __future__ import annotations

import pytest

from modbus_bridge.engine import ModbusEngine


class _FakeResult:
    def __init__(self, registers=None, error=False):
        self.registers = registers or []
        self._error = error

    def isError(self):
        return self._error


def _make_fake_client_cls():
    """Returns (client_cls, instances) -- instances maps port -> the fake
    client constructed for it, so a test can inspect both the primary and
    control connections' reads/writes after the fact."""
    instances: dict[str, "_FakeClient"] = {}

    class _FakeClient:
        def __init__(self, port, baudrate, bytesize, parity, stopbits, timeout):
            self.port = port
            self.baudrate = baudrate
            self.stopbits = stopbits
            self.reads: list[tuple[int, int]] = []  # (address, device_id)
            self.writes: list[tuple[int, int, int]] = []  # (address, value, device_id)
            self.register_values: dict[tuple[int, int], int] = {}  # (device_id, address) -> raw value
            instances[port] = self

        def connect(self):
            return True

        def read_holding_registers(self, address, count, device_id):
            self.reads.append((address, device_id))
            value = self.register_values.get((device_id, address), 0)
            return _FakeResult(registers=[value] * count)

        def write_register(self, address, value, device_id):
            self.writes.append((address, value, device_id))
            return _FakeResult()

        def close(self):
            pass

    return _FakeClient, instances


def test_tick_reads_bt_et_dt_from_their_own_slaves_with_divisor():
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine("PRIMARY", client_cls=client_cls)
    primary = instances["PRIMARY"]
    primary.register_values[(11, 0)] = 960   # BT slave 11 -> 96.0C
    primary.register_values[(13, 0)] = 2000  # ET slave 13 -> 200.0C
    primary.register_values[(12, 0)] = 2200  # DT slave 12 -> 220.0C

    sample = engine.tick(1.0)

    assert sample["bt"] == pytest.approx(96.0)
    assert sample["et"] == pytest.approx(200.0)
    assert sample["dt"] == pytest.approx(220.0)
    assert (0, 11) in primary.reads
    assert (0, 13) in primary.reads
    assert (0, 12) in primary.reads


def test_tick_reads_burner_sv_back_as_heater_pct_not_just_last_command():
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine("PRIMARY", client_cls=client_cls)
    primary = instances["PRIMARY"]

    # Simulates a device an operator already has running -- this app never
    # wrote anything, but the PLC's own holding register already holds a
    # real setpoint. 236.0C -> raw 2360 -> 85% across the default 100-260C
    # range, same math as the write-side test below, just the read side.
    primary.register_values[(12, 5)] = 2360

    sample = engine.tick(1.0)

    assert sample["heater_pct"] == pytest.approx(85.0)
    assert sample["burner_sv_c"] == pytest.approx(236.0)  # the raw SV, not the % mapping
    assert (5, 12) in primary.reads


def test_tick_reports_no_burner_sv_when_burner_register_disabled():
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine("PRIMARY", burner_register=None, client_cls=client_cls)

    sample = engine.tick(1.0)

    assert sample["burner_sv_c"] is None


def test_tick_reports_no_heater_pct_when_burner_register_disabled():
    # Unlike Air/Drum (separate control vs. feedback registers), Burner
    # reads and writes the same holding register -- disabling it disables
    # both ends, not just the readback, so there's no write left to echo.
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine("PRIMARY", burner_register=None, client_cls=client_cls)

    engine.apply_command({"heater_pct": 42.0})  # no-op: _write_burner_sv bails out too
    sample = engine.tick(1.0)

    assert sample["heater_pct"] is None


def test_apply_command_heater_pct_writes_sv_setpoint_to_burner_slave():
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine("PRIMARY", client_cls=client_cls)
    primary = instances["PRIMARY"]

    engine.apply_command({"heater_pct": 85.0})

    # 85% across the default 100-260C SV range -> 236.0C -> x10 -> 2360,
    # written to slave 12 (same slave as the DT probe), register 5.
    assert (5, 2360, 12) in primary.writes


def test_apply_command_heater_pct_extremes_map_to_sv_range_bounds():
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine("PRIMARY", client_cls=client_cls)
    primary = instances["PRIMARY"]

    engine.apply_command({"heater_pct": 0.0})
    engine.apply_command({"heater_pct": 100.0})

    assert (5, 1000, 12) in primary.writes  # 0% -> 100.0C -> 1000
    assert (5, 2600, 12) in primary.writes  # 100% -> 260.0C -> 2600


def test_apply_command_burner_sv_c_writes_degrees_directly():
    # Alternate write path onto the exact same register as heater_pct
    # (see ModbusEngine.apply_command) -- 180C -> x10 -> 1800, same slave
    # 12/register 5 as the %-based test above.
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine("PRIMARY", client_cls=client_cls)
    primary = instances["PRIMARY"]

    engine.apply_command({"burner_sv_c": 180.0})

    assert (5, 1800, 12) in primary.writes


def test_apply_command_burner_sv_c_clamps_to_sv_range():
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine("PRIMARY", client_cls=client_cls)
    primary = instances["PRIMARY"]

    engine.apply_command({"burner_sv_c": 9999.0})
    engine.apply_command({"burner_sv_c": -50.0})

    assert (5, 2600, 12) in primary.writes  # clamped to the 260C ceiling
    assert (5, 1000, 12) in primary.writes  # clamped to the 100C floor


def test_apply_command_burner_sv_c_and_heater_pct_stay_in_sync():
    # "one moves the other" -- writing via either unit updates the SAME
    # _last_values["heater_pct"] entry tick() falls back to when a live
    # feedback read fails, so the two sliders never independently drift.
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine("PRIMARY", client_cls=client_cls)

    engine.apply_command({"burner_sv_c": 180.0})  # 50% of the 100-260C range

    assert engine._last_values["heater_pct"] == pytest.approx(50.0)


def test_apply_command_burner_sv_c_is_a_noop_when_burner_register_disabled():
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine("PRIMARY", burner_register=None, client_cls=client_cls)
    primary = instances["PRIMARY"]

    engine.apply_command({"burner_sv_c": 180.0})

    assert primary.writes == []


def test_fahrenheit_native_converts_temp_reads_to_celsius():
    # Confirmed live (2026-10-02): a real FZ-94's generic PID controllers
    # can be installer-configured in either unit -- the register map/
    # divisor is identical either way, only the raw number's *meaning*
    # differs. 960 raw / 10 = 96.0, native Fahrenheit here -> (96-32)*5/9
    # = 35.56C internally (this app always works in Celsius).
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine("PRIMARY", client_cls=client_cls, fahrenheit_native=True)
    primary = instances["PRIMARY"]
    primary.register_values[(11, 0)] = 960

    sample = engine.tick(1.0)

    assert sample["bt"] == pytest.approx((96.0 - 32.0) * 5.0 / 9.0)


def test_fahrenheit_native_off_by_default_matches_existing_behavior():
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine("PRIMARY", client_cls=client_cls)  # fahrenheit_native defaults False
    primary = instances["PRIMARY"]
    primary.register_values[(11, 0)] = 960

    sample = engine.tick(1.0)

    assert sample["bt"] == pytest.approx(96.0)  # no conversion -- today's confirmed-Celsius behavior


def test_fahrenheit_native_converts_burner_sv_read_to_celsius():
    # Same raw value as test_tick_reads_burner_sv_back_as_heater_pct_not_
    # just_last_command above (2360), but now interpreted as native
    # Fahrenheit: 236.0F -> 113.33C internally, then the heater_pct %
    # mapping still applies against the (Celsius) default 100-260C range.
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine("PRIMARY", client_cls=client_cls, fahrenheit_native=True)
    primary = instances["PRIMARY"]
    primary.register_values[(12, 5)] = 2360

    sample = engine.tick(1.0)

    expected_c = (236.0 - 32.0) * 5.0 / 9.0
    assert sample["burner_sv_c"] == pytest.approx(expected_c)
    assert sample["heater_pct"] == pytest.approx((expected_c - 100.0) / 160.0 * 100.0)


def test_fahrenheit_native_converts_burner_sv_write_to_native():
    # The inverse of the read-side test above: an internal 180C setpoint
    # (same figure test_apply_command_burner_sv_c_writes_degrees_directly
    # writes when fahrenheit_native is off) should go out on the wire as
    # its Fahrenheit equivalent -- 356F -> x10 -> 3560 -- not 1800.
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine("PRIMARY", client_cls=client_cls, fahrenheit_native=True)
    primary = instances["PRIMARY"]

    engine.apply_command({"burner_sv_c": 180.0})

    assert (5, 3560, 12) in primary.writes


def test_apply_command_fan_and_drum_write_run_and_frequency_to_control_client():
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine("PRIMARY", control_port="CONTROL", client_cls=client_cls)
    primary = instances["PRIMARY"]
    control = instances["CONTROL"]

    engine.apply_command({"fan_pct": 60.0, "drum_speed_pct": 40.0})

    # Air = slave 2: run (register 8192, value 2) then frequency (8193, 60*100=6000)
    assert (8192, 2, 2) in control.writes
    assert (8193, 6000, 2) in control.writes
    # Drum = slave 1: same pair, its own slave
    assert (8192, 2, 1) in control.writes
    assert (8193, 4000, 1) in control.writes
    # Drive writes never touch the temperature/burner connection.
    assert primary.writes == []


def test_apply_command_fan_and_drum_honor_configured_frequency_scale_and_offset():
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine(
        "PRIMARY",
        control_port="CONTROL",
        client_cls=client_cls,
        air_frequency_scale=50.0,
        air_frequency_offset=1000.0,
        drum_frequency_scale=10.0,
        drum_frequency_offset=-50.0,
    )
    control = instances["CONTROL"]

    engine.apply_command({"fan_pct": 60.0, "drum_speed_pct": 40.0})

    # Air: raw = 60*50 + 1000 = 4000 (would be 6000 at the FZ-94 default of scale=100/offset=0)
    assert (8193, 4000, 2) in control.writes
    # Drum: raw = 40*10 + (-50) = 350 (would be 4000 at the default)
    assert (8193, 350, 1) in control.writes


def test_apply_command_zero_drive_value_sends_stop_not_run():
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine("PRIMARY", control_port="CONTROL", client_cls=client_cls)
    control = instances["CONTROL"]

    engine.apply_command({"fan_pct": 0.0})

    assert (8192, 1, 2) in control.writes  # 1 = Stop
    assert not any(addr == 8193 for addr, _, _ in control.writes)  # frequency left untouched


def test_apply_command_zero_then_nonzero_drive_value_resumes_at_new_speed():
    # Real VFD-L behavior (confirmed against a live FZ-94's manual
    # control): Off only stops the drive, it doesn't forget the frequency
    # setpoint -- turning back on with a new value should still write that
    # new value, not silently skip the frequency register a second time.
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine("PRIMARY", control_port="CONTROL", client_cls=client_cls)
    control = instances["CONTROL"]

    engine.apply_command({"fan_pct": 0.0})
    engine.apply_command({"fan_pct": 45.0})

    assert (8192, 2, 2) in control.writes  # 2 = Run
    assert (8193, 4500, 2) in control.writes


def test_drives_share_the_primary_connection_by_default():
    # Confirmed single-bus architecture (the FZ-94's stock setup
    # uses one connection for BT/ET/DT/Burner *and* Air/Drum) -- without
    # a separate control_port, drive writes go out on the same client as
    # everything else, not dropped as a no-op.
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine("PRIMARY", client_cls=client_cls)  # no control_port

    engine.apply_command({"fan_pct": 50.0, "drum_speed_pct": 50.0})

    assert list(instances.keys()) == ["PRIMARY"]  # no second client created
    primary = instances["PRIMARY"]
    assert (8192, 2, 1) in primary.writes
    assert (8193, 5000, 1) in primary.writes
    assert (8192, 2, 2) in primary.writes
    assert (8193, 5000, 2) in primary.writes


def test_default_connection_settings_match_the_shipped_preset():
    client_cls, instances = _make_fake_client_cls()
    ModbusEngine("PRIMARY", client_cls=client_cls)

    # 19200 baud, 8 data bits, no parity, 2 stop bits -- the FZ-94's stock
    # serial settings, not the earlier (wrong)
    # 2400/8N1 assumption.
    assert instances["PRIMARY"].baudrate == 19200
    assert instances["PRIMARY"].stopbits == 2


def test_control_connection_uses_its_own_baud_and_framing_when_configured():
    client_cls, instances = _make_fake_client_cls()
    ModbusEngine("PRIMARY", control_port="CONTROL", control_baudrate=9600, client_cls=client_cls)

    assert instances["PRIMARY"].baudrate == 19200
    assert instances["CONTROL"].baudrate == 9600
    assert instances["CONTROL"].stopbits == 2


def test_tick_prefers_real_drive_feedback_over_last_command_echo():
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine("PRIMARY", control_port="CONTROL", client_cls=client_cls)
    control = instances["CONTROL"]

    engine.apply_command({"fan_pct": 60.0, "drum_speed_pct": 40.0})
    # Real drive reports back a different speed than what was commanded --
    # tick() should surface *that* (register 8451, /100 divisor), not just
    # echo the command back.
    control.register_values[(2, 8451)] = 5800  # Air actually running at 58.0%
    control.register_values[(1, 8451)] = 3900  # Drum actually running at 39.0%

    sample = engine.tick(1.0)

    assert sample["fan_pct"] == pytest.approx(58.0)
    assert sample["drum_speed_pct"] == pytest.approx(39.0)
    assert (8451, 2) in control.reads
    assert (8451, 1) in control.reads


def test_tick_falls_back_to_command_echo_when_feedback_register_disabled():
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine(
        "PRIMARY",
        control_port="CONTROL",
        air_feedback_register=None,
        drum_feedback_register=None,
        client_cls=client_cls,
    )
    engine.apply_command({"fan_pct": 60.0, "drum_speed_pct": 40.0})

    sample = engine.tick(1.0)

    assert sample["fan_pct"] == pytest.approx(60.0)
    assert sample["drum_speed_pct"] == pytest.approx(40.0)


def test_drum_range_clamps_above_70_percent():
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine("PRIMARY", control_port="CONTROL", client_cls=client_cls)
    control = instances["CONTROL"]

    engine.apply_command({"drum_speed_pct": 95.0})  # above the FZ-94's own 0-70% drive limit

    assert (8193, 7000, 1) in control.writes  # clamped to 70% -> 7000, not 9500


def _feed_charge_sequence(engine, primary):
    """BT 100.0 -> 90.0 within the detector's 20s charge window -- the
    default 8C-drop-triggers-CHARGE heuristic (see roast_heuristics.detector)."""
    primary.register_values[(11, 0)] = 1000  # BT 100.0C
    engine.tick(1.0)
    primary.register_values[(11, 0)] = 900  # BT 90.0C -- a 10C drop, over the 8C threshold
    return engine.tick(1.0)


def test_charge_is_never_auto_detected():
    """Real hardware means a real operator marks milestones by hand --
    ModbusEngine constructs its LiveRoastDetector with
    detect_milestones=False (see engine.py's __init__), so even a sharp
    BT drop that would trip the default 8C/20s heuristic fires nothing.
    The detector's own detect_milestones capability is covered directly
    in tests/test_live_roast_detector.py -- this just confirms the
    engine's shipped default actually uses it."""
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine("PRIMARY", client_cls=client_cls)
    primary = instances["PRIMARY"]

    _feed_charge_sequence(engine, primary)

    assert engine.get_new_events() == []


def test_reset_detection_restarts_the_elapsed_time_clock():
    """reset_detection() exists so a roast that starts *recording* after
    a live connection was already open for a while (see
    RoastSession.begin_recording()) gets a time axis starting fresh at 0,
    not wherever the earlier preview window left off -- an actually wrong
    time axis on the persisted roast otherwise, not just cosmetic."""
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine("PRIMARY", client_cls=client_cls)
    primary = instances["PRIMARY"]
    primary.register_values[(11, 0)] = 1000  # BT 100.0C, steady

    for _ in range(5):
        sample = engine.tick(1.0)
    assert sample["time_s"] == 5.0

    engine.reset_detection()

    sample = engine.tick(1.0)
    assert sample["time_s"] == 1.0


def test_notify_manual_charge_lets_turning_point_fire_despite_no_auto_charge():
    """Confirms the engine's own wrapper method actually forwards to its
    detector (test_live_roast_detector.py already covers the detector's
    own logic in isolation) -- CHARGE never auto-fires here
    (detect_milestones=False, confirmed by test_charge_is_never_auto_detected
    above), but Turning Point still should once notify_manual_charge tells
    the engine CHARGE happened."""
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine("PRIMARY", client_cls=client_cls)
    primary = instances["PRIMARY"]

    engine.notify_manual_charge(0.0, 96.0)

    primary.register_values[(11, 0)] = 900  # BT 90.0C -- dips further
    engine.tick(1.0)
    assert engine.get_new_events() == []

    primary.register_values[(11, 0)] = 906  # BT 90.6C -- rebounds 0.6C, over the 0.5C default
    engine.tick(1.0)
    events = engine.get_new_events()

    assert [e["type"] for e in events] == ["TURNING_POINT"]


def test_detect_milestones_true_opts_into_auto_charge():
    # Opt-in (RoastCreateRequest.auto_detect_milestones) -- the flag
    # this engine forwards to its LiveRoastDetector at construction.
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine("PRIMARY", client_cls=client_cls, detect_milestones=True)
    primary = instances["PRIMARY"]

    _feed_charge_sequence(engine, primary)

    assert [e["type"] for e in engine.get_new_events()] == ["CHARGE"]


def test_reset_detection_preserves_the_opted_in_detect_milestones_flag():
    # reset_detection() rebuilds the detector from scratch (see its own
    # docstring) -- must carry the constructor's own detect_milestones
    # setting forward, not silently reset it back to the False default.
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine("PRIMARY", client_cls=client_cls, detect_milestones=True)
    primary = instances["PRIMARY"]

    engine.reset_detection()
    _feed_charge_sequence(engine, primary)

    assert [e["type"] for e in engine.get_new_events()] == ["CHARGE"]


def test_mark_milestone_fired_forwards_to_the_detector():
    """Confirms the engine's own wrapper actually forwards (the
    detector's own dedup logic is covered in isolation in
    tests/test_live_roast_detector.py)."""
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine("PRIMARY", client_cls=client_cls, detect_milestones=True)
    primary = instances["PRIMARY"]
    _feed_charge_sequence(engine, primary)
    engine.get_new_events()  # drain CHARGE
    primary.register_values[(11, 0)] = 906  # rebounds -- fires TURNING_POINT
    engine.tick(1.0)
    engine.get_new_events()  # drain TURNING_POINT

    engine.mark_milestone_fired("DRY_END")  # operator clicked it manually, early

    primary.register_values[(11, 0)] = 1650  # BT 165.0C -- crosses the 160C default
    engine.tick(1.0)

    assert engine.get_new_events() == []
