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

from backend.app import storage
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


def test_two_sequential_cold_edits_reuse_ids_from_the_first_get(client):
    # A client (e.g. the mobile app) that fetches a cold roast once, then
    # makes two edits in a row against the *same* ids from that one GET --
    # without refetching between them -- must not have the second edit
    # rejected with "event not found" just because the first edit rewrote
    # the .alog file. Confirmed this used to fail: repeated cold rewrites
    # compounded a synthetic lead-in sample (see alog_io.py's
    # roast_to_native_alog_dict), which silently shifted milestones'
    # nearest-sample matches enough that a roast with several milestones
    # close together could lose/reassign one between rewrites.
    resp = client.post("/api/v1/roasts", json={"title": "Multi-Edit Roast", "mode": "simulator"})
    roast_id = resp.json()["id"]
    for event_type in ("DRY_END", "FC_START", "FC_END"):
        time.sleep(1.1)
        client.post(f"/api/v1/roasts/{roast_id}/events", json={"type": event_type, "label": event_type})
    client.post(f"/api/v1/roasts/{roast_id}/stop")
    session_manager.sessions.pop(roast_id, None)

    resp = client.get(f"/api/v1/roasts/{roast_id}")
    events_by_type = {e["type"]: e for e in resp.json()["events"]}
    dry_end_id = events_by_type["DRY_END"]["id"]
    fc_end_id = events_by_type["FC_END"]["id"]
    fc_start_time = events_by_type["FC_START"]["time_s"]
    fc_end_time = events_by_type["FC_END"]["time_s"]
    # A whole second (matching the simulator's 1s sample interval) so it
    # lands exactly on a real sample -- avoids the unrelated, pre-existing
    # "retime snaps to the nearest recorded sample" behavior muddying what
    # this test is actually checking.
    new_fc_end_time = float(round((fc_start_time + fc_end_time) / 2))

    # Edit #1, using the id from the one GET above.
    resp = client.delete(f"/api/v1/roasts/{roast_id}/events/{dry_end_id}")
    assert resp.status_code == 204

    # Edit #2, using an id from that *same* original GET -- not a fresh
    # one fetched after edit #1's rewrite.
    resp = client.patch(f"/api/v1/roasts/{roast_id}/events/{fc_end_id}", json={"time_s": new_fc_end_time})
    assert resp.status_code == 200, resp.json()

    resp = client.get(f"/api/v1/roasts/{roast_id}")
    final = {e["type"]: e for e in resp.json()["events"]}
    assert "DRY_END" not in final
    assert final["FC_END"]["time_s"] == new_fc_end_time


def test_events_endpoints_404_for_unknown_roast(client):
    resp = client.delete("/api/v1/roasts/does-not-exist/events/also-does-not-exist")
    assert resp.status_code == 404

    resp = client.patch("/api/v1/roasts/does-not-exist/events/also-does-not-exist", json={"time_s": 0.0})
    assert resp.status_code == 404


# -- the same milestones before and after a restart ------------------------


def test_finished_roast_shows_the_same_milestones_before_and_after_a_restart(client):
    # The simulator fires Charge at 0.0 s, before its first sample (1.0 s)
    # exists, and the .alog file can only store a milestone as a sample --
    # so the file holds Charge at 1.0 s. A finished roast must show what
    # the file holds from the start, not change when the server restarts.
    roast_id, _ = _create_and_mark(client)
    time.sleep(1.2)
    client.post(f"/api/v1/roasts/{roast_id}/events", json={"type": "DROP", "label": "Drop"})
    client.post(f"/api/v1/roasts/{roast_id}/stop")
    warm = client.get(f"/api/v1/roasts/{roast_id}").json()

    session_manager.sessions.pop(roast_id, None)
    cold = client.get(f"/api/v1/roasts/{roast_id}").json()

    def milestones(roast):
        return {e["type"]: (e["time_s"], e["value"]) for e in roast["events"] if e["type"] != "CUSTOM"}

    assert milestones(warm) == milestones(cold)
    assert {"CHARGE", "DRY_END", "DROP"} <= milestones(warm).keys()
    assert warm["duration_s"] == cold["duration_s"]
    assert storage.get_roast_row(roast_id)["duration_s"] == cold["duration_s"]


