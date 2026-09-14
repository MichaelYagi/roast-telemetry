"""RoastSession's milestone-triggered automation (Artisan-style "Alarms")
-- add_event() firing/scheduling a bound ControlCommand when a matching
AlarmRule's trigger is marked, modbus_live only, delay_s=0 (immediate) vs
>0 (scheduled + cancellable on abort), and action failures never
propagating out of add_event().

Builds a SIMULATOR-mode RoastSession (cheap, no real hardware -- see
test_roast_session.py's identical rationale) then overrides `.mode` to
MODBUS_LIVE directly, since alarm-firing is gated on that but the actual
device/engine underneath doesn't matter for these tests -- apply_command
itself is replaced with a call-recording stub, not exercised for real.
No pytest-asyncio in this project -- each test drives its own async body
via asyncio.run() from an ordinary sync test function.
"""
from __future__ import annotations

import asyncio

from backend.app.models import AlarmRule, EventCreateRequest, RoastCreateRequest, RoastEventType, RoastMode, RoastStatus
from backend.app.roast_session.session import RoastSession, RoastSessionError


def make_recording_session(alarms=None) -> RoastSession:
    request = RoastCreateRequest(title="Test Roast", mode=RoastMode.SIMULATOR, alarms=alarms or [])
    session = RoastSession("test-roast-id", request)
    session.mode = RoastMode.MODBUS_LIVE  # alarm-firing is gated on this
    session.status = RoastStatus.ROASTING  # add_event requires an active recording
    return session


def test_immediate_rule_fires_apply_command_synchronously():
    rule = AlarmRule(trigger=RoastEventType.CHARGE, delay_s=0, drum_speed_pct=55.0)
    session = make_recording_session([rule])
    calls = []
    session.apply_command = lambda cmd: calls.append(cmd)

    session.add_event(EventCreateRequest(type=RoastEventType.CHARGE, label="Charge"))

    assert len(calls) == 1
    assert calls[0].drum_speed_pct == 55.0


def test_rule_bound_to_a_different_trigger_does_not_fire():
    rule = AlarmRule(trigger=RoastEventType.DROP, delay_s=0, heater_pct=0.0)
    session = make_recording_session([rule])
    calls = []
    session.apply_command = lambda cmd: calls.append(cmd)

    session.add_event(EventCreateRequest(type=RoastEventType.CHARGE, label="Charge"))

    assert calls == []


def test_delayed_rule_does_not_fire_immediately_but_does_after_the_delay():
    rule = AlarmRule(trigger=RoastEventType.DROP, delay_s=0.05, heater_pct=0.0, fan_pct=100.0)
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
    rule = AlarmRule(trigger=RoastEventType.DROP, delay_s=5.0, heater_pct=0.0)
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
    rule = AlarmRule(trigger=RoastEventType.CHARGE, delay_s=0, heater_pct=50.0)
    session = make_recording_session([rule])

    def boom(cmd):
        raise RoastSessionError("device write failed")

    session.apply_command = boom

    event = session.add_event(EventCreateRequest(type=RoastEventType.CHARGE, label="Charge"))

    # The milestone itself is still recorded despite the bound action failing.
    assert event["type"] == "CHARGE"


def test_multiple_rules_on_the_same_trigger_all_fire():
    rules = [
        AlarmRule(trigger=RoastEventType.CHARGE, delay_s=0, drum_speed_pct=55.0),
        AlarmRule(trigger=RoastEventType.CHARGE, delay_s=0, fan_pct=10.0),
    ]
    session = make_recording_session(rules)
    calls = []
    session.apply_command = lambda cmd: calls.append(cmd)

    session.add_event(EventCreateRequest(type=RoastEventType.CHARGE, label="Charge"))

    assert len(calls) == 2


def test_ms6514_live_never_evaluates_alarms():
    # No write capability at all for this mode -- alarms are effectively
    # meaningless there, and add_event() must never try to fire one.
    rule = AlarmRule(trigger=RoastEventType.CHARGE, delay_s=0, heater_pct=50.0)
    request = RoastCreateRequest(title="Test Roast", mode=RoastMode.SIMULATOR, alarms=[rule])
    session = RoastSession("test-roast-id", request)
    session.mode = RoastMode.MS6514_LIVE
    session.status = RoastStatus.ROASTING
    calls = []
    session.apply_command = lambda cmd: calls.append(cmd)

    session.add_event(EventCreateRequest(type=RoastEventType.CHARGE, label="Charge"))

    assert calls == []
