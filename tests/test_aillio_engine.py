"""aillio_bridge/engine.py -- AillioEngine's tick()/apply_command()/
status() contract, exercised against a fake transport_cls (the same
dependency-injection seam ModbusEngine's client_cls already provides)
so this runs in plain CI with no real USB device, pyusb, or libusb
involved at all.
"""
from __future__ import annotations

from struct import pack

import pytest

from aillio_bridge.engine import AillioEngine


def _state_bytes(*, bt=90.0, dt=100.0, heater=4, fan=6, drum=5, valid=10) -> bytes:
    buf = bytearray(64)
    buf[0:4] = pack("f", bt)
    buf[8:12] = pack("f", dt)
    buf[26] = fan
    buf[27] = heater
    buf[28] = drum
    buf[41] = valid
    return bytes(buf)


class _FakeTransport:
    """Records every write, and replies with whatever `state_bytes` is
    currently set to on each read -- a test flips that between tick()
    calls to simulate the device's state actually changing."""

    def __init__(self, vid_pid_candidates):
        self.vid_pid_candidates = vid_pid_candidates
        self.writes: list[list[int]] = []
        self.state_bytes = _state_bytes()
        self.closed = False

    def write(self, command):
        self.writes.append(list(command))

    def read(self, length):
        # Two 64-byte replies expected per poll (STATUS1, STATUS2) --
        # only the first carries real fields for this fake, the second
        # is just padding since AillioR1Protocol.parse_state only reads
        # the first 50 bytes of the concatenated buffer.
        return self.state_bytes if len(self.writes) % 2 == 1 else bytes(64)

    def close(self):
        self.closed = True


def _make_engine(transport_cls=_FakeTransport):
    return AillioEngine("r1", transport_cls=transport_cls)


def test_unknown_model_raises_value_error():
    with pytest.raises(ValueError):
        AillioEngine("r3", transport_cls=_FakeTransport)


def test_tick_reads_bt_dt_and_extras():
    engine = _make_engine()
    sample = engine.tick(1.0)
    assert sample["bt"] == pytest.approx(90.0)
    assert sample["dt"] == pytest.approx(100.0)
    assert sample["et"] is None  # no ET probe on this device
    assert engine.status()["connected"] is True


def test_tick_holds_last_known_values_when_a_poll_is_invalid():
    engine = _make_engine()
    first = engine.tick(1.0)
    assert first["bt"] == pytest.approx(90.0)

    # Simulate the device sending one of its periodic unrelated
    # messages (invalid marker) -- see AillioR1Protocol.parse_state.
    transport = engine._transport
    transport.state_bytes = _state_bytes(bt=999.0, valid=0)
    second = engine.tick(1.0)
    assert second["bt"] == pytest.approx(90.0)  # held, not clobbered with garbage


def test_apply_command_sends_the_right_number_of_heater_packets():
    engine = _make_engine()
    engine.tick(1.0)  # heater starts at native 4/9 == ~44.4%

    engine.apply_command({"heater_pct": 100.0})
    transport = engine._transport
    heater_writes = [w for w in transport.writes if w[0] == 0x34]
    # native 4 -> 9 is a diff of 5 increments
    assert len(heater_writes) == 5
    assert all(w == [0x34, 0x01, 0xAA, 0xAA] for w in heater_writes)


def test_apply_command_before_any_tick_is_refused_not_guessed(caplog):
    import logging

    engine = _make_engine()
    with caplog.at_level(logging.WARNING, logger="aillio_bridge.engine"):
        engine.apply_command({"heater_pct": 50.0})
    transport = engine._transport
    assert transport.writes == []
    assert any("current position unknown" in r.message for r in caplog.records)


def test_apply_command_on_a_channel_this_model_lacks_logs_a_warning(caplog):
    """A hypothetical model without a Drum channel -- exercised via the
    injectable `protocol` override (test-only) rather than a real
    second adapter, same "commanded but no matching channel" diagnostic
    ModbusEngine already has for exactly this situation."""
    import logging

    from aillio_bridge.r1 import AillioR1Protocol

    class _NoDrumProtocol(AillioR1Protocol):
        CONTROLLABLE_CHANNELS = ("heater_pct", "fan_pct")

    engine = AillioEngine("r1", transport_cls=_FakeTransport, protocol=_NoDrumProtocol())
    engine.tick(1.0)
    with caplog.at_level(logging.WARNING, logger="aillio_bridge.engine"):
        engine.apply_command({"drum_speed_pct": 50.0})
    assert any("no such channel" in r.message for r in caplog.records)
    assert engine._transport.writes == [[0x30, 0x01], [0x30, 0x03]]  # only the tick()'s own poll, no drum write


def test_close_releases_the_transport():
    engine = _make_engine()
    transport = engine._transport
    engine.close()
    assert transport.closed is True
    assert engine._transport is None


def test_connect_failure_is_caught_not_raised():
    class _FailingTransport:
        def __init__(self, vid_pid_candidates):
            raise RuntimeError("device not found")

    engine = AillioEngine("r1", transport_cls=_FailingTransport)
    status = engine.status()
    assert status["connected"] is False
    assert "device not found" in status["last_error"]
