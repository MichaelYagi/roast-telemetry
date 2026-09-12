"""PUT/GET /api/settings -- exercised through the real FastAPI app (via the
`client` fixture's isolated DB) rather than calling storage directly, since
the interesting behavior here (silently dropping unknown panel keys) lives
in the route handler, not in storage.py."""
from __future__ import annotations


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_get_settings_defaults(client):
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    assert resp.json() == {"ollama_url": None, "ollama_model": None, "broken_out_panels": []}


def test_put_settings_drops_unknown_panel_keys(client):
    resp = client.put("/api/settings", json={
        "ollama_url": "http://localhost:11434",
        "ollama_model": "llama3.1",
        "broken_out_panels": ["bt", "not_a_real_panel_key", "fan", "also_bogus"],
    })

    assert resp.status_code == 200
    assert resp.json()["broken_out_panels"] == ["bt", "fan"]

    # And it's what actually got persisted, not just what the PUT echoed back.
    get_resp = client.get("/api/settings")
    assert get_resp.json()["broken_out_panels"] == ["bt", "fan"]


def test_put_settings_preserves_panel_order(client):
    resp = client.put("/api/settings", json={
        "ollama_url": None,
        "ollama_model": None,
        "broken_out_panels": ["drum", "bt", "et"],
    })
    assert resp.json()["broken_out_panels"] == ["drum", "bt", "et"]
