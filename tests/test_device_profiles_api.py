"""backend/app/api/device_profiles.py -- the DeviceProfile CRUD router.
See tests/test_presets_api.py for the equivalent test file covering
RoastPreset's own built_in=True protected-row concept, added later
alongside this one (both reject PUT/DELETE on a seeded row)."""
from __future__ import annotations


def _minimal_payload(name="My Roaster"):
    return {
        "name": name,
        "temp_channels": [{"role": "bt", "slave_id": 1, "register_address": 0, "divisor": 10.0}],
        "control_channels": [],
    }


def test_built_in_fz94_profile_is_seeded_and_listed(client):
    resp = client.get("/api/v1/device-profiles")
    assert resp.status_code == 200
    ids = {p["id"] for p in resp.json()}
    assert "coffeetech-fz94" in ids
    fz94 = next(p for p in resp.json() if p["id"] == "coffeetech-fz94")
    assert fz94["built_in"] is True
    assert len(fz94["temp_channels"]) == 3  # bt/et/dt
    assert len(fz94["control_channels"]) == 3  # heater/fan/drum


def test_create_get_update_delete_a_custom_profile(client):
    created = client.post("/api/v1/device-profiles", json=_minimal_payload()).json()
    assert created["built_in"] is False
    assert created["name"] == "My Roaster"

    fetched = client.get(f"/api/v1/device-profiles/{created['id']}").json()
    assert fetched["id"] == created["id"]

    updated = client.put(f"/api/v1/device-profiles/{created['id']}", json=_minimal_payload(name="Renamed")).json()
    assert updated["name"] == "Renamed"

    del_resp = client.delete(f"/api/v1/device-profiles/{created['id']}")
    assert del_resp.status_code == 204
    assert client.get(f"/api/v1/device-profiles/{created['id']}").status_code == 404


def test_get_update_delete_unknown_profile_returns_404(client):
    assert client.get("/api/v1/device-profiles/does-not-exist").status_code == 404
    assert client.put("/api/v1/device-profiles/does-not-exist", json=_minimal_payload()).status_code == 404
    assert client.delete("/api/v1/device-profiles/does-not-exist").status_code == 404


def test_built_in_profile_rejects_update_and_delete(client):
    put_resp = client.put("/api/v1/device-profiles/coffeetech-fz94", json=_minimal_payload(name="Hacked"))
    assert put_resp.status_code == 403

    del_resp = client.delete("/api/v1/device-profiles/coffeetech-fz94")
    assert del_resp.status_code == 403

    # Neither actually took effect.
    still_there = client.get("/api/v1/device-profiles/coffeetech-fz94").json()
    assert still_there["name"] != "Hacked"
