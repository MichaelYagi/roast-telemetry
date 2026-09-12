"""alog_playback/player.py -- AlogPlayer, the alog_playback-mode engine.
Exercises the duck-typed engine contract (tick/get_new_events/apply_command/
is_finished) directly, without going through a RoastSession or the API."""
from __future__ import annotations

import pytest

from alog_playback.alog_io import roast_to_alog_dict, save_alog
from alog_playback.player import AlogPlayer


def _write_test_alog(tmp_path, *, n_points=6, step_s=1.0):
    profile = [
        {"time_s": i * step_s, "bt": 20.0 + i * 10.0, "et": 25.0 + i * 10.0,
         "ror_bt": None, "ror_et": None, "heater_pct": 70.0, "fan_pct": 50.0, "drum_speed_pct": 60.0}
        for i in range(n_points)
    ]
    events = [
        {"id": "e1", "time_s": 0.0, "type": "CHARGE", "label": "Charge", "value": 20.0},
        {"id": "e2", "time_s": (n_points - 1) * step_s, "type": "DROP", "label": "Drop", "value": None},
    ]
    alog_dict = roast_to_alog_dict(title="Test", profile=profile, events=events, notes=[])
    path = str(tmp_path / "playback.alog")
    save_alog(path, alog_dict)
    return path, profile


def test_tick_advances_clock_by_dt_times_speed(tmp_path):
    path, profile = _write_test_alog(tmp_path)
    player = AlogPlayer(path, speed=2.0)

    player.tick(1.0)

    assert player.playback_clock == pytest.approx(2.0)


def test_tick_interpolates_between_samples(tmp_path):
    path, profile = _write_test_alog(tmp_path, n_points=3, step_s=10.0)  # bt: 20, 30, 40 at t=0,10,20
    player = AlogPlayer(path, speed=1.0)

    sample = player.tick(5.0)  # t=5, halfway between the t=0 (bt=20) and t=10 (bt=30) samples

    assert sample["bt"] == pytest.approx(25.0)


def test_tick_clamps_at_end_and_marks_finished(tmp_path):
    path, profile = _write_test_alog(tmp_path, n_points=3, step_s=1.0)  # ends at t=2
    player = AlogPlayer(path, speed=1.0)

    assert not player.is_finished()
    player.tick(10.0)  # way past the end

    assert player.is_finished()
    assert player.playback_clock == pytest.approx(2.0)  # clamped to end, not overshot

    # Ticking again after finishing must not error or move past the end.
    sample = player.tick(1.0)
    assert sample["time_s"] == pytest.approx(2.0)


def test_apply_command_changes_speed(tmp_path):
    path, profile = _write_test_alog(tmp_path)
    player = AlogPlayer(path, speed=1.0)

    player.apply_command({"speed": 4.0})
    player.tick(1.0)

    assert player.playback_clock == pytest.approx(4.0)


def test_apply_command_rejects_negative_speed(tmp_path):
    path, profile = _write_test_alog(tmp_path)
    player = AlogPlayer(path, speed=1.0)

    player.apply_command({"speed": -5.0})

    assert player.speed == 0.0  # clamped, not negative


def test_zero_speed_does_not_advance_clock(tmp_path):
    path, profile = _write_test_alog(tmp_path)
    player = AlogPlayer(path, speed=0.0)

    player.tick(5.0)

    assert player.playback_clock == 0.0


def test_get_new_events_fires_each_event_exactly_once(tmp_path):
    path, profile = _write_test_alog(tmp_path, n_points=6, step_s=1.0)
    player = AlogPlayer(path, speed=1.0)

    player.tick(0.0)
    assert [e["type"] for e in player.get_new_events()] == ["CHARGE"]
    assert player.get_new_events() == []  # not fired again on a second call

    player.tick(5.0)  # now at/past the DROP event's time
    assert [e["type"] for e in player.get_new_events()] == ["DROP"]
    assert player.get_new_events() == []


def test_empty_profile_is_immediately_finished(tmp_path):
    alog_dict = roast_to_alog_dict(title="Empty", profile=[], events=[], notes=[])
    path = str(tmp_path / "empty.alog")
    save_alog(path, alog_dict)

    player = AlogPlayer(path)

    assert player.is_finished()
