"""alog_playback/alog_io.py -- reading/writing .alog files, including real
Artisan's own (non-JSON) shape. These are pure functions with no I/O beyond
a single file, so they're tested directly rather than through the API."""
from __future__ import annotations

import pytest

from alog_playback.alog_io import (
    _compute_ror,
    _extract_continuous_channels,
    _step_hold_align,
    alog_dict_to_points,
    load_alog,
    roast_to_artisan_native_dict,
    save_artisan_native_alog,
)


def test_roundtrip_through_save_and_load(tmp_path):
    # Full pipeline: build a roast -> write it (the app's one and only
    # .alog format, real Artisan's own) -> read it back through this
    # module's own reader too, not just Artisan's.
    profile = [
        {"time_s": 0.0, "bt": 20.0, "et": 25.0, "heater_pct": 70.0, "fan_pct": 50.0, "drum_speed_pct": 60.0},
        {"time_s": 1.0, "bt": 21.0, "et": 26.0, "heater_pct": 70.0, "fan_pct": 50.0, "drum_speed_pct": 60.0},
    ]
    events = [{"id": "e1", "time_s": 0.0, "type": "CHARGE", "label": "Charge", "value": 20.0}]
    notes = [{"id": "n1", "time_s": 0.5, "text": "hello", "author": None}]

    alog_dict = roast_to_artisan_native_dict(
        title="Test Roast", profile=profile, events=events, notes=notes,
        beans="Guatemala", weight_green_g=200.0, weight_roasted_g=170.0,
        roastertype="Coffee-Tech FZ94", roastdate="2026-01-01T00:00:00+00:00",
    )
    path = str(tmp_path / "roast.alog")
    save_artisan_native_alog(path, alog_dict)

    loaded = load_alog(path)
    points = alog_dict_to_points(loaded)

    assert points["title"] == "Test Roast"
    assert points["beans"] == "Guatemala"
    assert points["weight_green_g"] == 200.0
    assert points["weight_roasted_g"] == 170.0
    assert points["machine"] == {"brand": None, "model": "Coffee-Tech FZ94"}
    # Index 0 is a synthetic pre-charge lead-in sample -- see
    # roast_to_artisan_native_dict's docstring for why one gets prepended.
    assert [p["time_s"] for p in points["profile"]] == [-1.0, 0.0, 1.0]
    assert [p["bt"] for p in points["profile"]] == [20.0, 20.0, 21.0]
    assert points["events"][0]["type"] == "CHARGE"
    assert points["notes"][0]["text"] == "hello"


def test_charge_survives_when_it_predates_the_first_profile_sample(tmp_path):
    # Real bug: live-bridge/simulator sessions fire Charge synchronously
    # (resetting the clock) but only append the first profile sample once
    # a full tick interval has elapsed -- so Charge's own recorded time
    # can be *earlier* than profile[0], not coincide with it like the
    # roundtrip test above assumes. When that gap exactly equals the
    # synthetic lead-in's own 1-tick offset, a plain nearest-time search
    # used to tie between the lead-in slot and profile[0], and the
    # tie-break silently picked the lead-in (index 0, Artisan's "not
    # recorded" sentinel) -- losing the Charge marker entirely on every
    # read-back. Confirmed live with the simulator's default 1s interval,
    # this profile/events shape is exactly that scenario.
    profile = [
        {"time_s": 1.0, "bt": 95.98, "et": 202.8},
        {"time_s": 2.0, "bt": 95.92, "et": 205.46},
        {"time_s": 4.0, "bt": 90.0, "et": 210.0},
    ]
    events = [
        {"id": "e1", "time_s": 0.0, "type": "CHARGE", "label": "Charge", "value": 96.0},
        {"id": "e2", "time_s": 4.0, "type": "DRY_END", "label": "Dry End", "value": None},
    ]

    alog_dict = roast_to_artisan_native_dict(title="Gap Test", profile=profile, events=events, notes=[])
    path = str(tmp_path / "gap.alog")
    save_artisan_native_alog(path, alog_dict)

    points = alog_dict_to_points(load_alog(path))
    event_types = [e["type"] for e in points["events"]]
    assert "CHARGE" in event_types
    assert "DRY_END" in event_types


