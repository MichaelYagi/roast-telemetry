"""RoastSession.add_event -- milestone event sequencing. This is the rule
that a milestone can be marked as long as nothing *later* in the roast's
canonical order has fired yet (skipping ahead is fine; going back once a
later one is recorded is not), TURNING_POINT is never manually markable
(CHARGE now is -- real-hardware modes mark it by hand, see
roast_heuristics.LiveRoastDetector's detect_milestones=False), a milestone
can only be marked once the session is actually recording (not merely
armed/connected), and re-marking an already-recorded milestone is
rejected. CUSTOM events (imported control-channel adjustments) are exempt
entirely.

Builds a bare RoastSession directly (SIMULATOR mode, never .start()-ed) so
this never touches the DB, a WebSocket, or a background task -- add_event
is a plain synchronous method over in-memory state.
"""
from __future__ import annotations

import pytest

from backend.app.models import EventCreateRequest, RoastCreateRequest, RoastEventType, RoastMode, RoastStatus
from backend.app.roast_session.session import RoastSession, RoastSessionError


def make_session() -> RoastSession:
    request = RoastCreateRequest(title="Test Roast", mode=RoastMode.SIMULATOR)
    session = RoastSession("test-roast-id", request)
    # add_event now requires the session to actually be recording (see its
    # own status guard) -- these tests are specifically about milestone
    # sequencing once a roast is underway, not the armed/preview window,
    # which has its own dedicated test below.
    session.status = RoastStatus.ROASTING
    return session


def mark_auto(session: RoastSession, event_type: RoastEventType) -> None:
    """Simulates an auto-detected milestone (CHARGE/TURNING_POINT, or any
    other type as detected by a live-bridge engine) landing directly in
    session.events -- these bypass add_event's manual-entry checks
    entirely, same as the real engines' get_new_events() output does."""
    session.events.append({"id": "auto", "time_s": 0.0, "type": event_type.value, "label": event_type.value, "value": None})


def test_turning_point_cannot_be_marked_manually():
    session = make_session()
    with pytest.raises(RoastSessionError):
        session.add_event(EventCreateRequest(type=RoastEventType.TURNING_POINT, label="x"))


def test_charge_can_now_be_marked_manually():
    # Real-hardware modes (modbus_live/ms6514_live) no longer auto-detect
    # CHARGE at all -- the operator marks it like any other milestone.
    session = make_session()
    event = session.add_event(EventCreateRequest(type=RoastEventType.CHARGE, label="Charge"))
    assert event["type"] == "CHARGE"


def test_add_event_rejected_before_recording_starts():
    # Guards the armed/preview window (status IDLE, see
    # RoastSession.connect()) -- profile is empty there, so a click would
    # otherwise land at time_s=0.0 and silently survive into the real
    # recording once begin_recording() flips status.
    request = RoastCreateRequest(title="Test Roast", mode=RoastMode.SIMULATOR)
    session = RoastSession("test-roast-id", request)
    assert session.status == RoastStatus.IDLE

    with pytest.raises(RoastSessionError):
        session.add_event(EventCreateRequest(type=RoastEventType.DRY_END, label="Dry End"))


def test_milestones_can_be_marked_in_order():
    session = make_session()
    mark_auto(session, RoastEventType.CHARGE)
    mark_auto(session, RoastEventType.TURNING_POINT)

    session.add_event(EventCreateRequest(type=RoastEventType.DRY_END, label="Dry End"))
    session.add_event(EventCreateRequest(type=RoastEventType.FC_START, label="FC Start"))
    session.add_event(EventCreateRequest(type=RoastEventType.FC_END, label="FC End"))
    session.add_event(EventCreateRequest(type=RoastEventType.DROP, label="Drop"))

    types = [e["type"] for e in session.events]
    assert types == ["CHARGE", "TURNING_POINT", "DRY_END", "FC_START", "FC_END", "DROP"]


def test_skipping_ahead_is_allowed():
    # Real Artisan allows e.g. marking Drop without ever marking Second
    # Crack -- skipping is fine, only going *backward* after a later
    # milestone exists is rejected.
    session = make_session()
    session.add_event(EventCreateRequest(type=RoastEventType.DROP, label="Drop"))
    assert session.events[-1]["type"] == "DROP"


def test_cannot_remark_an_already_recorded_milestone():
    session = make_session()
    session.add_event(EventCreateRequest(type=RoastEventType.DRY_END, label="Dry End"))

    with pytest.raises(RoastSessionError):
        session.add_event(EventCreateRequest(type=RoastEventType.DRY_END, label="Dry End again"))


def test_cannot_mark_earlier_milestone_once_a_later_one_is_recorded():
    session = make_session()
    session.add_event(EventCreateRequest(type=RoastEventType.DROP, label="Drop"))

    with pytest.raises(RoastSessionError):
        session.add_event(EventCreateRequest(type=RoastEventType.DRY_END, label="Dry End"))


