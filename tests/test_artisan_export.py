"""alog_playback/alog_io.py's roast_to_artisan_native_dict/save_artisan_native_alog
-- exports a roast recorded natively by this app into a shape real Artisan
can actually open (Python-literal syntax + Artisan's own timeindex/computed/
specialevents fields), cloned from a confirmed-working real Artisan export
(artisan_native_template.json) rather than guessed from scratch."""
from __future__ import annotations

from alog_playback.alog_io import (
    load_alog,
    roast_to_artisan_native_dict,
    save_artisan_native_alog,
)


def _profile(n=10, step=1.0, bt0=90.0, et0=150.0):
    return [
        {
            "time_s": i * step,
            "bt": bt0 + i * 1.5,
            "et": et0 + i * 0.8,
            "heater_pct": 70.0,
            "fan_pct": 20.0,
            "drum_speed_pct": 50.0,
        }
        for i in range(n)
    ]


def test_output_is_python_literal_syntax_not_json(tmp_path):
    d = roast_to_artisan_native_dict(title="t", profile=_profile(3), events=[], notes=[])
    path = str(tmp_path / "out.alog")
    save_artisan_native_alog(path, d)
    raw = (tmp_path / "out.alog").read_text(encoding="utf-8")
    # JSON's true/false/null would break ast.literal_eval (real Artisan's
    # own loader) -- this must be Python's True/False/None instead.
    assert "true" not in raw and "false" not in raw and ": null" not in raw
    reloaded = load_alog(path)
    assert reloaded["title"] == "t"


def test_charge_at_first_sample_does_not_collide_with_not_recorded_sentinel():
    # This app's roasts start recording *at* Charge (t=0 is the first
    # sample) -- without a synthetic lead-in sample, Charge would always
    # land at timeindex[0], indistinguishable from Artisan's own "not
    # recorded" convention (0). This was a real bug caught before shipping.
    profile = _profile(20)
    events = [{"id": "e1", "time_s": 0.0, "type": "CHARGE", "label": "Charge"}]

    d = roast_to_artisan_native_dict(title="t", profile=profile, events=events, notes=[])

    assert d["timeindex"][0] != 0  # CHARGE is the 0th entry in timeindex
    assert d["computed"]["CHARGE_BT"] == profile[0]["bt"]
    assert d["computed"]["CHARGE_ET"] == profile[0]["et"]


def test_milestone_indices_and_computed_block():
    profile = _profile(400, step=1.0)
    events = [
        {"id": "e1", "time_s": 0.0, "type": "CHARGE", "label": "Charge"},
        {"id": "e2", "time_s": 45.0, "type": "TURNING_POINT", "label": "Turning Point"},
        {"id": "e3", "time_s": 215.0, "type": "DRY_END", "label": "Dry End"},
        {"id": "e4", "time_s": 327.0, "type": "FC_START", "label": "FC Start"},
        {"id": "e5", "time_s": 360.0, "type": "DROP", "label": "Drop"},
    ]

    d = roast_to_artisan_native_dict(title="t", profile=profile, events=events, notes=[])
    c = d["computed"]

    assert c["DRY_time"] == 215.0
    assert c["FCs_time"] == 327.0
    assert c["DROP_time"] == 360.0
    assert c["TP_time"] == 45.0
    # SC_START/SC_END/COOL_END never fired -- must stay "not recorded" (0),
    # not some fabricated index.
    assert d["timeindex"][4] == 0  # SC_START
    assert d["timeindex"][5] == 0  # SC_END
    assert d["timeindex"][7] == 0  # COOL_END
    # Phase durations derived from the above, confirmed against a real
    # Artisan export's own arithmetic (see the function's docstring).
    assert c["dryphasetime"] == 215.0
    assert c["midphasetime"] == 327.0 - 215.0
    assert c["finishphasetime"] == 360.0 - 327.0
    assert c["totaltime"] == 360.0


def test_weight_loss_computed_when_both_weights_present():
    d = roast_to_artisan_native_dict(
        title="t", profile=_profile(3), events=[], notes=[],
        weight_green_g=350.0, weight_roasted_g=301.0,
    )
    assert d["computed"]["weightin"] == 350.0
    assert d["computed"]["weightout"] == 301.0
    assert d["computed"]["weight_loss"] == round((350.0 - 301.0) / 350.0 * 100, 1)


def test_custom_events_become_parallel_arrays_not_dicts():
    profile = _profile(200)
    events = [
        {"id": "e1", "time_s": 0.0, "type": "CHARGE", "label": "Charge"},
        {"id": "e2", "time_s": 50.0, "type": "CUSTOM", "label": "Burner 80", "value": 80.0, "channel": "Burner"},
    ]
    d = roast_to_artisan_native_dict(title="t", profile=profile, events=events, notes=[])

    assert isinstance(d["specialevents"], list) and isinstance(d["specialevents"][0], int)
    assert d["specialeventsvalue"] == [80.0]
    assert d["etypes"][d["specialeventstype"][0]] == "Burner"


def test_heater_fan_drum_become_continuous_extra_channels_and_round_trip(tmp_path):
    from alog_playback.alog_io import alog_dict_to_points

    profile = _profile(50)
    d = roast_to_artisan_native_dict(title="t", profile=profile, events=[], notes=[])
    path = str(tmp_path / "out.alog")
    save_artisan_native_alog(path, d)

    reloaded = load_alog(path)
    points = alog_dict_to_points(reloaded)
    # Skip the synthetic lead-in sample (index 0) -- everything after it
    # should match the original profile's control values exactly.
    assert points["profile"][1]["heater_pct"] == 70.0
    assert points["profile"][1]["fan_pct"] == 20.0
    assert points["profile"][1]["drum_speed_pct"] == 50.0


def test_missing_weight_and_empty_profile_do_not_crash():
    d = roast_to_artisan_native_dict(title="Empty", profile=[], events=[], notes=[])
    assert d["timeindex"] == [0, 0, 0, 0, 0, 0, 0, 0]
    assert d["weight"] == [0.0, 0.0, "g"]
