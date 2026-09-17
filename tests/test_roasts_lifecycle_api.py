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


def test_roast_records_which_user_created_it(client):
    """The `client` fixture's auto-registered admin is "test-admin" (see
    conftest.py) -- create_roast/import_alog should stamp that onto the
    new roast, surfaced in the create response, the history list, and the
    detail view alike."""
    resp = client.post("/api/roasts", json={"title": "Attributed Roast", "mode": "simulator"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["created_by_username"] == "test-admin"
    roast_id = body["id"]

    resp = client.get("/api/roasts")
    listed = next(r for r in resp.json() if r["id"] == roast_id)
    assert listed["created_by_username"] == "test-admin"

    resp = client.get(f"/api/roasts/{roast_id}")
    assert resp.json()["created_by_username"] == "test-admin"

    client.post(f"/api/roasts/{roast_id}/stop")


def test_roast_created_before_this_field_existed_has_no_attribution(client):
    """A roast row with created_by_username left NULL (the migration's own
    backfill-nothing default -- see storage.py) should read back as None,
    not error or a placeholder string. Pops the in-memory session too
    (not just editing the DB row) -- otherwise get_roast_detail would
    still answer from the live session's own created_by_username instead
    of the DB row, which isn't what a genuinely pre-migration roast (no
    session at all, wiped by the process restart that brought the
    migration in) would look like -- see RoastSessionManager.
    get_roast_detail's session-first-then-DB-row fallback."""
    import backend.app.storage as storage
    from backend.app.roast_session.session import session_manager

    resp = client.post("/api/roasts", json={"title": "Old Roast", "mode": "simulator"})
    roast_id = resp.json()["id"]
    client.post(f"/api/roasts/{roast_id}/stop")
    session_manager.sessions.pop(roast_id, None)

    with storage._conn() as c:
        c.execute("UPDATE roasts SET created_by_username = NULL WHERE id = ?", (roast_id,))

    resp = client.get(f"/api/roasts/{roast_id}")
    assert resp.json()["created_by_username"] is None

    resp = client.get("/api/roasts")
    listed = next(r for r in resp.json() if r["id"] == roast_id)
    assert listed["created_by_username"] is None
