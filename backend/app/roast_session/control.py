"""Everything about driving the roaster during a roast, kept apart from the
session's recording logic: the safety limits every command passes through, the
emergency stop and automatic fail-safes, replaying a saved roast's settings,
and holding a target by adjusting the heater.

One `RoastControl` belongs to each `RoastSession`.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Optional

from .. import storage
from ..models import (
    ControlSafety,
    EventCreateRequest,
    FeedbackRequest,
    ProgramStep,
    RoastEventType,
    RoastMode,
    RoastStatus,
)
from ..roast_control import (
    CONTROL_KEYS,
    FeedbackController,
    ProgramRunner,
    apply_limits,
    safe_state_command,
)
from ..ws_manager import pubsub

if TYPE_CHECKING:  # pragma: no cover
    from .session import RoastSession

logger = logging.getLogger(__name__)

# Modes that can't drive a heater at all.
READ_ONLY_MODES = (RoastMode.MS6514_LIVE, RoastMode.ALOG_PLAYBACK)

# Holding a target relies on the bean-temperature reading; without one for this
# long the controller gives up and the roaster goes to its safe state.
FEEDBACK_NO_READING_LIMIT_S = 10.0


class RoastControlError(RuntimeError):
    pass


class RoastControl:
    def __init__(self, session: "RoastSession") -> None:
        self.session = session
        self.limits = self._load_limits()
        self.program: Optional[ProgramRunner] = None
        self.program_label: Optional[str] = None
        self.feedback: Optional[FeedbackController] = None
        self.tripped_reason: Optional[str] = None
        self._last_written: dict[str, float] = {}
        self._no_client_since: Optional[float] = None
        self._no_reading_since: Optional[float] = None
        self._feedback_last_sent: Optional[float] = None

    # -- setup ----------------------------------------------------------------
    @staticmethod
    def _load_limits() -> ControlSafety:
        try:
            raw = storage.get_settings().get("control")
            return ControlSafety(**raw) if raw else ControlSafety()
        except Exception:  # a bad saved value must never stop a roast from starting
            logger.exception("couldn't read the saved control limits -- using the defaults")
            return ControlSafety()

    def refresh_limits(self) -> None:
        """Picks up limits saved in Settings since this roast started."""
        self.limits = self._load_limits()

    @property
    def can_write(self) -> bool:
        return self.session.mode not in READ_ONLY_MODES

    # -- what the roaster is currently set to ---------------------------------
    def current(self) -> dict[str, Optional[float]]:
        latest = self.session.profile[-1] if self.session.profile else {}
        return {k: self._last_written.get(k, latest.get(k)) for k in CONTROL_KEYS}

    # -- writing --------------------------------------------------------------
    def write(self, payload: dict, *, source: str) -> dict:
        """The only way a command reaches the roaster. `source` is
        "operator" (sliders/API), "rule" (alarm rules), "program" or
        "feedback"; everything except the safe-state write itself passes
        through the safety limits."""
        payload = dict(payload)
        if self.limits.heater_max_pct < 100 and payload.get("burner_sv_c") is not None:
            # A temperature setpoint can't be checked against a percentage cap.
            raise RoastControlError("the burner temperature setpoint can't be used while a heater limit is set -- use the heater slider")
        limited = apply_limits(payload, self.limits, self.current())
        result = self.session.device.write(limited)
        for key in CONTROL_KEYS:
            if limited.get(key) is not None:
                self._last_written[key] = float(limited[key])
        return result

    def operator_command(self, payload: dict) -> dict:
        """A command typed or dragged by the operator. Touching the controls
        takes over from any automation."""
        if self.automation_active():
            self.stop_automation("the operator changed a control")
        return self.write(payload, source="operator")

    # -- safe state -----------------------------------------------------------
    async def enter_safe_state(self, reason: str, *, username: Optional[str] = None) -> bool:
        """Heater off, fan to the safe level, automation stopped. Also used by
        the emergency stop. Never raises: it's called when things are already
        going wrong, so a failed write is logged and reported, not thrown.

        The single chokepoint for every safety trip -- the manual Emergency
        Stop button and all of this session's fail-safes (the no-viewer
        watchdog, a lost temperature reading, a tick error) all land here,
        so this is also the one place that needs to log an activity_log
        entry for any of them. `username` is only ever set for the manual
        button (an HTTP request is in flight); every fail-safe passes
        nothing, since it fires on its own, not because anyone acted."""
        self.stop_automation(reason)
        self.session._cancel_pending_alarms()
        self.tripped_reason = reason
        written = False
        try:
            command = safe_state_command(self.limits)
            self.session.device.write(command)
            for key, value in command.items():
                self._last_written[key] = float(value)
            written = True
        except Exception:
            logger.exception("safe-state write failed (%s)", reason)
        await self._mark(f"Safety: {reason}" + ("" if written else " (couldn't reach the roaster)"))
        storage.log_activity(
            "safety", "safe_state", username=username, roast_id=self.session.id, roast_title=self.session.title,
            message=f'Safety stop on "{self.session.title}": {reason}',
        )
        return written

    def release(self) -> None:
        """Called when the operator ends the roast: automation stops and, if
        the heater is on, it's turned off -- the app shouldn't let go of a
        roaster that's still heating. The fan and drum are left as they are."""
        self.stop_automation("the roast ended")
        heater = self.current().get("heater_pct")
        if self.can_write and heater and heater > 0:
            try:
                self.session.device.write({"heater_pct": 0.0})
                self._last_written["heater_pct"] = 0.0
            except Exception:
                logger.exception("couldn't turn the heater off when the roast ended")

    async def emergency_stop(self, *, username: Optional[str] = None) -> bool:
        return await self.enter_safe_state("emergency stop", username=username)

    def clear_trip(self) -> None:
        self.tripped_reason = None

    async def _mark(self, label: str) -> None:
        """Records a note on the chart when the roast is being recorded."""
        session = self.session
        if session.status not in (RoastStatus.ROASTING, RoastStatus.COOLING):
            return
        try:
            event = session.add_event(
                EventCreateRequest(
                    type=RoastEventType.CUSTOM,
                    label=label,
                    value=session.profile[-1]["bt"] if session.profile else None,
                )
            )
            await pubsub.publish(session.id, {"type": "event", "roast_id": session.id, "event": event})
        except Exception:
            logger.exception("couldn't record %r", label)

    # -- automation -----------------------------------------------------------
    def automation_active(self) -> bool:
        return self.program is not None or self.feedback is not None

    def _require_writable(self) -> None:
        if not self.can_write:
            raise RoastControlError("this device is read-only, so it can't be controlled")
        if self.session.status not in (RoastStatus.IDLE, RoastStatus.ROASTING, RoastStatus.COOLING):
            raise RoastControlError("the roast isn't active")

    def start_program(self, steps: list[ProgramStep], label: str) -> None:
        self._require_writable()
        self.stop_automation("a new program was started")
        self.clear_trip()
        self.program = ProgramRunner(steps)
        self.program_label = label

    def start_feedback(self, config: FeedbackRequest) -> None:
        self._require_writable()
        if config.setpoint is None and not config.curve:
            raise RoastControlError("give a target: a single value or a curve")
        if config.output_min_pct > config.output_max_pct:
            raise RoastControlError("the lowest heater level can't be above the highest")
        if self.session.status == RoastStatus.COOLING:
            raise RoastControlError("target control only runs while roasting")
        self.stop_automation("target control was started")
        self.clear_trip()
        start_output = self.current().get("heater_pct") or 0.0
        self.feedback = FeedbackController(config, start_output, self._control_time() or 0.0)
        self._no_reading_since = None
        self._feedback_last_sent = None

    def stop_automation(self, reason: str) -> bool:
        had = self.automation_active()
        self.program = None
        self.program_label = None
        self.feedback = None
        self._no_reading_since = None
        return had

    # -- per-tick work --------------------------------------------------------
    def _charge_time(self) -> Optional[float]:
        for event in self.session.events:
            if event["type"] == RoastEventType.CHARGE.value:
                return event["time_s"]
        return None

    def _control_time(self) -> Optional[float]:
        """Roast time counted from Charge, or None until Charge is marked."""
        charge = self._charge_time()
        if charge is None or not self.session.profile:
            return None
        return self.session.profile[-1]["time_s"] - charge

    async def tick(self, sample: dict, *, recording: bool) -> None:
        """Called once per sample by the session's loop. Never raises."""
        try:
            await self._check_watchdog()
            if recording and self.automation_active():
                await self._run_automation(sample)
        except Exception:
            logger.exception("control tick failed")
            self.stop_automation("an error in automatic control")
            await self.enter_safe_state("error in automatic control")

    async def _check_watchdog(self) -> None:
        limit = self.limits.client_watchdog_s
        heater = self.current().get("heater_pct")
        if limit <= 0 or not heater or heater <= 0 or not self.can_write:
            self._no_client_since = None
            return
        if pubsub.subscriber_count(self.session.id) > 0:
            self._no_client_since = None
            return
        now = time.monotonic()
        if self._no_client_since is None:
            self._no_client_since = now
        elif now - self._no_client_since >= limit:
            self._no_client_since = None
            await self.enter_safe_state(f"nobody has had the roast open for {int(limit)} s")

    async def _run_automation(self, sample: dict) -> None:
        session = self.session
        t = self._control_time()
        if t is None:  # waiting for Charge
            return

        if self.feedback is not None:
            if any(e["type"] == RoastEventType.DROP.value for e in session.events) or session.status != RoastStatus.ROASTING:
                self.stop_automation("Drop")
                await self._mark("Target control stopped at Drop")
            else:
                await self._run_feedback(sample, t)

        if self.program is not None:
            command = self.program.due(t)
            if command:
                self.write(command, source="program")

    async def _run_feedback(self, sample: dict, t: float) -> None:
        measured = sample.get(self.feedback.config.variable)
        if measured is None:
            now = time.monotonic()
            self._no_reading_since = self._no_reading_since or now
            if now - self._no_reading_since >= FEEDBACK_NO_READING_LIMIT_S:
                await self.enter_safe_state("lost the temperature reading during target control")
            return
        self._no_reading_since = None
        output = self.feedback.update(t, float(measured))
        if output is None:
            return
        if self._feedback_last_sent is None or abs(output - self._feedback_last_sent) >= 0.5:
            self.write({"heater_pct": output}, source="feedback")
            self._feedback_last_sent = output

    # -- reporting ------------------------------------------------------------
    def status(self) -> dict:
        t = self._control_time()
        program = None
        if self.program is not None:
            program = {
                "label": self.program_label,
                "steps": len(self.program.steps),
                "waiting_for_charge": t is None,
                "next_step_in_s": None if t is None else self.program.next_step_in(t),
                "last_step_s": self.program.last_step_s,
            }
        feedback = None
        if self.feedback is not None:
            cfg = self.feedback.config
            feedback = {
                "variable": cfg.variable,
                "waiting_for_charge": t is None,
                "setpoint": self.feedback.last_setpoint if self.feedback.last_setpoint is not None else self.feedback.setpoint_at(t or 0.0),
                "output_pct": round(self.feedback.output, 1),
                "kp": self.feedback.kp,
                "ki": self.feedback.ki,
                "output_min_pct": cfg.output_min_pct,
                "output_max_pct": cfg.output_max_pct,
            }
        return {
            "can_write": self.can_write,
            "active": self.session.status in (RoastStatus.IDLE, RoastStatus.ROASTING, RoastStatus.COOLING),
            "program": program,
            "feedback": feedback,
            "tripped_reason": self.tripped_reason,
            "limits": self.limits.model_dump(),
            "current": self.current(),
        }
