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