def test_extra_channels_roundtrip_through_the_extraname2_bank(tmp_path):
    # role=EXTRA DeviceProfile channels (see RoastProfilePoint.extra) --
    # up to 2 round-trip through the real .alog format's extraname2 bank
    # (slot 0 is DT, slots 1/2 are free -- see roast_to_artisan_native_dict's
    # own comment on why that's a fixed ceiling, not arbitrary).
    profile = [
        {"time_s": 0.0, "bt": 20.0, "et": 25.0, "dt": 30.0, "extra": {"Flue": 40.0, "Ambient": 18.0}},
        {"time_s": 1.0, "bt": 21.0, "et": 26.0, "dt": 31.0, "extra": {"Flue": 41.0, "Ambient": 18.5}},
    ]
    alog_dict = roast_to_artisan_native_dict(
        title="Extra Channels Roast", profile=profile, events=[], notes=[],
    )
    path = str(tmp_path / "roast.alog")
    save_artisan_native_alog(path, alog_dict)

    loaded = load_alog(path)
    points = alog_dict_to_points(loaded)

    # Index 0 is the synthetic pre-charge lead-in sample (same convention
    # as the main roundtrip test above) -- it clones profile[0], so it
    # carries the same extra values too.
    profile_points = points["profile"]
    assert [p["dt"] for p in profile_points] == [30.0, 30.0, 31.0]
    assert [p["extra"]["Flue"] for p in profile_points] == [40.0, 40.0, 41.0]
    assert [p["extra"]["Ambient"] for p in profile_points] == [18.0, 18.0, 18.5]


def test_dt_less_roast_does_not_export_a_fake_flat_dt_curve(tmp_path):
    # Real bug: a roast with no third probe at all (simulator,
    # alog_playback, or any Modbus profile without a dt channel) used to
    # still get an extraname2 "DT" slot, because _fill_and_floatify
    # defaults an all-None series to a flat 0.0 -- indistinguishable, on
    # read-back, from a genuine probe that read exactly 0C the whole
    # roast. No real dt data anywhere in the profile must mean no DT
    # entry in the read-back profile at all.
    profile = [
        {"time_s": 0.0, "bt": 20.0, "et": 25.0},
        {"time_s": 1.0, "bt": 21.0, "et": 26.0},
    ]
    alog_dict = roast_to_artisan_native_dict(title="No DT probe", profile=profile, events=[], notes=[])
    path = str(tmp_path / "roast.alog")
    save_artisan_native_alog(path, alog_dict)

    points = alog_dict_to_points(load_alog(path))
    assert all(p.get("dt") is None for p in points["profile"])


def test_a_third_extra_channel_beyond_the_two_free_slots_does_not_export(tmp_path):
    # Documents the real, fixed ceiling (donor template has exactly 3
    # extraname2 slots: DT + 2 more) rather than silently corrupting
    # anything -- a third role=EXTRA channel just isn't in the export.
    profile = [
        {"time_s": 0.0, "bt": 20.0, "et": 25.0, "extra": {"A": 1.0, "B": 2.0, "C": 3.0}},
    ]
    alog_dict = roast_to_artisan_native_dict(title="R", profile=profile, events=[], notes=[])
    path = str(tmp_path / "roast.alog")
    save_artisan_native_alog(path, alog_dict)

    points = alog_dict_to_points(load_alog(path))
    extras = points["profile"][-1]["extra"]
    assert extras.get("A") == 1.0
    assert extras.get("B") == 2.0
    assert "C" not in extras


