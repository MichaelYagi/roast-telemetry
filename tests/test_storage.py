"""backend/app/storage.py -- SQLite persistence for roast metadata,
presets, settings, and reviews. Each test gets its own throwaway DB file
via the `isolated_db` fixture (see tests/conftest.py)."""
from __future__ import annotations

import sqlite3
import threading
import uuid


def test_settings_roundtrip_including_panel_list(isolated_db):
    isolated_db.set_settings(
        ollama_url="http://localhost:11434",
        ollama_model="llama3.1",
        broken_out_panels=["bt", "et", "time"],
        breakout_panel_colors={"bt": "#112233"},
    )

    result = isolated_db.get_settings()

    assert result == {
        "ollama_url": "http://localhost:11434",
        "ollama_model": "llama3.1",
        "broken_out_panels": ["bt", "et", "time"],
        "breakout_panel_colors": {"bt": "#112233"},
        # Never saved in this test -- falls back to the seeded default.
        "small_readout_panels": ["et", "bt", "dt", "ror_bt"],
        "temperature_unit": "c",
        "vertical_control_layout": [["drum_speed_pct"], ["fan_pct"], ["heater_pct"]],
        "vertical_control_arrows": {},
    }


def test_small_readout_panels_roundtrips_independently_of_broken_out_panels(isolated_db):
    isolated_db.set_settings(small_readout_panels=["heater", "fan"])

    result = isolated_db.get_settings()

    assert result["small_readout_panels"] == ["heater", "fan"]
    assert result["broken_out_panels"] == []  # untouched by the above


def test_breakout_panel_colors_is_shared_by_both_panels(isolated_db):
    # There's deliberately no separate small_readout_colors -- colors are
    # a property of the item (e.g. "bt" is the same blue everywhere it's
    # shown), not something that should drift between two panels
    # displaying the same value.
    isolated_db.set_settings(
        broken_out_panels=["bt"],
        small_readout_panels=["bt"],
        breakout_panel_colors={"bt": "#112233"},
    )

    result = isolated_db.get_settings()

    assert result["breakout_panel_colors"] == {"bt": "#112233"}
    assert "small_readout_colors" not in result


def test_small_readout_panels_explicit_empty_list_stays_empty(isolated_db):
    # Distinguishes "never saved" (seeded default) from "user deliberately
    # cleared it" (stays empty) -- both look falsy-ish at a glance, but
    # json.dumps([]) is the *string* "[]", which is truthy, unlike a
    # genuinely-missing key, so get_settings() can and does tell them apart.
    isolated_db.set_settings(small_readout_panels=[])

    assert isolated_db.get_settings()["small_readout_panels"] == []


def test_get_settings_defaults_on_empty_db(isolated_db):
    assert isolated_db.get_settings() == {
        "ollama_url": None,
        "ollama_model": None,
        "broken_out_panels": [],
        "breakout_panel_colors": {},
        "small_readout_panels": ["et", "bt", "dt", "ror_bt"],
        "temperature_unit": "c",
        "vertical_control_layout": [["drum_speed_pct"], ["fan_pct"], ["heater_pct"]],
        "vertical_control_arrows": {},
    }


def test_vertical_control_arrows_migrates_legacy_bool_shape(isolated_db):
    # vertical_control_arrows used to be a plain bool (arrows shown or
    # not, always a hardcoded step of 1) -- a row saved by an older
    # version of this app would still have that literal True/False shape
    # sitting in the DB. Writing it directly here (bypassing
    # set_settings's own normal float-typed callers, and the API layer's
    # _filter_arrows) simulates exactly that pre-existing row, not
    # something this app would write today.
    isolated_db.set_settings(vertical_control_arrows={"heater_pct": True, "fan_pct": False})

    result = isolated_db.get_settings()

    # True -> step 1 (arrows were on, so this keeps them on at the step
    # they always actually used); False is dropped entirely, same as a
    # never-saved key.
    assert result["vertical_control_arrows"] == {"heater_pct": 1.0}


