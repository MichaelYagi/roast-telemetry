"""ModbusEngine's Modbus TCP/Ethernet transport (transport="tcp") --
added for the Coffee-Tech FZ-94 Evo, a genuinely different roaster from
the plain FZ-94 that's always been RTU/USB-only. Exercised against a
fake TCP-shaped pymodbus client (matching ModbusTcpClient's own
constructor: host/port/timeout, no baudrate/bytesize/parity/stopbits --
see modbus_bridge/engine.py's transport="tcp" branch) so these run in
plain CI, no real network needed. See tests/test_modbus_engine.py's own
_make_fake_client_cls for the RTU-shaped equivalent this mirrors.
"""
from __future__ import annotations

import logging

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
from tests.test_modbus_engine import _FakeResult


def _make_fake_tcp_client_cls():
    instances: dict[tuple[str, int], "_FakeTcpClient"] = {}

    class _FakeTcpClient:
        def __init__(self, host, port, timeout):
            self.host = host
            self.port = port
            self.reads: list[tuple[int, int]] = []
            self.writes: list[tuple[int, int, int]] = []
            self.register_values: dict[tuple[int, int], int] = {}
            self.write_error = False
            instances[(host, port)] = self

        def connect(self):
            return True

        def read_holding_registers(self, address, count, device_id):
            self.reads.append((address, device_id))
            value = self.register_values.get((device_id, address), 0)
            return _FakeResult(registers=[value] * count)

        def write_register(self, address, value, device_id):
            self.writes.append((address, value, device_id))
            return _FakeResult(error=self.write_error)

        def close(self):
            pass

    return _FakeTcpClient, instances


def _evo_profile() -> DeviceProfile:
    return next(p for p in BUILT_IN_PROFILES if p.id == "coffeetech-fz94-evo")


def test_open_connections_constructs_a_tcp_client_with_host_and_port():
    client_cls, instances = _make_fake_tcp_client_cls()
    engine = ModbusEngine.from_profile(
        _evo_profile(), transport="tcp", host="192.168.1.2", tcp_port=502, client_cls=client_cls,
    )
    assert ("192.168.1.2", 502) in instances
    assert engine.port == "192.168.1.2:502"
    assert engine.status()["connected"] is True


def test_evo_profile_reads_bt_et_and_writes_air_drum_burner_direct_registers():
    client_cls, instances = _make_fake_tcp_client_cls()
    engine = ModbusEngine.from_profile(
        _evo_profile(), transport="tcp", host="192.168.1.2", tcp_port=502, client_cls=client_cls,
    )
    tcp = instances[("192.168.1.2", 502)]
    tcp.register_values[(1, 100)] = 965  # BT slave 1 reg 100 -> 96.5C
    tcp.register_values[(1, 80)] = 1200  # ET slave 1 reg 80 -> 120.0C

    sample = engine.tick(1.0)
    assert sample["bt"] == pytest.approx(96.5)
    assert sample["et"] == pytest.approx(120.0)

    engine.apply_command({"heater_pct": 50.0, "fan_pct": 45.0, "drum_speed_pct": 40.0})
    assert (35, 50, 1) in tcp.writes  # Burner
    assert (20, 45, 1) in tcp.writes  # Air
    assert (16, 40, 1) in tcp.writes  # Drum
    # No run/stop word for any of these -- direct register writes only.
    assert not any(addr == 8192 for addr, _, _ in tcp.writes)


def test_evo_profile_clamps_air_drum_to_their_configured_30_70_range():
    client_cls, instances = _make_fake_tcp_client_cls()
    engine = ModbusEngine.from_profile(
        _evo_profile(), transport="tcp", host="192.168.1.2", tcp_port=502, client_cls=client_cls,
    )
    tcp = instances[("192.168.1.2", 502)]

    engine.apply_command({"fan_pct": 90.0})  # above the 30-70 range
    assert (20, 70, 1) in tcp.writes  # clamped to the range max, not written as 90


def test_out_of_range_write_logs_a_warning(caplog):
    client_cls, instances = _make_fake_tcp_client_cls()
    engine = ModbusEngine.from_profile(
        _evo_profile(), transport="tcp", host="192.168.1.2", tcp_port=502, client_cls=client_cls,
    )
    with caplog.at_level(logging.WARNING, logger="modbus_bridge.engine"):
        engine.apply_command({"drum_speed_pct": 5.0})  # below the 30-70 range
    assert any("out of range" in r.message for r in caplog.records)


def test_device_rejected_write_logs_a_warning(caplog):
    client_cls, instances = _make_fake_tcp_client_cls()
    engine = ModbusEngine.from_profile(
        _evo_profile(), transport="tcp", host="192.168.1.2", tcp_port=502, client_cls=client_cls,
    )
    tcp = instances[("192.168.1.2", 502)]
    tcp.write_error = True
    with caplog.at_level(logging.WARNING, logger="modbus_bridge.engine"):
        engine.apply_command({"heater_pct": 50.0})
    assert any("rejected by device" in r.message for r in caplog.records)


def test_commanding_an_unconfigured_channel_logs_a_warning(caplog):
    """damper_pct-style unassigned commands are a silent no-op today --
    this is exactly the "wrong control mode" signal the diagnostics
    logging is meant to surface (see the Evo profile: no damper channel
    is configured on it)."""
    profile = DeviceProfile(
        id="test-no-drum", name="Test Roaster (no Drum)", created_at="2026-01-01T00:00:00+00:00",
        temp_channels=[ModbusTempChannel(role=ModbusChannelRole.BT, slave_id=1, register_address=0, divisor=10.0)],
        control_channels=[
            ModbusControlChannel(
                maps_to="fan_pct", kind=ModbusControlKind.DIRECT_REGISTER, slave_id=1,
                write_register=20, write_scale=1.0, value_range=(0.0, 100.0),
            ),
        ],
    )
    client_cls, _ = _make_fake_tcp_client_cls()
    engine = ModbusEngine.from_profile(profile, transport="tcp", host="10.0.0.1", tcp_port=502, client_cls=client_cls)
    with caplog.at_level(logging.WARNING, logger="modbus_bridge.engine"):
        engine.apply_command({"drum_speed_pct": 40.0})
    assert any("no matching control channel" in r.message for r in caplog.records)
