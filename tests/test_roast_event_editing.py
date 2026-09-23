"""RoastSession.delete_event/retime_event -- editing an already-marked
milestone (item #7 from the real-hardware feedback round: right-click a
milestone on the chart to delete or drag-retime it).
Confirmed via code reading that this genuinely didn't exist before this
change -- POST /roasts/{id}/events was add-only, and add_event() itself
explicitly rejects re-marking anything already recorded.

Two layers, matching test_roast_session.py's own split:
- Direct-session unit tests (bare RoastSession, SIMULATOR mode, never
  .start()-ed) for the validation/recompute logic itself, with full
  synchronous control over session.profile/session.events.
- API-level tests (the `client` fixture) for the two persistence
  branches: a "warm" roast (session still in memory) and a "cold" one
  (session popped from session_manager.sessions, simulating a backend
  restart since the roast finished -- the exact same trick
  test_roast_created_before_this_field_existed_has_no_attribution in
  tests/test_roasts_lifecycle_api.py already uses).
"""
from __future__ import annotations

import time

from backend.app.models import EventCreateRequest, RoastCreateRequest, RoastEventType, RoastMode, RoastStatus
from backend.app.roast_session.session import RoastSession, RoastSessionError, session_manager


def make_session() -> RoastSession:
    request = RoastCreateRequest(title="Test Roast", mode=RoastMode.SIMULATOR)
    session = RoastSession("test-roast-id", request)
    session.status = RoastStatus.ROASTING
    return session


def mark_auto(session: RoastSession, event_type: RoastEventType, time_s: float = 0.0) -> None:
    session.events.append({"id": event_type.value, "time_s": time_s, "type": event_type.value, "label": event_type.value, "value": None})


def feed_profile(session: RoastSession, *, count: int = 10, start_bt: float = 90.0, step_bt: float = 5.0) -> None:
    for i in range(count):
        session.profile.append({"time_s": float(i * 30), "bt": start_bt + i * step_bt})


# -- direct-session unit tests -------------------------------------------


def test_delete_event_removes_it():
    session = make_session()
    event = session.add_event(EventCreateRequest(type=RoastEventType.DRY_END, label="Dry End"))

    session.delete_event(event["id"])

    assert session.events == []


def test_delete_event_lets_the_milestone_be_remarked():
    session = make_session()
    event = session.add_event(EventCreateRequest(type=RoastEventType.DRY_END, label="Dry End"))
    session.delete_event(event["id"])

    remarked = session.add_event(EventCreateRequest(type=RoastEventType.DRY_END, label="Dry End"))

    assert remarked["type"] == "DRY_END"


def test_delete_event_rejects_turning_point():
    session = make_session()
    mark_auto(session, RoastEventType.TURNING_POINT)
    tp_id = session.events[0]["id"]

    try:
        session.delete_event(tp_id)
        assert False, "expected RoastSessionError"
    except RoastSessionError:
        pass
    assert len(session.events) == 1


def test_delete_event_rejects_unknown_id():
    session = make_session()
    try:
        session.delete_event("does-not-exist")
        assert False, "expected RoastSessionError"
    except RoastSessionError:
        pass


def test_retime_event_moves_it_and_recomputes_value():
    session = make_session()
    feed_profile(session)  # time_s 0,30,...,270 -- bt 90,95,...,135
    event = session.add_event(EventCreateRequest(type=RoastEventType.DRY_END, label="Dry End", value=999.0))

    updated = session.retime_event(event["id"], 90.0)  # nearest sample: time_s=90 -> bt=105.0

    assert updated["time_s"] == 90.0
    assert updated["value"] == 105.0
    assert session.events[0]["time_s"] == 90.0


def test_retime_event_rejects_turning_point():
    session = make_session()
    mark_auto(session, RoastEventType.TURNING_POINT)
    tp_id = session.events[0]["id"]

    try:
        session.retime_event(tp_id, 50.0)
        assert False, "expected RoastSessionError"
    except RoastSessionError:
        pass