def test_retime_snaps_to_the_nearest_sample():
    session = make_session()
    feed_profile(session)  # samples every 30 s
    event = session.add_event(EventCreateRequest(type=RoastEventType.DRY_END, label="Dry End"))

    updated = session.retime_event(event["id"], 100.0)

    assert updated["time_s"] == 90.0
    assert updated["value"] == 105.0


def test_retime_error_names_the_milestones():
    session = make_session()
    feed_profile(session)
    dry_end = session.add_event(EventCreateRequest(type=RoastEventType.DRY_END, label="Dry End"))
    dry_end["time_s"] = 60.0
    fc_start = session.add_event(EventCreateRequest(type=RoastEventType.FC_START, label="FC Start"))
    fc_start["time_s"] = 180.0

    try:
        session.retime_event(dry_end["id"], 210.0)
        assert False, "expected RoastSessionError"
    except RoastSessionError as exc:
        assert str(exc) == "Dry End can't be moved past FC Start"


def test_turning_point_error_names_it():
    session = make_session()
    mark_auto(session, RoastEventType.TURNING_POINT)

    try:
        session.delete_event(session.events[0]["id"])
        assert False, "expected RoastSessionError"
    except RoastSessionError as exc:
        assert str(exc) == "Turning Point is detected automatically -- it can't be edited"


def test_activity_log_names_the_milestone_and_times(client):
    roast_id, event_id = _create_and_mark(client)
    client.post(f"/api/v1/roasts/{roast_id}/stop")
    dry_end = next(e for e in client.get(f"/api/v1/roasts/{roast_id}").json()["events"] if e["id"] == event_id)
    target = dry_end["time_s"] - 1.0

    assert client.patch(f"/api/v1/roasts/{roast_id}/events/{event_id}", json={"time_s": target}).status_code == 200
    assert client.delete(f"/api/v1/roasts/{roast_id}/events/{event_id}").status_code == 204

    def mmss(t):
        return f"{int(round(t)) // 60}:{int(round(t)) % 60:02d}"

    messages = [r["message"] for r in storage.list_activity() if r["roast_id"] == roast_id]
    assert f'Moved Dry End from {mmss(dry_end["time_s"])} to {mmss(target)} on "Editable Roast"' in messages
    assert f'Deleted Dry End (was at {mmss(target)}) on "Editable Roast"' in messages


# -- adding a milestone to a finished roast ---------------------------------


def test_add_milestone_rejected_while_recording():
    session = make_session()  # ROASTING
    feed_profile(session)

    try:
        session.add_milestone_at(RoastEventType.FC_START, 90.0)
        assert False, "expected RoastSessionError"
    except RoastSessionError as exc:
        assert "still recording" in str(exc)


def test_add_milestone_checks_type_and_order():
    from backend.app.roast_session.session import _add_milestone_at

    profile = [{"time_s": float(t * 30), "bt": 90.0 + t * 5} for t in range(10)]  # 0..270
    events = [
        {"id": "a", "type": "DRY_END", "time_s": 60.0, "label": "Dry End", "value": None},
        {"id": "b", "type": "FC_END", "time_s": 180.0, "label": "FC End", "value": None},
        {"id": "c", "type": "TURNING_POINT", "time_s": 30.0, "label": "Turning Point", "value": None},
    ]
    for event_type, time_s, message in [
        (RoastEventType.TURNING_POINT, 90.0, "Turning Point is detected automatically -- it can't be added by hand"),
        (RoastEventType.DRY_END, 90.0, "Dry End is already marked on this roast -- move it instead"),
        (RoastEventType.FC_START, 210.0, "FC Start can't be added past FC End"),
        (RoastEventType.FC_START, 40.0, "FC Start can't be added before Dry End"),
    ]:
        try:
            _add_milestone_at(events, profile, event_type, time_s)
            assert False, f"expected RoastSessionError for {event_type}"
        except RoastSessionError as exc:
            assert str(exc) == message

    added = _add_milestone_at(events, profile, RoastEventType.FC_START, 100.0)  # nearest sample: 90 s
    assert (added["time_s"], added["value"], added["label"]) == (90.0, 105.0, "First Crack Start")
    assert added in events


