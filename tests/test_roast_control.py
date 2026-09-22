"""Safety limits, emergency stop, fail-safes, replaying a saved roast and
holding a target. The calculations are tested on their own; the session-level
tests use a recording fake in place of the roaster."""
from __future__ import annotations

import asyncio

import pytest

from backend.app.models import (
    ControlCommand,
    ControlSafety,
    CurvePoint,
    FeedbackRequest,
    ProgramStep,
    RoastCreateRequest,
    RoastMode,
    RoastStatus,
)
from backend.app.roast_control import (
    FeedbackController,
    ProgramRunner,
    apply_limits,
    program_from_profile,
    safe_state_command,
)
from backend.app.roast_session import control as control_module
from backend.app.roast_session.session import RoastSession, RoastSessionError
from backend.app.ws_manager import pubsub


# -- limits -------------------------------------------------------------------


def test_heater_is_capped():
    limits = ControlSafety(heater_max_pct=80)
    assert apply_limits({"heater_pct": 95}, limits, {})["heater_pct"] == 80
    assert apply_limits({"heater_pct": 40}, limits, {})["heater_pct"] == 40


def test_turning_the_heater_on_raises_a_low_fan():
    limits = ControlSafety(fan_min_pct=30)
    out = apply_limits({"heater_pct": 50}, limits, {"fan_pct": 10})
    assert out == {"heater_pct": 50, "fan_pct": 30}


def test_fan_is_raised_when_it_is_unknown_too():
    out = apply_limits({"heater_pct": 50}, ControlSafety(fan_min_pct=30), {})
    assert out["fan_pct"] == 30


def test_lowering_the_fan_while_heating_is_held_at_the_minimum():
    out = apply_limits({"fan_pct": 5}, ControlSafety(fan_min_pct=30), {"heater_pct": 60})
    assert out["fan_pct"] == 30


def test_fan_can_drop_when_the_heater_is_off():
    out = apply_limits({"fan_pct": 5}, ControlSafety(fan_min_pct=30), {"heater_pct": 0})
    assert out["fan_pct"] == 5


def test_drum_minimum_applies_while_heating():
    out = apply_limits({"heater_pct": 20}, ControlSafety(drum_min_pct=40), {"drum_speed_pct": 10})
    assert out["drum_speed_pct"] == 40


def test_default_limits_change_nothing():
    cmd = {"heater_pct": 100, "fan_pct": 0, "drum_speed_pct": 0}
    assert apply_limits(cmd, ControlSafety(), {}) == cmd


def test_safe_state_is_heater_off_fan_high():
    assert safe_state_command(ControlSafety(safe_fan_pct=90)) == {"heater_pct": 0.0, "fan_pct": 90}


# -- replaying a program -----------------------------------------------------


def test_program_holds_each_step_until_the_next():
    runner = ProgramRunner([ProgramStep(time_s=0, heater_pct=80), ProgramStep(time_s=60, heater_pct=50)])
    assert runner.due(-5) == {}
    assert runner.due(0) == {"heater_pct": 80.0}
    assert runner.due(30) == {}  # nothing new to send
    assert runner.due(60) == {"heater_pct": 50.0}


def test_program_ramps_smoothly():
    runner = ProgramRunner([ProgramStep(time_s=0, heater_pct=80), ProgramStep(time_s=100, heater_pct=40, ramp=True)])
    assert runner.due(0) == {"heater_pct": 80.0}
    assert runner.due(50)["heater_pct"] == 60.0
    assert runner.due(100)["heater_pct"] == 40.0


def test_program_does_not_rewrite_for_tiny_changes():
    runner = ProgramRunner([ProgramStep(time_s=0, heater_pct=80), ProgramStep(time_s=100, heater_pct=79.5, ramp=True)], min_delta=1.0)
    runner.due(0)
    assert runner.due(50) == {}
    assert runner.due(100) == {"heater_pct": 79.5}  # final value is still delivered