def test_get_settings_tolerates_corrupt_panel_colors_json(isolated_db):
    with sqlite3.connect(isolated_db.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('breakout_panel_colors', 'not valid json')"
        )
        conn.commit()

    result = isolated_db.get_settings()
    assert result["breakout_panel_colors"] == {}


def test_set_settings_only_updates_given_keys(isolated_db):
    isolated_db.set_settings(ollama_url="http://a")
    isolated_db.set_settings(ollama_model="llama3.1")

    result = isolated_db.get_settings()
    assert result["ollama_url"] == "http://a"
    assert result["ollama_model"] == "llama3.1"


def test_get_settings_tolerates_corrupt_panel_json(isolated_db):
    # Simulate a DB written by some future/older version that stored
    # something get_settings can't parse -- it must fall back to an empty
    # list rather than raising and taking the whole endpoint down.
    with sqlite3.connect(isolated_db.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('broken_out_panels', 'not valid json')"
        )
        conn.commit()

    result = isolated_db.get_settings()
    assert result["broken_out_panels"] == []


def test_insert_get_update_delete_roast_row(isolated_db):
    isolated_db.insert_roast({
        "id": "r1", "title": "Roast 1", "mode": "simulator", "status": "roasting", "created_at": "2026-01-01T00:00:00",
        "beans": "Guatemala", "weight_green_g": 200.0, "weight_roasted_g": None,
        "duration_s": None, "alog_path": None,
    })

    row = isolated_db.get_roast_row("r1")
    assert row["title"] == "Roast 1"
    assert row["status"] == "roasting"

    isolated_db.update_roast("r1", status="complete", duration_s=420.0)
    row = isolated_db.get_roast_row("r1")
    assert row["status"] == "complete"
    assert row["duration_s"] == 420.0

    isolated_db.delete_roast_row("r1")
    assert isolated_db.get_roast_row("r1") is None


def test_list_roast_rows_filters_by_status_and_mode(isolated_db):
    for i, (status, mode) in enumerate([("roasting", "simulator"), ("complete", "simulator"), ("complete", "alog_playback")]):
        isolated_db.insert_roast({
            "id": f"r{i}", "title": f"Roast {i}", "mode": mode, "status": status, "created_at": f"2026-01-0{i + 1}T00:00:00",
            "beans": None, "weight_green_g": None, "weight_roasted_g": None,
            "duration_s": None, "alog_path": None,
        })

    complete_only = isolated_db.list_roast_rows(status="complete")
    assert {r["id"] for r in complete_only} == {"r1", "r2"}

    complete_sim_only = isolated_db.list_roast_rows(status="complete", mode="simulator")
    assert {r["id"] for r in complete_sim_only} == {"r1"}


def test_abort_stale_roasts_only_touches_active_statuses(isolated_db):
    for roast_id, status in [("active1", "roasting"), ("active2", "cooling"), ("done", "complete")]:
        isolated_db.insert_roast({
            "id": roast_id, "title": roast_id, "mode": "simulator", "status": status, "created_at": "2026-01-01T00:00:00",
            "beans": None, "weight_green_g": None, "weight_roasted_g": None,
            "duration_s": None, "alog_path": None,
        })

    cleaned = isolated_db.abort_stale_roasts()

    assert set(cleaned) == {"active1", "active2"}
    assert isolated_db.get_roast_row("active1")["status"] == "aborted"
    assert isolated_db.get_roast_row("active2")["status"] == "aborted"
    assert isolated_db.get_roast_row("done")["status"] == "complete"  # untouched


def test_preset_crud(isolated_db):
    isolated_db.insert_preset({
        "id": "p1", "name": "My Config", "created_at": "2026-01-01T00:00:00",
        "config_json": '{"title": "x", "mode": "simulator"}',
        "heater_pct": 70.0, "fan_pct": 50.0, "drum_speed_pct": 60.0,
    })

    row = isolated_db.get_preset_row("p1")
    assert row["name"] == "My Config"
    assert row["heater_pct"] == 70.0

    isolated_db.update_preset_row("p1", {
        "name": "Renamed", "config_json": row["config_json"],
        "heater_pct": 80.0, "fan_pct": 50.0, "drum_speed_pct": 60.0,
    })
    row = isolated_db.get_preset_row("p1")
    assert row["name"] == "Renamed"
    assert row["heater_pct"] == 80.0

    assert len(isolated_db.list_preset_rows()) == 1


