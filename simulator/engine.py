"""Simulator mode wrapper.

A simulator mode fabricates BT/ET without hardware, driven by a
simplified thermodynamic model plus scripted roast events. This module
implements one of our own: a small first-order
thermal model (ET chases a heater-driven setpoint, BT lags ET with a
charge-dip/turning-point phase) with threshold-based event detection
(TURNING_POINT, DRY_END, FC_START, FC_END, DROP). It's tuned to produce
curves that look and behave like a typical drum roast, not to reproduce
any particular machine's exact numbers.

Two ways to use this module:

1. Direct/singleton, matching the spec's flat function API:
   ``start_simulated_roast()``, ``get_live_data()``, ``get_events()``,
   ``stop_simulated_roast()``.
2. ``SimulatorEngine`` instances for concurrent roasts (used by
   ``mock_device`` / the backend's roast session manager), exposing the
   duck-typed engine contract: ``tick(dt)``, ``get_new_events()``,
   ``apply_command(cmd)``, ``is_finished()``, ``status()``.
"""
from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from roast_heuristics.ror import RateOfRise


class RoastPhase(str, Enum):
    IDLE = "idle"
    CHARGE = "charge"
    DIP = "dip"
    RISING = "rising"
    DEVELOPMENT = "development"
    COOLING = "cooling"
    DONE = "done"


@dataclass
class SimulatorConfig:
    ambient_c: float = 22.0
    charge_bt_c: float = 96.0
    charge_et_c: float = 200.0
    turning_point_c: float = 82.0
    dip_duration_s: float = 45.0
    dry_end_c: float = 160.0
    fc_start_c: float = 196.0
    fc_end_c: float = 205.0
    drop_bt_c: float = 218.0
    max_roast_time_s: float = 900.0  # hard stop safety valve (15 min)
    cooling_duration_s: float = 180.0
    heater_pct: float = 70.0
    fan_pct: float = 20.0
    drum_speed_pct: float = 50.0
    et_time_constant: float = 0.05
    bt_time_constant: float = 0.002


