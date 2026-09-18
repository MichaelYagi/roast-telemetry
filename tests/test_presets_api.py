"""backend/app/api/presets.py -- the RoastPreset CRUD router. Mirrors
test_device_profiles_api.py's own shape closely: built_in=True seeded
rows (see main.py's _DEFAULT_PRESETS) now reject PUT/DELETE the same
way DeviceProfile.built_in rows always have, plus the manufacturer
grouping field added alongside it."""
from __future__ import annotations


def _minimal_payload(name="My Config"):
    return {"name": name, "config": {"title": "", "mode": "simulator"}}


def test_built_in_presets_are_seeded_and_listed(client):
    resp = client.get("/api/presets")
    assert resp.status_code == 200
    ids = {p["id"] for p in resp.json()}
    assert "default-fz94-usb" in ids
    assert "default-fz94-evo" in ids

    evo = next(p for p in resp.json() if p["id"] == "default-fz94-evo")
    assert evo["built_in"] is True
    assert evo["manufacturer"] == "Coffee-Tech"
    assert evo["config"]["modbus_transport"] == "tcp"
    assert evo["config"]["modbus_device_profile_id"] == "coffeetech-fz94-evo"


def test_create_get_update_delete_a_custom_preset(client):
    created = client.post("/api/presets", json=_minimal_payload()).json()
    assert created["built_in"] is False
    assert created["name"] == "My Config"

    fetched = client.get(f"/api/presets/{created['id']}").json()
    assert fetched["id"] == created["id"]

    updated = client.put(f"/api/presets/{created['id']}", json=_minimal_payload(name="Renamed")).json()
    assert updated["name"] == "Renamed"

    del_resp = client.delete(f"/api/presets/{created['id']}")
    assert del_resp.status_code == 204
    assert client.get(f"/api/presets/{created['id']}").status_code == 404


def test_get_update_delete_unknown_preset_returns_404(client):
    assert client.get("/api/presets/does-not-exist").status_code == 404
    assert client.put("/api/presets/does-not-exist", json=_minimal_payload()).status_code == 404
    assert client.delete("/api/presets/does-not-exist").status_code == 404


def test_built_in_preset_rejects_update_and_delete(client):
    put_resp = client.put("/api/presets/default-fz94-evo", json=_minimal_payload(name="Hacked"))
    assert put_resp.status_code == 403

    del_resp = client.delete("/api/presets/default-fz94-evo")
    assert del_resp.status_code == 403

    still_there = client.get("/api/presets/default-fz94-evo").json()
    assert still_there["name"] != "Hacked"


def test_manufacturer_round_trips_on_a_custom_preset(client):
    payload = {**_minimal_payload(), "manufacturer": "Behmor"}
    created = client.post("/api/presets", json=payload).json()
    assert created["manufacturer"] == "Behmor"

    fetched = client.get(f"/api/presets/{created['id']}").json()
    assert fetched["manufacturer"] == "Behmor"
