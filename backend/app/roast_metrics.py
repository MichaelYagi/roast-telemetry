"""The numbers a roast boils down to, one flat row per roast, for comparing
roasts and analysing many of them together.

`alog_metrics` reads a roast's recorded curve and milestones. `build_row` adds
the parts that live in the database (weights, outcomes, the saved beans).
All times are seconds; times of milestones are counted from Charge.
"""
from __future__ import annotations

import bisect
from typing import Optional

from .models import Roast
from .roast_stats import events_by_type, phase_breakdown, ror_flags

# key, label, unit, group. The frontend gets this list from GET /analysis/metrics,
# so labels and units are defined here once.
METRICS: list[dict] = [
    {"key": "charge_temp_c", "label": "Charge temperature", "unit": "°C", "group": "Temperatures"},
    {"key": "tp_temp_c", "label": "Turning point temperature", "unit": "°C", "group": "Temperatures"},
    {"key": "dry_end_temp_c", "label": "Dry End temperature", "unit": "°C", "group": "Temperatures"},
    {"key": "fc_start_temp_c", "label": "First Crack temperature", "unit": "°C", "group": "Temperatures"},
    {"key": "fc_end_temp_c", "label": "First Crack End temperature", "unit": "°C", "group": "Temperatures"},
    {"key": "drop_temp_c", "label": "Drop temperature", "unit": "°C", "group": "Temperatures"},
    {"key": "tp_time_s", "label": "Turning point time", "unit": "s", "group": "Timing"},
    {"key": "dry_end_time_s", "label": "Dry End time", "unit": "s", "group": "Timing"},
    {"key": "fc_start_time_s", "label": "First Crack time", "unit": "s", "group": "Timing"},
    {"key": "fc_end_time_s", "label": "First Crack End time", "unit": "s", "group": "Timing"},
    {"key": "drop_time_s", "label": "Drop time", "unit": "s", "group": "Timing"},
    {"key": "duration_s", "label": "Roast duration (Charge to Drop)", "unit": "s", "group": "Timing"},
    {"key": "dry_time_s", "label": "Dry phase time", "unit": "s", "group": "Phases"},
    {"key": "maillard_time_s", "label": "Maillard phase time", "unit": "s", "group": "Phases"},
    {"key": "development_time_s", "label": "Development time", "unit": "s", "group": "Phases"},
    {"key": "dry_pct", "label": "Dry %", "unit": "%", "group": "Phases"},
    {"key": "maillard_pct", "label": "Maillard %", "unit": "%", "group": "Phases"},
    {"key": "dtr_pct", "label": "Development % (DTR)", "unit": "%", "group": "Phases"},
    {"key": "max_ror", "label": "Peak rate of rise", "unit": "°C/min", "group": "Rate of rise"},
    {"key": "ror_at_fc_start", "label": "Rate of rise at First Crack", "unit": "°C/min", "group": "Rate of rise"},
    {"key": "ror_at_drop", "label": "Rate of rise at Drop", "unit": "°C/min", "group": "Rate of rise"},
    {"key": "ror_crashes", "label": "Rate-of-rise crashes", "unit": "", "group": "Rate of rise"},
    {"key": "ror_flatlines", "label": "Rate-of-rise flatlines", "unit": "", "group": "Rate of rise"},
    {"key": "ror_flicks", "label": "Rate-of-rise flicks", "unit": "", "group": "Rate of rise"},
    {"key": "weight_green_g", "label": "Green weight", "unit": "g", "group": "Weights"},
    {"key": "weight_roasted_g", "label": "Roasted weight", "unit": "g", "group": "Weights"},
    {"key": "weight_loss_pct", "label": "Weight loss", "unit": "%", "group": "Weights"},
    {"key": "color_agtron", "label": "Color (Agtron)", "unit": "", "group": "Outcome"},
    {"key": "cupping_score", "label": "Cupping score", "unit": "", "group": "Outcome"},
    {"key": "rating", "label": "Rating", "unit": "/5", "group": "Outcome"},
    {"key": "bean_density_g_l", "label": "Bean density", "unit": "g/L", "group": "Beans"},
    {"key": "bean_moisture_pct", "label": "Bean moisture", "unit": "%", "group": "Beans"},
    {"key": "bean_altitude_m", "label": "Bean altitude", "unit": "m", "group": "Beans"},
]
METRIC_KEYS = [m["key"] for m in METRICS]

# Milestone -> (time key, temperature key)
_MILESTONES = {
    "TURNING_POINT": ("tp_time_s", "tp_temp_c"),
    "DRY_END": ("dry_end_time_s", "dry_end_temp_c"),
    "FC_START": ("fc_start_time_s", "fc_start_temp_c"),
    "FC_END": ("fc_end_time_s", "fc_end_temp_c"),
    "DROP": ("drop_time_s", "drop_temp_c"),
}


