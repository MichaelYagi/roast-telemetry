"""tc4_bridge.engine.TC4Engine -- direct unit tests via an injected fake
serial_cls (same DI pattern ModbusEngine's own client_cls injection
point uses), so these run fully in-process with no real/virtual
hardware involved and stay fast.
"""
from __future__ import annotations

import os
import pty

from tc4_bridge.engine import TC4Engine, _parse_read_response


class FakeSerial:
    """Records every write, and returns whatever's next in a
    pre-loaded response queue on readline() -- an empty queue means
    "no response", matching a real timeout."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.is_open = True
        self.written: list[bytes] = []
        self.responses: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def readline(self) -> bytes:
        return self.responses.pop(0) if self.responses else b""

    def close(self) -> None:
        self.is_open = False


def _make_engine(**kwargs) -> tuple[TC4Engine, FakeSerial]:
    fakes: list[FakeSerial] = []

    def factory(**kw):
        fake = FakeSerial(**kw)
        fakes.append(fake)
        return fake

    engine = TC4Engine("COM5", serial_cls=factory, **kwargs)
    return engine, fakes[0]


# -- _parse_read_response ---------------------------------------------


def test_parse_read_response_normal_line():
    parsed = _parse_read_response("22.5,95.8,180.2,110.0,0.0")
    assert parsed == {"bt": 95.8, "et": 180.2, "dt": 110.0}


def test_parse_read_response_missing_optional_fields():
    # ambient,chan1,chan2 -- the minimum 3 fields this app treats as a
    # valid reading (needs at least BT); chan3 (dt) legitimately absent.
    parsed = _parse_read_response("22.5,95.8,180.2")
    assert parsed == {"bt": 95.8, "et": 180.2, "dt": None}


def test_parse_read_response_too_short_is_none():
    assert _parse_read_response("22.5,95.8") is None  # only ambient+BT, no ET
    assert _parse_read_response("22.5") is None
    assert _parse_read_response("") is None


def test_parse_read_response_non_numeric_is_none():
    assert _parse_read_response("not,a,valid,response,line") is None


# -- construction -------------------------------------------------------


def test_engine_sends_units_c_once_at_connect():
    engine, fake = _make_engine()
    assert fake.written == [b"UNITS,C\n"]
    assert engine.status()["connected"] is True


def test_engine_requires_a_port():
    try:
        TC4Engine("", serial_cls=FakeSerial)
        assert False, "expected ValueError"
    except ValueError:
        pass


# -- tick() ---------------------------------------------------------------


def test_tick_parses_a_valid_reading():
    engine, fake = _make_engine()
    fake.responses.append(b"22.5,95.8,180.2,110.0,0.0\n")

    sample = engine.tick(1.0)

    assert sample["bt"] == 95.8
    assert sample["et"] == 180.2
    assert sample["dt"] == 110.0
    assert sample["time_s"] == 1.0
    assert b"READ\n" in fake.written


def test_tick_falls_back_to_last_known_value_on_bad_read():
    engine, fake = _make_engine()
    fake.responses.append(b"22.5,95.8,180.2,110.0,0.0\n")
    engine.tick(1.0)

    fake.responses.append(b"garbage\n")  # unparseable -- keeps last-known values
    sample = engine.tick(1.0)

    assert sample["bt"] == 95.8
    assert sample["et"] == 180.2
    assert sample["dt"] == 110.0
    assert engine.status()["last_error"] is not None


def test_tick_handles_no_response_at_all():
    engine, fake = _make_engine()
    # No responses queued -- readline() returns b"", same as a real timeout.
    sample = engine.tick(1.0)
    assert sample["bt"] is None
    assert sample["et"] is None
    assert engine.status()["last_error"] is not None


# -- apply_command() -------------------------------------------------------


def test_apply_command_writes_heater_and_fan():
    engine, fake = _make_engine()
    fake.written.clear()

    engine.apply_command({"heater_pct": 55.4, "fan_pct": 30.0})

    assert b"OT1,55\n" in fake.written
    assert b"DCFAN,30\n" in fake.written


def test_apply_command_clamps_to_0_100():
    engine, fake = _make_engine()
    fake.written.clear()

    engine.apply_command({"heater_pct": 150, "fan_pct": -20})

    assert b"OT1,100\n" in fake.written
    assert b"DCFAN,0\n" in fake.written


def test_apply_command_ignores_drum_speed_pct():
    engine, fake = _make_engine()
    fake.written.clear()

    engine.apply_command({"drum_speed_pct": 50})

    assert fake.written == []


# -- API-level: POST /roasts is the ON action, not ON+start ---------------
#
# Real bug caught via live end-to-end testing (not the unit tests above):
# tc4_live was missing from backend/app/api/roasts.py's create_roast mode
# tuple, so it fell through to the "connect *and* start recording in one
# step" branch meant for simulator/alog_playback -- every tc4_live roast
# started already "roasting" instead of connected-but-idle, skipping the
# real ON/START split every other live-bridge mode gets. Uses a real pty
# (Python's own stdlib, no socat/subprocess needed) as the "device" --
# TC4Engine's connect check only cares that the serial port actually
# opens, not that anything meaningful answers on it.


def test_tc4_live_create_roast_connects_without_recording(client):
    master_fd, slave_fd = pty.openpty()
    try:
        port = os.ttyname(slave_fd)
        resp = client.post("/api/roasts", json={"title": "TC4 Connect Test", "mode": "tc4_live", "tc4_port": port})
        assert resp.status_code == 201
        assert resp.json()["status"] == "idle"
    finally:
        os.close(master_fd)
        os.close(slave_fd)
