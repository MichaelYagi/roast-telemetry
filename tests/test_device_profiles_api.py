"""backend/app/api/device_profiles.py -- the DeviceProfile CRUD router.
Presets have no equivalent API-level test file (only storage-layer CRUD
in test_storage.py, see test_preset_crud there) since they have no
analogous protected-row concept; device profiles do (built_in=True
seeded rows reject PUT/DELETE), which is worth exercising through the
actual HTTP layer, not just storage.
"""
from __future__ import annotations


def _minimal_payload(name="My Roaster"):
    return {
        "name": name,
        "temp_channels": [{"role": "bt", "slave_id": 1, "register_address": 0, "divisor": 10.0}],
        "control_channels": [],
    }


def test_built_in_fz94_profile_is_seeded_and_listed(client):
    resp = client.get("/api/device-profiles")
    assert resp.status_code == 200
    ids = {p["id"] for p in resp.json()}
    assert "coffeetech-fz94" in ids
    fz94 = next(p for p in resp.json() if p["id"] == "coffeetech-fz94")
    assert fz94["built_in"] is True
    assert len(fz94["temp_channels"]) == 3  # bt/et/dt
    assert len(fz94["control_channels"]) == 3  # heater/fan/drum


def test_create_get_update_delete_a_custom_profile(client):
    created = client.post("/api/device-profiles", json=_minimal_payload()).json()
    assert created["built_in"] is False
    assert created["name"] == "My Roaster"

    fetched = client.get(f"/api/device-profiles/{created['id']}").json()
    assert fetched["id"] == created["id"]

    updated = client.put(f"/api/device-profiles/{created['id']}", json=_minimal_payload(name="Renamed")).json()
    assert updated["name"] == "Renamed"

    del_resp = client.delete(f"/api/device-profiles/{created['id']}")
    assert del_resp.status_code == 204
    assert client.get(f"/api/device-profiles/{created['id']}").status_code == 404


def test_get_update_delete_unknown_profile_returns_404(client):
    assert client.get("/api/device-profiles/does-not-exist").status_code == 404
    assert client.put("/api/device-profiles/does-not-exist", json=_minimal_payload()).status_code == 404
    assert client.delete("/api/device-profiles/does-not-exist").status_code == 404


def test_built_in_profile_rejects_update_and_delete(client):
    put_resp = client.put("/api/device-profiles/coffeetech-fz94", json=_minimal_payload(name="Hacked"))
    assert put_resp.status_code == 403

    del_resp = client.delete("/api/device-profiles/coffeetech-fz94")
    assert del_resp.status_code == 403

    # Neither actually took effect.
    still_there = client.get("/api/device-profiles/coffeetech-fz94").json()
    assert still_there["name"] != "Hacked"