def test_retime_event_rejects_out_of_range_time():
    session = make_session()
    feed_profile(session)  # last sample at time_s=270
    event = session.add_event(EventCreateRequest(type=RoastEventType.DRY_END, label="Dry End"))

    try:
        session.retime_event(event["id"], 999.0)
        assert False, "expected RoastSessionError"
    except RoastSessionError:
        pass
    assert session.events[0]["time_s"] != 999.0


def test_retime_event_rejects_crossing_a_later_recorded_milestone():
    session = make_session()
    feed_profile(session)
    dry_end = session.add_event(EventCreateRequest(type=RoastEventType.DRY_END, label="Dry End"))
    dry_end["time_s"] = 60.0  # add_event() always stamps "now" (last profile sample) -- force distinct times
    fc_start = session.add_event(EventCreateRequest(type=RoastEventType.FC_START, label="FC Start"))
    fc_start["time_s"] = 180.0

    try:
        session.retime_event(dry_end["id"], fc_start["time_s"] + 10.0)
        assert False, "expected RoastSessionError"
    except RoastSessionError:
        pass


def test_retime_event_rejects_crossing_an_earlier_recorded_milestone():
    session = make_session()
    feed_profile(session)
    dry_end = session.add_event(EventCreateRequest(type=RoastEventType.DRY_END, label="Dry End"))
    dry_end["time_s"] = 60.0
    fc_start = session.add_event(EventCreateRequest(type=RoastEventType.FC_START, label="FC Start"))
    fc_start["time_s"] = 180.0

    try:
        session.retime_event(fc_start["id"], dry_end["time_s"] - 10.0)
        assert False, "expected RoastSessionError"
    except RoastSessionError:
        pass


def test_retime_event_allowed_between_neighbors():
    session = make_session()
    feed_profile(session)
    dry_end = session.add_event(EventCreateRequest(type=RoastEventType.DRY_END, label="Dry End"))
    dry_end["time_s"] = 60.0  # add_event() always stamps "now" (last profile sample) -- force distinct times
    fc_start = session.add_event(EventCreateRequest(type=RoastEventType.FC_START, label="FC Start"))
    fc_start["time_s"] = 180.0

    updated = session.retime_event(dry_end["id"], 120.0)

    assert updated["time_s"] == 120.0


# -- API-level tests: warm (in-memory session) and cold (popped, alog-only) --


def _create_and_mark(client, event_type: str = "DRY_END") -> tuple[str, str]:
    resp = client.post("/api/v1/roasts", json={"title": "Editable Roast", "mode": "simulator"})
    roast_id = resp.json()["id"]
    # Real wait, not mocked -- marking immediately on connect (no tick
    # elapsed yet) lands this milestone at the exact same profile sample
    # as the simulator's own auto-fired Charge (sample_interval_s=1.0
    # default), which after an .alog round-trip collapses both to the
    # identical cold time_s -- a genuine ambiguity a real roast almost
    # never hits, but this test's synchronous POST right after create
    # did every time. Confirmed live: this used to be masked by a
    # separate bug where Charge silently failed to round-trip at all
    # (see alog_io.py's own milestone_idx comment) -- once that was
    # fixed, Charge legitimately competed for the same timestamp here.
    time.sleep(2.2)
    resp = client.post(f"/api/v1/roasts/{roast_id}/events", json={"type": event_type, "label": event_type})
    event_id = resp.json()["id"]
    return roast_id, event_id


def test_delete_event_via_api_warm(client):
    roast_id, event_id = _create_and_mark(client)

    resp = client.delete(f"/api/v1/roasts/{roast_id}/events/{event_id}")
    assert resp.status_code == 204

    # Not asserting events == [] -- the simulator auto-detects its own
    # CHARGE independently of this manually-marked DRY_END, so it's
    # legitimately still present; just confirm the deleted one is gone.
    resp = client.get(f"/api/v1/roasts/{roast_id}")
    assert event_id not in [e["id"] for e in resp.json()["events"]]

    client.post(f"/api/v1/roasts/{roast_id}/stop")