def _seed(isolated_db, preset_id="default-fz94-usb"):
    isolated_db.seed_default_presets([
        {"id": preset_id, "name": "FZ-94, USB", "created_at": "2026-01-01T00:00:00", "config_json": "{}"},
    ])


def test_seed_default_presets_inserts_once(isolated_db):
    _seed(isolated_db)

    rows = isolated_db.list_preset_rows()
    assert len(rows) == 1
    assert rows[0]["id"] == "default-fz94-usb"


def test_seed_default_presets_does_not_duplicate_on_repeat_calls(isolated_db):
    _seed(isolated_db)
    _seed(isolated_db)

    assert len(isolated_db.list_preset_rows()) == 1


def test_seed_default_presets_does_not_resurrect_a_deleted_preset(isolated_db):
    # A seeded preset is a starting point, not something the app should
    # keep forcing back -- deleting it must be permanent across restarts
    # (a second "startup", simulated here by calling seed again).
    _seed(isolated_db)
    isolated_db.delete_preset_row("default-fz94-usb")

    _seed(isolated_db)

    assert isolated_db.list_preset_rows() == []


def test_seed_default_presets_still_seeds_a_newly_added_one(isolated_db):
    # Simulates an existing install upgrading to a version that ships an
    # additional default preset -- its own marker is separate, so it
    # should still get seeded even though the first one's marker already
    # exists from an earlier "startup".
    _seed(isolated_db, preset_id="default-fz94-usb")

    isolated_db.seed_default_presets([
        {"id": "default-fz94-usb", "name": "FZ-94, USB", "created_at": "2026-01-01T00:00:00", "config_json": "{}"},
        {"id": "default-ms6514-usb", "name": "Mastech MS6514, USB", "created_at": "2026-01-01T00:00:00", "config_json": "{}"},
    ])

    ids = {r["id"] for r in isolated_db.list_preset_rows()}
    assert ids == {"default-fz94-usb", "default-ms6514-usb"}


def test_seed_default_presets_tolerates_a_row_that_exists_without_its_marker(isolated_db):
    # An install that ran an earlier version of this function (one global
    # marker for the whole batch, not per-id) can already have this row
    # even though its new per-id marker doesn't exist yet -- must not
    # crash on the primary-key collision, and must still set the marker
    # so it doesn't keep re-checking every startup.
    isolated_db.insert_preset({
        "id": "default-fz94-usb", "name": "FZ-94, USB", "created_at": "2026-01-01T00:00:00", "config_json": "{}",
    })

    _seed(isolated_db, preset_id="default-fz94-usb")  # does not raise

    assert len(isolated_db.list_preset_rows()) == 1


def test_device_profile_crud(isolated_db):
    isolated_db.insert_device_profile({
        "id": "dp1", "name": "My Roaster", "created_at": "2026-01-01T00:00:00",
        "config_json": '{"name": "My Roaster", "temp_channels": []}',
    })

    row = isolated_db.get_device_profile_row("dp1")
    assert row["name"] == "My Roaster"
    assert row["built_in"] == 0  # default, unlike seeded built-ins

    isolated_db.update_device_profile_row("dp1", {"name": "Renamed", "config_json": row["config_json"]})
    row = isolated_db.get_device_profile_row("dp1")
    assert row["name"] == "Renamed"

    assert len(isolated_db.list_device_profile_rows()) == 1
    isolated_db.delete_device_profile_row("dp1")
    assert isolated_db.list_device_profile_rows() == []


def _seed_device_profile(isolated_db, profile_id="coffeetech-fz94"):
    isolated_db.seed_default_device_profiles([
        {"id": profile_id, "name": "Coffee-Tech FZ-94 (built-in)", "created_at": "2026-01-01T00:00:00", "config_json": "{}"},
    ])


