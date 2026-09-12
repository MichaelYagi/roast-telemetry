"""Builds the structured summary + prompt text fed to Ollama for a
post-roast review. Deliberately doesn't hand the LLM the raw per-second
profile (hundreds of samples -- too much for a local model's context, and
small models reason about it poorly anyway). Instead this computes the
same kind of derived signals a human would look at on the chart --phase
balance, RoR shape flags, milestones, control changes-- in plain Python,
and lets the LLM's job be interpretation + suggestions, not arithmetic.
"""
from __future__ import annotations

from typing import Optional

from .models import Roast

PHASE_DEFS = [
    ("Dry", "CHARGE", "DRY_END"),
    ("Maillard", "DRY_END", "FC_START"),
    ("Development", "FC_START", "DROP"),
]

# RoR heuristic thresholds -- deliberately simple and a little loose; these
# feed an LLM's interpretation, not a strict pass/fail grader, so a few
# false positives/negatives are fine as long as the real ones get flagged.
CRASH_DROP_C_PER_MIN = 8.0
CRASH_WINDOW_S = 30.0
FLATLINE_BAND_C_PER_MIN = 1.0
FLATLINE_MIN_DURATION_S = 45.0
FLICK_RISE_C_PER_MIN = 3.0
FLICK_WINDOW_S = 30.0

DECIMATED_POINTS = 24


def _events_by_type(roast: Roast) -> dict[str, dict]:
    return {ev.type.value: ev for ev in roast.events}


def _phase_breakdown(events: dict[str, dict]) -> list[dict]:
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


def _decimate(profile: list) -> list[dict]:
    if not profile:
        return []
    step = max(1, len(profile) // DECIMATED_POINTS)
    return [
        {"time_s": p["time_s"], "bt": p.get("bt"), "et": p.get("et"), "ror_bt": p.get("ror_bt")}
        for p in profile[::step]
    ]


def _ror_flags(profile: list, events: dict[str, dict]) -> dict[str, list]:
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


MAX_CONTROL_CHANGES = 15


def _control_changes(profile: list) -> list[dict]:
    changes = []
    last = {"heater_pct": None, "fan_pct": None, "drum_speed_pct": None}
    for p in profile:
        for field in last:
            value = p.get(field)
            if value is not None and value != last[field]:
                if last[field] is not None:  # skip the very first population
                    changes.append({"time_s": p["time_s"], "channel": field, "value": value})
                last[field] = value
    # A busy roast can produce dozens of tiny adjustments -- capping (and
    # evenly sampling rather than truncating) keeps this from dominating
    # the prompt's token budget and pulling the model's attention away
    # from the roast-domain framing/instructions around it. Verified via
    # a real generation call this actually matters: an earlier version
    # with ~80 raw entries here caused a 14B local model to misread the
    # whole roast as a washing-machine cycle.
    if len(changes) > MAX_CONTROL_CHANGES:
        step = len(changes) / MAX_CONTROL_CHANGES
        changes = [changes[int(i * step)] for i in range(MAX_CONTROL_CHANGES)]
    return changes


def build_summary(roast: Roast) -> dict:
    events = _events_by_type(roast)
    profile = [p.model_dump() for p in roast.profile]
    weight_loss_pct = None
    if roast.weight_green_g and roast.weight_roasted_g:
        weight_loss_pct = round((1 - roast.weight_roasted_g / roast.weight_green_g) * 100, 1)

    return {
        "coffee_bean_roast_title": roast.title,
        "green_coffee_beans": roast.beans,
        "coffee_roasting_machine": roast.machine_label,
        "green_bean_weight_g": roast.weight_green_g,
        "roasted_bean_weight_g": roast.weight_roasted_g,
        "roast_weight_loss_pct": weight_loss_pct,
        "total_roast_duration_s": roast.duration_s,
        "roast_milestones": {
            ev_type: {"time_s": ev.time_s, "bean_temp_c": ev.value}
            for ev_type, ev in events.items()
            if ev_type != "CUSTOM"
        },
        "roast_phases": _phase_breakdown(events),
        "rate_of_rise_shape_flags": _ror_flags(profile, events) if "TURNING_POINT" in events else {"crashes": [], "flatlines": [], "flicks": []},
        "bean_temp_et_ror_curve_sampled": _decimate(profile),
        "roaster_burner_fan_drum_setting_changes": _control_changes(profile),
        "operator_notes_during_roast": [{"time_s": n.time_s, "text": n.text} for n in roast.notes],
    }


PROMPT_TEMPLATE = """You are an experienced coffee roaster reviewing a completed COFFEE BEAN ROASTING \
session on a drum coffee roaster machine. Below is structured data from that coffee roast -- \
milestone events (Charge/Turning Point/Dry End/First Crack/Drop/Cool End), phase timing (Dry/ \
Maillard/Development), rate-of-rise (RoR, how fast the bean temperature is climbing) shape flags, \
a sampled bean-temperature/exhaust-temperature/RoR curve, changes to the roaster's burner heat, \
fan airflow, and drum rotation speed settings (each 0-100%), and any notes the operator logged \
during the roast.

Write a plain-text review (no markdown formatting, no headers with #, just clear paragraphs and \
"-" bullet lists where useful) covering:
1. A brief summary of what happened in this coffee roast.
2. What went well.
3. Any concerns or risks you see in the data (reference specific times/values).
4. Concrete, specific suggestions for the next roast of this bean/setup.

Be specific and reference the actual numbers given -- avoid generic advice that doesn't engage \
with this roast's actual data.

COFFEE ROAST DATA (JSON, all temperatures in Celsius, all times in seconds since Charge):
{summary}

Remember: this is data from roasting coffee beans in a drum roaster, not any other kind of \
machine or appliance. Write your review now.
"""


def build_prompt(summary: dict) -> str:
    import json
    return PROMPT_TEMPLATE.format(summary=json.dumps(summary, indent=2))
