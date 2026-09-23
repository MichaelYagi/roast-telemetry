"""PUT /roasts/{id}/tags, GET /roasts/tags, and search/filter via
GET /roasts?q=...&tag=... -- tags are freely editable at any time,
mirroring the warm/cold split test_roast_weight_editing.py already
established for weight edits. Unlike weight, tags never touch the
.alog file (not part of the format), so the cold path here only needs
the DB row to exist, not a saved .alog.
"""
from __future__ import annotations

from backend.app import storage
from backend.app.roast_session.session import session_manager


def _create_roast(client, title="Tagged Roast", beans=None, mode="simulator") -> str:
    payload = {"title": title, "mode": mode}
    if beans is not None:
        payload["beans"] = beans
    resp = client.post("/api/v1/roasts", json=payload)
    return resp.json()["id"]


# -- direct storage tests --------------------------------------------------


def test_set_roast_tags_round_trips(isolated_db):
    storage.set_roast_tags("r1", ["espresso", "light"])
    assert storage.get_tags_for_roasts(["r1"]) == {"r1": ["espresso", "light"]}


def test_set_roast_tags_replaces_the_whole_set(isolated_db):
    storage.set_roast_tags("r1", ["espresso", "light"])
    storage.set_roast_tags("r1", ["dark"])
    assert storage.get_tags_for_roasts(["r1"]) == {"r1": ["dark"]}


def test_set_roast_tags_dedupes(isolated_db):
    storage.set_roast_tags("r1", ["espresso", "espresso", "light"])
    assert storage.get_tags_for_roasts(["r1"]) == {"r1": ["espresso", "light"]}


def test_get_tags_for_roasts_batches_across_multiple_roasts(isolated_db):
    storage.set_roast_tags("r1", ["espresso"])
    storage.set_roast_tags("r2", ["filter"])
    result = storage.get_tags_for_roasts(["r1", "r2", "r3"])
    assert result == {"r1": ["espresso"], "r2": ["filter"]}


def test_list_distinct_tags_orders_by_count_then_alpha(isolated_db):
    storage.set_roast_tags("r1", ["espresso"])
    storage.set_roast_tags("r2", ["espresso", "light"])
    storage.set_roast_tags("r3", ["dark"])
    tags = storage.list_distinct_tags()
    assert tags == [
        {"tag": "espresso", "count": 2},
        {"tag": "dark", "count": 1},
        {"tag": "light", "count": 1},
    ]


# -- API-level tests: warm (in-memory session) and cold (popped) ----------


def test_set_tags_via_api_warm(client):
    roast_id = _create_roast(client)

    resp = client.put(f"/api/v1/roasts/{roast_id}/tags", json={"tags": ["espresso", "light"]})
    assert resp.status_code == 200
    assert resp.json()["tags"] == ["espresso", "light"]

    resp = client.get(f"/api/v1/roasts/{roast_id}")
    assert resp.json()["tags"] == ["espresso", "light"]

    client.post(f"/api/v1/roasts/{roast_id}/stop")


def test_set_tags_via_api_cold(client):
    roast_id = _create_roast(client)
    client.post(f"/api/v1/roasts/{roast_id}/stop")
    session_manager.sessions.pop(roast_id, None)

    resp = client.put(f"/api/v1/roasts/{roast_id}/tags", json={"tags": ["dark"]})
    assert resp.status_code == 200

    resp = client.get(f"/api/v1/roasts/{roast_id}")
    assert resp.json()["tags"] == ["dark"]


def test_set_tags_404_for_unknown_roast(client):
    resp = client.put("/api/v1/roasts/does-not-exist/tags", json={"tags": ["x"]})
    assert resp.status_code == 404


def test_create_roast_with_upfront_tags(client):
    resp = client.post("/api/v1/roasts", json={"title": "Upfront", "mode": "simulator", "tags": ["single-origin"]})
    assert resp.json()["tags"] == ["single-origin"]
    client.post(f"/api/v1/roasts/{resp.json()['id']}/stop")


def test_list_roasts_endpoint_exposes_distinct_tags(client):
    roast_id = _create_roast(client)
    client.put(f"/api/v1/roasts/{roast_id}/tags", json={"tags": ["espresso"]})
    client.post(f"/api/v1/roasts/{roast_id}/stop")

    resp = client.get("/api/v1/roasts/tags")
    assert resp.status_code == 200
    assert {"tag": "espresso", "count": 1} in resp.json()


# -- search/filter (GET /roasts?q=...&tag=...) -----------------------------


def test_list_roasts_filters_by_tag(client):
    a = _create_roast(client, title="Roast A")
    b = _create_roast(client, title="Roast B")
    client.put(f"/api/v1/roasts/{a}/tags", json={"tags": ["espresso"]})
    client.put(f"/api/v1/roasts/{b}/tags", json={"tags": ["filter"]})
    client.post(f"/api/v1/roasts/{a}/stop")
    client.post(f"/api/v1/roasts/{b}/stop")

    resp = client.get("/api/v1/roasts", params={"tag": "espresso"})
    ids = [r["id"] for r in resp.json()]
    assert a in ids
    assert b not in ids