def _finished_roast(client) -> tuple[str, dict]:
    roast_id, _ = _create_and_mark(client)  # Charge (auto) + Dry End
    time.sleep(2.2)
    client.post(f"/api/v1/roasts/{roast_id}/stop")
    return roast_id, client.get(f"/api/v1/roasts/{roast_id}").json()


def test_add_milestone_to_a_finished_roast_warm(client):
    roast_id, roast = _finished_roast(client)
    last = roast["profile"][-1]["time_s"]

    resp = client.post(f"/api/v1/roasts/{roast_id}/events", json={"type": "FC_START", "label": "x", "time_s": last})
    assert resp.status_code == 200
    assert resp.json()["label"] == "First Crack Start"  # the server names it, not the request
    assert resp.json()["time_s"] == last

    events = client.get(f"/api/v1/roasts/{roast_id}").json()["events"]
    assert [e["time_s"] for e in events if e["type"] == "FC_START"] == [last]
    messages = [r["message"] for r in storage.list_activity() if r["roast_id"] == roast_id]
    minutes, seconds = divmod(int(round(last)), 60)
    assert f'Added FC Start at {minutes}:{seconds:02d} on "Editable Roast"' in messages

    again = client.post(f"/api/v1/roasts/{roast_id}/events", json={"type": "FC_START", "label": "x", "time_s": last})
    assert again.status_code == 409


def test_add_milestone_to_a_finished_roast_cold(client):
    roast_id, roast = _finished_roast(client)
    last = roast["profile"][-1]["time_s"]
    session_manager.sessions.pop(roast_id, None)  # as after a server restart

    resp = client.post(f"/api/v1/roasts/{roast_id}/events", json={"type": "DROP", "label": "Drop", "time_s": last})
    assert resp.status_code == 200
    assert resp.json()["id"] == "milestone-DROP"  # the id the next GET will show

    cold = client.get(f"/api/v1/roasts/{roast_id}").json()
    drop = next(e for e in cold["events"] if e["type"] == "DROP")
    charge = next(e for e in cold["events"] if e["type"] == "CHARGE")
    assert drop["id"] == "milestone-DROP" and drop["time_s"] == last
    assert cold["duration_s"] == round(last - charge["time_s"], 1)  # Charge to the new Drop
    # It didn't reach Drop live (this roast was stopped with only Charge
    # and Dry End marked) -- adding Drop by hand afterward should clear
    # the "Incomplete" badge live, not leave it stuck at whatever
    # _finish() decided at the time.
    assert cold["reached_drop"] is True


def test_add_milestone_drop_sets_reached_drop_warm(client):
    roast_id, roast = _finished_roast(client)  # stopped with only Charge + Dry End marked
    assert client.get(f"/api/v1/roasts/{roast_id}").json()["reached_drop"] is False
    last = roast["profile"][-1]["time_s"]

    resp = client.post(f"/api/v1/roasts/{roast_id}/events", json={"type": "DROP", "label": "x", "time_s": last})
    assert resp.status_code == 200

    assert client.get(f"/api/v1/roasts/{roast_id}").json()["reached_drop"] is True


def test_deleting_drop_resets_reached_drop_warm(client):
    resp = client.post("/api/v1/roasts", json={"title": "Reached Drop Roast", "mode": "simulator"})
    roast_id = resp.json()["id"]
    drop = client.post(f"/api/v1/roasts/{roast_id}/events", json={"type": "DROP", "label": "Drop"}).json()
    client.post(f"/api/v1/roasts/{roast_id}/stop")
    assert client.get(f"/api/v1/roasts/{roast_id}").json()["reached_drop"] is True

    resp = client.delete(f"/api/v1/roasts/{roast_id}/events/{drop['id']}")
    assert resp.status_code == 204

    assert client.get(f"/api/v1/roasts/{roast_id}").json()["reached_drop"] is False


