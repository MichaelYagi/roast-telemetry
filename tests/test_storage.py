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
    )

    result = isolated_db.get_settings()

    assert result == {
        "ollama_url": "http://localhost:11434",
        "ollama_model": "llama3.1",
        "broken_out_panels": ["bt", "et", "time"],
    }


def test_get_settings_defaults_on_empty_db(isolated_db):
    assert isolated_db.get_settings() == {
        "ollama_url": None,
        "ollama_model": None,
        "broken_out_panels": [],
    }


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
        "id": "r1", "title": "Roast 1", "mode": "simulator", "machine_id": None,
        "machine_label": None, "status": "roasting", "created_at": "2026-01-01T00:00:00",
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
            "id": f"r{i}", "title": f"Roast {i}", "mode": mode, "machine_id": None,
            "machine_label": None, "status": status, "created_at": f"2026-01-0{i + 1}T00:00:00",
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
            "id": roast_id, "title": roast_id, "mode": "simulator", "machine_id": None,
            "machine_label": None, "status": status, "created_at": "2026-01-01T00:00:00",
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

    isolated_db.delete_preset_row("p1")
    assert isolated_db.get_preset_row("p1") is None
    assert isolated_db.list_preset_rows() == []