def test_program_channels_are_independent():
    runner = ProgramRunner([ProgramStep(time_s=0, heater_pct=70), ProgramStep(time_s=30, fan_pct=60)])
    assert runner.due(0) == {"heater_pct": 70.0}
    assert runner.due(30) == {"fan_pct": 60.0}


def test_program_from_a_saved_roast_is_timed_from_charge():
    profile = [
        {"time_s": -10.0, "heater_pct": 10.0, "fan_pct": 20.0},
        {"time_s": 0.0, "heater_pct": 80.0, "fan_pct": 30.0},
        {"time_s": 30.0, "heater_pct": 81.0, "fan_pct": 30.0},  # tiny change: skipped
        {"time_s": 60.0, "heater_pct": 60.0, "fan_pct": 30.0},
        {"time_s": 90.0, "heater_pct": 60.0, "fan_pct": 50.0},
    ]
    steps = program_from_profile(profile, charge_time_s=0.0)
    assert [(s.time_s, s.heater_pct, s.fan_pct) for s in steps] == [(0.0, 80.0, 30.0), (60.0, 60.0, None), (90.0, None, 50.0)]


def test_program_from_a_roast_without_control_data_is_empty():
    assert program_from_profile([{"time_s": 0.0, "heater_pct": None}], 0.0) == []
    # A file that never recorded controls reads back as zeros -- repeating that would kill the heater and fan.
    zeros = [{"time_s": float(t), "heater_pct": 0.0, "fan_pct": 0.0, "drum_speed_pct": 0.0} for t in range(5)]
    assert program_from_profile(zeros, 0.0) == []


# -- holding a target -----------------------------------------------------------


def _simulate(variable, setpoint, seconds=900):
    """A made-up roaster: heat delivered lags the heater; the bean temperature
    climbs with it and loses heat as it gets hotter."""
    controller = FeedbackController(FeedbackRequest(variable=variable, setpoint=setpoint), start_output=50.0, start_time_s=0.0)
    bt, prev, delivered, heater, ror_smooth = 150.0, 150.0, 50.0, 50.0, 0.0
    history = []
    for t in range(1, seconds + 1):
        delivered += (heater - delivered) / 30.0
        bt += (0.35 * delivered - 0.09 * (bt - 20)) / 60.0
        ror = (bt - prev) * 60
        prev = bt
        ror_smooth += (ror - ror_smooth) * 0.2
        out = controller.update(float(t), bt if variable == "bt" else ror_smooth)
        if out is not None:
            heater = out
        history.append((bt, ror_smooth, heater))
    return history


def test_rate_of_rise_is_held_near_the_target():
    history = _simulate("ror_bt", 5.0)
    assert abs(history[-1][1] - 5.0) < 0.5


def test_bean_temperature_settles_near_the_target():
    history = _simulate("bt", 190.0)
    assert abs(history[-1][0] - 190.0) < 3.0


def test_output_stays_inside_the_limits_and_never_jumps():
    controller = FeedbackController(
        FeedbackRequest(variable="bt", setpoint=500, output_min_pct=10, output_max_pct=70, max_step_pct_per_s=2.0),
        start_output=40.0,
        start_time_s=0.0,
    )
    last = 40.0
    for t in range(1, 120):
        out = controller.update(float(t), 100.0)  # far below target: wants full heat
        assert 10 <= out <= 70
        assert abs(out - last) <= 2.0 + 1e-6
        last = out
    assert last == 70


def test_no_reading_or_no_time_means_no_change():
    controller = FeedbackController(FeedbackRequest(variable="bt", setpoint=200), start_output=50.0, start_time_s=0.0)
    assert controller.update(1.0, None) is None
    assert controller.update(0.0, 150.0) is None  # no time has passed


def test_target_can_follow_a_curve():
    config = FeedbackRequest(variable="ror_bt", curve=[CurvePoint(time_s=0, value=10), CurvePoint(time_s=100, value=4)])
    controller = FeedbackController(config, 50.0, 0.0)
    assert controller.setpoint_at(0) == 10
    assert controller.setpoint_at(50) == 7
    assert controller.setpoint_at(500) == 4  # held flat after the last point


