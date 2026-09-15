"""Read/write real Artisan-compatible ``.alog`` files.

This app writes exactly one ``.alog`` shape: real Artisan's own native
one, built by ``roast_to_artisan_native_dict``/``save_artisan_native_alog``
below -- so File > Open in real Artisan opens a roast recorded by this
app directly, same as any roast Artisan recorded itself. An earlier
version wrote its own minimal JSON shape instead (only meant to round-trip
through this app's own reader, not actually open in Artisan) -- ``load_alog``
below still reads that shape too, purely so roasts already on disk from
before this change keep working, not because anything writes it anymore.

Verified against an actual Kaleido-exported ``.alog``: the file is a
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
``specialeventsStrings`` for a custom label). Real Artisan carries many
more fields (energy/AUC accounting, PID tuning, alarms, ...) that this
platform has no use for -- see ``roast_to_artisan_native_dict``'s
docstring for how those get handled on write (cloned from a real,
confirmed-working export rather than guessed at).
"""
from __future__ import annotations

import ast
import bisect
import copy
import json
import os
import re
import uuid
from datetime import datetime
from typing import Any, Optional


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


# Real Artisan's fixed manual/continuous control-channel vocabulary and
# its index into it -- same convention _extract_continuous_channels below
# already reads (`{N}` extraname labels, specialeventstype indices).
ARTISAN_ETYPES = ["Air", "Drum", "Damper", "Burner", "--"]
_ARTISAN_CHANNEL_INDEX = {"Air": 0, "Drum": 1, "Damper": 2, "Burner": 3}

# CHARGE/DRY_END/FC_START/FC_END/SC_START/SC_END/DROP/COOL_END, in
# timeindex's fixed order -- Turning Point is deliberately excluded, same
# as real Artisan (it lives in computed['TP_idx'] instead, see below).
_ARTISAN_TIMEINDEX_TYPES = [
    "CHARGE", "DRY_END", "FC_START", "FC_END", "SC_START", "SC_END", "DROP", "COOL_END",
]

_ARTISAN_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "artisan_native_template.json")
_artisan_template_cache: Optional[dict] = None


def _load_artisan_template() -> dict:
    global _artisan_template_cache
    if _artisan_template_cache is None:
        with open(_ARTISAN_TEMPLATE_PATH, "r", encoding="utf-8") as f:
            _artisan_template_cache = json.load(f)
    return copy.deepcopy(_artisan_template_cache)


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
    """Real Artisan's "extra device" temperature arrays (extratemp1/2)
    aren't built to tolerate gaps the way its main BT/ET channels are --
    a `None` anywhere in one is exactly what made a real export get
    rejected outright as "Invalid artisan format" instead of just
    rendering with a hole in the curve. Heater/Fan/Drum legitimately
    start as None for the first tick or two of a real roast, before the
    operator has touched a slider -- forward-fill (and, for that leading
    gap specifically, backward-fill from the first real value) instead of
    ever emitting one. Also normalizes int/float mixing from upstream
    (some engines report a control value as a bare int) -- Artisan's own
    files are consistently float."""
    filled: list[Optional[float]] = []
    last: Optional[float] = None
    for v in values:
        if v is not None:
            last = float(v)
        filled.append(last)
    first_known = next((v for v in filled if v is not None), 0.0)
    return [v if v is not None else first_known for v in filled]