def test_load_alog_parses_real_artisan_python_literal(tmp_path):
    # Real Artisan .alog files are `str(dict)`, not JSON -- single quotes,
    # True/False/None -- loaded back with ast.literal_eval, never eval().
    raw = (
        "{'timex': [0.0, 1.0, 2.0], 'temp1': [25.0, 26.0, 27.0], "
        "'temp2': [20.0, 21.0, 22.0], 'weight': [200.0, 170.0, 'g'], "
        "'roastertype': 'FZ94', 'some_flag': True, 'other_flag': None}"
    )
    path = tmp_path / "real.alog"
    path.write_text(raw, encoding="utf-8")

    data = load_alog(str(path))

    assert data["timex"] == [0.0, 1.0, 2.0]
    assert data["temp2"] == [20.0, 21.0, 22.0]
    # setdefault fills these in for a file that never had them
    assert data["specialevents"] == []
    assert data["notes"] == []
    assert data["ror_bt"] == [None, None, None]


def test_load_alog_missing_required_field_raises(tmp_path):
    path = tmp_path / "bad.alog"
    path.write_text('{"timex": [0.0], "temp1": [25.0]}', encoding="utf-8")
    try:
        load_alog(str(path))
        assert False, "expected ValueError for missing temp2"
    except ValueError as exc:
        assert "temp2" in str(exc)


def test_extract_named_milestones_and_turning_point():
    # A synthetic real-Artisan-shaped dict: timeindex maps
    # CHARGE/DRY_END/FC_START/FC_END/SC_START/SC_END/DROP/COOL_END to
    # indices into timex (0 = not recorded -- real Artisan's own sentinel,
    # so index 0 of timex is reserved as a pre-charge baseline sample and
    # never itself a real milestone), and Turning Point comes from
    # computed.TP_idx instead.
    timex = [-5.0, 0.0, 10.0, 60.0, 300.0, 480.0, 600.0, 720.0]
    temp2 = [21.0, 20.0, 19.0, 100.0, 196.0, 205.0, 220.0, 90.0]
    data = {
        "timex": timex,
        "temp1": [t + 5 for t in temp2],
        "temp2": temp2,
        # CHARGE=idx1, DRY_END=idx3, FC_START=idx4, FC_END=idx5,
        # SC_START=not recorded, SC_END=not recorded, DROP=idx6, COOL_END=idx7
        "timeindex": [1, 3, 4, 5, 0, 0, 6, 7],
        "computed": {"TP_idx": 2},
    }

    points = alog_dict_to_points(data)
    events_by_type = {e["type"]: e for e in points["events"]}

    assert events_by_type["CHARGE"]["time_s"] == 0.0
    assert events_by_type["DRY_END"]["time_s"] == 60.0
    assert events_by_type["FC_START"]["time_s"] == 300.0
    assert events_by_type["FC_START"]["value"] == 196.0
    assert events_by_type["FC_END"]["time_s"] == 480.0
    assert "SC_START" not in events_by_type
    assert "SC_END" not in events_by_type
    assert events_by_type["DROP"]["time_s"] == 600.0
    assert events_by_type["COOL_END"]["time_s"] == 720.0
    assert events_by_type["TURNING_POINT"]["time_s"] == 10.0
    # Events must come out sorted by time, not by however timeindex listed them.
    assert [e["time_s"] for e in points["events"]] == sorted(e["time_s"] for e in points["events"])


def test_extract_manual_events_with_channel_and_custom_label():
    timex = [0.0, 30.0, 90.0]
    data = {
        "timex": timex,
        "temp1": [25.0, 40.0, 60.0],
        "temp2": [20.0, 35.0, 55.0],
        "etypes": ["Air", "Drum", "Damper", "Burner", "--"],
        "specialevents": [1, 2],  # indices into timex
        "specialeventstype": [3, 0],  # Burner, Air
        "specialeventsvalue": [80.0, 40.0],
        "specialeventsStrings": ["", "Custom label"],
    }

    points = alog_dict_to_points(data)
    events = points["events"]

    assert len(events) == 2
    burner_event = next(e for e in events if e["channel"] == "Burner")
    assert burner_event["time_s"] == 30.0
    assert burner_event["value"] == 80.0
    assert burner_event["label"] == "Burner 80"

    air_event = next(e for e in events if e["channel"] == "Air")
    assert air_event["label"] == "Custom label"  # explicit string wins over the generated one