def test_cannot_mark_fc_start_after_fc_end_already_recorded():
    session = make_session()
    session.add_event(EventCreateRequest(type=RoastEventType.FC_END, label="FC End"))

    with pytest.raises(RoastSessionError):
        session.add_event(EventCreateRequest(type=RoastEventType.FC_START, label="FC Start"))


def test_custom_events_are_exempt_from_sequencing_and_repeatable():
    session = make_session()
    session.add_event(EventCreateRequest(type=RoastEventType.DROP, label="Drop"))

    # CUSTOM isn't a milestone at all -- fires freely regardless of what
    # milestones have or haven't been recorded, any number of times.
    session.add_event(EventCreateRequest(type=RoastEventType.CUSTOM, label="Burner 80", value=80.0))
    session.add_event(EventCreateRequest(type=RoastEventType.CUSTOM, label="Burner 90", value=90.0))

    custom_events = [e for e in session.events if e["type"] == "CUSTOM"]
    assert len(custom_events) == 2


def test_add_event_records_current_profile_time():
    session = make_session()
    session.profile.append({"time_s": 42.5, "bt": 100.0})

    event = session.add_event(EventCreateRequest(type=RoastEventType.DROP, label="Drop"))

    assert event["time_s"] == 42.5


def test_add_event_with_empty_profile_defaults_to_zero():
    session = make_session()
    event = session.add_event(EventCreateRequest(type=RoastEventType.DROP, label="Drop"))
    assert event["time_s"] == 0.0


def test_manual_charge_notifies_an_engine_that_supports_it():
    """Turning Point stays auto-detected even when CHARGE is a manual
    click (see roast_heuristics.LiveRoastDetector.notify_manual_charge) --
    this confirms add_event() actually forwards to the engine, not just
    that the detector-level behavior is correct in isolation (already
    covered by tests/test_live_roast_detector.py)."""
    session = make_session()
    calls = []
    session._engine.notify_manual_charge = lambda time_s, bt: calls.append((time_s, bt))
    session.profile.append({"time_s": 12.5, "bt": 96.0})

    session.add_event(EventCreateRequest(type=RoastEventType.CHARGE, label="Charge", value=96.0))

    assert calls == [(12.5, 96.0)]


def test_manual_charge_with_no_bt_value_does_not_notify():
    # value is optional on EventCreateRequest -- can't seed Turning Point
    # tracking without a real BT reading, so this must be skipped, not
    # crash on None.
    session = make_session()
    calls = []
    session._engine.notify_manual_charge = lambda time_s, bt: calls.append((time_s, bt))

    session.add_event(EventCreateRequest(type=RoastEventType.CHARGE, label="Charge"))

    assert calls == []


def test_other_milestones_do_not_notify_manual_charge():
    session = make_session()
    calls = []
    session._engine.notify_manual_charge = lambda time_s, bt: calls.append((time_s, bt))

    session.add_event(EventCreateRequest(type=RoastEventType.DROP, label="Drop", value=217.0))

    assert calls == []


def test_engine_without_notify_manual_charge_is_fine():
    # SimulatorEngine (what make_session() actually builds) has no such
    # method -- add_event() must not crash just because CHARGE was marked.
    session = make_session()
    event = session.add_event(EventCreateRequest(type=RoastEventType.CHARGE, label="Charge", value=96.0))
    assert event["type"] == "CHARGE"


def test_manual_dry_end_and_fc_start_mark_the_engine_fired():
    """Only matters when auto_detect_milestones is on -- prevents a later
    independent auto-fire of the same type from landing as a duplicate
    (see roast_heuristics.LiveRoastDetector.mark_milestone_fired's own
    docstring). Confirms add_event() actually forwards for both types."""
    session = make_session()
    calls = []
    session._engine.mark_milestone_fired = lambda event_type: calls.append(event_type)

    session.add_event(EventCreateRequest(type=RoastEventType.DRY_END, label="Dry End"))
    session.add_event(EventCreateRequest(type=RoastEventType.FC_START, label="FC Start"))

    assert calls == ["DRY_END", "FC_START"]


def test_other_milestones_do_not_mark_milestone_fired():
    session = make_session()
    calls = []
    session._engine.mark_milestone_fired = lambda event_type: calls.append(event_type)

    session.add_event(EventCreateRequest(type=RoastEventType.DROP, label="Drop"))

    assert calls == []


def test_engine_without_mark_milestone_fired_is_fine():
    # SimulatorEngine has no such method -- add_event() must not crash.
    session = make_session()
    event = session.add_event(EventCreateRequest(type=RoastEventType.DRY_END, label="Dry End"))
    assert event["type"] == "DRY_END"
