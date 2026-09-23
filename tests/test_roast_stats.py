"""backend/app/roast_stats.py -- phase_breakdown/ror_flags/weight_loss_pct/
compute_roast_stats. Extracted from roast_review.py (see that module's own
tests, still passing unchanged through build_summary's public output) so
the same math can back GET /roasts/{id}/stats, History's trends toggle,
and the Compare page's numeric table without a second implementation.
"""
from __future__ import annotations

from backend.app.models import Roast, RoastEvent, RoastEventType, RoastMode, RoastStatus
from backend.app.roast_session.session import session_manager
from backend.app.roast_stats import compute_roast_stats, events_by_type, phase_breakdown, ror_flags, weight_loss_pct


def _event(event_type: RoastEventType, time_s: float, value=None) -> RoastEvent:
    return RoastEvent(id=event_type.value, time_s=time_s, type=event_type, label=event_type.value, value=value)


def _make_roast(**overrides) -> Roast:
    defaults = dict(
        id="r1", title="Test Roast", mode=RoastMode.SIMULATOR, status=RoastStatus.STOPPED,
        created_at="2026-01-01T00:00:00", profile=[], events=[], notes=[],
    )
    return Roast(**{**defaults, **overrides})


# -- phase_breakdown --------------------------------------------------------


def test_phase_breakdown_empty_without_charge_and_drop():
    events = events_by_type(_make_roast(events=[_event(RoastEventType.DRY_END, 300)]))
    assert phase_breakdown(events) == []


def test_phase_breakdown_computes_all_three_phases():
    roast = _make_roast(events=[
        _event(RoastEventType.CHARGE, 0),
        _event(RoastEventType.DRY_END, 300),
        _event(RoastEventType.FC_START, 480),
        _event(RoastEventType.DROP, 600),
    ])
    phases = phase_breakdown(events_by_type(roast))
    assert phases == [
        {"phase": "Dry", "duration_s": 300.0, "pct_of_roast": 50.0},
        {"phase": "Maillard", "duration_s": 180.0, "pct_of_roast": 30.0},
        {"phase": "Development", "duration_s": 120.0, "pct_of_roast": 20.0},
    ]


def test_phase_breakdown_skips_phases_missing_a_milestone():
    # DRY_END never marked -- Dry and Maillard both need it, only
    # Development (FC_START->DROP) doesn't.
    roast = _make_roast(events=[
        _event(RoastEventType.CHARGE, 0),
        _event(RoastEventType.FC_START, 480),
        _event(RoastEventType.DROP, 600),
    ])
    phases = phase_breakdown(events_by_type(roast))
    assert [p["phase"] for p in phases] == ["Development"]


# -- ror_flags ----------------------------------------------------------


def test_ror_flags_empty_for_a_short_stable_curve():
    # Constant RoR, but short enough (under FLATLINE_MIN_DURATION_S) that
    # it doesn't also trip the flatline detector -- a genuinely flat
    # curve sustained longer than that *should* flag as a flatline (see
    # its own dedicated test below), so this one stays deliberately brief.
    events = events_by_type(_make_roast(events=[_event(RoastEventType.TURNING_POINT, 0)]))
    profile = [{"time_s": float(t), "ror_bt": 10.0} for t in range(0, 40, 10)]
    flags = ror_flags(profile, events)
    assert flags == {"crashes": [], "flatlines": [], "flicks": []}


def test_ror_flags_detects_a_flatline():
    events = events_by_type(_make_roast(events=[_event(RoastEventType.TURNING_POINT, 0)]))
    profile = [{"time_s": float(t), "ror_bt": 10.0} for t in range(0, 300, 10)]
    flags = ror_flags(profile, events)
    assert len(flags["flatlines"]) == 1
    assert flags["flatlines"][0]["start_s"] == 0.0


def test_ror_flags_detects_a_crash():
    events = events_by_type(_make_roast(events=[_event(RoastEventType.TURNING_POINT, 0)]))
    # RoR climbs to 20, then drops sharply to 5 within the crash window.
    profile = [
        {"time_s": 0.0, "ror_bt": 20.0},
        {"time_s": 10.0, "ror_bt": 20.0},
        {"time_s": 20.0, "ror_bt": 5.0},
    ]
    flags = ror_flags(profile, events)
    assert len(flags["crashes"]) == 1
    assert flags["crashes"][0]["ror_bt"] == 5.0


# -- weight_loss_pct ------------------------------------------------------


def test_weight_loss_pct_computed_normally():
    assert weight_loss_pct(_make_roast(weight_green_g=200.0, weight_roasted_g=170.0)) == 15.0


def test_weight_loss_pct_none_when_roasted_weight_never_recorded():
    assert weight_loss_pct(_make_roast(weight_green_g=200.0, weight_roasted_g=None)) is None


