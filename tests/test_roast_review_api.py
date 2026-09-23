"""GET /api/v1/roasts/{id}/review -- status="none" (200) for "no review
yet" vs. a real 404 for a roast that doesn't exist at all. Previously
both collapsed into the same 404, which fired on every single
unreviewed roast's detail page load -- a routine, common state, not an
error -- showing up as a failed network request in the browser console
during completely normal browsing. See ReviewStatus.NONE's own comment
in models.py.
"""
from __future__ import annotations


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
