"""POST /api/roasts / POST /api/roasts/{id}/start -- the ON-connects/
START-records split at the actual HTTP layer. Uses a nonexistent serial
port throughout (same BOGUS_PORT trick as test_modbus_register_overrides.py)
-- ModbusEngine.__init__ catches that connect failure internally rather
than raising, which is exactly the case RoastSessionManager.create()'s new
post-construction check exists to turn into a real, visible API error
instead of a silent 201."""
from __future__ import annotations

BOGUS_PORT = "/dev/nonexistent-for-tests"


def test_create_roast_with_unreachable_modbus_port_returns_an_error_not_201(client):
    resp = client.post("/api/roasts", json={
        "title": "Test Roast",
        "mode": "modbus_live",
        "modbus_port": BOGUS_PORT,
    })

    assert resp.status_code == 400
    assert BOGUS_PORT in resp.json()["detail"] or "connect" in resp.json()["detail"].lower()


def test_create_roast_with_unreachable_modbus_port_creates_no_roast(client):
    client.post("/api/roasts", json={
        "title": "Test Roast", "mode": "modbus_live", "modbus_port": BOGUS_PORT,
    })

    resp = client.get("/api/roasts")
    assert resp.status_code == 200
    assert resp.json() == []


def test_begin_recording_on_unknown_roast_returns_404(client):
    resp = client.post("/api/roasts/does-not-exist/start")
    assert resp.status_code == 404


def test_simulator_mode_still_creates_and_starts_atomically(client):
    """Sanity check that the mode branch in create_roast didn't change
    anything for simulator -- still one call, still shows up in history
    immediately as roasting."""
    resp = client.post("/api/roasts", json={"title": "Sim Roast", "mode": "simulator"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "roasting"

    resp = client.get("/api/roasts")
    assert any(r["id"] == body["id"] for r in resp.json())

    client.post(f"/api/roasts/{body['id']}/stop")
