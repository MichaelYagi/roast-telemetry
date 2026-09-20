"""RoastSession's automation rules ("alarms") -- event,
temperature, and time triggers all fire a bound ControlCommand (and/or
publish an optional banner message), modbus_live only, delay_s=0
(fires as soon as the condition is met) vs >0 (scheduled + cancellable
on abort), and action failures never propagating out of add_event().

Builds a SIMULATOR-mode RoastSession (cheap, no real hardware -- see
test_roast_session.py's identical rationale) then overrides `.mode` to
MODBUS_LIVE directly, since alarm-firing is gated on that but the actual
device/engine underneath doesn't matter for these tests -- apply_command
itself is replaced with a call-recording stub, not exercised for real.

Every rule -- including delay_s=0 ones -- now fires via
asyncio.create_task (see RoastSession._schedule_rule), not a direct
synchronous call, since a rule's optional `message` needs a pubsub
publish regardless of timing. That means every test here needs a
running event loop and a brief await after add_event()/
_evaluate_ambient_alarms() for the scheduled task to actually run, even
for "immediate" rules -- there's no truly synchronous path left. No
pytest-asyncio in this project -- each test drives its own async body
via asyncio.run() from an ordinary sync test function.
"""
from __future__ import annotations

import asyncio

from backend.app.models import AlarmRule, AlarmTriggerKind, EventCreateRequest, RoastCreateRequest, RoastEventType, RoastMode, RoastStatus
from backend.app.roast_session.session import RoastSession, RoastSessionError
from backend.app.ws_manager import pubsub


def make_recording_session(alarms=None) -> RoastSession:
    request = RoastCreateRequest(title="Test Roast", mode=RoastMode.SIMULATOR, alarms=alarms or [])
    session = RoastSession("test-roast-id", request)
    session.mode = RoastMode.MODBUS_LIVE  # alarm-firing is gated on this
    session.status = RoastStatus.ROASTING  # add_event requires an active recording
    return session


def event_rule(event_type, **kwargs) -> AlarmRule:
    return AlarmRule(trigger_kind=AlarmTriggerKind.EVENT, event_type=event_type, **kwargs)


def test_immediate_rule_fires_apply_command():
    rule = event_rule(RoastEventType.CHARGE, delay_s=0, drum_speed_pct=55.0)
    session = make_recording_session([rule])
    calls = []
    session.apply_command = lambda cmd: calls.append(cmd)

    async def body():
        session.add_event(EventCreateRequest(type=RoastEventType.CHARGE, label="Charge"))
        await asyncio.sleep(0.05)
        assert len(calls) == 1
        assert calls[0].drum_speed_pct == 55.0

    asyncio.run(body())


def test_rule_bound_to_a_different_trigger_does_not_fire():
    rule = event_rule(RoastEventType.DROP, delay_s=0, heater_pct=0.0)
    session = make_recording_session([rule])
    calls = []
    session.apply_command = lambda cmd: calls.append(cmd)

    async def body():
        session.add_event(EventCreateRequest(type=RoastEventType.CHARGE, label="Charge"))
        await asyncio.sleep(0.05)
        assert calls == []

    asyncio.run(body())


def test_delayed_rule_does_not_fire_immediately_but_does_after_the_delay():
    rule = event_rule(RoastEventType.DROP, delay_s=0.05, heater_pct=0.0, fan_pct=100.0)
    session = make_recording_session([rule])
    calls = []
    session.apply_command = lambda cmd: calls.append(cmd)

    async def body():
        session.add_event(EventCreateRequest(type=RoastEventType.DROP, label="Drop"))
        assert calls == []  # not yet -- still delayed
        await asyncio.sleep(0.1)
        assert len(calls) == 1
        assert calls[0].heater_pct == 0.0
        assert calls[0].fan_pct == 100.0

    asyncio.run(body())


def test_abort_cancels_a_pending_delayed_rule_before_it_fires():
    rule = event_rule(RoastEventType.DROP, delay_s=5.0, heater_pct=0.0)
    session = make_recording_session([rule])
    calls = []
    session.apply_command = lambda cmd: calls.append(cmd)

    async def body():
        session.add_event(EventCreateRequest(type=RoastEventType.DROP, label="Drop"))
        assert len(session._pending_alarm_tasks) == 1

        session._cancel_pending_alarms()
        await asyncio.sleep(0.05)  # let the cancellation actually land

        assert calls == []

    asyncio.run(body())