# -- session-level ------------------------------------------------------------


class FakeDevice:
    def __init__(self):
        self.writes = []
        self.fail = False

    def write(self, command):
        if self.fail:
            raise RuntimeError("cable unplugged")
        self.writes.append(dict(command))
        return {"ok": True}

    def disconnect(self):
        pass


def make_session(mode=RoastMode.SIMULATOR) -> RoastSession:
    session = RoastSession("control-test", RoastCreateRequest(title="Control", mode=mode))
    session.status = RoastStatus.ROASTING
    session.device = FakeDevice()
    session.control.limits = ControlSafety()
    return session


def feed(session, t, **fields):
    sample = {"time_s": float(t), "bt": 150.0, "ror_bt": 5.0, **fields}
    session.profile.append(sample)
    return sample


def mark_charge(session, t=0.0):
    session.events.append({"id": "c", "type": "CHARGE", "time_s": t, "label": "Charge", "value": None})


def test_operator_commands_pass_through_the_limits():
    session = make_session()
    session.control.limits = ControlSafety(heater_max_pct=70, fan_min_pct=25)
    session.apply_command(ControlCommand(heater_pct=100))
    assert session.device.writes[-1] == {"heater_pct": 70.0, "fan_pct": 25.0}


def test_rule_commands_pass_through_the_limits_too():
    session = make_session()
    session.control.limits = ControlSafety(heater_max_pct=60)
    session.apply_command(ControlCommand(heater_pct=90), source="rule")
    assert session.device.writes[-1]["heater_pct"] == 60


def test_burner_setpoint_is_refused_while_a_heater_limit_is_set():
    session = make_session()
    session.control.limits = ControlSafety(heater_max_pct=70)
    with pytest.raises(RoastSessionError):
        session.apply_command(ControlCommand(burner_sv_c=250))
    assert session.device.writes == []


def test_emergency_stop_turns_the_heater_off_and_stops_automation():
    session = make_session()
    feed(session, 0)
    mark_charge(session)
    session.control.start_program([ProgramStep(time_s=0, heater_pct=80)], "test")

    async def body():
        return await session.control.emergency_stop()

    assert asyncio.run(body()) is True
    assert session.device.writes[-1] == {"heater_pct": 0.0, "fan_pct": 100}
    assert not session.control.automation_active()
    assert session.control.tripped_reason == "emergency stop"
    assert any(e["label"].startswith("Safety:") for e in session.events)


def test_emergency_stop_reports_when_it_could_not_reach_the_roaster():
    session = make_session()
    session.device.fail = True
    feed(session, 0)

    async def body():
        return await session.control.emergency_stop()

    assert asyncio.run(body()) is False
    assert any("couldn't reach" in e["label"] for e in session.events)


def test_program_waits_for_charge_then_runs():
    session = make_session()
    session.control.start_program([ProgramStep(time_s=0, heater_pct=80), ProgramStep(time_s=30, heater_pct=50)], "test")

    async def body():
        await session.control.tick(feed(session, 5), recording=True)
        assert session.device.writes == []  # no Charge yet
        mark_charge(session, 10.0)
        await session.control.tick(feed(session, 10), recording=True)
        await session.control.tick(feed(session, 41), recording=True)

    asyncio.run(body())
    assert [w["heater_pct"] for w in session.device.writes] == [80.0, 50.0]


def test_a_manual_change_takes_over_from_automation():
    session = make_session()
    session.control.start_program([ProgramStep(time_s=0, heater_pct=80)], "test")
    session.apply_command(ControlCommand(heater_pct=30))
    assert not session.control.automation_active()


def test_alarm_rules_do_not_cancel_automation():
    session = make_session()
    session.control.start_program([ProgramStep(time_s=0, heater_pct=80)], "test")
    session.apply_command(ControlCommand(fan_pct=60), source="rule")
    assert session.control.automation_active()


