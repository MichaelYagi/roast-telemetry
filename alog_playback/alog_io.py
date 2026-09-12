"""Read/write Artisan-compatible ``.alog`` files.

This module reads two shapes:

1. **Real Artisan files.** Verified against an actual Kaleido-exported
   ``.alog``: the file is a Python dict literal (``str(dict)``, meant to
   be loaded with ``eval`` -- we use ``ast.literal_eval`` instead, which
   is safe), with ``timex``/``temp1`` (ET)/``temp2`` (BT) arrays,
   ``weight`` as ``[green, roasted, unit]``, named roast-phase markers in
   an 8-element ``timeindex`` array (CHARGE/DRY_END/FC_START/FC_END/
   SC_START/SC_END/DROP/COOL_END, 0 = not recorded -- confirmed by cross-
   checking every non-zero index's ET/BT against the file's own
   ``computed`` block), Turning Point separately at
   ``computed['TP_idx']``, and manual control-channel adjustments as
   parallel arrays (``specialevents`` = indices into ``timex``,
   ``specialeventstype`` = index into ``etypes`` e.g. ``['Air','Drum',
   'Damper','Burner','--']``, ``specialeventsvalue``, and optionally
   ``specialeventsStrings`` for a custom label). Real Artisan carries many
   more fields (energy/AUC accounting, PID tuning, alarms, ...) that this
   platform has no use for and ignores.
2. **Our own writer's shape**, below -- plain JSON with event/note
   records as objects instead of parallel arrays, since round-tripping
   only needs to be internally consistent.

Schema this module writes (top level keys)::

    {
      "version": "roast-telemetry-1.0",
      "title": str,
      "roastdate": iso8601 str,
      "beans": str,
      "weight": {"green_g": float|None, "roasted_g": float|None},
      "machine": {"brand": str|None, "model": str|None},
      "timex": [float, ...],          # seconds since charge
      "temp1": [float|None, ...],     # ET, aligned with timex
      "temp2": [float|None, ...],     # BT, aligned with timex
      "ror_bt": [float|None, ...],
      "ror_et": [float|None, ...],
      "control": [{"time_s","heater_pct","fan_pct","drum_speed_pct"}, ...],
      "specialevents": [{"time_s","type","label","value"}, ...],
      "notes": [{"time_s","text","author"}, ...],
    }
"""
from __future__ import annotations

import ast
import bisect
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

ALOG_VERSION = "roast-telemetry-1.0"


