"""RoastSession.add_event -- milestone event sequencing. This is the rule
that a milestone can be marked as long as nothing *later* in the roast's
canonical order has fired yet (skipping ahead is fine; going back once a
later one is recorded is not), CHARGE/TURNING_POINT are never manually
markable, and re-marking an already-recorded milestone is rejected. CUSTOM
events (imported control-channel adjustments) are exempt entirely.

Builds a bare RoastSession directly (SIMULATOR mode, never .start()-ed) so
this never touches the DB, a WebSocket, or a background task -- add_event
is a plain synchronous method over in-memory state.
"""
from __future__ import annotations

import pytest

from backend.app.models import EventCreateRequest, RoastCreateRequest, RoastEventType, RoastMode
from backend.app.roast_session.session import RoastSession, RoastSessionError


def make_session() -> RoastSession:
    request = RoastCreateRequest(title="Test Roast", mode=RoastMode.SIMULATOR)
    return RoastSession("test-roast-id", request)


def mark_auto(session: RoastSession, event_type: RoastEventType) -> None:
    """Simulates an auto-detected milestone (CHARGE/TURNING_POINT, or any
    other type as detected by a live-bridge engine) landing directly in
    session.events -- these bypass add_event's manual-entry checks
    entirely, same as the real engines' get_new_events() output does."""
    session.events.append({"id": "auto", "time_s": 0.0, "type": event_type.value, "label": event_type.value, "value": None})


@pytest.mark.parametrize("event_type", [RoastEventType.CHARGE, RoastEventType.TURNING_POINT])
def test_always_auto_types_cannot_be_marked_manually(event_type):
    session = make_session()
    with pytest.raises(RoastSessionError):
        session.add_event(EventCreateRequest(type=event_type, label="x"))


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
