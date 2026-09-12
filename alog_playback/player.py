"""Replays a real ``.alog`` roast log at real (or accelerated) speed.

Implements the same duck-typed engine contract as
``simulator.SimulatorEngine`` (``tick``/``get_new_events``/
``apply_command``/``is_finished``/``status``) so a ``MockDevice`` -- and
therefore the rest of the backend -- can't tell whether it's talking to a
live simulated roast or a replayed historical one.
"""
from __future__ import annotations

import bisect
from typing import Optional

from .alog_io import alog_dict_to_points, load_alog


class AlogPlayer:
    def __init__(self, path: str, speed: float = 1.0):
        self.path = path
        self.speed = speed
        raw = load_alog(path)
        parsed = alog_dict_to_points(raw)
        self.meta = {
            "title": parsed["title"],
            "beans": parsed["beans"],
            "weight_green_g": parsed["weight_green_g"],
            "weight_roasted_g": parsed["weight_roasted_g"],
            "machine": parsed["machine"],
            "roastdate": parsed["roastdate"],
        }
        self.profile = sorted(parsed["profile"], key=lambda p: p["time_s"])
        self.events = sorted(parsed["events"], key=lambda e: e["time_s"])
        self.notes = sorted(parsed["notes"], key=lambda n: n["time_s"])
        self._timex = [p["time_s"] for p in self.profile]
        self._end_time = self._timex[-1] if self._timex else 0.0

        self.playback_clock = 0.0
        self._next_event_idx = 0
        self._finished = not self.profile

    # -- interpolation ----------------------------------------------------
    def _interpolate(self, t: float) -> dict:
        if not self.profile:
            return {"time_s": t, "bt": None, "et": None, "ror_bt": None, "ror_et": None,
                    "heater_pct": None, "fan_pct": None, "drum_speed_pct": None}
        if t <= self._timex[0]:
            p = self.profile[0]
            return {**p, "time_s": t}
        if t >= self._timex[-1]:
            p = self.profile[-1]
            return {**p, "time_s": t}

        idx = bisect.bisect_right(self._timex, t) - 1
        idx = max(0, min(idx, len(self.profile) - 2))
        p0, p1 = self.profile[idx], self.profile[idx + 1]
        span = p1["time_s"] - p0["time_s"]
        frac = 0.0 if span <= 0 else (t - p0["time_s"]) / span

        def lerp(key: str):
            a, b = p0.get(key), p1.get(key)
            if a is None or b is None:
                return a if a is not None else b
            return a + (b - a) * frac

        return {
            "time_s": t,
            "bt": lerp("bt"),
            "et": lerp("et"),
            "ror_bt": lerp("ror_bt"),
            "ror_et": lerp("ror_et"),
            "heater_pct": lerp("heater_pct"),
            "fan_pct": lerp("fan_pct"),
            "drum_speed_pct": lerp("drum_speed_pct"),
        }

    # -- engine contract ----------------------------------------------------
    def tick(self, dt: float) -> dict:
        if self._finished:
            return self._interpolate(self._end_time)
        self.playback_clock = min(self._end_time, self.playback_clock + dt * self.speed)
        sample = self._interpolate(self.playback_clock)
        if self.playback_clock >= self._end_time:
            self._finished = True
        return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in sample.items()}

    def get_new_events(self) -> list:
        fired = []
        while (
            self._next_event_idx < len(self.events)
            and self.events[self._next_event_idx]["time_s"] <= self.playback_clock
        ):
            fired.append(self.events[self._next_event_idx])
            self._next_event_idx += 1
        return fired

    def apply_command(self, cmd: dict) -> None:
        """Playback is read-only historical data; only speed is adjustable."""
        if "speed" in cmd and cmd["speed"] is not None:
            self.speed = max(0.0, float(cmd["speed"]))

    def is_finished(self) -> bool:
        return self._finished

    def status(self) -> dict:
        return {
            "mode": "alog_playback",
            "path": self.path,
            "playback_clock": round(self.playback_clock, 1),
            "end_time": round(self._end_time, 1),
            "speed": self.speed,
            "finished": self._finished,
            "meta": self.meta,
        }

    def notes_up_to(self, t: Optional[float] = None) -> list:
        t = self.playback_clock if t is None else t
        return [n for n in self.notes if n["time_s"] <= t]