def _ease_in_out(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


class SimulatorEngine:
    """A single simulated roast's thermal model + event detector."""

    def __init__(self, config: Optional[SimulatorConfig] = None):
        self.config = config or SimulatorConfig()
        self.phase = RoastPhase.IDLE
        self.time_s = 0.0
        self.bt = self.config.ambient_c
        self.et = self.config.ambient_c
        self.heater_pct = self.config.heater_pct
        self.fan_pct = self.config.fan_pct
        self.drum_speed_pct = self.config.drum_speed_pct
        self._bt_ror = RateOfRise()
        self._et_ror = RateOfRise()
        self._events_fired: set = set()
        self._event_queue: list = []
        self._cooling_started_at: Optional[float] = None
        self._running = False

    # -- lifecycle -----------------------------------------------------
    def start(self) -> None:
        self.phase = RoastPhase.CHARGE
        self.time_s = 0.0
        self.bt = self.config.charge_bt_c
        self.et = self.config.charge_et_c
        self._bt_ror.reset()
        self._et_ror.reset()
        self._events_fired.clear()
        self._event_queue.clear()
        self._running = True
        self._emit_event("CHARGE", "Charge", self.bt)
        self.phase = RoastPhase.DIP

    def stop(self) -> None:
        self._running = False
        self.phase = RoastPhase.DONE

    def is_finished(self) -> bool:
        return self.phase == RoastPhase.DONE

    # -- control ---------------------------------------------------------
    def apply_command(self, cmd: dict) -> None:
        if "heater_pct" in cmd and cmd["heater_pct"] is not None:
            self.heater_pct = max(0.0, min(100.0, float(cmd["heater_pct"])))
        if "fan_pct" in cmd and cmd["fan_pct"] is not None:
            self.fan_pct = max(0.0, min(100.0, float(cmd["fan_pct"])))
        if "drum_speed_pct" in cmd and cmd["drum_speed_pct"] is not None:
            self.drum_speed_pct = max(0.0, min(100.0, float(cmd["drum_speed_pct"])))

    # -- simulation step ---------------------------------------------------
    def tick(self, dt: float) -> dict:
        if not self._running:
            return self._sample()

        self.time_s += dt
        cfg = self.config
        time_since_charge = self.time_s

        et_target = cfg.ambient_c + self.heater_pct * 3.4 - self.fan_pct * 0.2
        self.et += (et_target - self.et) * cfg.et_time_constant * dt

        if self.phase == RoastPhase.DIP and time_since_charge < cfg.dip_duration_s:
            progress = time_since_charge / cfg.dip_duration_s
            self.bt = cfg.charge_bt_c + (cfg.turning_point_c - cfg.charge_bt_c) * _ease_in_out(progress)
        else:
            if self.phase == RoastPhase.DIP:
                self.phase = RoastPhase.RISING
                self._emit_event("TURNING_POINT", "Turning Point", self.bt)
            bt_k = cfg.bt_time_constant * (1 + self.drum_speed_pct / 200.0)
            self.bt += (self.et - self.bt) * bt_k * dt

        self._bt_ror.update(self.time_s, self.bt)
        self._et_ror.update(self.time_s, self.et)

        self._detect_events()

        if self.phase in (RoastPhase.RISING, RoastPhase.DEVELOPMENT):
            if self.bt >= cfg.drop_bt_c or self.time_s >= cfg.max_roast_time_s:
                self._emit_event("DROP", "Drop", self.bt)
                self.phase = RoastPhase.COOLING
                self._cooling_started_at = self.time_s

        if self.phase == RoastPhase.COOLING:
            elapsed = self.time_s - (self._cooling_started_at or self.time_s)
            self.bt = max(cfg.ambient_c, self.bt - dt * 1.2)
            self.et = max(cfg.ambient_c, self.et - dt * 1.5)
            if elapsed >= cfg.cooling_duration_s:
                self._emit_event("COOL_END", "Cool End", self.bt)
                self.phase = RoastPhase.DONE
                self._running = False

        return self._sample()

    def _detect_events(self) -> None:
        cfg = self.config
        if self.phase == RoastPhase.RISING and "DRY_END" not in self._events_fired and self.bt >= cfg.dry_end_c:
            self._emit_event("DRY_END", "Dry End", self.bt)
        if self.phase == RoastPhase.RISING and "FC_START" not in self._events_fired and self.bt >= cfg.fc_start_c:
            self._emit_event("FC_START", "First Crack Start", self.bt)
            self.phase = RoastPhase.DEVELOPMENT
        if self.phase == RoastPhase.DEVELOPMENT and "FC_END" not in self._events_fired and self.bt >= cfg.fc_end_c:
            self._emit_event("FC_END", "First Crack End", self.bt)

    def _emit_event(self, event_type: str, label: str, value: float) -> None:
        self._events_fired.add(event_type)
        self._event_queue.append({
            "id": str(uuid.uuid4()),
            "time_s": round(self.time_s, 1),
            "type": event_type,
            "label": label,
            "value": round(value, 1),
        })

    def _sample(self) -> dict:
        return {
            "time_s": round(self.time_s, 1),
            "bt": round(self.bt, 2),
            "et": round(self.et, 2),
            "ror_bt": self._bt_ror.value,
            "ror_et": self._et_ror.value,
            "heater_pct": round(self.heater_pct, 1),
            "fan_pct": round(self.fan_pct, 1),
            "drum_speed_pct": round(self.drum_speed_pct, 1),
        }

    def get_new_events(self) -> list:
        events, self._event_queue = self._event_queue, []
        return events

    def status(self) -> dict:
        return {
            "phase": self.phase.value,
            "running": self._running,
            "time_s": round(self.time_s, 1),
        }


# ---------------------------------------------------------------------------
# Flat/singleton convenience API, matching the spec literally.
# ---------------------------------------------------------------------------
_default_engine: Optional[SimulatorEngine] = None


def start_simulated_roast(config: Optional[SimulatorConfig] = None) -> SimulatorEngine:
    global _default_engine
    _default_engine = SimulatorEngine(config)
    _default_engine.start()
    return _default_engine


def stop_simulated_roast() -> None:
    if _default_engine is not None:
        _default_engine.stop()


def get_live_data(dt: float = 1.0) -> dict:
    if _default_engine is None:
        raise RuntimeError("No simulated roast is running. Call start_simulated_roast() first.")
    return _default_engine.tick(dt)


def get_events() -> list:
    if _default_engine is None:
        return []
    return _default_engine.get_new_events()
