"""POST /roasts/{id}/weight (roasted) and /roasts/{id}/weight-green --
both editable at any time, including for a roast whose in-memory session
is gone (a real backend restart, or just time passing since it finished).

Before this change, set_weight (roasted) only ever worked against a
still-live session_manager.get(roast_id) -- no cold DB-only fallback the
way delete_event/retime_event already had (see test_roast_event_editing.py's
own warm/cold split, same pattern followed here) -- so editing weight on
a genuinely historical roast 404'd. weight-green didn't exist at all.
"""
from __future__ import annotations

from backend.app.roast_session.session import session_manager


def _create_roast(client) -> str:
    resp = client.post("/api/roasts", json={"title": "Weight Editing Roast", "mode": "simulator"})
    return resp.json()["id"]


# -- warm (session still in memory) --------------------------------------


def test_set_weight_roasted_via_api_warm(client):
    roast_id = _create_roast(client)

    resp = client.post(f"/api/roasts/{roast_id}/weight", params={"grams": 1200.0})
    assert resp.status_code == 200
    assert resp.json()["weight_roasted_g"] == 1200.0

    resp = client.get(f"/api/roasts/{roast_id}")
    assert resp.json()["weight_roasted_g"] == 1200.0

    client.post(f"/api/roasts/{roast_id}/stop")


def test_set_weight_green_via_api_warm(client):
    roast_id = _create_roast(client)

    resp = client.post(f"/api/roasts/{roast_id}/weight-green", params={"grams": 1500.0})
    assert resp.status_code == 200
    assert resp.json()["weight_green_g"] == 1500.0

    resp = client.get(f"/api/roasts/{roast_id}")
    assert resp.json()["weight_green_g"] == 1500.0

    client.post(f"/api/roasts/{roast_id}/stop")


# -- cold (popped, alog+DB-row only) --------------------------------------


def test_set_weight_roasted_via_api_cold(client):
    # Simulates a backend restart since this roast finished -- same trick
    # test_roast_event_editing.py's own cold tests use.
    roast_id = _create_roast(client)
    client.post(f"/api/roasts/{roast_id}/stop")
    session_manager.sessions.pop(roast_id, None)

    resp = client.post(f"/api/roasts/{roast_id}/weight", params={"grams": 1180.0})
    assert resp.status_code == 200
    assert resp.json()["weight_roasted_g"] == 1180.0

    resp = client.get(f"/api/roasts/{roast_id}")
    assert resp.json()["weight_roasted_g"] == 1180.0


def test_set_weight_green_via_api_cold(client):
    roast_id = _create_roast(client)
    client.post(f"/api/roasts/{roast_id}/stop")
    session_manager.sessions.pop(roast_id, None)

    resp = client.post(f"/api/roasts/{roast_id}/weight-green", params={"grams": 1520.0})
    assert resp.status_code == 200
    assert resp.json()["weight_green_g"] == 1520.0

    resp = client.get(f"/api/roasts/{roast_id}")
    assert resp.json()["weight_green_g"] == 1520.0


def test_set_weight_endpoints_404_for_unknown_roast(client):
    resp = client.post("/api/roasts/does-not-exist/weight", params={"grams": 1000.0})
    assert resp.status_code == 404

    resp = client.post("/api/roasts/does-not-exist/weight-green", params={"grams": 1000.0})
    assert resp.status_code == 404