def test_seed_default_device_profiles_inserts_once_as_built_in(isolated_db):
    _seed_device_profile(isolated_db)

    rows = isolated_db.list_device_profile_rows()
    assert len(rows) == 1
    assert rows[0]["id"] == "coffeetech-fz94"
    assert rows[0]["built_in"] == 1


def test_seed_default_device_profiles_does_not_duplicate_on_repeat_calls(isolated_db):
    _seed_device_profile(isolated_db)
    _seed_device_profile(isolated_db)

    assert len(isolated_db.list_device_profile_rows()) == 1


def test_seed_default_device_profiles_does_not_resurrect_a_deleted_one(isolated_db):
    _seed_device_profile(isolated_db)
    isolated_db.delete_device_profile_row("coffeetech-fz94")

    _seed_device_profile(isolated_db)

    assert isolated_db.list_device_profile_rows() == []


def _register_user(isolated_db, username, results, index):
    user = {
        "id": str(uuid.uuid4()), "username": username,
        "password_hash": "x", "created_at": "2026-01-01T00:00:00",
    }
    try:
        results[index] = ("ok", isolated_db.insert_user_and_check_first(user))
    except isolated_db.DuplicateUsernameError:
        results[index] = ("duplicate", None)


def test_concurrent_registrations_only_one_becomes_admin(isolated_db):
    # Real OS threads, not sequential calls -- exercises the actual
    # BEGIN IMMEDIATE serialization, not just the Python-level logic.
    # Without it, two concurrent first-ever registrations could both
    # read count()==0 before either committed, making both admin.
    results = [None, None]
    threads = [
        threading.Thread(target=_register_user, args=(isolated_db, "alice", results, 0)),
        threading.Thread(target=_register_user, args=(isolated_db, "bob", results, 1)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results[0][0] == "ok"
    assert results[1][0] == "ok"
    is_first_flags = [results[0][1], results[1][1]]
    assert sorted(is_first_flags) == [False, True]  # exactly one admin, not zero or two

    admins = [r for r in isolated_db.list_users() if r["role"] == "admin"]
    assert len(admins) == 1


def test_concurrent_same_username_registration_one_wins_one_is_duplicate(isolated_db):
    results = [None, None]
    threads = [
        threading.Thread(target=_register_user, args=(isolated_db, "sameuser", results, 0)),
        threading.Thread(target=_register_user, args=(isolated_db, "sameuser", results, 1)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    outcomes = sorted(r[0] for r in results)
    assert outcomes == ["duplicate", "ok"]  # never both "ok" (two rows), never both erroring unhandled


def test_api_key_hash_roundtrip_and_null_by_default(isolated_db):
    user = {
        "id": str(uuid.uuid4()), "username": "alice",
        "password_hash": "x", "created_at": "2026-01-01T00:00:00",
    }
    isolated_db.insert_user_and_check_first(user)

    assert isolated_db.get_user_by_api_key_hash("some-hash") is None  # nothing set yet

    isolated_db.set_user_api_key_hash(user["id"], "some-hash")
    found = isolated_db.get_user_by_api_key_hash("some-hash")
    assert found["id"] == user["id"]

    isolated_db.set_user_api_key_hash(user["id"], None)  # revoke
    assert isolated_db.get_user_by_api_key_hash("some-hash") is None


def test_multiple_users_can_each_have_no_api_key_at_once(isolated_db):
    # NULL api_key_hash must not collide against the unique index -- SQLite
    # treats each NULL as distinct for UNIQUE purposes, but worth nailing
    # down explicitly since this column's uniqueness is a separate index,
    # not an inline column constraint (see storage.py's own comment on why).
    for name in ("alice", "bob", "carol"):
        isolated_db.insert_user_and_check_first({
            "id": str(uuid.uuid4()), "username": name,
            "password_hash": "x", "created_at": "2026-01-01T00:00:00",
        })
    assert len(isolated_db.list_users()) == 3