def test_a_failing_action_does_not_break_add_event():
    rule = event_rule(RoastEventType.CHARGE, delay_s=0, heater_pct=50.0)
    session = make_recording_session([rule])

    def boom(cmd):
        raise RoastSessionError("device write failed")

    session.apply_command = boom

    async def body():
        event = session.add_event(EventCreateRequest(type=RoastEventType.CHARGE, label="Charge"))
        # The milestone itself is recorded synchronously, before the bound
        # action's own (failing) task ever runs.
        assert event["type"] == "CHARGE"
        # Let the scheduled task actually run and fail -- confirms the
        # failure itself doesn't raise anywhere unhandled either (no
        # exception propagating out of asyncio.run/body below would mean
        # this failed silently, which is exactly what's being tested).
        await asyncio.sleep(0.05)

    asyncio.run(body())


def test_multiple_rules_on_the_same_trigger_all_fire():
    rules = [
        event_rule(RoastEventType.CHARGE, delay_s=0, drum_speed_pct=55.0),
        event_rule(RoastEventType.CHARGE, delay_s=0, fan_pct=10.0),
    ]
    session = make_recording_session(rules)
    calls = []
    session.apply_command = lambda cmd: calls.append(cmd)

    async def body():
        session.add_event(EventCreateRequest(type=RoastEventType.CHARGE, label="Charge"))
        await asyncio.sleep(0.05)
        assert len(calls) == 2

    asyncio.run(body())


def test_ms6514_live_never_evaluates_alarms():
    # No write capability at all for this mode -- alarms are effectively
    # meaningless there, and add_event() must never try to fire one.
    rule = event_rule(RoastEventType.CHARGE, delay_s=0, heater_pct=50.0)
    request = RoastCreateRequest(title="Test Roast", mode=RoastMode.SIMULATOR, alarms=[rule])
    session = RoastSession("test-roast-id", request)
    session.mode = RoastMode.MS6514_LIVE
    session.status = RoastStatus.ROASTING
    calls = []
    session.apply_command = lambda cmd: calls.append(cmd)

    session.add_event(EventCreateRequest(type=RoastEventType.CHARGE, label="Charge"))

    assert calls == []


def test_temperature_rule_fires_once_threshold_is_crossed():
    rule = AlarmRule(trigger_kind=AlarmTriggerKind.TEMPERATURE, channel="bt", threshold_c=200.0, delay_s=0, fan_pct=80.0)
    session = make_recording_session([rule])
    calls = []
    session.apply_command = lambda cmd: calls.append(cmd)

    async def body():
        session._evaluate_ambient_alarms({"bt": 150.0, "time_s": 1.0})
        await asyncio.sleep(0.05)
        assert calls == []  # below threshold -- not crossed yet

        session._evaluate_ambient_alarms({"bt": 205.0, "time_s": 2.0})
        await asyncio.sleep(0.05)
        assert len(calls) == 1
        assert calls[0].fan_pct == 80.0

    asyncio.run(body())


def test_ambient_rule_does_not_refire_while_condition_stays_true():
    rule = AlarmRule(trigger_kind=AlarmTriggerKind.TEMPERATURE, channel="bt", threshold_c=200.0, delay_s=0, fan_pct=80.0)
    session = make_recording_session([rule])
    calls = []
    session.apply_command = lambda cmd: calls.append(cmd)

    async def body():
        session._evaluate_ambient_alarms({"bt": 205.0, "time_s": 1.0})
        session._evaluate_ambient_alarms({"bt": 210.0, "time_s": 2.0})
        session._evaluate_ambient_alarms({"bt": 215.0, "time_s": 3.0})
        await asyncio.sleep(0.05)
        assert len(calls) == 1  # not 3 -- one-shot per roast

    asyncio.run(body())


def test_time_rule_fires_once_elapsed_time_is_reached():
    rule = AlarmRule(trigger_kind=AlarmTriggerKind.TIME, at_time_s=300.0, delay_s=0, heater_pct=0.0)
    session = make_recording_session([rule])
    calls = []
    session.apply_command = lambda cmd: calls.append(cmd)

    async def body():
        session._evaluate_ambient_alarms({"bt": 180.0, "time_s": 299.0})
        await asyncio.sleep(0.05)
        assert calls == []

        session._evaluate_ambient_alarms({"bt": 181.0, "time_s": 300.0})
        await asyncio.sleep(0.05)
        assert len(calls) == 1

    asyncio.run(body())


def test_alarm_fired_publishes_the_rules_message():
    rule = event_rule(RoastEventType.FC_START, delay_s=0, fan_pct=40.0, message="First crack -- airflow up")
    session = make_recording_session([rule])
    session.apply_command = lambda cmd: None

    async def body():
        queue = pubsub.subscribe(session.id)
        try:
            session.add_event(EventCreateRequest(type=RoastEventType.FC_START, label="FC Start"))
            message = await asyncio.wait_for(queue.get(), timeout=1.0)
            assert message["type"] == "alarm_fired"
            assert message["message"] == "First crack -- airflow up"
        finally:
            pubsub.unsubscribe(session.id, queue)

    asyncio.run(body())