def test_target_control_adjusts_the_heater_after_charge_and_stops_at_drop():
    session = make_session()
    mark_charge(session, 0.0)
    session.control.start_feedback(FeedbackRequest(variable="ror_bt", setpoint=10.0))

    async def body():
        for t in range(1, 30):
            await session.control.tick(feed(session, t, ror_bt=4.0), recording=True)
        assert session.device.writes, "expected the heater to be raised toward the higher target"
        assert session.device.writes[-1]["heater_pct"] > 0
        session.events.append({"id": "d", "type": "DROP", "time_s": 30.0, "label": "Drop", "value": None})
        await session.control.tick(feed(session, 31, ror_bt=4.0), recording=True)

    asyncio.run(body())
    assert session.control.feedback is None


def test_losing_the_temperature_reading_during_target_control_trips_the_fail_safe(monkeypatch):
    monkeypatch.setattr(control_module, "FEEDBACK_NO_READING_LIMIT_S", 0.05)
    session = make_session()
    mark_charge(session, 0.0)
    session.control.start_feedback(FeedbackRequest(variable="bt", setpoint=200.0))

    async def body():
        await session.control.tick(feed(session, 1, bt=None), recording=True)
        await asyncio.sleep(0.1)
        await session.control.tick(feed(session, 2, bt=None), recording=True)

    asyncio.run(body())
    assert session.device.writes[-1] == {"heater_pct": 0.0, "fan_pct": 100}
    assert "temperature reading" in session.control.tripped_reason


def test_watchdog_trips_when_nobody_is_watching_and_the_heater_is_on():
    session = make_session()
    session.control.limits = ControlSafety(client_watchdog_s=0.05)
    session.control._last_written["heater_pct"] = 60.0

    async def body():
        await session.control.tick(feed(session, 1), recording=True)
        await asyncio.sleep(0.1)
        await session.control.tick(feed(session, 2), recording=True)

    asyncio.run(body())
    assert session.device.writes[-1] == {"heater_pct": 0.0, "fan_pct": 100}
    assert "nobody has had the roast open" in session.control.tripped_reason


def test_watchdog_stays_quiet_while_someone_is_watching():
    session = make_session()
    session.control.limits = ControlSafety(client_watchdog_s=0.05)
    session.control._last_written["heater_pct"] = 60.0

    async def body():
        queue = pubsub.subscribe(session.id)
        try:
            await session.control.tick(feed(session, 1), recording=True)
            await asyncio.sleep(0.1)
            await session.control.tick(feed(session, 2), recording=True)
        finally:
            pubsub.unsubscribe(session.id, queue)

    asyncio.run(body())
    assert session.device.writes == []
    assert session.control.tripped_reason is None


def test_watchdog_can_be_turned_off_and_ignores_a_cold_heater():
    session = make_session()
    session.control.limits = ControlSafety(client_watchdog_s=0)
    session.control._last_written["heater_pct"] = 60.0

    async def body():
        await session.control.tick(feed(session, 1), recording=True)
        await asyncio.sleep(0.05)
        await session.control.tick(feed(session, 2), recording=True)

    asyncio.run(body())
    assert session.device.writes == []


def test_read_only_devices_cannot_be_automated():
    session = make_session()
    session.mode = RoastMode.MS6514_LIVE  # (constructing one needs a serial port)
    with pytest.raises(control_module.RoastControlError):
        session.control.start_program([ProgramStep(time_s=0, heater_pct=50)], "test")


def test_ending_the_roast_turns_a_running_heater_off():
    session = make_session()
    session.control._last_written["heater_pct"] = 70.0
    session.control.start_program([ProgramStep(time_s=0, heater_pct=70)], "test")
    session.control.release()
    assert session.device.writes[-1] == {"heater_pct": 0.0}
    assert not session.control.automation_active()


def test_a_failing_write_during_automation_trips_the_fail_safe():
    session = make_session()
    mark_charge(session, 0.0)
    session.control.start_program([ProgramStep(time_s=0, heater_pct=80)], "test")
    session.device.fail = True

    async def body():
        await session.control.tick(feed(session, 1), recording=True)

    asyncio.run(body())
    assert session.control.tripped_reason is not None
    assert not session.control.automation_active()


