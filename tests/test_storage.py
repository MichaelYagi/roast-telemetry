"""backend/app/storage.py -- SQLite persistence for roast metadata,
presets, settings, and reviews. Each test gets its own throwaway DB file
via the `isolated_db` fixture (see tests/conftest.py)."""
from __future__ import annotations

import sqlite3


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
    }


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