def roast_to_artisan_native_dict(
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
    """Build a file real Artisan can actually open (File > Open) -- this
    is the only .alog shape this app writes (see this module's own
    docstring). Real Artisan's loader wants Python-literal syntax
    (True/False/None), the `timeindex`/`computed` milestone blocks, and
    parallel-array `specialevents`, not a list of dicts.

    Rather than guess at the ~190 other fields a real Artisan save
    carries (colorimeter settings, alarm config, BTU/CO2 accounting,
    cupping scores, ...) and risk the file being rejected over one we
    didn't know was required, this clones a real, confirmed-working
    Artisan export (artisan_native_template.json, captured from an
    actual roast) and only overwrites the fields that make *this*
    roast's own curve, milestones, events, and metadata correct.
    Everything else keeps that donor roast's own values -- cosmetically
    stale (its cupping notes, alarm settings, etc.) but never a reason
    Artisan would fail to load or render the file.

    Serialize the result with save_artisan_native_alog (Python-literal
    syntax, not JSON) -- see that function below.
    """
    data = _load_artisan_template()

    # Real Artisan files always have a brief pre-charge lead-in, so index 0
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
    temp1 = [p.get("et") for p in export_profile]  # Artisan's ET channel
    temp2 = [p.get("bt") for p in export_profile]  # Artisan's BT channel

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
            return 0  # Artisan's own "not recorded" sentinel
        return _nearest_index(timex, e["time_s"])

    timeindex = [milestone_idx(t) for t in _ARTISAN_TIMEINDEX_TYPES]
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
    # TP_idx == 0 as a *valid* recorded index (matching real Artisan's own
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

    # Phase durations -- confirmed against the template's own donor roast
    # (dryphasetime == DRY_time, midphasetime == FCs_time - DRY_time, etc,
    # since profile time_s is already charge-relative, i.e. t=0 is Charge).
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
    # Artisan's own parallel-array log, not a list of dicts.
    custom_events = [e for e in events if e.get("type") == "CUSTOM"]
    specialevents, specialeventstype, specialeventsvalue, specialeventsStrings = [], [], [], []
    for e in custom_events:
        idx = _nearest_index(timex, e["time_s"]) if timex else 0
        specialevents.append(idx)
        specialeventstype.append(_ARTISAN_CHANNEL_INDEX.get(e.get("channel"), 4))
        specialeventsvalue.append(e.get("value") if e.get("value") is not None else 0.0)
        specialeventsStrings.append(e.get("label") or "")

    # Heater/Fan/Drum as continuous "extra device" channels (Artisan's
    # `{N}` extraname convention, same one _extract_continuous_channels
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
    # The donor template's own extraname2 (second extra-device bank, e.g.
    # a Kaleido's SV/AT/AH sensors) is left the same *length* rather than
    # emptied -- roughly a dozen *other* per-device fields
    # (extradevicecolor2, extraCurveVisibility2, extraNoneTempHint2, ...)
    # are all still sized to match its original device count, and
    # clearing extraname2 without also clearing every one of those was
    # the actual cause of a later "setProfile() list index out of range"
    # crash: Artisan indexes those metadata arrays by device position
    # regardless of what's actually in extraname2/extratemp2. Slot 0 is
    # repurposed for DT (drum space temp -- a real third probe on the
    # FZ-94, its own Modbus slave ID, not the same thing as BT/ET;
    # see modbus_bridge/engine.py) when available; any further
    # role=EXTRA channels from a DeviceProfile (see
    # RoastProfilePoint.extra) repurpose whatever slots remain the exact
    # same safe way, ordered by first appearance in this roast's own
    # profile -- there are only 3 slots total in the donor template (DT
    # + 2 more), a real, fixed ceiling, not an arbitrary one; anything
    # past that still lives in profile[i]["extra"] for the live chart/
    # readouts, it just doesn't round-trip through this export. Any slot
    # still unused after that stays a flat placeholder curve, same as
    # before this app had any extra-channel data to put there at all.
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
    for i in range(len(extraname2)):
        if i == 0 and has_dt:
            extraname2[0] = "DT"
            extratemp2.append(_fill_and_floatify([p.get("dt") for p in export_profile]))
        elif i >= 1 and i - 1 < len(extra_labels):
            label = extra_labels[i - 1]
            extraname2[i] = label
            extratemp2.append(_fill_and_floatify([(p.get("extra") or {}).get(label) for p in export_profile]))
        else:
            extratemp2.append([0.0] * len(timex))
    extratimex = [timex] * max(len(extraname1), len(extraname2), 1)

    roastdate_dt = None
    if roastdate:
        try:
            roastdate_dt = datetime.fromisoformat(roastdate)
        except ValueError:
            pass

    # Real Artisan's own file has no native per-timestamp `notes` list at
    # all -- only this single free-text `roastingnotes` field (confirmed
    # against the raw donor export: no `'notes':` key anywhere in it).
    # alog_dict_to_points below parses this same "[Ns] text" format back
    # into individual note entries, so this app's own reader still
    # recovers them -- writing an extra `notes` key here (a shape real
    # Artisan doesn't have) was tried first and is exactly what made
    # Artisan reject the file as invalid.
    notes_text = "\n".join(f"[{n.get('time_s', 0):.0f}s] {n.get('text', '')}" for n in notes)

    # Temp axis range -- was left as the donor template's own stale
    # ymin/ymax (0-275, sized for that roast's own curve), unrelated to
    # this one's actual temperatures. 0 as a floor (a roast chart never
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
        # Artisan's own UI reads this (a Unix timestamp), not the
        # roastdate/roastisodate/roasttime strings above -- without it the
        # date shown in the app is the donor template's own, regardless of
        # what those string fields say.
        "roastepoch": int(roastdate_dt.timestamp()) if roastdate_dt else data.get("roastepoch", 0),
        **({"ymin": 0, "ymax": computed_ymax} if all_temps else {}),
        "timex": timex,
        "temp1": temp1,
        "temp2": temp2,
        "timeindex": timeindex,
        "computed": computed,
        "etypes": ARTISAN_ETYPES,
        "specialevents": specialevents,
        "specialeventstype": specialeventstype,
        "specialeventsvalue": specialeventsvalue,
        "specialeventsStrings": specialeventsStrings,
        "extraname1": extraname1,
        "extratemp1": extratemp1,
        "extraname2": extraname2,
        "extratemp2": extratemp2,
        "extratimex": extratimex,
    })
    return data