def test_list_roasts_search_matches_title(client):
    a = _create_roast(client, title="Ethiopia Yirgacheffe")
    b = _create_roast(client, title="Colombia Supremo")
    client.post(f"/api/v1/roasts/{a}/stop")
    client.post(f"/api/v1/roasts/{b}/stop")

    resp = client.get("/api/v1/roasts", params={"q": "Yirgacheffe"})
    ids = [r["id"] for r in resp.json()]
    assert a in ids
    assert b not in ids


def test_list_roasts_search_matches_beans(client):
    a = _create_roast(client, title="Roast A", beans="Ethiopia Guji")
    b = _create_roast(client, title="Roast B", beans="Brazil Cerrado")
    client.post(f"/api/v1/roasts/{a}/stop")
    client.post(f"/api/v1/roasts/{b}/stop")

    resp = client.get("/api/v1/roasts", params={"q": "Guji"})
    ids = [r["id"] for r in resp.json()]
    assert a in ids
    assert b not in ids


def test_list_roasts_search_matches_tags(client):
    a = _create_roast(client, title="Roast A")
    b = _create_roast(client, title="Roast B")
    client.put(f"/api/v1/roasts/{a}/tags", json={"tags": ["competition-prep"]})
    client.post(f"/api/v1/roasts/{a}/stop")
    client.post(f"/api/v1/roasts/{b}/stop")

    resp = client.get("/api/v1/roasts", params={"q": "competition"})
    ids = [r["id"] for r in resp.json()]
    assert a in ids
    assert b not in ids


def test_list_roasts_search_does_not_duplicate_multi_tag_roasts(client):
    # Regression check for the LEFT JOIN -- a roast matching more than one
    # tag row must still appear exactly once (SELECT DISTINCT r.*).
    a = _create_roast(client, title="Multi Tag Roast")
    client.put(f"/api/v1/roasts/{a}/tags", json={"tags": ["espresso", "light", "competition"]})
    client.post(f"/api/v1/roasts/{a}/stop")

    resp = client.get("/api/v1/roasts", params={"q": "Multi Tag"})
    ids = [r["id"] for r in resp.json()]
    assert ids.count(a) == 1


# -- GET /roasts/count -- backs HistoryDashboard.jsx's pagination -------


def test_count_roasts_matches_list_length_unfiltered(client):
    _create_roast(client, title="Count A")
    _create_roast(client, title="Count B")

    total = client.get("/api/v1/roasts/count").json()["total"]
    listed = len(client.get("/api/v1/roasts").json())
    assert total == listed


def test_count_roasts_respects_filters(client):
    a = _create_roast(client, title="Filtered Count A")
    b = _create_roast(client, title="Filtered Count B")
    client.put(f"/api/v1/roasts/{a}/tags", json={"tags": ["decaf"]})
    client.post(f"/api/v1/roasts/{a}/stop")
    client.post(f"/api/v1/roasts/{b}/stop")

    resp = client.get("/api/v1/roasts/count", params={"tag": "decaf"})
    assert resp.json()["total"] == 1


def test_count_roasts_not_capped_by_list_limit(client):
    # The regression this guards: list_roasts caps `limit` at 500, but
    # count_roasts must report the TRUE total regardless -- pagination
    # needs the real count to compute how many pages exist, not one
    # clamped to a single page's own max size.
    for i in range(3):
        _create_roast(client, title=f"Uncapped {i}")

    total = client.get("/api/v1/roasts/count").json()["total"]
    assert total == 3
    capped_list = client.get("/api/v1/roasts", params={"limit": 1}).json()
    assert len(capped_list) == 1
    assert total > len(capped_list)


# -- "Roasted by" filter (GET /roasts?created_by=...) and GET /roasts/roasters --


def test_list_roasts_filters_by_created_by(client):
    a = _create_roast(client, title="Roaster A's Roast")
    b = _create_roast(client, title="Roaster B's Roast")
    client.post(f"/api/v1/roasts/{a}/stop")
    client.post(f"/api/v1/roasts/{b}/stop")
    # Simulates a roast created by a different account -- simplest way to
    # get a second distinct created_by_username without a full second
    # register/login flow, same direct-storage-manipulation precedent
    # test_roasts_lifecycle_api.py's own attribution tests already use.
    storage.update_roast(b, created_by_username="someone-else")

    resp = client.get("/api/v1/roasts", params={"created_by": "someone-else"})
    ids = [r["id"] for r in resp.json()]
    assert b in ids
    assert a not in ids


def test_count_roasts_filters_by_created_by(client):
    a = _create_roast(client, title="Count Roaster A")
    b = _create_roast(client, title="Count Roaster B")
    client.post(f"/api/v1/roasts/{a}/stop")
    client.post(f"/api/v1/roasts/{b}/stop")
    storage.update_roast(b, created_by_username="someone-else")

    resp = client.get("/api/v1/roasts/count", params={"created_by": "someone-else"})
    assert resp.json()["total"] == 1


def test_list_roasters_endpoint(client):
    a = _create_roast(client, title="Roaster List A")
    b = _create_roast(client, title="Roaster List B")
    client.post(f"/api/v1/roasts/{a}/stop")
    client.post(f"/api/v1/roasts/{b}/stop")
    storage.update_roast(b, created_by_username="someone-else")

    resp = client.get("/api/v1/roasts/roasters")
    assert resp.status_code == 200
    roasters = {r["created_by_username"]: r["count"] for r in resp.json()}
    assert roasters == {"test-admin": 1, "someone-else": 1}
