"""Shared live-roast heuristics: rolling rate-of-rise plus independent
auto-detection of Charge/Turning Point/Dry End/FC Start from a raw BT/ET
stream.

Used by any engine that receives live temperature readings but no event
data of its own -- ``modbus_bridge`` (talking straight to a roaster's
PLC) and ``ms6514_bridge`` (a raw thermocouple meter), neither of which
has any concept of roast events at all. Both engines just call
``observe(time_s, bt, et)`` each tick and drain ``get_new_events()`` --
the detection logic itself lives here once instead of being duplicated
per data source.

When ``detect_milestones`` is on (the default): CHARGE is detected from
a sharp BT drop (cold beans hitting the hot drum); TURNING_POINT as the
BT minimum right after; DRY_END/FC_START from configurable BT
thresholds, since real roasts vary too much by bean to hardcode these.
DROP/COOL_END aren't threshold-detectable -- they're roast-level
judgment calls -- so those stay manual (mark them with this platform's
own event buttons) regardless. With ``detect_milestones`` off, CHARGE/
DRY_END/FC_START all become manual calls instead -- for hardware/
operators where an algorithmic guess from the temperature curve isn't
trusted or wanted; RoR is computed the same either way.

TURNING_POINT is the one exception, even with ``detect_milestones``
off: it's a pure observation (the BT minimum right after Charge), no
operator judgment involved, so it's tracked and auto-emitted regardless
-- confirmed against a real FZ-94 roast, where it is auto-plotted
on the chart even with every other milestone marked by hand. See
``notify_manual_charge()`` -- when CHARGE is marked manually rather
than auto-detected, the caller tells the detector it happened, which
starts Turning Point tracking exactly as if CHARGE had auto-fired.
"""
from __future__ import annotations

import uuid
from collections import deque
from typing import Optional


def _ror(history: deque) -> Optional[float]:
    if len(history) < 2:
        return None
    t0, v0 = history[0]
    t1, v1 = history[-1]
    if t1 - t0 <= 0:
        return None
    return round((v1 - v0) / (t1 - t0) * 60.0, 2)