# -- API ---------------------------------------------------------------------------


def test_control_endpoints_404_for_an_unknown_roast(client):
    assert client.get("/api/roasts/nope/control").status_code == 404
    assert client.post("/api/roasts/nope/emergency-stop").status_code == 404


def test_emergency_stop_and_status_through_the_api(client):
    roast_id = client.post("/api/roasts", json={"title": "E-stop", "mode": "simulator"}).json()["id"]
    status = client.get(f"/api/roasts/{roast_id}/control").json()
    assert status["can_write"] is True and status["program"] is None and status["tripped_reason"] is None

    stopped = client.post(f"/api/roasts/{roast_id}/emergency-stop").json()
    assert stopped["tripped_reason"] == "emergency stop"
    assert client.get(f"/api/roasts/{roast_id}/control").json()["tripped_reason"] == "emergency stop"
    client.post(f"/api/roasts/{roast_id}/stop")


def test_program_and_feedback_through_the_api(client):
    roast_id = client.post("/api/roasts", json={"title": "Auto", "mode": "simulator"}).json()["id"]
    started = client.put(f"/api/roasts/{roast_id}/control/program", json={"steps": [{"time_s": 0, "heater_pct": 60}]})
    assert started.status_code == 200 and started.json()["program"]["steps"] == 1

    fb = client.put(f"/api/roasts/{roast_id}/control/feedback", json={"variable": "ror_bt", "setpoint": 8})
    assert fb.status_code == 200
    assert fb.json()["feedback"]["setpoint"] == 8 and fb.json()["program"] is None  # target control replaced the program

    assert client.put(f"/api/roasts/{roast_id}/control/feedback", json={"variable": "bt"}).status_code == 409  # no target
    stopped = client.delete(f"/api/roasts/{roast_id}/control/automation").json()
    assert stopped["feedback"] is None
    client.post(f"/api/roasts/{roast_id}/stop")


def test_repeating_a_roast_with_no_control_data_is_refused(client, tmp_path):
    from alog_playback.alog_io import roast_to_native_alog_dict, save_native_alog

    data = roast_to_native_alog_dict(
        title="No controls", profile=[{"time_s": 0.0, "bt": 100.0, "et": 120.0}, {"time_s": 30.0, "bt": 110.0, "et": 130.0}],
        events=[], notes=[], beans=None, weight_green_g=None, weight_roasted_g=None, roastdate="2026-01-01T00:00:00+00:00",
    )
    path = tmp_path / "plain.alog"
    save_native_alog(str(path), data)
    source_id = client.post("/api/roasts/import", params={"path": str(path)}).json()["id"]

    roast_id = client.post("/api/roasts", json={"title": "Target", "mode": "simulator"}).json()["id"]
    resp = client.post(f"/api/roasts/{roast_id}/control/program/from-roast", json={"source_roast_id": source_id})
    assert resp.status_code == 409
    assert client.post(f"/api/roasts/{roast_id}/control/program/from-roast", json={"source_roast_id": "nope"}).status_code == 404
    client.post(f"/api/roasts/{roast_id}/stop")


def test_safety_limits_are_saved_and_survive_a_settings_save_that_omits_them(client):
    saved = client.put(
        "/api/settings",
        json={"ollama_url": None, "ollama_model": None, "control": {"heater_max_pct": 85, "fan_min_pct": 20, "client_watchdog_s": 60}},
    ).json()
    assert saved["control"]["heater_max_pct"] == 85

    # A page that doesn't know about the field must not reset it.
    again = client.put("/api/settings", json={"ollama_url": None, "ollama_model": None, "history_page_size": 50}).json()
    assert again["control"]["heater_max_pct"] == 85
    assert client.get("/api/settings").json()["control"]["fan_min_pct"] == 20


def test_safety_limits_reject_nonsense(client):
    resp = client.put("/api/settings", json={"ollama_url": None, "ollama_model": None, "control": {"heater_max_pct": 150}})
    assert resp.status_code == 422