def test_weight_loss_pct_handles_a_genuine_total_loss_batch():
    assert weight_loss_pct(_make_roast(weight_green_g=200.0, weight_roasted_g=0.0)) == 100.0


# -- compute_roast_stats ---------------------------------------------------


def test_compute_roast_stats_full_roast():
    roast = _make_roast(
        weight_green_g=200.0,
        weight_roasted_g=170.0,
        duration_s=600.0,
        events=[
            _event(RoastEventType.CHARGE, 0),
            _event(RoastEventType.TURNING_POINT, 30),
            _event(RoastEventType.DRY_END, 300),
            _event(RoastEventType.FC_START, 480),
            _event(RoastEventType.DROP, 600),
        ],
        profile=[{"time_s": float(t), "ror_bt": 10.0} for t in range(0, 600, 30)],
    )
    stats = compute_roast_stats(roast)
    assert stats.weight_loss_pct == 15.0
    assert stats.duration_s == 600.0
    assert stats.dry_pct == 50.0
    assert stats.dtr_pct == 20.0
    assert len(stats.phases) == 3
    assert stats.ror_flags.crashes == []


def test_compute_roast_stats_handles_a_roast_with_no_milestones():
    roast = _make_roast()
    stats = compute_roast_stats(roast)
    assert stats.phases == []
    assert stats.dry_pct is None
    assert stats.dtr_pct is None
    assert stats.ror_flags.crashes == []
    assert stats.ror_flags.flatlines == []
    assert stats.ror_flags.flicks == []


# -- API-level: GET /roasts/{id}/stats and /roasts/stats-batch -------------
#
# Milestone events get their time_s from the roast's own live clock (see
# RoastSession.add_event), not a client-supplied value, so exact phase
# percentages aren't controllable/deterministic here -- these check
# structure/plumbing (200s, 404s, warm vs cold, weight_loss_pct behaving
# sanely, phases present once CHARGE+DROP exist), leaving exact-number
# coverage to the direct compute_roast_stats tests above.


def _create_roast(client, **payload) -> str:
    resp = client.post("/api/v1/roasts", json={"title": "Stats Roast", "mode": "simulator", **payload})
    return resp.json()["id"]


def test_get_roast_stats_404_for_unknown_roast(client):
    resp = client.get("/api/v1/roasts/does-not-exist/stats")
    assert resp.status_code == 404


def test_get_roast_stats_via_api_warm(client):
    roast_id = _create_roast(client)
    resp = client.get(f"/api/v1/roasts/{roast_id}/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["phases"] == []  # no CHARGE/DROP marked yet in this fast-running test
    assert body["weight_loss_pct"] is None
    client.post(f"/api/v1/roasts/{roast_id}/stop")


def test_get_roast_stats_reflects_weight_once_set(client):
    roast_id = _create_roast(client)
    client.post(f"/api/v1/roasts/{roast_id}/weight-green", params={"grams": 200.0})
    client.post(f"/api/v1/roasts/{roast_id}/weight", params={"grams": 170.0})

    resp = client.get(f"/api/v1/roasts/{roast_id}/stats")
    assert resp.json()["weight_loss_pct"] == 15.0
    client.post(f"/api/v1/roasts/{roast_id}/stop")


def test_get_roast_stats_via_api_cold(client):
    roast_id = _create_roast(client)
    client.post(f"/api/v1/roasts/{roast_id}/weight-green", params={"grams": 200.0})
    client.post(f"/api/v1/roasts/{roast_id}/weight", params={"grams": 180.0})
    client.post(f"/api/v1/roasts/{roast_id}/stop")
    session_manager.sessions.pop(roast_id, None)

    resp = client.get(f"/api/v1/roasts/{roast_id}/stats")
    assert resp.status_code == 200
    assert resp.json()["weight_loss_pct"] == 10.0


def test_stats_batch_respects_filters(client):
    a = _create_roast(client, title="Batch A")
    b = _create_roast(client, title="Batch B")
    client.put(f"/api/v1/roasts/{a}/tags", json={"tags": ["decaf"]})
    client.post(f"/api/v1/roasts/{a}/stop")
    client.post(f"/api/v1/roasts/{b}/stop")

    resp = client.get("/api/v1/roasts/stats-batch", params={"tag": "decaf"})
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()]
    assert a in ids
    assert b not in ids


def test_stats_batch_entries_have_stats_fields(client):
    roast_id = _create_roast(client)
    client.post(f"/api/v1/roasts/{roast_id}/weight-green", params={"grams": 200.0})
    client.post(f"/api/v1/roasts/{roast_id}/weight", params={"grams": 150.0})
    client.post(f"/api/v1/roasts/{roast_id}/stop")

    resp = client.get("/api/v1/roasts/stats-batch")
    entry = next(r for r in resp.json() if r["id"] == roast_id)
    assert entry["weight_loss_pct"] == 25.0
    assert "dtr_pct" in entry
    assert "ror_flags" in entry