def test_deleting_drop_resets_reached_drop_cold(client):
    resp = client.post("/api/v1/roasts", json={"title": "Reached Drop Roast", "mode": "simulator"})
    roast_id = resp.json()["id"]
    client.post(f"/api/v1/roasts/{roast_id}/events", json={"type": "DROP", "label": "Drop"})
    client.post(f"/api/v1/roasts/{roast_id}/stop")
    session_manager.sessions.pop(roast_id, None)  # as after a server restart
    cold_drop_id = next(e for e in client.get(f"/api/v1/roasts/{roast_id}").json()["events"] if e["type"] == "DROP")["id"]

    resp = client.delete(f"/api/v1/roasts/{roast_id}/events/{cold_drop_id}")
    assert resp.status_code == 204

    assert client.get(f"/api/v1/roasts/{roast_id}").json()["reached_drop"] is False


def test_marking_without_a_time_still_needs_a_recording_roast(client):
    roast_id, _ = _finished_roast(client)
    session_manager.sessions.pop(roast_id, None)

    resp = client.post(f"/api/v1/roasts/{roast_id}/events", json={"type": "FC_START", "label": "FC Start"})
    assert resp.status_code == 404


# -- reset to the original recording -------------------------------------


def test_finished_roast_carries_its_original_events(client):
    # Captured once, at the moment the roast finished -- before any of
    # this test's own edits -- so it should exactly match what GET showed
    # right after stopping, untouched by anything that happens later.
    roast_id, roast = _finished_roast(client)
    assert roast["original_events"] is not None
    assert {(e["type"], e["time_s"]) for e in roast["original_events"]} == {(e["type"], e["time_s"]) for e in roast["events"]}


def test_reset_events_restores_deleted_and_retimed_milestones_warm(client):
    roast_id, roast = _finished_roast(client)  # Charge (auto) + Dry End
    original = roast["original_events"]
    dry_end = next(e for e in roast["events"] if e["type"] == "DRY_END")

    client.delete(f"/api/v1/roasts/{roast_id}/events/{dry_end['id']}")
    assert "DRY_END" not in [e["type"] for e in client.get(f"/api/v1/roasts/{roast_id}").json()["events"]]

    resp = client.post(f"/api/v1/roasts/{roast_id}/events/reset")
    assert resp.status_code == 200

    restored = client.get(f"/api/v1/roasts/{roast_id}").json()
    assert {(e["type"], e["time_s"]) for e in restored["events"]} == {(e["type"], e["time_s"]) for e in original}


def test_reset_events_restores_deleted_milestones_cold(client):
    roast_id, roast = _finished_roast(client)
    original = roast["original_events"]
    dry_end = next(e for e in roast["events"] if e["type"] == "DRY_END")
    client.delete(f"/api/v1/roasts/{roast_id}/events/{dry_end['id']}")
    session_manager.sessions.pop(roast_id, None)  # as after a server restart

    resp = client.post(f"/api/v1/roasts/{roast_id}/events/reset")
    assert resp.status_code == 200

    restored = client.get(f"/api/v1/roasts/{roast_id}").json()
    assert {(e["type"], e["time_s"]) for e in restored["events"]} == {(e["type"], e["time_s"]) for e in original}


def test_reset_events_recomputes_duration_and_reached_drop(client):
    roast_id, roast = _finished_roast(client)  # stopped before Drop
    last = roast["profile"][-1]["time_s"]
    client.post(f"/api/v1/roasts/{roast_id}/events", json={"type": "DROP", "label": "x", "time_s": last})
    assert client.get(f"/api/v1/roasts/{roast_id}").json()["reached_drop"] is True

    resp = client.post(f"/api/v1/roasts/{roast_id}/events/reset")
    assert resp.status_code == 200

    restored = client.get(f"/api/v1/roasts/{roast_id}").json()
    assert restored["reached_drop"] is False
    assert "DROP" not in [e["type"] for e in restored["events"]]


def test_reset_events_rejects_a_roast_with_no_original_snapshot(client):
    # Simulates a roast that finished before original_events_json existed --
    # storage still has the row, but with that column NULL, same as any
    # pre-migration roast.
    roast_id, _ = _finished_roast(client)
    storage.update_roast(roast_id, original_events_json=None)

    resp = client.post(f"/api/v1/roasts/{roast_id}/events/reset")
    assert resp.status_code == 409


def test_reset_events_404_for_unknown_roast(client):
    resp = client.post("/api/v1/roasts/does-not-exist/events/reset")
    assert resp.status_code == 404
