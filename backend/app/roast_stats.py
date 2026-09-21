"""Derived roast metrics -- phase breakdown (Dry/Maillard/Development,
including DRY%/DTR as each phase's own pct_of_roast), RoR crash/flatline/
flick flags, and weight-loss%. Extracted from roast_review.py, which
originally computed all of this purely as private internal steps for
building the AI-review LLM prompt -- moved here, made public, so the
same already-correct math can be exposed directly as its own endpoint
(single-roast stats, History trends, Compare table) without a second,
possibly-drifting reimplementation. roast_review.py now imports from
here instead of defining these itself; its own build_summary()/tests
are otherwise unchanged.
"""
from __future__ import annotations

from typing import Optional

from .models import Roast, RoastRorFlags, RoastStats

PHASE_DEFS = [
    ("Dry", "CHARGE", "DRY_END"),
    ("Maillard", "DRY_END", "FC_START"),
    ("Development", "FC_START", "DROP"),
]

# RoR heuristic thresholds -- deliberately simple and a little loose; these
# originally fed an LLM's interpretation, not a strict pass/fail grader, so
# a few false positives/negatives are fine as long as the real ones get
# flagged. Same reasoning applies now that they're shown directly too.
CRASH_DROP_C_PER_MIN = 8.0
CRASH_WINDOW_S = 30.0
FLATLINE_BAND_C_PER_MIN = 1.0
FLATLINE_MIN_DURATION_S = 45.0
FLICK_RISE_C_PER_MIN = 3.0
FLICK_WINDOW_S = 30.0


def events_by_type(roast: Roast) -> dict[str, dict]:
    return {ev.type.value: ev for ev in roast.events}


def phase_breakdown(events: dict[str, dict]) -> list[dict]:
    charge, drop = events.get("CHARGE"), events.get("DROP")
    if not charge or not drop:
        return []
    total = drop.time_s - charge.time_s
    phases = []
    for label, from_type, to_type in PHASE_DEFS:
        frm, to = events.get(from_type), events.get(to_type)
        if not frm or not to:
            continue
        duration = to.time_s - frm.time_s
        phases.append({
            "phase": label,
            "duration_s": round(duration, 1),
            "pct_of_roast": round(duration / total * 100, 1) if total else None,
        })
    return phases


def ror_flags(profile: list, events: dict[str, dict]) -> dict[str, list]:
    tp_time = events["TURNING_POINT"].time_s if "TURNING_POINT" in events else 0.0
    dry_end_time = events["DRY_END"].time_s if "DRY_END" in events else tp_time
    points = [(p["time_s"], p["ror_bt"]) for p in profile if p.get("ror_bt") is not None and p["time_s"] >= tp_time]

    crashes, flatlines, flicks = [], [], []

    # Crash: RoR drops sharply from its recent (trailing window) peak.
    j = 0
    for i, (t, ror) in enumerate(points):
        while j < i and points[j][0] < t - CRASH_WINDOW_S:
            j += 1
        recent_max = max(v for _, v in points[j:i + 1])
        if recent_max - ror >= CRASH_DROP_C_PER_MIN:
            crashes.append({"time_s": round(t, 1), "ror_bt": ror, "dropped_from": round(recent_max, 1)})

    # Flatline: RoR stays within a narrow band for a sustained stretch,
    # only interesting after Dry End (a flat RoR during the dip is normal).
    band_start = None
    for t, ror in points:
        if t < dry_end_time:
            continue
        if band_start is None:
            band_start, band_val = t, ror
        elif abs(ror - band_val) > FLATLINE_BAND_C_PER_MIN:
            if t - band_start >= FLATLINE_MIN_DURATION_S:
                flatlines.append({"start_s": round(band_start, 1), "end_s": round(t, 1)})
            band_start, band_val = t, ror
    if band_start is not None and points and points[-1][0] - band_start >= FLATLINE_MIN_DURATION_S:
        flatlines.append({"start_s": round(band_start, 1), "end_s": round(points[-1][0], 1)})

    # Flick: a local minimum (post Dry End) followed by a real rise shortly after.
    post_dry = [(t, r) for t, r in points if t >= dry_end_time]
    for i in range(1, len(post_dry) - 1):
        t, ror = post_dry[i]
        if post_dry[i - 1][1] > ror < post_dry[i + 1][1]:
            future = [r for ft, r in post_dry[i:] if ft - t <= FLICK_WINDOW_S]
            if future and max(future) - ror >= FLICK_RISE_C_PER_MIN:
                flicks.append({"time_s": round(t, 1), "ror_bt": ror, "rose_to": round(max(future), 1)})

    return {"crashes": crashes[:5], "flatlines": flatlines[:5], "flicks": flicks[:5]}


def weight_loss_pct(roast: Roast) -> Optional[float]:
    # weight_green_g keeps its truthy check (0 or None both make the
    # division meaningless) -- weight_roasted_g needs `is not None`
    # specifically, since 0 is a real, legitimate value there (a
    # total-loss/scorched batch), not the same as "never measured".
    if roast.weight_green_g and roast.weight_roasted_g is not None:
        return round((1 - roast.weight_roasted_g / roast.weight_green_g) * 100, 1)
    return None


def _charge_to_drop_s(events: dict[str, dict]) -> Optional[float]:
    charge, drop = events.get("CHARGE"), events.get("DROP")
    if charge and drop and drop.time_s > charge.time_s:
        return round(drop.time_s - charge.time_s, 1)
    return None


def compute_roast_stats(roast: Roast) -> RoastStats:
    events = events_by_type(roast)
    profile = [p.model_dump() for p in roast.profile]
    phases = phase_breakdown(events)
    dry = next((p for p in phases if p["phase"] == "Dry"), None)
    dev = next((p for p in phases if p["phase"] == "Development"), None)
    flags = ror_flags(profile, events) if "TURNING_POINT" in events else {"crashes": [], "flatlines": [], "flicks": []}
    return RoastStats(
        weight_loss_pct=weight_loss_pct(roast),
        duration_s=_charge_to_drop_s(events) if _charge_to_drop_s(events) is not None else roast.duration_s,
        phases=phases,
        dry_pct=dry["pct_of_roast"] if dry else None,
        dtr_pct=dev["pct_of_roast"] if dev else None,
        ror_flags=RoastRorFlags(**flags),
    )
