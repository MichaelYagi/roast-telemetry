"""A roast's duration is Charge to Drop (what the phase percentages already
use), not the length of the whole recording that includes cooling."""
from __future__ import annotations

from alog_playback.alog_io import roast_to_native_alog_dict, save_native_alog
from backend.app import storage
from backend.app.roast_session.session import roast_duration_s, session_manager


def _ev(kind, t):
    return {"id": kind, "type": kind, "time_s": t, "label": kind, "value": None}


def test_duration_is_charge_to_drop():
    profile = [{"time_s": float(t)} for t in range(-6, 60)]
    assert roast_duration_s(profile, [_ev("CHARGE", -6.0), _ev("DROP", 6.0), _ev("COOL_END", 30.0)]) == 12.0


def test_duration_falls_back_to_the_recording_length():
    profile = [{"time_s": 0.0}, {"time_s": 40.0}]
    assert roast_duration_s(profile, []) == 40.0
    assert roast_duration_s(profile, [_ev("CHARGE", 0.0)]) == 40.0  # no drop yet
    assert roast_duration_s([], []) == 0.0


def _write_alog(path, drop_at):
    profile = [{"time_s": float(t), "bt": 100.0 + t, "et": 120.0 + t} for t in range(0, 61)]
    events = [_ev("CHARGE", 0.0), _ev("DROP", drop_at)]
    save_native_alog(
        str(path),
        roast_to_native_alog_dict(
            title="t", profile=profile, events=events, notes=[], beans=None,
            weight_green_g=None, weight_roasted_g=None, roastdate="2026-01-01T00:00:00+00:00",
        ),
    )


def test_import_uses_charge_to_drop(client, tmp_path):
    path = tmp_path / "r.alog"
    _write_alog(path, drop_at=40.0)
    summary = client.post("/api/roasts/import", params={"path": str(path)}).json()
    assert summary["duration_s"] == 40.0  # not the 60 s recording


def test_stats_panel_duration_matches_phases(client, tmp_path):
    path = tmp_path / "r.alog"
    _write_alog(path, drop_at=40.0)
    roast_id = client.post("/api/roasts/import", params={"path": str(path)}).json()["id"]
    assert client.get(f"/api/roasts/{roast_id}/stats").json()["duration_s"] == 40.0


def test_backfill_fixes_old_rows_once(client, tmp_path):
    path = tmp_path / "r.alog"
    _write_alog(path, drop_at=40.0)
    roast_id = client.post("/api/roasts/import", params={"path": str(path)}).json()["id"]
    storage.update_roast(roast_id, duration_s=60.0)  # how an old roast was stored
    storage.set_schema_version(0)

    session_manager.backfill_durations()
    assert storage.get_roast_row(roast_id)["duration_s"] == 40.0
    assert storage.get_schema_version() == 1

    storage.update_roast(roast_id, duration_s=99.0)
    session_manager.backfill_durations()  # already done -- leaves it alone
    assert storage.get_roast_row(roast_id)["duration_s"] == 99.0


def test_moving_drop_updates_the_saved_duration(client, tmp_path):
    path = tmp_path / "r.alog"
    _write_alog(path, drop_at=40.0)
    roast_id = client.post("/api/roasts/import", params={"path": str(path)}).json()["id"]
    events = client.get(f"/api/roasts/{roast_id}").json()["events"]
    drop = next(e for e in events if e["type"] == "DROP")
    assert client.patch(f"/api/roasts/{roast_id}/events/{drop['id']}", json={"time_s": 50.0}).status_code == 200
    assert storage.get_roast_row(roast_id)["duration_s"] == 50.0
