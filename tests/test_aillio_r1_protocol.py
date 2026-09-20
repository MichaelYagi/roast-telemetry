"""aillio_bridge/r1.py -- pure byte-parsing/command-building logic for
the Aillio Bullet R1, traced from the manufacturer's own shipped
roaster-scope driver source (see that module's own docstring for the
full sourcing note). No real hardware, pyusb, or libusb involved --
parse_state()/build_commands() are pure functions of bytes/numbers in,
bytes/numbers out, exercised here with synthetic byte arrays built
directly from the confirmed offsets.
"""
from __future__ import annotations

from struct import pack

import pytest

from aillio_bridge.r1 import DRUM_RANGE, FAN_RANGE, HEATER_RANGE, AillioR1Protocol


def _state_bytes(
    *, bt=90.0, dt=100.0, exhaust=80.0, minutes=1, seconds=30,
    fan=6, heater=4, drum=5, state=0x06, irt=95.0, pcb_temp=40.0,
    valid=10, fan_rpm=1200, voltage=240,
) -> bytes:
    buf = bytearray(64)
    buf[0:4] = pack("f", bt)
    buf[8:12] = pack("f", dt)
    buf[16:20] = pack("f", exhaust)
    buf[24] = minutes
    buf[25] = seconds
    buf[26] = fan
    buf[27] = heater
    buf[28] = drum
    buf[29] = state
    buf[32:36] = pack("f", irt)
    buf[36:40] = pack("f", pcb_temp)
    buf[41] = valid
    buf[44:46] = pack("h", fan_rpm)
    buf[48:50] = pack("h", voltage)
    return bytes(buf)


def test_parse_state_decodes_every_field():
    p = AillioR1Protocol()
    sample = p.parse_state(_state_bytes(bt=91.2, dt=101.5, heater=9, fan=12, drum=1, state=0x06))
    assert sample["bt"] == pytest.approx(91.2)
    assert sample["dt"] == pytest.approx(101.5)
    assert sample["heater_pct"] == pytest.approx(100.0)  # native 9 -> top of 0-9 range
    assert sample["fan_pct"] == pytest.approx(100.0)  # native 12 -> top of 1-12 range
    assert sample["drum_speed_pct"] == pytest.approx(0.0)  # native 1 -> bottom of 1-9 range
    assert sample["extra"]["State"] == "roasting"
    assert sample["extra"]["Fan RPM"] == 1200
    assert sample["extra"]["Voltage"] == 240


def test_parse_state_rejects_an_invalid_marker():
    p = AillioR1Protocol()
    assert p.parse_state(_state_bytes(valid=7)) is None


def test_parse_state_rejects_a_too_short_buffer():
    p = AillioR1Protocol()
    assert p.parse_state(b"\x00" * 10) is None


def test_native_ranges_match_the_source_driver():
    assert HEATER_RANGE == (0, 9)
    assert FAN_RANGE == (1, 12)
    assert DRUM_RANGE == (1, 9)


def test_build_commands_heater_sends_that_many_increments():
    p = AillioR1Protocol()
    # current=0% (native 0), target=100% (native 9) -> 9 INCR packets
    packets = p.build_commands("heater_pct", current=0.0, target=100.0)
    assert len(packets) == 9
    assert all(pkt == [0x34, 0x01, 0xAA, 0xAA] for pkt in packets)


def test_build_commands_heater_sends_decrements_when_lowering():
    p = AillioR1Protocol()
    packets = p.build_commands("heater_pct", current=100.0, target=0.0)
    assert len(packets) == 9
    assert all(pkt == [0x34, 0x02, 0xAA, 0xAA] for pkt in packets)


def test_build_commands_no_change_sends_nothing():
    p = AillioR1Protocol()
    assert p.build_commands("heater_pct", current=50.0, target=50.0) == []


def test_build_commands_fan_caps_at_eleven_packets():
    p = AillioR1Protocol()
    # A huge (implausible) diff should still be capped, matching the
    # source driver's own defensive min(d, 11) clamp -- guards against
    # an absurd packet run if state were ever wildly desynced.
    packets = p.build_commands("fan_pct", current=0.0, target=100.0)
    assert len(packets) <= 11


def test_build_commands_drum_is_a_single_absolute_set_packet():
    p = AillioR1Protocol()
    # Drum has no INCR/DECR pair on this model -- one direct-set packet
    # regardless of current position.
    packets = p.build_commands("drum_speed_pct", current=100.0, target=0.0)
    assert len(packets) == 1
    assert packets[0][0:2] == [0x32, 0x01]
    assert packets[0][2] == 1  # native bottom of the 1-9 range


def test_build_commands_unknown_channel_returns_nothing():
    p = AillioR1Protocol()
    assert p.build_commands("burner_sv_c", current=0.0, target=100.0) == []
