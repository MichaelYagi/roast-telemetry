"""Read/write ``.alog`` roast-log files.

This app writes exactly one ``.alog`` shape: the format's native
one, built by ``roast_to_native_alog_dict``/``save_native_alog``
below -- so a roast recorded by this app opens directly in any software
that reads ``.alog``. An earlier
version wrote its own minimal JSON shape instead (only meant to round-trip
through this app's own reader, not readable elsewhere) -- ``load_alog``
below still reads that shape too, purely so roasts already on disk from
before this change keep working, not because anything writes it anymore.

Verified against an actual real-world ``.alog`` export: the file is a
Python dict literal (``str(dict)``, meant to be loaded with ``eval`` --
we use ``ast.literal_eval`` instead, which is safe), with
``timex``/``temp1`` (ET)/``temp2`` (BT) arrays, ``weight`` as ``[green,
roasted, unit]``, named roast-phase markers in an 8-element ``timeindex``
array (CHARGE/DRY_END/FC_START/FC_END/SC_START/SC_END/DROP/COOL_END, 0 =
not recorded -- confirmed by cross-checking every non-zero index's ET/BT
against the file's own ``computed`` block), Turning Point separately at
``computed['TP_idx']``, and manual control-channel adjustments as
parallel arrays (``specialevents`` = indices into ``timex``,
``specialeventstype`` = index into ``etypes`` e.g. ``['Air','Drum',
'Damper','Burner','--']``, ``specialeventsvalue``, and optionally
``specialeventsStrings`` for a custom label). The format carries many
more fields (energy/AUC accounting, PID tuning, alarms, ...) that this
platform has no use for -- see ``roast_to_native_alog_dict``'s
docstring for how those get handled on write (left out; readers fall
back to their own defaults).
"""
from __future__ import annotations

import ast
import bisect
import copy
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from roast_heuristics.ror import DEFAULT_SPAN_S, rate_of_rise_series

from .alog_profile_base import new_profile_base


