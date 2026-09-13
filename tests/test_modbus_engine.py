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
    # real setpoint. 227.5C -> raw 2275 -> 85% across the default 100-250C
    # range, same math as the write-side test below, just the read side.
    primary.register_values[(12, 5)] = 2275

    sample = engine.tick(1.0)

    assert sample["heater_pct"] == pytest.approx(85.0)
    assert (5, 12) in primary.reads


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

    # 85% across the default 100-250C SV range -> 227.5C -> x10 -> 2275,
    # written to slave 12 (same slave as the DT probe), register 5.
    assert (5, 2275, 12) in primary.writes


def test_apply_command_heater_pct_extremes_map_to_sv_range_bounds():
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine("PRIMARY", client_cls=client_cls)
    primary = instances["PRIMARY"]

    engine.apply_command({"heater_pct": 0.0})
    engine.apply_command({"heater_pct": 100.0})

    assert (5, 1000, 12) in primary.writes  # 0% -> 100.0C -> 1000
    assert (5, 2500, 12) in primary.writes  # 100% -> 250.0C -> 2500


def test_apply_command_fan_and_drum_write_run_and_frequency_to_control_client():
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine("PRIMARY", control_port="CONTROL", client_cls=client_cls)
    primary = instances["PRIMARY"]
    control = instances["CONTROL"]

    engine.apply_command({"fan_pct": 60.0, "drum_speed_pct": 40.0})

    # Air = slave 1: run (register 8192, value 2) then frequency (8193, 60*100=6000)
    assert (8192, 2, 1) in control.writes
    assert (8193, 6000, 1) in control.writes
    # Drum = slave 2: same pair, its own slave
    assert (8192, 2, 2) in control.writes
    assert (8193, 4000, 2) in control.writes
    # Drive writes never touch the temperature/burner connection.
    assert primary.writes == []


def test_apply_command_zero_drive_value_sends_stop_not_run():
    client_cls, instances = _make_fake_client_cls()
    engine = ModbusEngine("PRIMARY", control_port="CONTROL", client_cls=client_cls)
    control = instances["CONTROL"]

    engine.apply_command({"fan_pct": 0.0})

    assert (8192, 1, 1) in control.writes  # 1 = Stop
    assert (8193, 0, 1) in control.writes


def test_drives_share_the_primary_connection_by_default():
    # Confirmed single-bus architecture (Artisan's own shipped FZ94.aset
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


def test_default_connection_settings_match_the_shipped_artisan_preset():
    client_cls, instances = _make_fake_client_cls()
    ModbusEngine("PRIMARY", client_cls=client_cls)

    # 19200 baud, 8 data bits, no parity, 2 stop bits -- straight from
    # Artisan's own FZ94.aset [Modbus] block, not the earlier (wrong)
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
    control.register_values[(1, 8451)] = 5800  # Air actually running at 58.0%
    control.register_values[(2, 8451)] = 3900  # Drum actually running at 39.0%

    sample = engine.tick(1.0)

    assert sample["fan_pct"] == pytest.approx(58.0)
    assert sample["drum_speed_pct"] == pytest.approx(39.0)
    assert (8451, 1) in control.reads
    assert (8451, 2) in control.reads


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

    assert (8193, 7000, 2) in control.writes  # clamped to 70% -> 7000, not 9500
