"""GET /api/v1/roasts/{id}/review -- status="none" (200) for "no review
yet" vs. a real 404 for a roast that doesn't exist at all. Previously
both collapsed into the same 404, which fired on every single
unreviewed roast's detail page load -- a routine, common state, not an
error -- showing up as a failed network request in the browser console
during completely normal browsing. See ReviewStatus.NONE's own comment
in models.py.
"""
from __future__ import annotations

import time

from alog_playback.alog_io import roast_to_native_alog_dict, save_native_alog


def _ev(kind, t):
    return {"id": kind, "type": kind, "time_s": float(t), "label": kind, "value": None}


def _make_alog(path, *, title="Roast", fc_at=480, drop_at=600):
    profile = [{"time_s": float(t), "bt": 100.0 + 0.3 * t, "et": 150.0 + 0.3 * t} for t in range(0, drop_at + 1, 20)]
    events = [_ev("CHARGE", 0), _ev("TURNING_POINT", 60), _ev("DRY_END", 240), _ev("FC_START", fc_at), _ev("DROP", drop_at)]
    save_native_alog(
        str(path),
        roast_to_native_alog_dict(
            title=title, profile=profile, events=events, notes=[], beans=None,
            weight_green_g=None, weight_roasted_g=None, roastdate="2026-03-01T10:00:00+00:00",
        ),
    )


def _wait_for_review(client, roast_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        review = client.get(f"/api/v1/roasts/{roast_id}/review").json()
        if review["status"] != "pending":
            return review
        time.sleep(0.05)
    raise AssertionError("the review never finished")


def test_review_compares_to_the_beans_other_roasts_once_there_are_enough(client, tmp_path, monkeypatch):
    from backend.app import ollama_client

    seen = {}

    async def fake_generate(url, model, prompt, options=None):
        seen["prompt"] = prompt
        return "ok"

    monkeypatch.setattr(ollama_client, "generate", fake_generate)
    client.put("/api/v1/settings", json={"ollama_url": "http://ollama.test", "ollama_model": "test-model"})

    # Five similar Ethiopia roasts (development time ~120s) plus one with
    # First Crack much later -- the one under review -- so its development
    # time is far shorter than the other five.
    for i in range(5):
        path = tmp_path / f"normal{i}.alog"
        _make_alog(path, title=f"normal{i}", fc_at=480, drop_at=600)
        rid = client.post("/api/v1/roasts/import", params={"path": str(path), "title": f"normal{i}"}).json()["id"]
        client.put(f"/api/v1/roasts/{rid}/beans", json={"name": "Ethiopia"})

    path = tmp_path / "underdeveloped.alog"
    _make_alog(path, title="underdeveloped", fc_at=590, drop_at=600)
    roast_id = client.post("/api/v1/roasts/import", params={"path": str(path), "title": "underdeveloped"}).json()["id"]
    client.put(f"/api/v1/roasts/{roast_id}/beans", json={"name": "Ethiopia"})

    client.post(f"/api/v1/roasts/{roast_id}/review")
    review = _wait_for_review(client, roast_id)

    assert review["status"] == "ready"
    prompt = seen["prompt"]
    assert "compared_to_this_beans_other_roasts" in prompt
    assert "Development time" in prompt
    assert '"roasts_compared": 5' in prompt


def test_review_has_no_comparison_without_enough_bean_history(client, tmp_path, monkeypatch):
    from backend.app import ollama_client

    seen = {}

    async def fake_generate(url, model, prompt, options=None):
        seen["prompt"] = prompt
        return "ok"

    monkeypatch.setattr(ollama_client, "generate", fake_generate)
    client.put("/api/v1/settings", json={"ollama_url": "http://ollama.test", "ollama_model": "test-model"})

    path = tmp_path / "solo.alog"
    _make_alog(path, title="solo")
    roast_id = client.post("/api/v1/roasts/import", params={"path": str(path), "title": "solo"}).json()["id"]
    client.put(f"/api/v1/roasts/{roast_id}/beans", json={"name": "Ethiopia"})

    client.post(f"/api/v1/roasts/{roast_id}/review")
    review = _wait_for_review(client, roast_id)

    assert review["status"] == "ready"
    # The prompt's own instructions mention the field name in prose regardless
    # -- check the actual JSON data section, not the whole prompt text.
    json_section = seen["prompt"].split("COFFEE ROAST DATA (JSON")[1]
    assert "compared_to_this_beans_other_roasts" not in json_section


def test_get_review_with_no_review_yet_returns_200_status_none(client):
    create = client.post("/api/v1/roasts", json={"title": "Unreviewed Roast", "mode": "simulator"})
    roast_id = create.json()["id"]

    resp = client.get(f"/api/v1/roasts/{roast_id}/review")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "none"
    assert body["roast_id"] == roast_id
    assert body["review_text"] is None
    assert body["created_at"] is None


def test_get_review_for_nonexistent_roast_is_404(client):
    resp = client.get("/api/v1/roasts/does-not-exist/review")
    assert resp.status_code == 404