def load_alog(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # A native .alog is a Python dict literal (str(dict),
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


# The format's fixed manual/continuous control-channel vocabulary and
# its index into it -- same convention _extract_continuous_channels below
# already reads (`{N}` extraname labels, specialeventstype indices).
NATIVE_ETYPES = ["Air", "Drum", "Damper", "Burner", "--"]
_CHANNEL_INDEX = {"Air": 0, "Drum": 1, "Damper": 2, "Burner": 3}

# CHARGE/DRY_END/FC_START/FC_END/SC_START/SC_END/DROP/COOL_END, in
# timeindex's fixed order -- Turning Point is deliberately excluded, same
# as the format itself (it lives in computed['TP_idx'] instead, see below).
_TIMEINDEX_TYPES = [
    "CHARGE", "DRY_END", "FC_START", "FC_END", "SC_START", "SC_END", "DROP", "COOL_END",
]

def _nearest_index(sorted_times: list, t: float) -> int:
    """Index into `sorted_times` closest to `t` -- events fire at whatever
    the roast's clock happened to read, which won't always land exactly
    on a sample time_s."""
    if not sorted_times:
        return 0
    idx = bisect.bisect_left(sorted_times, t)
    if idx <= 0:
        return 0
    if idx >= len(sorted_times):
        return len(sorted_times) - 1
    before, after = sorted_times[idx - 1], sorted_times[idx]
    return idx - 1 if (t - before) <= (after - t) else idx


def _fill_and_floatify(values: list) -> list:
    """The format's "extra device" temperature arrays (extratemp1/2)
    aren't built to tolerate gaps the way the main BT/ET channels are --
    a `None` anywhere in one is exactly what made a real export get
    rejected outright as an invalid file instead of just
    rendering with a hole in the curve. Heater/Fan/Drum legitimately
    start as None for the first tick or two of a real roast, before the
    operator has touched a slider -- forward-fill (and, for that leading
    gap specifically, backward-fill from the first real value) instead of
    ever emitting one. Also normalizes int/float mixing from upstream
    (some engines report a control value as a bare int) -- real
    files are consistently float."""
    filled: list[Optional[float]] = []
    last: Optional[float] = None
    for v in values:
        if v is not None:
            last = float(v)
        filled.append(last)
    first_known = next((v for v in filled if v is not None), 0.0)
    return [v if v is not None else first_known for v in filled]


def roast_to_native_alog_dict(
    *,
    title: str,
    profile: list,
    events: list,
    notes: list,
    beans: Optional[str] = None,
    weight_green_g: Optional[float] = None,
    weight_roasted_g: Optional[float] = None,
    roastertype: Optional[str] = None,
    roastdate: Optional[str] = None,
) -> dict:
    """Build a file other .alog readers can actually open -- this
    is the only .alog shape this app writes (see this module's own
    docstring). Readers want Python-literal syntax
    (True/False/None), the `timeindex`/`computed` milestone blocks, and
    parallel-array `specialevents`, not a list of dicts.

    Starts from alog_profile_base.new_profile_base() -- the format's
    field names with neutral values -- and overwrites the
    fields that make *this* roast's own curve, milestones, events, and
    metadata correct. Fields the format treats as optional are left out, so
    readers use their own defaults for them.

    Serialize the result with save_native_alog (Python-literal
    syntax, not JSON) -- see that function below.
    """
    data = new_profile_base()

    # Real .alog files always have a brief pre-charge lead-in, so index 0
    # being reserved as "not recorded" never collides with a real milestone
    # there. This app's own roasts don't -- recording starts *at* Charge,
    # so Charge is almost always sample 0 -- which would otherwise make
    # every exported roast's own Charge marker indistinguishable from "not
    # recorded" the moment it's read back. A single synthetic flat sample
    # one tick before the first real one sidesteps that ambiguity instead
    # of just accepting a broken Charge marker on every export.
    export_profile = profile
    if profile:
        lead_in = dict(profile[0])
        lead_in["time_s"] = profile[0]["time_s"] - 1.0
        export_profile = [lead_in, *profile]

    timex = [p["time_s"] for p in export_profile]
    temp1 = [p.get("et") for p in export_profile]  # the format's ET channel
    temp2 = [p.get("bt") for p in export_profile]  # the format's BT channel

    # Only the first occurrence of each milestone type -- matches
    # MILESTONE_SEQUENCE's "permanently set once marked" semantics.
    milestone_events: dict[str, dict] = {}
    for e in events:
        et = e.get("type")
        if et and et != "CUSTOM" and et not in milestone_events:
            milestone_events[et] = e

    def milestone_idx(event_type: str) -> int:
        e = milestone_events.get(event_type)
        if e is None or not timex:
            return 0  # the format's "not recorded" sentinel
        if event_type == "CHARGE":
            # Charge is always "the start of recording" in this app's
            # data model once it's actually been marked -- export_profile[1]
            # (the first real sample, right after the single synthetic
            # lead-in point), not something to nearest-match by timestamp
            # like every other milestone. Live-bridge/simulator sessions
            # fire Charge synchronously (resetting the clock) but only
            # append the first profile sample once a full tick interval
            # has elapsed, so Charge's own recorded time can legitimately
            # predate profile[0] -- a plain nearest-time search then hits
            # an exact tie between the lead-in and profile[0] whenever
            # that gap equals the lead-in's own 1-tick offset, and
            # _nearest_index's tie-break silently picked the lead-in slot
            # (index 0, the format's "not recorded" sentinel), losing the
            # Charge marker entirely on every affected roast. Confirmed
            # live with the simulator's default 1-second sample interval.
            return 1 if len(timex) > 1 else 0
        return _nearest_index(timex, e["time_s"])

    timeindex = [milestone_idx(t) for t in _TIMEINDEX_TYPES]
    charge_idx, dry_idx, fcs_idx, fce_idx, scs_idx, sce_idx, drop_idx, coolend_idx = timeindex

    def time_at(idx: int) -> Optional[float]:
        return timex[idx] if timex and 0 < idx < len(timex) else None

    def bt_at(idx: int) -> Optional[float]:
        return temp2[idx] if temp2 and 0 < idx < len(temp2) else None

    def et_at(idx: int) -> Optional[float]:
        return temp1[idx] if temp1 and 0 < idx < len(temp1) else None

    tp_event = milestone_events.get("TURNING_POINT")
    tp_idx = _nearest_index(timex, tp_event["time_s"]) if tp_event and timex else None

    computed = dict(data.get("computed") or {})
    # None (not 0) when there's no real Turning Point -- unlike the other
    # milestones, our reader's own _extract_named_milestones treats
    # TP_idx == 0 as a *valid* recorded index (matching the format's
    # tolerance for TP legitimately landing on the first sample), so
    # writing 0 here for "not recorded" would fabricate a bogus Turning
    # Point at the synthetic lead-in sample every time one wasn't fired.
    computed["TP_idx"] = tp_idx
    if tp_idx is not None and timex and 0 <= tp_idx < len(timex):
        computed["TP_time"] = timex[tp_idx]
        computed["TP_ET"] = et_at(tp_idx) if tp_idx > 0 else (temp1[0] if temp1 else None)
        computed["TP_BT"] = bt_at(tp_idx) if tp_idx > 0 else (temp2[0] if temp2 else None)
    computed["CHARGE_ET"] = et_at(charge_idx)
    computed["CHARGE_BT"] = bt_at(charge_idx)
    computed["DRY_time"], computed["DRY_ET"], computed["DRY_BT"] = time_at(dry_idx), et_at(dry_idx), bt_at(dry_idx)
    computed["FCs_time"], computed["FCs_ET"], computed["FCs_BT"] = time_at(fcs_idx), et_at(fcs_idx), bt_at(fcs_idx)
    computed["DROP_time"], computed["DROP_ET"], computed["DROP_BT"] = time_at(drop_idx), et_at(drop_idx), bt_at(drop_idx)
    computed["COOL_time"], computed["COOL_ET"], computed["COOL_BT"] = time_at(coolend_idx), et_at(coolend_idx), bt_at(coolend_idx)

    # Phase durations (dryphasetime == DRY_time, midphasetime ==
    # FCs_time - DRY_time, etc, since profile time_s is already
    # charge-relative, i.e. t=0 is Charge).
    dry_t, fcs_t, drop_t, cool_t = computed["DRY_time"], computed["FCs_time"], computed["DROP_time"], computed["COOL_time"]
    if dry_t is not None:
        computed["dryphasetime"] = dry_t
    if dry_t is not None and fcs_t is not None:
        computed["midphasetime"] = fcs_t - dry_t
    if fcs_t is not None and drop_t is not None:
        computed["finishphasetime"] = drop_t - fcs_t
    if drop_t is not None and cool_t is not None:
        computed["coolphasetime"] = cool_t - drop_t
    if drop_t is not None:
        computed["totaltime"] = drop_t

    if weight_green_g:
        computed["weightin"] = weight_green_g
        # is not None, not a truthy check -- weight_roasted_g == 0 is a
        # real, legitimate measurement (a total-loss/scorched batch),
        # not the same as "never weighed". A truthy check here silently
        # dropped weight-loss/yield data from the export for exactly the
        # roasts where it's most worth recording.
        if weight_roasted_g is not None:
            computed["weightout"] = weight_roasted_g
            loss_pct = (weight_green_g - weight_roasted_g) / weight_green_g * 100
            computed["weight_loss"] = round(loss_pct, 1)
            computed["total_yield"] = weight_roasted_g
            computed["total_loss"] = round(loss_pct, 1)

    # Manual control-channel adjustments (CUSTOM events with a channel) --
    # The format's parallel-array log, not a list of dicts.
    custom_events = [e for e in events if e.get("type") == "CUSTOM"]
    specialevents, specialeventstype, specialeventsvalue, specialeventsStrings = [], [], [], []
    for e in custom_events:
        idx = _nearest_index(timex, e["time_s"]) if timex else 0
        specialevents.append(idx)
        specialeventstype.append(_CHANNEL_INDEX.get(e.get("channel"), 4))
        specialeventsvalue.append(e.get("value") if e.get("value") is not None else 0.0)
        specialeventsStrings.append(e.get("label") or "")

    # Heater/Fan/Drum as continuous "extra device" channels (the
    # format's `{N}` extraname convention, same one _extract_continuous_channels
    # below already reads back) -- these are per-sample power settings,
    # not discrete manual adjustments, so they belong here, not in
    # specialevents. All three share this roast's own timeline, so one
    # shared extratimex entry per channel is enough (no resampling needed).
    extraname1 = ["{3}", "{0}", "{1}"]  # Burner, Air, Drum
    extratemp1 = [
        _fill_and_floatify([p.get("heater_pct") for p in export_profile]),
        _fill_and_floatify([p.get("fan_pct") for p in export_profile]),
        _fill_and_floatify([p.get("drum_speed_pct") for p in export_profile]),
    ]
    # Bank 2 keeps the base profile's slot count (EXTRA_SLOTS) rather than
    # being emptied -- roughly a dozen per-device lists (extradevicecolor2,
    # extraCurveVisibility2, extraNoneTempHint2, ...) are all sized to
    # match it, and readers index those by device position regardless of
    # what's actually in extraname2/extratemp2 (a mismatch was the cause
    # of a "setProfile() list index out of range" crash). Slot 0 is used
    # for DT (drum space temp -- a real third probe on the FZ-94, its own
    # Modbus slave ID, not the same thing as BT/ET; see
    # modbus_bridge/engine.py) when available; any further role=EXTRA
    # channels from a DeviceProfile (see RoastProfilePoint.extra) take the
    # remaining slots, ordered by first appearance in this roast's own
    # profile. There are only EXTRA_SLOTS slots in total, a real, fixed
    # ceiling; anything past that still lives in profile[i]["extra"] for
    # the live chart/readouts, it just doesn't round-trip through this
    # export. A slot with nothing in it stays a flat placeholder curve
    # (hidden by default, see extraCurveVisibility2 below).
    extra_labels: list[str] = []
    for p in export_profile:
        for label in p.get("extra") or {}:
            if label not in extra_labels:
                extra_labels.append(label)

    # _fill_and_floatify defaults an all-None series to a flat 0.0 (see
    # its own docstring -- correct for Heater/Fan/Drum, which legitimately
    # start at 0% before an operator touches anything, but wrong for DT:
    # a roast with no third probe at all (simulator, alog_playback, or
    # any Modbus profile without a dt channel) must not come out the
    # other end looking like a real probe reading a flat 0C the whole
    # roast. Only claim slot 0 as "DT" when there's genuinely at least
    # one real reading to back it up; otherwise it's just another unused
    # placeholder slot, same as the else branch below.
    has_dt = any(p.get("dt") is not None for p in export_profile)
    extraname2 = list(data.get("extraname2") or [])
    extratemp2 = []
    extra2_used = []  # which slots carry real data (the rest are placeholders)
    for i in range(len(extraname2)):
        if i == 0 and has_dt:
            extraname2[0] = "DT"
            extratemp2.append(_fill_and_floatify([p.get("dt") for p in export_profile]))
            extra2_used.append(True)
        elif i >= 1 and i - 1 < len(extra_labels):
            label = extra_labels[i - 1]
            extraname2[i] = label
            extratemp2.append(_fill_and_floatify([(p.get("extra") or {}).get(label) for p in export_profile]))
            extra2_used.append(True)
        else:
            extratemp2.append([0.0] * len(timex))
            extra2_used.append(False)
    extratimex = [timex] * max(len(extraname1), len(extraname2), 1)

    roastdate_dt = None
    if roastdate:
        try:
            roastdate_dt = datetime.fromisoformat(roastdate)
        except ValueError:
            pass

    # The format has no native per-timestamp `notes` list at
    # all -- only this single free-text `roastingnotes` field (confirmed
    # against real exports: no `'notes':` key anywhere in them).
    # alog_dict_to_points below parses this same "[Ns] text" format back
    # into individual note entries, so this app's own reader still
    # recovers them -- writing an extra `notes` key here (a shape the
    # format doesn't have) was tried first and is exactly what made
    # other readers reject the file as invalid.
    notes_text = "\n".join(_format_note_line(n) for n in notes)

    # Temp axis range, sized to this roast's own temperatures. 0 as a floor (a roast chart never
    # needs to show sub-zero), max recorded temp rounded up to the next
    # 25 with a little headroom above it so the curve doesn't touch the
    # top edge.
    all_temps = [t for t in (temp1 + temp2) if t is not None]
    if all_temps:
        computed_ymax = (int(max(all_temps)) // 25 + 2) * 25

    data.update({
        "roastUUID": str(uuid.uuid4()),
        "mode": "C",
        "title": title,
        "beans": beans or "",
        "roastingnotes": notes_text,
        "weight": [weight_green_g or 0.0, weight_roasted_g or 0.0, "g"],
        "roastertype": roastertype or "Roast Telemetry (simulated)",
        "roastdate": roastdate_dt.strftime("%a %b %d %Y") if roastdate_dt else data.get("roastdate", ""),
        "roastisodate": roastdate_dt.strftime("%Y-%m-%d") if roastdate_dt else data.get("roastisodate", ""),
        "roasttime": roastdate_dt.strftime("%H:%M:%S") if roastdate_dt else data.get("roasttime", ""),
        # Readers show this (a Unix timestamp), not the
        # roastdate/roastisodate/roasttime strings above -- without it the
        # date shown in the app would be blank, regardless of what those
        # string fields say.
        "roastepoch": int(roastdate_dt.timestamp()) if roastdate_dt else data.get("roastepoch", 0),
        **({"ymin": 0, "ymax": computed_ymax} if all_temps else {}),
        "timex": timex,
        # The format's schema is list[float]: a missing reading is -1, never None.
        "temp1": [-1.0 if t is None else float(t) for t in temp1],
        "temp2": [-1.0 if t is None else float(t) for t in temp2],
        "timeindex": timeindex,
        # Every computed field is a plain number that may be *absent* but
        # never None -- readers reject the whole file as invalid on a None
        # here, so unrecorded milestones are left out, as real files do.
        "computed": {k: v for k, v in computed.items() if v is not None},
        "etypes": NATIVE_ETYPES,
        "specialevents": specialevents,
        "specialeventstype": specialeventstype,
        "specialeventsvalue": specialeventsvalue,
        "specialeventsStrings": specialeventsStrings,
        "extraname1": extraname1,
        "extratemp1": extratemp1,
        "extraname2": extraname2,
        "extratemp2": extratemp2,
        "extratimex": extratimex,
        # Only slots that carry real data are drawn; placeholders stay hidden.
        "extraCurveVisibility2": (extra2_used + [False] * 10)[:10],
    })
    return data


def save_native_alog(path: str, data: dict) -> None:
    """Writes Python-literal syntax (True/False/None, single-quoted
    strings), matching the native file format -- NOT JSON.
    `repr()` on a dict/list/str/int/float/bool/None tree round-trips
    exactly through ast.literal_eval (what load_alog uses to read real
    files), which is the only property that actually matters
    here -- byte-for-byte formatting doesn't need to match any other
    writer, just be parseable."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(repr(data))


def _extract_weight(data: dict) -> tuple[Optional[float], Optional[float]]:
    w = data.get("weight")
    if isinstance(w, dict):
        return w.get("green_g"), w.get("roasted_g")
    if isinstance(w, (list, tuple)) and len(w) >= 2:
        # Format convention: [in_amount, out_amount, unit].
        green, roasted = w[0], w[1]
        return (green or None), (roasted or None)
    return None, None


# The format's ``timeindex`` field is 8 indices into ``timex`` for these
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

    # Turning Point isn't in timeindex -- it's the
    # BT minimum, stored it separately under computed['TP_idx'].
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
    in real files as parallel arrays rather than objects."""
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
    which is returned as-is. Otherwise this is a real .alog file --
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


def alog_created_at(data: dict) -> Optional[str]:
    """When a roast file says it was roasted, as a proper ISO date-time (UTC),
    or None if it says nothing usable. Files carry the moment several ways: a
    Unix timestamp, an ISO date plus a time, and a human-readable string
    ("Sat Mar 01 2025") -- tried in that order."""
    epoch = data.get("roastepoch")
    if isinstance(epoch, (int, float)) and epoch > 0:
        try:
            return datetime.fromtimestamp(epoch, timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            pass
    iso_date, roast_time = data.get("roastisodate"), data.get("roasttime")
    if iso_date:
        try:
            when = datetime.fromisoformat(f"{iso_date}T{roast_time or '00:00:00'}")
            return when.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass
    text = data.get("roastdate")
    if text:
        for fmt in ("%a %b %d %Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(str(text), fmt).replace(tzinfo=timezone.utc).isoformat()
            except ValueError:
                continue
    return None


def _extract_roastdate(data: dict) -> Optional[str]:
    iso_date, roast_time = data.get("roastisodate"), data.get("roasttime")
    if iso_date and roast_time:
        return f"{iso_date}T{roast_time}"
    return data.get("roastdate")


def _compute_ror(timex: list, temps: list, window_s: float = DEFAULT_SPAN_S) -> list:
    """Rate-of-rise (degrees/minute) at every point, worked out the same
    way as live readings (roast_heuristics/ror.py: a rate over a ``window_s``
    span, then lightly smoothed) so an imported roast and a live one show
    comparable numbers.

    Real .alog files store RoR only as phase averages in ``computed``,
    not a full per-sample array -- this reconstructs one from the raw
    temperature curve so RoR is available for any imported file.
    """
    ror = rate_of_rise_series(timex, temps, span_s=window_s)
    # Until a full span of readings exists (e.g. the first ~20s after charge)
    # the rate is taken over a much shorter interval, so a small raw delta
    # divided by a small dt and scaled by 60 wildly amplifies sensor noise into
    # physically implausible spikes (seen as RoR in the hundreds of deg/min
    # right at charge). Real roast software simply leaves RoR blank until the
    # window is actually full.
    start = timex[0] if timex else 0.0
    return [
        None if (v is None or timex[i] - start < window_s * 0.9) else v
        for i, v in enumerate(ror)
    ]


# Machines that actually log Burner/Air/Drum telemetry (e.g.
# Kaleido) report it as *continuous* per-sample "extra device" channels --
# parallel `extratemp1`/`extratemp2` arrays (each itself a list of one
# array per extra device) plus their own `extratimex` timeline, with
# `extraname1`/`extraname2` giving each array's label. A label of the form
# `{N}` is the format's convention for "use etypes[N]'s name" (e.g. `{3}` ->
# etypes[3] == "Burner"), tying an extra channel back to the same
# Burner/Air/Drum/Damper vocabulary used by manual specialevents. This is
# distinct from -- and far higher resolution than -- the specialevents log,
# which only records manual slider *adjustments*, not the resulting value
# at every sample.
_CHANNEL_FIELD = {"Burner": "heater_pct", "Air": "fan_pct", "Drum": "drum_speed_pct"}
_EXTRANAME_ETYPE_RE = re.compile(r"^\{(\d+)\}$")
# Not an etype-indexed control channel like the above -- a genuine third
# temperature probe (Coffee-Tech FZ-94's drum space temp, its own
# Modbus slave ID). Written/read by plain label, same as how a real
# Kaleido file's own SV/AT/AH sensors are plain-labeled rather than
# `{N}`-indexed -- this app just happens to recognize "DT" specifically.
_DT_EXTRANAME_LABEL = "DT"


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
    """Map the format's extra-device channels onto our heater_pct/
    fan_pct/drum_speed_pct/dt fields. Any *other* plain-text label (not
    DT, not a `{N}` etype reference) becomes an `extra:<label>`
    pseudo-field instead of being silently dropped -- see
    alog_dict_to_points below, which folds those into each point's own
    `extra` dict (see RoastProfilePoint.extra). Covers both this app's
    own role=EXTRA DeviceProfile channels round-tripping through
    roast_to_native_alog_dict's extraname2 slots, and a genuine
    third-party .alog file (e.g. a real Kaleido export) carrying its
    own extra sensors (SV/AT/AH, ...) this app has no other name for --
    previously discarded entirely, now preserved generically."""
    etypes = data.get("etypes") or []
    extratimex = data.get("extratimex") or []
    pairs = [
        (data.get("extraname1") or [], data.get("extratemp1") or []),
        (data.get("extraname2") or [], data.get("extratemp2") or []),
    ]

    result: dict[str, list] = {}
    for names, temps in pairs:
        for i, name in enumerate(names):
            if i >= len(temps):
                continue
            if name == _DT_EXTRANAME_LABEL:
                field = "dt"
            else:
                m = _EXTRANAME_ETYPE_RE.match(str(name)) if name else None
                if m:
                    etype_idx = int(m.group(1))
                    field = _CHANNEL_FIELD.get(etypes[etype_idx]) if 0 <= etype_idx < len(etypes) else None
                elif name:
                    field = f"extra:{name}"
                else:
                    field = None
            if not field or field in result:
                continue
            values = temps[i]
            src_times = extratimex[i] if i < len(extratimex) else timex
            if not values or len(values) != len(src_times):
                continue
            result[field] = _step_hold_align(src_times, values, timex)
    return result


def _f_to_c(value):
    # -1 is the format's "no reading" marker, not a temperature.
    if not isinstance(value, (int, float)) or value == -1:
        return value
    return (value - 32.0) * 5.0 / 9.0


def _c_to_f(value):
    if not isinstance(value, (int, float)) or value == -1:
        return value
    return value * 9.0 / 5.0 + 32.0


# Milestone temperature fields inside `computed` -- everything else in that
# dict is a time offset, a weight/loss number, or an index, never a
# temperature, so this is an explicit whitelist, not a blanket scan.
_COMPUTED_TEMP_KEYS = (
    "TP_ET", "TP_BT", "CHARGE_ET", "CHARGE_BT", "DRY_ET", "DRY_BT",
    "FCs_ET", "FCs_BT", "DROP_ET", "DROP_BT", "COOL_ET", "COOL_BT",
)


def _etypes_role_is_percent(match: re.Match) -> bool:
    idx = int(match.group(1))
    return NATIVE_ETYPES[idx] in _CHANNEL_FIELD if 0 <= idx < len(NATIVE_ETYPES) else False


def _percent_channel_indices(names: list) -> set[int]:
    """Which indices in an extraname1/extraname2 list are this app's own
    Burner/Air/Drum *percentage* channels (the `{N}` etype convention this
    module's own writer below uses for extraname1 -- see
    roast_to_native_alog_dict) rather than a genuine temperature probe.
    Those must never go through a Celsius<->Fahrenheit conversion: they
    aren't a temperature in either unit, and (for extraname1 specifically)
    a real third-party file could legitimately use these same slots for an
    actual extra temperature probe -- this only excludes the specific
    labels this app's own writer produces, not the whole bank."""
    indices = set()
    for i, name in enumerate(names):
        m = _EXTRANAME_ETYPE_RE.match(str(name)) if name else None
        if m and _etypes_role_is_percent(m):
            indices.add(i)
    return indices


def _fahrenheit_file_to_celsius(data: dict) -> dict:
    """A copy of `data` with every temperature (and rate of rise) converted
    from Fahrenheit to Celsius. Files record which unit they use in `mode`;
    everything in this app is Celsius."""
    out = dict(data)
    for key in ("temp1", "temp2"):
        out[key] = [_f_to_c(v) for v in data.get(key) or []]
    for names_key, temps_key in (("extraname1", "extratemp1"), ("extraname2", "extratemp2")):
        if data.get(temps_key):
            skip = _percent_channel_indices(data.get(names_key) or [])
            out[temps_key] = [
                series if (i in skip or not isinstance(series, list)) else [_f_to_c(v) for v in series]
                for i, series in enumerate(data[temps_key])
            ]
    for key in ("ror_bt", "ror_et"):
        if data.get(key):
            out[key] = [None if v is None else (v * 5.0 / 9.0 if isinstance(v, (int, float)) else v) for v in data[key]]
    return out


def _celsius_dict_to_fahrenheit(data: dict) -> dict:
    """The write-side mirror of _fahrenheit_file_to_celsius -- everything
    this app itself ever stores is Celsius, so this is only ever applied
    once, right before handing an already-built dict to a caller that
    wants a Fahrenheit-unit export (a roast/analysis download endpoint
    reading the current Temperature Unit setting), never to what's kept
    on disk. Converts the same fields that function converts, in reverse,
    plus `computed`'s own milestone temperatures and the chart's
    ymin/ymax -- none of which _fahrenheit_file_to_celsius needs to touch
    on the read side, since alog_dict_to_points below never reads any of
    those three back into the app at all."""
    out = dict(data)
    out["mode"] = "F"
    for key in ("temp1", "temp2"):
        out[key] = [_c_to_f(v) for v in data.get(key) or []]
    for names_key, temps_key in (("extraname1", "extratemp1"), ("extraname2", "extratemp2")):
        if data.get(temps_key):
            skip = _percent_channel_indices(data.get(names_key) or [])
            out[temps_key] = [
                series if (i in skip or not isinstance(series, list)) else [_c_to_f(v) for v in series]
                for i, series in enumerate(data[temps_key])
            ]
    for key in ("ror_bt", "ror_et"):
        if data.get(key):
            out[key] = [None if v is None else (v * 9.0 / 5.0 if isinstance(v, (int, float)) else v) for v in data[key]]
    computed = dict(data.get("computed") or {})
    for key in _COMPUTED_TEMP_KEYS:
        if computed.get(key) is not None:
            computed[key] = _c_to_f(computed[key])
    out["computed"] = computed
    for key in ("ymin", "ymax"):
        if data.get(key) is not None:
            out[key] = _c_to_f(data[key])
    return out


def alog_dict_to_points(data: dict) -> dict:
    """Flatten an .alog dict back into profile/events/notes lists."""
    if str(data.get("mode", "C")).upper() == "F":
        data = _fahrenheit_file_to_celsius(data)
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
    # extra:<label> pseudo-fields (see _extract_continuous_channels) fold
    # into each point's own `extra` dict below rather than becoming top-
    # level RoastProfilePoint fields the way heater_pct/fan_pct/dt do --
    # there's no fixed set of these, unlike the other three.
    extra_channels = {k[len("extra:"):]: v for k, v in continuous.items() if k.startswith("extra:")}

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
            "dt": continuous["dt"][i] if "dt" in continuous else c.get("dt"),
            "extra": {label: values[i] for label, values in extra_channels.items() if values[i] is not None},
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
        "notes": _extract_notes(data),
    }


# One note per line: "[<seconds>s] text", optionally with the moment it was
# written and who wrote it: "[<seconds>s <UTC time> @<who>] text". Files
# written before those were kept hold "[<seconds>s] text", which this still
# reads.
_ROASTINGNOTES_LINE_RE = re.compile(
    r"^\[(\d+(?:\.\d+)?)s(?: (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ))?(?: @([^\]]+))?\] (.*)$"
)


def _format_note_line(note: dict) -> str:
    author = re.sub(r"[\]\r\n]", "", str(note.get("author") or "")).strip()
    when = f" {note['created_at']}" if note.get("created_at") else ""
    who = f" @{author}" if author else ""
    return f"[{float(note.get('time_s') or 0):.1f}s{when}{who}] {note.get('text', '')}"


def note_timestamp() -> str:
    """The moment a note is written, in UTC to the second."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def round_note_time(time_s: float) -> float:
    """Notes keep tenths of a second -- what the saved line holds."""
    return round(float(time_s), 1)


def assign_note_ids(notes: list) -> list:
    """Gives each note an id made from its own content (time, author, text)
    so the same note has the same id in memory and after it's read back from
    the file, and an id held by a stale page simply stops matching once the
    note is edited or deleted -- it never lands on a different note. Notes
    with identical content get a numbered suffix."""
    seen: dict[str, int] = {}
    for n in notes:
        key = "\x00".join(
            [
                f"{float(n.get('time_s') or 0):.1f}",
                str(n.get("author") or ""),
                str(n.get("created_at") or ""),
                str(n.get("text") or ""),
            ]
        )
        digest = "n" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
        count = seen.get(digest, 0)
        seen[digest] = count + 1
        n["id"] = digest if count == 0 else f"{digest}-{count}"
    return notes


def _extract_notes(data: dict) -> list[dict]:
    """A real `notes` list (this app's own writer shape, or a future
    one if the format ever gets a documented per-item
    shape) is used as-is. The format has no such field today -- our own
    writer instead flattens notes into `roastingnotes` (its
    single free-text field, so a human opening the file elsewhere still
    sees them), one per line as "[<time_s>s] <text>" -- parsed back out
    here so this app's own reader still recovers individual notes."""
    raw = data.get("notes")
    if raw:
        return assign_note_ids([dict(n) for n in raw])
    notes = []
    for line in str(data.get("roastingnotes") or "").splitlines():
        m = _ROASTINGNOTES_LINE_RE.match(line)
        if m:
            notes.append(
                {"time_s": float(m.group(1)), "created_at": m.group(2), "author": m.group(3) or None, "text": m.group(4)}
            )
        elif line.strip():
            # Free text without a "[<time>s]" prefix (e.g. an imported
            # file's own roasting notes): keep it as a note at time 0 so
            # it shows up and isn't dropped the next time the file is
            # rewritten.
            notes.append({"time_s": 0.0, "created_at": None, "text": line.strip(), "author": None})
    return assign_note_ids(notes)
