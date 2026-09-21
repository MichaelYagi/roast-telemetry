"""Roaster control logic that doesn't touch hardware: safety limits, replaying
a saved roast's heater/fan/drum settings, and holding a target by adjusting
the heater.

Everything here is plain calculation -- the roast session decides when to call
it and does the actual writing -- so it can be tested without a device.
"""
from __future__ import annotations

from typing import Optional

from .models import ControlSafety, FeedbackRequest, ProgramStep

CONTROL_KEYS = ("heater_pct", "fan_pct", "drum_speed_pct")


# -- safety -----------------------------------------------------------------


def apply_limits(command: dict, limits: ControlSafety, current: dict) -> dict:
    """Returns `command` with the safety limits applied.

    - The heater is capped at `heater_max_pct`.
    - While the heater is (or is being set) above 0, the fan and drum are held
      at or above their minimums -- a command that would turn the heater on
      with the fan too low also raises the fan.

    `current` is the last known value of each channel (None when unknown)."""
    out = dict(command)
    if out.get("heater_pct") is not None:
        out["heater_pct"] = min(float(out["heater_pct"]), limits.heater_max_pct)

    heater = out["heater_pct"] if out.get("heater_pct") is not None else current.get("heater_pct")
    if heater is not None and heater > 0:
        for key, minimum in (("fan_pct", limits.fan_min_pct), ("drum_speed_pct", limits.drum_min_pct)):
            if minimum <= 0:
                continue
            wanted = out.get(key) if out.get(key) is not None else current.get(key)
            if wanted is None or wanted < minimum:
                out[key] = minimum
    return out


def safe_state_command(limits: ControlSafety) -> dict:
    """Heater off, fan at the safe level; the drum is left turning."""
    return {"heater_pct": 0.0, "fan_pct": limits.safe_fan_pct}


# -- replaying a saved roast --------------------------------------------------


def _channel_points(steps: list[ProgramStep], key: str) -> list[tuple[float, float, bool]]:
    points = [(s.time_s, float(getattr(s, key)), s.ramp) for s in steps if getattr(s, key) is not None]
    points.sort(key=lambda p: p[0])
    return points


def _value_at(points: list[tuple[float, float, bool]], t: float) -> Optional[float]:
    if not points or t < points[0][0]:
        return None
    for i in range(len(points) - 1):
        t0, v0, _ = points[i]
        t1, v1, ramp1 = points[i + 1]
        if t < t1:
            if ramp1 and t1 > t0:
                return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
            return v0
    return points[-1][1]


class ProgramRunner:
    """Turns a list of steps into commands as roast time advances.

    `due(t)` gives the channels that need a new value at time `t` (seconds
    from Charge), so the roaster isn't rewritten every second: a value is
    only sent when it has moved by `min_delta` or when a channel reaches its
    final value."""

    def __init__(self, steps: list[ProgramStep], *, min_delta: float = 1.0) -> None:
        self.steps = list(steps)
        self.min_delta = min_delta
        self._points = {key: _channel_points(self.steps, key) for key in CONTROL_KEYS}
        self._last_sent: dict[str, float] = {}
        self.last_step_s = max((s.time_s for s in self.steps), default=0.0)

    def due(self, t: float) -> dict:
        command: dict[str, float] = {}
        for key, points in self._points.items():
            target = _value_at(points, t)
            if target is None:
                continue
            target = round(target, 1)
            last = self._last_sent.get(key)
            at_end = bool(points) and t >= points[-1][0]
            if last is None or abs(target - last) >= self.min_delta or (at_end and target != last):
                command[key] = target
                self._last_sent[key] = target
        return command

    def next_step_in(self, t: float) -> Optional[float]:
        upcoming = [s.time_s - t for s in self.steps if s.time_s > t]
        return min(upcoming) if upcoming else None