def load_alog(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Real Artisan's native .alog is a Python dict literal (str(dict),
        # loaded back via eval) rather than strict JSON -- e.g. single
        # quotes, and True/False/None instead of true/false/null.
        # ast.literal_eval parses that safely without executing anything.
        data = ast.literal_eval(raw)

    for required in ("timex", "temp1", "temp2"):
        if required not in data:
            raise ValueError(f".alog file {path!r} is missing required field {required!r}")
    data.setdefault("specialevents", [])
    data.setdefault("notes", [])
    data.setdefault("control", [])
    data.setdefault("ror_bt", [None] * len(data["timex"]))
    data.setdefault("ror_et", [None] * len(data["timex"]))
    return data


def save_alog(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    data = {**data, "version": data.get("version", ALOG_VERSION)}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def roast_to_alog_dict(
    *,
    title: str,
    profile: list,
    events: list,
    notes: list,
    beans: Optional[str] = None,
    weight_green_g: Optional[float] = None,
    weight_roasted_g: Optional[float] = None,
    machine_brand: Optional[str] = None,
    machine_model: Optional[str] = None,
    roastdate: Optional[str] = None,
) -> dict:
    """Build an .alog-shaped dict from in-memory roast state.

    ``profile`` is a list of dicts with keys time_s/bt/et/ror_bt/ror_et.
    ``events``/``notes`` are lists of dicts as produced by the roast
    session (RoastEvent/RoastNote shapes).
    """
    return {
        "version": ALOG_VERSION,
        "title": title,
        "roastdate": roastdate or datetime.now(timezone.utc).isoformat(),
        "beans": beans,
        "weight": {"green_g": weight_green_g, "roasted_g": weight_roasted_g},
        "machine": {"brand": machine_brand, "model": machine_model},
        "timex": [p["time_s"] for p in profile],
        "temp1": [p.get("et") for p in profile],
        "temp2": [p.get("bt") for p in profile],
        "ror_bt": [p.get("ror_bt") for p in profile],
        "ror_et": [p.get("ror_et") for p in profile],
        "control": [
            {
                "time_s": p["time_s"],
                "heater_pct": p.get("heater_pct"),
                "fan_pct": p.get("fan_pct"),
                "drum_speed_pct": p.get("drum_speed_pct"),
            }
            for p in profile
        ],
        "specialevents": [dict(e) for e in events],
        "notes": [dict(n) for n in notes],
    }


def _extract_weight(data: dict) -> tuple[Optional[float], Optional[float]]:
    w = data.get("weight")
    if isinstance(w, dict):
        return w.get("green_g"), w.get("roasted_g")
    if isinstance(w, (list, tuple)) and len(w) >= 2:
        # Real Artisan convention: [in_amount, out_amount, unit].
        green, roasted = w[0], w[1]
        return (green or None), (roasted or None)
    return None, None


# Real Artisan's ``timeindex`` field is 8 indices into ``timex`` for these
# named roast-phase markers, in this fixed order (0 means "not recorded").
# Verified against a real .alog's `computed` block (CHARGE_ET/BT,
# DRY_ET/BT, FCs_ET/BT, DROP_ET/BT, COOL_ET/BT all matched temp1/temp2 at
# these exact indices).
TIMEINDEX_LABELS = [
    ("CHARGE", "Charge"),
    ("DRY_END", "Dry End"),
    ("FC_START", "First Crack Start"),
    ("FC_END", "First Crack End"),
    ("SC_START", "Second Crack Start"),
    ("SC_END", "Second Crack End"),
    ("DROP", "Drop"),
    ("COOL_END", "Cool End"),
]


def _extract_named_milestones(data: dict, timex: list, temp2: list) -> list[dict]:
    events = []
    timeindex = data.get("timeindex") or []
    for i, (etype, label) in enumerate(TIMEINDEX_LABELS):
        if i >= len(timeindex):
            break
        idx = timeindex[i]
        if not isinstance(idx, int) or idx <= 0 or idx >= len(timex):
            continue
        events.append({
            "id": f"milestone-{etype}",
            "time_s": timex[idx],
            "type": etype,
            "label": label,
            "value": temp2[idx] if idx < len(temp2) else None,
        })

    # Turning Point isn't in timeindex -- Artisan auto-computes it as the
    # BT minimum and stores it separately under computed['TP_idx'].
    tp_idx = (data.get("computed") or {}).get("TP_idx")
    if isinstance(tp_idx, int) and 0 <= tp_idx < len(timex):
        events.append({
            "id": "milestone-TURNING_POINT",
            "time_s": timex[tp_idx],
            "type": "TURNING_POINT",
            "label": "Turning Point",
            "value": temp2[tp_idx] if tp_idx < len(temp2) else None,
        })
    return events


def _extract_manual_events(data: dict, timex: list) -> list[dict]:
    """Manual control-channel adjustments (burner/air/drum/damper), logged
    by real Artisan as parallel arrays rather than objects."""
    idxs = data.get("specialevents") or []
    types = data.get("specialeventstype") or []
    values = data.get("specialeventsvalue") or []
    strings = data.get("specialeventsStrings") or []
    etypes = data.get("etypes") or []

    events = []
    for i, idx in enumerate(idxs):
        if not isinstance(idx, int) or idx < 0 or idx >= len(timex):
            continue
        channel = None
        if i < len(types) and isinstance(types[i], int) and 0 <= types[i] < len(etypes):
            channel = etypes[types[i]]
        value = values[i] if i < len(values) else None
        custom_label = strings[i] if i < len(strings) and strings[i] else None
        if custom_label:
            label = custom_label
        elif channel and value is not None:
            label = f"{channel} {value:g}"
        else:
            label = f"Event {i + 1}"
        events.append({
            "id": f"specialevent-{i}",
            "time_s": timex[idx],
            "type": "CUSTOM",
            "label": label,
            "value": value,
            "channel": channel,
        })
    return events


def _extract_events(data: dict, timex: list, temp2: list) -> list[dict]:
    """Our own writer emits ``specialevents`` as a list of dicts already,
    which is returned as-is. Otherwise this is a real Artisan file --
    combine its named milestones (timeindex/computed) with its manual
    control-channel event log (specialevents parallel arrays)."""
    raw = data.get("specialevents") or []
    if raw and isinstance(raw[0], dict):
        return list(raw)

    events = _extract_named_milestones(data, timex, temp2) + _extract_manual_events(data, timex)
    return sorted(events, key=lambda e: e["time_s"])


def _extract_machine(data: dict) -> dict:
    machine = data.get("machine")
    if isinstance(machine, dict):
        return machine
    roastertype = data.get("roastertype")
    return {"brand": None, "model": roastertype} if roastertype else {}


def _extract_roastdate(data: dict) -> Optional[str]:
    iso_date, roast_time = data.get("roastisodate"), data.get("roasttime")
    if iso_date and roast_time:
        return f"{iso_date}T{roast_time}"
    return data.get("roastdate")


def _compute_ror(timex: list, temps: list, window_s: float = 24.0) -> list:
    """Trailing-window rate-of-rise (degrees/minute), Artisan-style.

    Real Artisan files store RoR only as phase averages in ``computed``,
    not a full per-sample array -- this reconstructs one from the raw
    temperature curve so RoR is available for any imported file.
    """
    ror = [None] * len(timex)
    j = 0
    for i, t in enumerate(timex):
        target = t - window_s
        while j + 1 < i and timex[j + 1] <= target:
            j += 1
        if temps[i] is None or j >= i or temps[j] is None:
            continue
        dt = timex[i] - timex[j]
        # Until the trailing window is fully populated (e.g. the first ~24s
        # after charge), `dt` is much shorter than `window_s`, so dividing a
        # small raw delta by a small dt and scaling by 60 wildly amplifies
        # sensor noise into physically implausible spikes (seen as RoR in
        # the hundreds of deg/min right at charge). Real roast software
        # simply leaves RoR blank until the window is actually full.
        if dt <= 0 or dt < window_s * 0.9:
            continue
        ror[i] = (temps[i] - temps[j]) / dt * 60.0
    return ror


# Real Artisan machines that actually log Burner/Air/Drum telemetry (e.g.
# Kaleido) report it as *continuous* per-sample "extra device" channels --
# parallel `extratemp1`/`extratemp2` arrays (each itself a list of one
# array per extra device) plus their own `extratimex` timeline, with
# `extraname1`/`extraname2` giving each array's label. A label of the form
# `{N}` is Artisan's convention for "use etypes[N]'s name" (e.g. `{3}` ->
# etypes[3] == "Burner"), tying an extra channel back to the same
# Burner/Air/Drum/Damper vocabulary used by manual specialevents. This is
# distinct from -- and far higher resolution than -- the specialevents log,
# which only records manual slider *adjustments*, not the resulting value
# at every sample.
_CHANNEL_FIELD = {"Burner": "heater_pct", "Air": "fan_pct", "Drum": "drum_speed_pct"}
_EXTRANAME_ETYPE_RE = re.compile(r"^\{(\d+)\}$")


def _step_hold_align(src_times: list, src_values: list, target_times: list) -> list:
    """Resample a (possibly differently-timed) step-valued channel onto
    `target_times`, holding each sample's value forward until the next one
    (matching how a control-channel reading actually behaves between
    updates) rather than interpolating a slope that was never there."""
    if not src_times or not src_values:
        return [None] * len(target_times)
    out = []
    for t in target_times:
        idx = bisect.bisect_right(src_times, t) - 1
        out.append(src_values[idx] if idx >= 0 else src_values[0])
    return out


def _extract_continuous_channels(data: dict, timex: list) -> dict[str, list]:
    """Map real Artisan's extra-device channels onto our heater_pct/
    fan_pct/drum_speed_pct fields, aligned to the main `timex`."""
    etypes = data.get("etypes") or []
    extratimex = data.get("extratimex") or []
    pairs = [
        (data.get("extraname1") or [], data.get("extratemp1") or []),
        (data.get("extraname2") or [], data.get("extratemp2") or []),
    ]

    result: dict[str, list] = {}
    for names, temps in pairs:
        for i, name in enumerate(names):
            m = _EXTRANAME_ETYPE_RE.match(str(name)) if name else None
            if not m or i >= len(temps):
                continue
            etype_idx = int(m.group(1))
            if not (0 <= etype_idx < len(etypes)):
                continue
            field = _CHANNEL_FIELD.get(etypes[etype_idx])
            if not field or field in result:
                continue
            values = temps[i]
            src_times = extratimex[i] if i < len(extratimex) else timex
            if not values or len(values) != len(src_times):
                continue
            result[field] = _step_hold_align(src_times, values, timex)
    return result


def alog_dict_to_points(data: dict) -> dict:
    """Flatten an .alog dict back into profile/events/notes lists."""
    timex = data["timex"]
    temp1 = data["temp1"]
    temp2 = data["temp2"]
    ror_bt = data.get("ror_bt") or [None] * len(timex)
    ror_et = data.get("ror_et") or [None] * len(timex)
    if all(v is None for v in ror_bt):
        ror_bt = _compute_ror(timex, temp2)
    if all(v is None for v in ror_et):
        ror_et = _compute_ror(timex, temp1)
    control_list = data.get("control") or []
    control = {c["time_s"]: c for c in control_list if isinstance(c, dict) and "time_s" in c}
    continuous = _extract_continuous_channels(data, timex)

    profile = []
    for i, t in enumerate(timex):
        c = control.get(t, {})
        profile.append({
            "time_s": t,
            "et": temp1[i] if i < len(temp1) else None,
            "bt": temp2[i] if i < len(temp2) else None,
            "ror_bt": ror_bt[i] if i < len(ror_bt) else None,
            "ror_et": ror_et[i] if i < len(ror_et) else None,
            "heater_pct": continuous["heater_pct"][i] if "heater_pct" in continuous else c.get("heater_pct"),
            "fan_pct": continuous["fan_pct"][i] if "fan_pct" in continuous else c.get("fan_pct"),
            "drum_speed_pct": continuous["drum_speed_pct"][i] if "drum_speed_pct" in continuous else c.get("drum_speed_pct"),
        })

    weight_green_g, weight_roasted_g = _extract_weight(data)

    return {
        "title": data.get("title"),
        "beans": data.get("beans"),
        "weight_green_g": weight_green_g,
        "weight_roasted_g": weight_roasted_g,
        "machine": _extract_machine(data),
        "roastdate": _extract_roastdate(data),
        "profile": profile,
        "events": _extract_events(data, timex, temp2),
        "notes": data.get("notes") or [],
    }