def test_retime_event_via_api_warm(client):
    roast_id, event_id = _create_and_mark(client)
    resp = client.get(f"/api/v1/roasts/{roast_id}")
    # Retiming to its own current time_s is trivially valid (already
    # passed add_event's own sequencing rule) regardless of how far the
    # simulator's background tick has actually gotten by the time this
    # runs -- avoids a flaky hardcoded target time.
    current_time_s = next(e for e in resp.json()["events"] if e["id"] == event_id)["time_s"]

    resp = client.patch(f"/api/v1/roasts/{roast_id}/events/{event_id}", json={"time_s": current_time_s})
    assert resp.status_code == 200
    assert resp.json()["time_s"] == current_time_s

    client.post(f"/api/v1/roasts/{roast_id}/stop")


def test_delete_event_via_api_cold(client):
    # Simulates a backend restart since this roast finished -- same trick
    # test_roast_created_before_this_field_existed_has_no_attribution
    # already uses: pop the in-memory session, forcing every read/write
    # through storage + the .alog file alone. Native .alog
    # milestones don't carry the original in-memory UUID across a
    # round-trip -- _extract_named_milestones synthesizes a stable
    # "milestone-{TYPE}" id instead (confirmed in alog_playback/alog_io.py) --
    # so this fetches the *post-pop* id via GET, exactly like a real
    # client viewing a historical roast page would, rather than reusing
    # the stale pre-pop UUID.
    roast_id, event_id = _create_and_mark(client)
    client.post(f"/api/v1/roasts/{roast_id}/stop")
    session_manager.sessions.pop(roast_id, None)
    resp = client.get(f"/api/v1/roasts/{roast_id}")
    cold_event_id = next(e for e in resp.json()["events"] if e["type"] == "DRY_END")["id"]
    assert cold_event_id != event_id  # confirms this test is actually exercising the id-remap, not a no-op

    resp = client.delete(f"/api/v1/roasts/{roast_id}/events/{cold_event_id}")
    assert resp.status_code == 204

    resp = client.get(f"/api/v1/roasts/{roast_id}")
    assert cold_event_id not in [e["id"] for e in resp.json()["events"]]


def test_retime_event_via_api_cold(client):
    roast_id, event_id = _create_and_mark(client)
    client.post(f"/api/v1/roasts/{roast_id}/stop")
    session_manager.sessions.pop(roast_id, None)
    resp = client.get(f"/api/v1/roasts/{roast_id}")
    cold_event = next(e for e in resp.json()["events"] if e["type"] == "DRY_END")

    resp = client.patch(f"/api/v1/roasts/{roast_id}/events/{cold_event['id']}", json={"time_s": cold_event["time_s"]})
    assert resp.status_code == 200

    resp = client.get(f"/api/v1/roasts/{roast_id}")
    updated = next(e for e in resp.json()["events"] if e["id"] == cold_event["id"])
    assert updated["time_s"] == cold_event["time_s"]


def test_delete_event_via_api_rejects_turning_point(client):
    resp = client.post("/api/v1/roasts", json={"title": "TP Roast", "mode": "simulator"})
    roast_id = resp.json()["id"]
    session = session_manager.get(roast_id)
    mark_auto(session, RoastEventType.TURNING_POINT)

    resp = client.delete(f"/api/v1/roasts/{roast_id}/events/TURNING_POINT")
    assert resp.status_code == 409

    client.post(f"/api/v1/roasts/{roast_id}/stop")


def test_events_endpoints_404_for_unknown_roast(client):
    resp = client.delete("/api/v1/roasts/does-not-exist/events/also-does-not-exist")
    assert resp.status_code == 404

    resp = client.patch("/api/v1/roasts/does-not-exist/events/also-does-not-exist", json={"time_s": 0.0})
    assert resp.status_code == 404