class LiveRoastDetector:
    def __init__(
        self,
        ror_window_s: float = 30.0,
        charge_drop_c: float = 8.0,
        charge_window_s: float = 20.0,
        turning_point_rebound_c: float = 0.5,
        dry_end_c: Optional[float] = 160.0,
        fc_start_c: Optional[float] = 196.0,
        detect_milestones: bool = True,
    ):
        # bt_history backs both RoR and charge-drop detection -- keep it
        # wide enough for whichever window is larger.
        self._ror_window_s = max(ror_window_s, charge_window_s)
        self._charge_drop_c = charge_drop_c
        self._charge_window_s = charge_window_s
        self._turning_point_rebound_c = turning_point_rebound_c
        self._dry_end_c = dry_end_c
        self._fc_start_c = fc_start_c
        # False for hardware where the operator marks every milestone by
        # hand (see modbus_bridge/ms6514_bridge's own constructors) -- RoR
        # is still computed either way, only CHARGE/TURNING_POINT/DRY_END/
        # FC_START detection is skipped. TURNING_POINT specifically can
        # only ever fire as a side effect of CHARGE's own auto-detected
        # phase transition (see _detect_events below), so disabling
        # CHARGE detection alone already silences it -- this flag just
        # makes that explicit/skippable in one place instead of relying
        # on that being an obvious consequence.
        self._detect_milestones = detect_milestones

        self._bt_history: deque = deque()
        self._et_history: deque = deque()
        self._phase = "pre_charge"  # -> "dip" -> "post_tp"
        self._bt_min_since_charge: Optional[float] = None
        self._bt_min_time_since_charge: Optional[float] = None
        self._events_fired: set = set()
        self._event_queue: list = []

    def observe(self, time_s: float, bt: Optional[float], et: Optional[float]) -> dict:
        """Feed one live reading in; returns a sample dict with RoR filled in."""
        if bt is not None:
            self._bt_history.append((time_s, bt))
        if et is not None:
            self._et_history.append((time_s, et))
        cutoff = time_s - self._ror_window_s
        while self._bt_history and self._bt_history[0][0] < cutoff:
            self._bt_history.popleft()
        while self._et_history and self._et_history[0][0] < cutoff:
            self._et_history.popleft()

        if bt is not None:
            if self._phase == "dip":
                # Always runs, regardless of detect_milestones -- a pure
                # observation, not a judgment call (see module docstring).
                self._detect_turning_point(time_s, bt)
            elif self._detect_milestones:
                if self._phase == "pre_charge":
                    self._detect_charge(time_s, bt)
                elif self._phase == "post_tp":
                    self._detect_dry_end_and_fc_start(time_s, bt)

        return {
            "time_s": round(time_s, 1),
            "bt": bt,
            "et": et,
            "ror_bt": _ror(self._bt_history),
            "ror_et": _ror(self._et_history),
        }

    def notify_manual_charge(self, time_s: float, bt: float) -> None:
        """Called by the engine when CHARGE was marked manually rather
        than auto-detected (detect_milestones=False -- see
        modbus_bridge/ms6514_bridge's own constructors and
        RoastSession.add_event). Starts tracking toward Turning Point
        from this point on, exactly as if CHARGE had auto-fired here --
        see the module docstring for why Turning Point stays automatic
        even then. No-op if we're not still in "pre_charge" (e.g. this
        somehow got called twice, or the detector already auto-detected
        its own CHARGE first)."""
        if self._phase != "pre_charge":
            return
        self._phase = "dip"
        self._bt_min_since_charge = bt
        self._bt_min_time_since_charge = time_s

    def mark_milestone_fired(self, event_type: str) -> None:
        """Called when DRY_END or FC_START was marked manually -- with
        detect_milestones=True (opt-in auto-detection), the operator can
        still override/click early. Without this,
        the detector would have no way to know that happened (its own
        _events_fired only tracks what *it* emitted) and would still
        independently fire its own copy once BT crosses the configured
        threshold -- landing as a genuine duplicate, since the
        auto-fired path merges straight into RoastSession.events via
        _run_loop's get_new_events(), bypassing add_event()'s own
        "already marked" check entirely. CHARGE doesn't need this --
        notify_manual_charge's phase transition already fully prevents
        _detect_charge from ever running again on its own."""
        self._events_fired.add(event_type)

    def _detect_charge(self, time_s: float, bt: float) -> None:
        # CHARGE: BT fell by charge_drop_c within charge_window_s -- the
        # classic signature of cold beans hitting the hot drum.
        window_start = time_s - self._charge_window_s
        recent = [(t, v) for t, v in self._bt_history if t >= window_start]
        if recent:
            peak_t, peak_bt = max(recent, key=lambda tv: tv[1])
            if peak_bt - bt >= self._charge_drop_c:
                self._emit_event("CHARGE", "Charge", peak_bt, peak_t)
                self._phase = "dip"
                self._bt_min_since_charge = bt
                self._bt_min_time_since_charge = time_s

    def _detect_turning_point(self, time_s: float, bt: float) -> None:
        if self._bt_min_since_charge is None or bt < self._bt_min_since_charge:
            self._bt_min_since_charge = bt
            self._bt_min_time_since_charge = time_s
        elif bt >= self._bt_min_since_charge + self._turning_point_rebound_c:
            self._emit_event("TURNING_POINT", "Turning Point", self._bt_min_since_charge, self._bt_min_time_since_charge)
            self._phase = "post_tp"

    def _detect_dry_end_and_fc_start(self, time_s: float, bt: float) -> None:
        if self._dry_end_c is not None and "DRY_END" not in self._events_fired and bt >= self._dry_end_c:
            self._emit_event("DRY_END", "Dry End", bt, time_s)
        if self._fc_start_c is not None and "FC_START" not in self._events_fired and bt >= self._fc_start_c:
            self._emit_event("FC_START", "First Crack Start", bt, time_s)

    def _emit_event(self, event_type: str, label: str, value: float, time_s: float) -> None:
        self._events_fired.add(event_type)
        self._event_queue.append({
            "id": str(uuid.uuid4()),
            "time_s": round(time_s, 1),
            "type": event_type,
            "label": label,
            "value": round(value, 1),
        })

    def get_new_events(self) -> list:
        events, self._event_queue = self._event_queue, []
        return events