def _nearest(profile: list[dict], times: list[float], t: float, key: str) -> Optional[float]:
    """The reading of `key` at the sample closest to time `t`."""
    if not profile:
        return None
    i = bisect.bisect_left(times, t)
    candidates = [j for j in (i - 1, i) if 0 <= j < len(profile)]
    best = min(candidates, key=lambda j: abs(times[j] - t))
    value = profile[best].get(key)
    return None if value is None else float(value)


def alog_metrics(roast: Roast) -> dict[str, Optional[float]]:
    """Everything that can be worked out from the recorded curve and events."""
    out: dict[str, Optional[float]] = {k: None for k in METRIC_KEYS}
    profile = [p.model_dump() for p in roast.profile]
    if not profile:
        return out
    times = [p["time_s"] for p in profile]
    events = events_by_type(roast)

    charge = events.get("CHARGE")
    t0 = charge.time_s if charge else None
    if charge:
        out["charge_temp_c"] = _nearest(profile, times, charge.time_s, "bt")

    for event_type, (time_key, temp_key) in _MILESTONES.items():
        event = events.get(event_type)
        if event is None:
            continue
        if t0 is not None:
            out[time_key] = round(event.time_s - t0, 1)
        out[temp_key] = _nearest(profile, times, event.time_s, "bt")

    drop = events.get("DROP")
    if charge and drop and drop.time_s > charge.time_s:
        out["duration_s"] = round(drop.time_s - charge.time_s, 1)

    for phase in phase_breakdown(events):
        name = {"Dry": "dry", "Maillard": "maillard", "Development": "development"}.get(phase["phase"])
        if name is None:
            continue
        out[f"{name}_time_s"] = phase["duration_s"]
        if name != "development":
            out[f"{name}_pct"] = phase["pct_of_roast"]
        else:
            out["dtr_pct"] = phase["pct_of_roast"]

    tp = events.get("TURNING_POINT")
    tp_time = tp.time_s if tp else (t0 if t0 is not None else times[0])
    rors = [p["ror_bt"] for p in profile if p.get("ror_bt") is not None and p["time_s"] >= tp_time]
    if rors:
        out["max_ror"] = round(max(rors), 1)
    fc = events.get("FC_START")
    if fc:
        out["ror_at_fc_start"] = _round(_nearest(profile, times, fc.time_s, "ror_bt"))
    if drop:
        out["ror_at_drop"] = _round(_nearest(profile, times, drop.time_s, "ror_bt"))

    if "TURNING_POINT" in events:
        flags = ror_flags(profile, events)
        out["ror_crashes"] = float(len(flags["crashes"]))
        out["ror_flatlines"] = float(len(flags["flatlines"]))
        out["ror_flicks"] = float(len(flags["flicks"]))
    return out


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 1)


def row_source(row: dict) -> str:
    """Where the roast's data came from: "recorded" from a device, "uploaded"
    from a log file, or "replay" -- a roast made by playing a saved log back,
    which just repeats that log's data."""
    if row.get("mode") == "alog_playback":
        return "replay" if row.get("source_alog_path") else "uploaded"
    return "recorded"


def build_row(row: dict, curve_metrics: dict, bean: Optional[dict], tags: list[str]) -> dict:
    """One roast as a flat record: who/what/when, every metric, and the
    fields used for grouping."""
    green, roasted = row.get("weight_green_g"), row.get("weight_roasted_g")
    loss = None
    if green and roasted is not None:
        loss = round((1 - roasted / green) * 100, 1)

    metrics = dict(curve_metrics)
    metrics.update(
        {
            "weight_green_g": green,
            "weight_roasted_g": roasted,
            "weight_loss_pct": loss,
            "color_agtron": row.get("color_agtron"),
            "cupping_score": row.get("cupping_score"),
            "rating": None if row.get("rating") is None else float(row["rating"]),
            "bean_density_g_l": bean.get("density_g_l") if bean else None,
            "bean_moisture_pct": bean.get("moisture_pct") if bean else None,
            "bean_altitude_m": bean.get("altitude_m") if bean else None,
        }
    )
    beans = (bean["name"] if bean else None) or row.get("beans") or None
    return {
        "id": row["id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "mode": row["mode"],
        "status": row["status"],
        "roaster": row.get("created_by_username"),
        "source": row_source(row),
        "beans": beans,
        "origin": bean.get("origin") if bean else None,
        "process": bean.get("process") if bean else None,
        "tags": tags,
        "simulated": "simulated" in tags,
        "tasting_notes": row.get("tasting_notes"),
        "metrics": metrics,
    }
