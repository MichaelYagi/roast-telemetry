"""PUT /roasts/{id}/title -- renaming a roast after it's recorded, warm
(in-memory session) and cold (popped, alog+DB-row only), mirroring the
split test_roast_weight_editing.py and test_roast_tags.py already
established. Title is part of the .alog format (unlike tags), so this
follows the weight setters' pattern: a full _rewrite_alog/_rewrite_cold_alog
on every change, not just a DB write.
"""
from __future__ import annotations

from backend.app.roast_session.session import session_manager


def _create_roast(client, title="Original Title") -> str:
    resp = client.post("/api/v1/roasts", json={"title": title, "mode": "simulator"})
    return resp.json()["id"]


# -- warm (session still in memory) --------------------------------------


def test_set_title_via_api_warm(client):
    roast_id = _create_roast(client)

    resp = client.put(f"/api/v1/roasts/{roast_id}/title", json={"title": "Renamed Roast"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "Renamed Roast"

    resp = client.get(f"/api/v1/roasts/{roast_id}")
    assert resp.json()["title"] == "Renamed Roast"

    client.post(f"/api/v1/roasts/{roast_id}/stop")


def test_set_title_strips_surrounding_whitespace(client):
    roast_id = _create_roast(client)

    resp = client.put(f"/api/v1/roasts/{roast_id}/title", json={"title": "  Padded Title  "})
    assert resp.status_code == 200
    assert resp.json()["title"] == "Padded Title"

    client.post(f"/api/v1/roasts/{roast_id}/stop")


# -- cold (popped, alog+DB-row only) --------------------------------------


def test_set_title_via_api_cold(client):
    roast_id = _create_roast(client)
    client.post(f"/api/v1/roasts/{roast_id}/stop")
    session_manager.sessions.pop(roast_id, None)

    resp = client.put(f"/api/v1/roasts/{roast_id}/title", json={"title": "Renamed After Restart"})
    assert resp.status_code == 200

    resp = client.get(f"/api/v1/roasts/{roast_id}")
    assert resp.json()["title"] == "Renamed After Restart"


# -- validation and errors -------------------------------------------------


def test_set_title_404_for_unknown_roast(client):
    resp = client.put("/api/v1/roasts/does-not-exist/title", json={"title": "Anything"})
    assert resp.status_code == 404


def test_set_title_rejects_a_blank_title(client):
    roast_id = _create_roast(client)

    resp = client.put(f"/api/v1/roasts/{roast_id}/title", json={"title": "   "})
    assert resp.status_code == 422

    resp = client.get(f"/api/v1/roasts/{roast_id}")
    assert resp.json()["title"] == "Original Title"  # unchanged

    client.post(f"/api/v1/roasts/{roast_id}/stop")