def program_from_profile(profile: list, charge_time_s: float, *, min_delta: float = 2.0) -> list[ProgramStep]:
    """Builds steps from a saved roast: each time the recorded heater, fan or
    drum value moved by at least `min_delta` after Charge, it becomes a step
    (seconds from Charge). Channels the roast never recorded are left out."""
    # A roast loaded from a file that never recorded these channels reads back
    # as all zeros. Repeating that would turn the heater and fan off at Charge,
    # so it counts as "no data".
    if not any((sample.get(key) or 0) > 0 for sample in profile for key in CONTROL_KEYS):
        return []
    by_time: dict[float, dict] = {}
    last: dict[str, float] = {}
    for sample in profile:
        t = sample["time_s"] - charge_time_s
        if t < 0:
            continue
        for key in CONTROL_KEYS:
            value = sample.get(key)
            if value is None:
                continue
            if key not in last or abs(value - last[key]) >= min_delta:
                last[key] = float(value)
                by_time.setdefault(round(t, 1), {})[key] = round(float(value), 1)
    return [ProgramStep(time_s=t, **values) for t, values in sorted(by_time.items())]


# -- holding a target ---------------------------------------------------------

# Deliberately gentle starting values. They are a starting point, not tuned for
# any particular roaster -- see the docs before relying on them.
DEFAULT_GAINS = {
    "ror_bt": (1.0, 0.05),  # % heater per (°C/min) of error
    "bt": (1.5, 0.01),  # % heater per °C of error
}


def curve_value(curve: list, t: float) -> Optional[float]:
    """Linear interpolation over (time_s, value) points, held flat outside them."""
    if not curve:
        return None
    pts = sorted(((c.time_s, c.value) if hasattr(c, "time_s") else (c["time_s"], c["value"])) for c in curve)
    if t <= pts[0][0]:
        return pts[0][1]
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t <= t1:
            return v0 if t1 == t0 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return pts[-1][1]


class FeedbackController:
    """PI control of the heater toward a setpoint on bean temperature or its
    rate of rise. Starts from the heater's current level, never moves faster
    than `max_step_pct_per_s`, and stays inside the output limits."""

    def __init__(self, config: FeedbackRequest, start_output: float, start_time_s: float) -> None:
        self.config = config
        kp, ki = DEFAULT_GAINS[config.variable]
        self.kp = config.kp if config.kp is not None else kp
        self.ki = config.ki if config.ki is not None else ki
        self.output = self._clamp(start_output)
        self._integral = self.output
        self._last_t = start_time_s
        self.last_setpoint: Optional[float] = None

    def _clamp(self, value: float) -> float:
        return max(self.config.output_min_pct, min(self.config.output_max_pct, value))

    def setpoint_at(self, t: float) -> Optional[float]:
        if self.config.curve:
            return curve_value(self.config.curve, t)
        return self.config.setpoint

    def update(self, t: float, measured: Optional[float]) -> Optional[float]:
        """The new heater level, or None when there's nothing to change
        (no reading, no setpoint, or no time has passed)."""
        dt = t - self._last_t
        setpoint = self.setpoint_at(t)
        self.last_setpoint = setpoint
        if measured is None or setpoint is None or dt <= 0:
            return None
        self._last_t = t
        error = setpoint - measured
        # Only build up the integral while the output isn't already pushed
        # against a limit in the direction the error is asking for, so it
        # doesn't wind up (and overshoot) during a long climb.
        candidate = self._integral + self.ki * error * dt
        unclamped = self.kp * error + candidate
        pushing_past_limit = (unclamped > self.config.output_max_pct and error > 0) or (
            unclamped < self.config.output_min_pct and error < 0
        )
        if not pushing_past_limit:
            self._integral = self._clamp(candidate)
        wanted = self._clamp(self.kp * error + self._integral)
        step = self.config.max_step_pct_per_s * dt
        self.output = max(self.output - step, min(self.output + step, wanted))
        return round(self.output, 1)