def test_our_own_writer_shape_events_pass_through_unchanged():
    # Our own writer already stores specialevents as dicts (roast_to_alog_dict) --
    # _extract_events must return those as-is, not reinterpret them as Artisan's
    # parallel-array format.
    data = {
        "timex": [0.0, 1.0],
        "temp1": [25.0, 26.0],
        "temp2": [20.0, 21.0],
        "specialevents": [{"id": "e1", "time_s": 0.0, "type": "CHARGE", "label": "Charge", "value": 20.0}],
    }
    points = alog_dict_to_points(data)
    assert points["events"] == data["specialevents"]


def test_compute_ror_suppresses_noise_until_window_full():
    # BT rises at a constant 1 deg/sec = 60 deg/min, sampled every second.
    # The trailing window is 24s -- real RoR shouldn't appear before that
    # much history exists (this was a real bug: dividing a tiny raw delta
    # by a tiny dt right after charge produced implausible spikes).
    timex = list(range(0, 40))
    temps = [20.0 + t for t in timex]

    ror = _compute_ror(timex, temps, window_s=24.0)

    for i in range(0, 22):
        assert ror[i] is None, f"expected no RoR yet at t={i}"
    # Once the window is fully populated, RoR should reflect the true rate.
    assert ror[30] == pytest.approx(60.0)


def test_step_hold_align_holds_last_value_forward():
    src_times = [0.0, 10.0, 20.0]
    src_values = [30.0, 50.0, 70.0]
    target_times = [0.0, 5.0, 9.9, 10.0, 15.0, 25.0]

    out = _step_hold_align(src_times, src_values, target_times)

    assert out == [30.0, 30.0, 30.0, 50.0, 50.0, 70.0]


def test_step_hold_align_empty_source_returns_all_none():
    assert _step_hold_align([], [], [0.0, 1.0]) == [None, None]


def test_extract_continuous_channels_maps_burner_air_drum():
    # Real Kaleido-style files report Burner/Air/Drum as continuous
    # "extra device" channels: extraname{1,2} labeled "{N}" (an index into
    # etypes) paired with extratemp{1,2}/extratimex arrays -- this is what
    # was previously missing entirely (only manual specialevents parsed).
    timex = [0.0, 5.0, 10.0, 15.0]
    data = {
        "etypes": ["Air", "Drum", "Damper", "Burner", "--"],
        "extraname1": ["{3}", "{0}"],  # Burner, Air
        "extratemp1": [[80.0, 85.0], [40.0, 45.0]],
        "extraname2": ["{1}"],  # Drum
        "extratemp2": [[60.0, 65.0]],
        "extratimex": [[0.0, 10.0], [0.0, 10.0], [0.0, 10.0]],
    }

    result = _extract_continuous_channels(data, timex)

    assert result["heater_pct"] == [80.0, 80.0, 85.0, 85.0]  # Burner, step-held onto main timex
    assert result["fan_pct"] == [40.0, 40.0, 45.0, 45.0]  # Air
    assert result["drum_speed_pct"] == [60.0, 60.0, 65.0, 65.0]  # Drum


def test_alog_dict_to_points_prefers_continuous_channels_over_control_log():
    # When both a continuous channel and the coarser manual `control` log
    # are present, the continuous (higher-resolution) one should win.
    timex = [0.0, 10.0]
    data = {
        "timex": timex,
        "temp1": [25.0, 30.0],
        "temp2": [20.0, 28.0],
        "etypes": ["Air", "Drum", "Damper", "Burner", "--"],
        "extraname1": ["{3}"],
        "extratemp1": [[80.0, 90.0]],
        "extratimex": [[0.0, 10.0]],
        "control": [{"time_s": 0.0, "heater_pct": 10.0}, {"time_s": 10.0, "heater_pct": 10.0}],
    }
    points = alog_dict_to_points(data)
    assert [p["heater_pct"] for p in points["profile"]] == [80.0, 90.0]
