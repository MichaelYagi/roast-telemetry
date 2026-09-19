"""Builds the structured summary + prompt text fed to Ollama for a
post-roast review. Deliberately doesn't hand the LLM the raw per-second
profile (hundreds of samples -- too much for a local model's context, and
small models reason about it poorly anyway). Instead this computes the
same kind of derived signals a human would look at on the chart --phase
balance, RoR shape flags, milestones, control changes-- in plain Python,
and lets the LLM's job be interpretation + suggestions, not arithmetic.

Phase breakdown/RoR flags/weight-loss-% themselves live in roast_stats.py
now (this module originally defined them as private internal steps, but
that same math is also exactly what GET /roasts/{id}/stats, History's
trends, and the Compare page's numeric table need -- see that module's
own docstring). This file just adds the LLM-prompt-specific framing on
top: decimating the curve to a token budget, summarizing control
changes, and the prompt template itself.
"""
from __future__ import annotations

from .models import Roast
from .roast_stats import events_by_type, phase_breakdown, ror_flags, weight_loss_pct as compute_weight_loss_pct

DECIMATED_POINTS = 24


def _decimate(profile: list) -> list[dict]:
    if not profile:
        return []
    step = max(1, len(profile) // DECIMATED_POINTS)
    return [
        {"time_s": p["time_s"], "bt": p.get("bt"), "et": p.get("et"), "ror_bt": p.get("ror_bt")}
        for p in profile[::step]
    ]


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
    events = events_by_type(roast)
    profile = [p.model_dump() for p in roast.profile]

    return {
        "coffee_bean_roast_title": roast.title,
        "green_coffee_beans": roast.beans,
        "green_bean_weight_g": roast.weight_green_g,
        "roasted_bean_weight_g": roast.weight_roasted_g,
        "roast_weight_loss_pct": compute_weight_loss_pct(roast),
        "total_roast_duration_s": roast.duration_s,
        "roast_milestones": {
            ev_type: {"time_s": ev.time_s, "bean_temp_c": ev.value}
            for ev_type, ev in events.items()
            if ev_type != "CUSTOM"
        },
        "roast_phases": phase_breakdown(events),
        "rate_of_rise_shape_flags": ror_flags(profile, events) if "TURNING_POINT" in events else {"crashes": [], "flatlines": [], "flicks": []},
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
