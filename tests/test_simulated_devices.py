"""Built-in simulated devices (hardware_fakes/sim.py): a "sim://..." port or
host makes the server start the matching fake itself, connect the real
engine to it, tag the roast "simulated", and stop the fake when the roast
ends. These use the real engines against the real fakes over loopback TCP --
nothing is mocked."""
from __future__ import annotations

import threading
import time

import pytest

from backend.app.roast_session.session import session_manager
from hardware_fakes import sim


def _sim_threads() -> list[str]:
    return [t.name for t in threading.enumerate() if t.name.startswith(("sim-", "tcp-serial"))]


def _wait_for_no_sim_threads(timeout: float = 4.0) -> list[str]:
    deadline = time.monotonic() + timeout
    while _sim_threads() and time.monotonic() < deadline:
        time.sleep(0.05)
    return _sim_threads()


# ---- the four fakes, against their real engines --------------------------------

def _read_twice(engine):
    time.sleep(0.6)
    out = [engine.tick(0.5) for _ in range(2)]
    return out[-1]


def test_fz94_simulated_device_serves_the_real_modbus_engine():
    from modbus_bridge import ModbusEngine

    handle = sim.start("sim://fz94")
    engine = ModbusEngine(handle.serial_url)
    try:
        reading = _read_twice(engine)
        assert engine.status()["connected"] is True and engine.status()["last_error"] is None
        assert reading["bt"] is not None and reading["et"] is not None
        engine.apply_command({"heater_pct": 50, "fan_pct": 40, "drum_speed_pct": 60})  # writes are accepted
    finally:
        engine.close()
        handle.stop()
    assert _wait_for_no_sim_threads() == []


def test_fz94_evo_simulated_device_serves_the_real_modbus_tcp_engine():
    from modbus_bridge import ModbusEngine
    from modbus_bridge.device_profiles import COFFEETECH_FZ94_EVO
    from pymodbus.client import ModbusTcpClient

    handle = sim.start("sim://fz94_evo")
    assert handle.serial_url is None  # the Evo is reached by host + port, not a serial URL
    engine = ModbusEngine.from_profile(
        COFFEETECH_FZ94_EVO, None, transport="tcp", host=handle.host, tcp_port=handle.port, client_cls=ModbusTcpClient
    )
    try:
        reading = _read_twice(engine)
        assert engine.status()["connected"] is True
        assert reading["bt"] is not None
    finally:
        engine.close()
        handle.stop()
    assert _wait_for_no_sim_threads() == []


def test_ms6514_simulated_device_serves_the_real_meter_engine():
    from ms6514_bridge.engine import MS6514Engine

    handle = sim.start("sim://ms6514")
    engine = MS6514Engine(handle.serial_url)
    try:
        time.sleep(1.0)
        reading = engine.tick(0.5)
        assert engine.status()["connected"] is True
        assert reading["bt"] is not None and reading["et"] is not None
    finally:
        engine.close()
        handle.stop()
    assert _wait_for_no_sim_threads() == []


def test_tc4_simulated_device_serves_the_real_tc4_engine():
    from tc4_bridge.engine import TC4Engine

    handle = sim.start("sim://tc4")
    engine = TC4Engine(handle.serial_url)
    try:
        reading = _read_twice(engine)
        assert engine.status()["connected"] is True
        assert reading["bt"] is not None and reading["et"] is not None
        engine.apply_command({"heater_pct": 30, "fan_pct": 20})
    finally:
        engine.close()
        handle.stop()
    assert _wait_for_no_sim_threads() == []


def test_unknown_simulated_device_is_rejected():
    with pytest.raises(ValueError, match="Unknown simulated device"):
        sim.start("sim://nope")


def test_every_kind_has_a_starter():
    """KINDS and start() must stay in step (a new device model needs both)."""
    for key in sim.KINDS:
        handle = sim.start(f"sim://{key}")
        handle.stop()
    assert _wait_for_no_sim_threads() == []


# ---- the simulated roast begins at START, not at ON ----------------------------

def _open(kind: str):
    """(handle, real engine connected to it) for one simulated device kind."""
    from modbus_bridge import ModbusEngine
    from modbus_bridge.device_profiles import COFFEETECH_FZ94_EVO
    from ms6514_bridge.engine import MS6514Engine
    from pymodbus.client import ModbusTcpClient
    from tc4_bridge.engine import TC4Engine

    handle = sim.start(f"sim://{kind}")
    if kind == "fz94":
        return handle, ModbusEngine(handle.serial_url)
    if kind == "fz94_evo":
        return handle, ModbusEngine.from_profile(
            COFFEETECH_FZ94_EVO, None, transport="tcp", host=handle.host, tcp_port=handle.port, client_cls=ModbusTcpClient
        )
    if kind == "ms6514":
        return handle, MS6514Engine(handle.serial_url)
    return handle, TC4Engine(handle.serial_url)