def test_mark_milestone_auto_marks_the_target_and_publishes_it():
    rule = event_rule(RoastEventType.CHARGE, delay_s=0, mark_milestone=RoastEventType.DRY_END)
    session = make_recording_session([rule])
    session.apply_command = lambda cmd: None
    session.profile = [{"time_s": 30.0, "bt": 150.0}]

    async def body():
        queue = pubsub.subscribe(session.id)
        try:
            session.add_event(EventCreateRequest(type=RoastEventType.CHARGE, label="Charge"))
            message = await asyncio.wait_for(queue.get(), timeout=1.0)
            assert message["type"] == "event"
            assert message["event"]["type"] == RoastEventType.DRY_END.value
            assert message["event"]["value"] == 150.0
            assert message["event"]["label"] == "Dry End"
            types = {e["type"] for e in session.events}
            assert types == {RoastEventType.CHARGE.value, RoastEventType.DRY_END.value}
        finally:
            pubsub.unsubscribe(session.id, queue)

    asyncio.run(body())


def test_mark_milestone_out_of_sequence_silently_fails():
    # DRY_END is already marked by the time this rule's trigger (a later
    # FC_START click) fires -- add_event's own "already marked" guard
    # rejects the auto-mark, and that RoastSessionError must not
    # propagate out of the firing task.
    rule = event_rule(RoastEventType.FC_START, delay_s=0, mark_milestone=RoastEventType.DRY_END)
    session = make_recording_session([rule])
    session.apply_command = lambda cmd: None
    session.events.append({"id": "x", "time_s": 10.0, "type": RoastEventType.DRY_END.value, "label": "Dry End", "value": 160.0})

    async def body():
        session.add_event(EventCreateRequest(type=RoastEventType.FC_START, label="FC Start"))
        await asyncio.sleep(0.05)
        types = [e["type"] for e in session.events]
        assert types.count(RoastEventType.DRY_END.value) == 1  # not duplicated

    asyncio.run(body())


def test_mark_milestone_works_with_a_delayed_rule_too():
    rule = event_rule(RoastEventType.CHARGE, delay_s=0.05, mark_milestone=RoastEventType.DRY_END)
    session = make_recording_session([rule])
    session.apply_command = lambda cmd: None

    async def body():
        session.add_event(EventCreateRequest(type=RoastEventType.CHARGE, label="Charge"))
        await asyncio.sleep(0.15)
        types = {e["type"] for e in session.events}
        assert RoastEventType.DRY_END.value in types

    asyncio.run(body())


def test_disabled_event_rule_does_not_fire():
    rule = event_rule(RoastEventType.CHARGE, delay_s=0, drum_speed_pct=55.0, enabled=False)
    session = make_recording_session([rule])
    calls = []
    session.apply_command = lambda cmd: calls.append(cmd)

    async def body():
        session.add_event(EventCreateRequest(type=RoastEventType.CHARGE, label="Charge"))
        await asyncio.sleep(0.05)
        assert calls == []

    asyncio.run(body())


def test_disabled_ambient_rule_does_not_fire():
    rule = AlarmRule(trigger_kind=AlarmTriggerKind.TEMPERATURE, channel="bt", threshold_c=200.0, delay_s=0, fan_pct=80.0, enabled=False)
    session = make_recording_session([rule])
    calls = []
    session.apply_command = lambda cmd: calls.append(cmd)

    async def body():
        session._evaluate_ambient_alarms({"bt": 205.0, "time_s": 1.0})
        await asyncio.sleep(0.05)
        assert calls == []

    asyncio.run(body())


def test_rule_defaults_to_enabled_when_field_omitted():
    # An older saved config's rules never had `enabled` at all -- must
    # default to firing normally, not silently go inert.
    rule = event_rule(RoastEventType.CHARGE, delay_s=0, drum_speed_pct=55.0)
    assert rule.enabled is True


def test_delay_s_zero_still_publishes_alarm_fired():
    # Immediate rules used to skip publishing entirely as an optimization
    # -- no longer valid now that a message needs a way to reach the
    # frontend regardless of timing.
    rule = event_rule(RoastEventType.CHARGE, delay_s=0, drum_speed_pct=55.0)
    session = make_recording_session([rule])
    session.apply_command = lambda cmd: None

    async def body():
        queue = pubsub.subscribe(session.id)
        try:
            session.add_event(EventCreateRequest(type=RoastEventType.CHARGE, label="Charge"))
            message = await asyncio.wait_for(queue.get(), timeout=1.0)
            assert message["type"] == "alarm_fired"
        finally:
            pubsub.unsubscribe(session.id, queue)

    asyncio.run(body())