def save_artisan_native_alog(path: str, data: dict) -> None:
    """Writes Python-literal syntax (True/False/None, single-quoted
    strings), matching real Artisan's own file format -- NOT JSON.
    `repr()` on a dict/list/str/int/float/bool/None tree round-trips
    exactly through ast.literal_eval (what load_alog uses to read real
    Artisan files), which is the only property that actually matters
    here -- byte-for-byte formatting doesn't need to match Artisan's own
    writer, just be parseable by it."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(repr(data))


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
    """Map real Artisan's extra-device channels onto our heater_pct/
    fan_pct/drum_speed_pct/dt fields. Any *other* plain-text label (not
    DT, not a `{N}` etype reference) becomes an `extra:<label>`
    pseudo-field instead of being silently dropped -- see
    alog_dict_to_points below, which folds those into each point's own
    `extra` dict (see RoastProfilePoint.extra). Covers both this app's
    own role=EXTRA DeviceProfile channels round-tripping through
    roast_to_artisan_native_dict's extraname2 slots, and a genuine
    third-party Artisan file (e.g. a real Kaleido export) carrying its
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


_ROASTINGNOTES_LINE_RE = re.compile(r"^\[(\d+)s\] (.*)$")


def _extract_notes(data: dict) -> list[dict]:
    """A real `notes` list (this app's own writer shape, or a future
    real-Artisan one if it ever turns out to have a documented per-item
    shape) is used as-is. Real Artisan has no such field today -- our own
    writer instead flattens notes into `roastingnotes` (real Artisan's
    single free-text field, so a human opening the file in Artisan still
    sees them), one per line as "[<time_s>s] <text>" -- parsed back out
    here so this app's own reader still recovers individual notes."""
    raw = data.get("notes")
    if raw:
        return list(raw)
    notes = []
    for i, line in enumerate(str(data.get("roastingnotes") or "").splitlines()):
        m = _ROASTINGNOTES_LINE_RE.match(line)
        if m:
            notes.append({"id": f"note-{i}", "time_s": float(m.group(1)), "text": m.group(2), "author": None})
    return notes