@pytest.mark.parametrize("kind", ["fz94", "fz94_evo", "ms6514", "tc4"])
def test_a_simulated_device_waits_at_charge_until_the_roast_begins(kind):
    """Regression: the simulated roast used to start running when the app connected (ON),
    so time spent connected before START used the roast up -- start late enough and
    the recording began mid-roast, or after it had finished (flat, straight lines)."""
    handle, engine = _open(kind)
    try:
        time.sleep(0.8)
        first = engine.tick(0.5)
        time.sleep(2.0)
        still = engine.tick(0.5)
        assert abs(still["et"] - first["et"]) < 0.5, "the device moved while merely connected"
        assert abs(still["bt"] - 96.0) < 1.5 and abs(still["et"] - 200.0) < 4.0  # the charge readings

        handle.begin_roast()
        for _ in range(6):  # read at a realistic cadence (the meter engine returns the oldest buffered frame)
            time.sleep(0.5)
            moving = engine.tick(0.5)
        assert moving["et"] - still["et"] > 3.0, "the roast did not start when begin_roast() was called"
    finally:
        engine.close()
        handle.stop()
    assert _wait_for_no_sim_threads() == []


# ---- through the HTTP API ---------------------------------------------------------

def _create(client, **fields):
    return client.post("/api/roasts", json={"title": "Sim roast", **fields})


def _tags(client, roast_id):
    return next(r for r in client.get("/api/roasts").json() if r["id"] == roast_id)["tags"]


@pytest.mark.parametrize(
    "fields",
    [
        {"mode": "modbus_live", "modbus_port": "sim://fz94"},
        {"mode": "ms6514_live", "ms6514_port": "sim://ms6514"},
        {"mode": "tc4_live", "tc4_port": "sim://tc4"},
        {
            "mode": "modbus_live", "modbus_transport": "tcp", "modbus_host": "sim://fz94_evo",
            "modbus_device_profile_id": "coffeetech-fz94-evo",
        },
    ],
    ids=["fz94", "ms6514", "tc4", "fz94_evo"],
)
def test_roast_on_a_simulated_device_records_tags_and_cleans_up(client, fields):
    resp = _create(client, **fields)
    assert resp.status_code == 201, resp.text
    roast_id = resp.json()["id"]
    session = session_manager.get(roast_id)
    assert session._sim is not None  # the fake is running behind the connection

    assert client.post(f"/api/roasts/{roast_id}/start").status_code == 200
    time.sleep(1.2)
    assert client.post(f"/api/roasts/{roast_id}/stop").status_code == 200

    assert "simulated" in _tags(client, roast_id)
    assert session._sim is None  # stopped with the roast
    assert _wait_for_no_sim_threads() == []
    # ...and the port the roast recorded is the sim:// value, not the throwaway socket URL
    detail = client.get(f"/api/roasts/{roast_id}").json()
    recorded = detail.get("modbus_port") or detail.get("modbus_host") or detail.get("ms6514_port") or detail.get("tc4_port")
    assert recorded.startswith("sim://")


def test_turning_a_simulated_device_off_before_recording_stops_it_and_saves_nothing(client):
    resp = _create(client, mode="tc4_live", tc4_port="sim://tc4")
    roast_id = resp.json()["id"]
    assert session_manager.get(roast_id)._sim is not None

    client.post(f"/api/roasts/{roast_id}/stop")

    assert _wait_for_no_sim_threads() == []
    assert client.get("/api/roasts").json() == []  # nothing was ever recorded


def test_a_simulated_device_for_another_data_source_is_refused(client):
    resp = _create(client, mode="modbus_live", modbus_port="sim://tc4")
    assert resp.status_code == 400
    assert "can't be used with this data source" in resp.json()["detail"]
    assert _wait_for_no_sim_threads() == []


def test_an_unknown_simulated_device_is_refused(client):
    resp = _create(client, mode="ms6514_live", ms6514_port="sim://nope")
    assert resp.status_code == 400
    assert "Unknown simulated device" in resp.json()["detail"]


def test_a_failure_while_building_the_engine_does_not_leave_the_fake_running(client):
    # An unknown device profile makes the session fail *after* the fake started.
    resp = _create(
        client, mode="modbus_live", modbus_port="sim://fz94", modbus_device_profile_id="no-such-profile"
    )
    assert resp.status_code == 400
    assert _wait_for_no_sim_threads() == []


def test_real_ports_still_work_unchanged(client):
    resp = _create(client, mode="ms6514_live", ms6514_port="/dev/nonexistent-for-tests")
    assert resp.status_code == 400  # a bad real port still fails like before, and isn't tagged or simulated


@pytest.mark.parametrize(
    "fields",
    [
        {"mode": "modbus_live", "modbus_port": "sim://fz94"},
        {"mode": "ms6514_live", "ms6514_port": "sim://ms6514"},
        {"mode": "tc4_live", "tc4_port": "sim://tc4"},
        {
            "mode": "modbus_live", "modbus_transport": "tcp", "modbus_host": "sim://fz94_evo",
            "modbus_device_profile_id": "coffeetech-fz94-evo",
        },
    ],
    ids=["fz94", "ms6514", "tc4", "fz94_evo"],
)
def test_the_recording_starts_at_charge_however_long_you_stay_connected_first(client, fields):
    roast_id = _create(client, **fields).json()["id"]
    time.sleep(4.0)  # connected, not recording -- the simulated roast must not advance meanwhile
    assert client.post(f"/api/roasts/{roast_id}/start").status_code == 200
    time.sleep(4.5)
    client.post(f"/api/roasts/{roast_id}/stop")

    profile = client.get(f"/api/roasts/{roast_id}").json()["profile"]
    assert len(profile) >= 2
    assert abs(profile[0]["bt"] - 96.0) < 1.5      # starts at Charge...
    assert profile[0]["et"] < 207.0                # (a roast already 4 s in would read 210+)
    assert profile[-1]["et"] > profile[0]["et"]    # ...and then really moves
