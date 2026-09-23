"""Control against the built-in simulated FZ-94, through the real Modbus
engine and the real API (unlike test_roast_control.py, which uses a fake
device). These run in real time, a few seconds each."""
from __future__ import annotations

import time

from backend.app.roast_session.session import session_manager


def _start_recording(client, **fields):
    roast_id = client.post(
        "/api/v1/roasts", json={"title": "Control sim", "mode": "modbus_live", "modbus_port": "sim://fz94", **fields}
    ).json()["id"]
    assert client.post(f"/api/v1/roasts/{roast_id}/start").status_code == 200
    return roast_id


def _wait_for(predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def _latest(roast_id):
    profile = session_manager.get(roast_id).profile
    return profile[-1] if profile else {}


def test_emergency_stop_turns_the_simulated_heater_off(client):
    roast_id = _start_recording(client)
    assert client.post(f"/api/v1/roasts/{roast_id}/commands", json={"heater_pct": 60, "fan_pct": 30}).status_code == 200
    assert _wait_for(lambda: session_manager.get(roast_id).profile and (_latest(roast_id).get("heater_pct") or 0) > 40)

    result = client.post(f"/api/v1/roasts/{roast_id}/emergency-stop").json()
    assert result["ok"] is True

    assert _wait_for(lambda: (_latest(roast_id).get("heater_pct") or 0) < 5 and (_latest(roast_id).get("fan_pct") or 0) > 90)
    events = client.get(f"/api/v1/roasts/{roast_id}").json()["events"]
    assert any(e["label"] == "Safety: emergency stop" for e in events)
    client.post(f"/api/v1/roasts/{roast_id}/stop")


def test_safety_limits_apply_to_the_real_device(client):
    client.put("/api/v1/settings", json={"ollama_url": None, "ollama_model": None, "control": {"heater_max_pct": 50, "fan_min_pct": 40}})
    roast_id = _start_recording(client)
    client.post(f"/api/v1/roasts/{roast_id}/commands", json={"heater_pct": 100, "fan_pct": 0})

    assert _wait_for(lambda: session_manager.get(roast_id).profile and (_latest(roast_id).get("heater_pct") or 0) > 30)
    time.sleep(1.2)
    latest = _latest(roast_id)
    assert latest["heater_pct"] <= 52
    assert latest["fan_pct"] >= 38
    client.post(f"/api/v1/roasts/{roast_id}/stop")


def test_target_control_moves_the_simulated_heater(client):
    roast_id = _start_recording(client)
    assert client.post(f"/api/v1/roasts/{roast_id}/commands", json={"heater_pct": 30}).status_code == 200
    assert client.post(f"/api/v1/roasts/{roast_id}/events", json={"type": "CHARGE", "label": "Charge"}).status_code == 200
    assert _wait_for(lambda: (_latest(roast_id).get("heater_pct") or 0) > 20)

    # A bean-temperature target far above the simulated roaster's: the heater should be brought up,
    # gradually (at most 1% per second), so this takes several seconds.
    started = client.put(f"/api/v1/roasts/{roast_id}/control/feedback", json={"variable": "bt", "setpoint": 400})
    assert started.status_code == 200
    assert _wait_for(lambda: (_latest(roast_id).get("heater_pct") or 0) > 36, timeout=20)
    status = client.get(f"/api/v1/roasts/{roast_id}/control").json()
    assert status["feedback"] is not None and status["feedback"]["output_pct"] > 33

    # Moving a slider by hand takes over.
    client.post(f"/api/v1/roasts/{roast_id}/commands", json={"heater_pct": 10})
    assert client.get(f"/api/v1/roasts/{roast_id}/control").json()["feedback"] is None
    client.post(f"/api/v1/roasts/{roast_id}/stop")


def test_watchdog_turns_the_heater_off_when_nobody_is_watching(client):
    client.put("/api/v1/settings", json={"ollama_url": None, "ollama_model": None, "control": {"client_watchdog_s": 2}})
    roast_id = _start_recording(client)
    client.post(f"/api/v1/roasts/{roast_id}/commands", json={"heater_pct": 60})
    assert _wait_for(lambda: session_manager.get(roast_id).profile and (_latest(roast_id).get("heater_pct") or 0) > 40)

    # No page has the roast open (the test client isn't subscribed), so after ~2 s the fail-safe trips.
    assert _wait_for(lambda: session_manager.get(roast_id).control.tripped_reason is not None, timeout=10)
    assert _wait_for(lambda: (_latest(roast_id).get("heater_pct") or 0) < 5)
    client.post(f"/api/v1/roasts/{roast_id}/stop")


def test_stopping_the_roast_switches_the_heater_off(client):
    roast_id = _start_recording(client)
    client.post(f"/api/v1/roasts/{roast_id}/commands", json={"heater_pct": 60})
    assert _wait_for(lambda: session_manager.get(roast_id).profile and (_latest(roast_id).get("heater_pct") or 0) > 40)
    session = session_manager.get(roast_id)

    client.post(f"/api/v1/roasts/{roast_id}/stop")
    assert session.control.current()["heater_pct"] == 0.0
