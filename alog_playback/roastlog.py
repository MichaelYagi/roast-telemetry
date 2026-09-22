"""A second interchange shape for a roast's curve, alongside the native
``.alog`` file: a tab-separated table (conventionally saved with a ``.csv``
extension) and the same table as an Excel sheet. Both carry the same
information -- a metadata line (date, unit, each milestone's clock time) and
one row per reading (elapsed time, ET, BT, rate of rise, an event label,
setpoint, heater/air/drum) -- so a spreadsheet program, or another tool that
reads this shape, can open what this app exports, and this app can read what
that shape produces.

Built from the shape of real example files, not from reading anyone's source:
the column names, the metadata line, and the milestone abbreviations (TP,
DRYe, FCs, FCe, SCs, SCe) are the vocabulary such files already use, so a
reader of them (this app's own included) can recognize a file without a
person having to relabel every column by hand. Two things this app cannot
recover from that shape at all: a title and green/roasted weights (it simply
has no place for them) -- an import in this shape gets a generic title
instead, and no weights, same as ``.alog`` would if those were absent.
"""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from typing import Optional

MILESTONE_LABELS = {
    "CHARGE": "CHARGE",
    "TP": "TURNING_POINT",
    "DRY END": "DRY_END",
    "FCS": "FC_START",
    "FCE": "FC_END",
    "SCS": "SC_START",
    "SCE": "SC_END",
    "DROP": "DROP",
    "COOL": "COOL_END",
}
# The reverse, for writing.
_MILESTONE_TEXT = {
    "CHARGE": "CHARGE",
    "TURNING_POINT": "TP",
    "DRY_END": "DRY End",
    "FC_START": "FCs",
    "FC_END": "FCe",
    "SC_START": "SCs",
    "SC_END": "SCe",
    "DROP": "DROP",
    "COOL_END": "COOL",
}
_META_KEYS = ("Date", "Unit", "CHARGE", "TP", "DRYe", "FCs", "FCe", "SCs", "SCe", "DROP", "COOL", "Time")
_META_TO_MILESTONE = {"CHARGE": "CHARGE", "TP": "TURNING_POINT", "DRYe": "DRY_END", "FCs": "FC_START", "FCe": "FC_END", "SCs": "SC_START", "SCe": "SC_END", "DROP": "DROP", "COOL": "COOL_END"}
DATA_HEADERS = ["Time1", "Time2", "ET", "BT", "Δ BT", "Event", "Air", "Drum", "SV", "Heater"]


def _fmt_clock(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    seconds = max(0, round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _parse_clock(text: str) -> Optional[float]:
    text = (text or "").strip()
    if not text:
        return None
    parts = text.split(":")
    try:
        parts = [float(p) for p in parts]
    except ValueError:
        return None
    while len(parts) < 3:
        parts.insert(0, 0.0)
    h, m, s = parts[-3:]
    return h * 3600 + m * 60 + s


def _f_to_c(value: float) -> float:
    return (value - 32.0) * 5.0 / 9.0


def _c_to_f(value: float) -> float:
    return value * 9.0 / 5.0 + 32.0


# -- writing ------------------------------------------------------------------------


def roast_to_rows(*, profile: list, events: list, temperature_unit: str = "c") -> tuple[list[str], list[list]]:
    """The metadata line and the data rows (as plain values, not yet joined or
    written) -- shared by the CSV and Excel writers below."""
    to_native = (lambda c: c) if temperature_unit == "c" else _c_to_f
    start_t = profile[0]["time_s"] if profile else 0.0
    charge = next((e for e in events if e["type"] == "CHARGE"), None)
    charge_t = charge["time_s"] if charge else start_t
    milestones = {e["type"]: e["time_s"] - charge_t for e in events if e["type"] in _MILESTONE_TEXT and e["type"] != "CHARGE"}
    duration = (profile[-1]["time_s"] - charge_t) if profile else 0.0

    meta = {
        "Date": datetime.now(timezone.utc).strftime("%d.%m.%Y"),
        "Unit": "F" if temperature_unit == "f" else "C",
        # The CHARGE metadata field is Time1's own elapsed-since-start value at
        # the charge row, not zero -- confirmed against a real example file,
        # where the two matched exactly.
        "CHARGE": _fmt_clock(charge_t - start_t) if charge else "",
        "TP": _fmt_clock(milestones.get("TURNING_POINT")),
        "DRYe": _fmt_clock(milestones.get("DRY_END")),
        "FCs": _fmt_clock(milestones.get("FC_START")),
        "FCe": _fmt_clock(milestones.get("FC_END")),
        "SCs": _fmt_clock(milestones.get("SC_START")),
        "SCe": _fmt_clock(milestones.get("SC_END")),
        "DROP": _fmt_clock(milestones.get("DROP")),
        "COOL": _fmt_clock(milestones.get("COOL_END")),
        "Time": _fmt_clock(duration),
    }
    meta_line = [f"{key}:{meta[key]}" for key in _META_KEYS]

    events_by_time: dict[float, list[str]] = {}
    for e in events:
        events_by_time.setdefault(e["time_s"], []).append(_MILESTONE_TEXT.get(e["type"], e.get("label", "")))

    rows: list[list] = []
    for p in profile:
        t = p["time_s"] - charge_t
        label = ",".join(events_by_time.get(p["time_s"], []))
        rows.append(
            [
                _fmt_clock(p["time_s"] - start_t),
                _fmt_clock(t) if t >= 0 else "",
                round(to_native(p["et"]), 2) if p.get("et") is not None else "",
                round(to_native(p["bt"]), 2) if p.get("bt") is not None else "",
                round(p["ror_bt"] * (1.8 if temperature_unit == "f" else 1.0), 2) if p.get("ror_bt") is not None else "",
                label,
                p.get("fan_pct") if p.get("fan_pct") is not None else -1,
                p.get("drum_speed_pct") if p.get("drum_speed_pct") is not None else -1,
                p.get("burner_sv_c") if p.get("burner_sv_c") is not None else -1,
                p.get("heater_pct") if p.get("heater_pct") is not None else -1,
            ]
        )
    return meta_line, rows


def save_roastlog_csv(*, profile: list, events: list, temperature_unit: str = "c") -> str:
    """A tab-separated table: a metadata line, then Time1/Time2/ET/BT/.../Heater
    columns, one row per reading. Saved with a ``.csv`` extension by convention
    even though the delimiter is a tab, matching real-world files of this
    shape (confirmed against samples, not guessed)."""
    meta_line, rows = roast_to_rows(profile=profile, events=events, temperature_unit=temperature_unit)
    out = io.StringIO()
    out.write("\t".join(meta_line) + "\r\n")
    out.write("\t".join(DATA_HEADERS) + "\r\n")
    for row in rows:
        out.write("\t".join("" if v == "" else str(v) for v in row) + "\r\n")
    return out.getvalue()


def save_roastlog_xlsx(*, profile: list, events: list, temperature_unit: str = "c") -> bytes:
    """The same table as an Excel workbook: row 1 the metadata keys, row 2
    their values, row 3 blank, row 4 the column headers, then one row per
    reading -- matching the row layout of real-world files of this shape."""
    from openpyxl import Workbook

    meta_line, rows = roast_to_rows(profile=profile, events=events, temperature_unit=temperature_unit)
    pairs = [item.split(":", 1) for item in meta_line]
    wb = Workbook()
    ws = wb.active
    ws.title = "Profile"
    ws.append([p[0] for p in pairs])
    ws.append([p[1] for p in pairs])
    ws.append([])
    ws.append(DATA_HEADERS)
    for row in rows:
        ws.append([None if v == "" else v for v in row])
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def roast_to_json(alog_dict: dict) -> str:
    """The same fields a native ``.alog`` file holds, written as valid JSON
    (``true``/``false``/``null``) instead of that file's Python-literal
    syntax -- this app's own reader already accepts either, and so, for a
    real-world export in this same shape, does whatever wrote it (confirmed
    against a real example: identical field names -- ``timex``/``temp1``/
    ``temp2``/``specialevents``/etc. -- just JSON syntax)."""
    return json.dumps(alog_dict, indent=1, ensure_ascii=False)


# -- reading --------------------------------------------------------------------------


class RoastlogParseError(ValueError):
    pass


def _split_meta(line: str, delimiter: str) -> dict:
    meta: dict[str, str] = {}
    for field in line.strip("\r\n").split(delimiter):
        if ":" not in field:
            continue
        key, _, value = field.partition(":")
        meta[key.strip()] = value.strip()
    return meta


def _column_index(headers: list[str], *names: str) -> Optional[int]:
    lowered = [h.strip().lower() for h in headers]
    for name in names:
        if name in lowered:
            return lowered.index(name)
    return None


def _parse_rows(meta: dict, headers: list[str], data_rows: list[list]) -> dict:
    unit = (meta.get("Unit") or "C").strip().upper()
    to_c = _f_to_c if unit == "F" else (lambda v: v)

    i_t1 = _column_index(headers, "time1", "time")
    i_t2 = _column_index(headers, "time2")
    i_et = _column_index(headers, "et")
    i_bt = _column_index(headers, "bt")
    i_ror = _column_index(headers, "Δ bt", "Δbt", "delta bt", "ror bt", "ror_bt")
    i_event = _column_index(headers, "event")
    i_sv = _column_index(headers, "sv")
    i_heater = _column_index(headers, "heater", "burner")
    i_air = _column_index(headers, "air")
    i_drum = _column_index(headers, "drum")
    if i_air is None and i_drum is None and i_sv is not None and i_heater is not None and i_heater - i_sv == 3:
        # Real-world files leave these two columns unlabeled (blank header
        # cells) right between SV and Heater -- confirmed against a real
        # example file, not guessed.
        i_air, i_drum = i_sv + 1, i_sv + 2
    if i_t1 is None or (i_bt is None and i_et is None):
        raise RoastlogParseError("no Time/BT/ET columns were found -- this doesn't look like a roast log table")

    charge_offset = _parse_clock(meta.get("CHARGE", ""))

    def cell(row: list, i: Optional[int]):
        if i is None or i >= len(row):
            return None
        v = row[i]
        return None if v in (None, "") else v

    def num(row: list, i: Optional[int]) -> Optional[float]:
        v = cell(row, i)
        if v is None:
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return None if f == -1 else f

    profile: list[dict] = []
    events: list[dict] = []
    for row in data_rows:
        if not any(c not in (None, "") for c in row):
            continue
        t1 = _parse_clock(str(cell(row, i_t1) or ""))
        if t1 is None:
            continue
        t2_text = cell(row, i_t2)
        if t2_text:
            t = _parse_clock(str(t2_text))
        elif charge_offset is not None:
            t = t1 - charge_offset
        else:
            t = t1
        point = {
            "time_s": round(t, 2),
            "et": round(to_c(num(row, i_et)), 2) if num(row, i_et) is not None else None,
            "bt": round(to_c(num(row, i_bt)), 2) if num(row, i_bt) is not None else None,
            "ror_bt": round(num(row, i_ror) / (1.8 if unit == "F" else 1.0), 2) if num(row, i_ror) is not None else None,
            "fan_pct": num(row, i_air),
            "drum_speed_pct": num(row, i_drum),
            "burner_sv_c": num(row, i_sv),
            "heater_pct": num(row, i_heater),
        }
        profile.append(point)

        label_text = cell(row, i_event)
        if label_text:
            for label in str(label_text).split(","):
                label = label.strip()
                if not label:
                    continue
                kind = MILESTONE_LABELS.get(label.upper())
                events.append(
                    {
                        "id": f"{kind or 'CUSTOM'}-{len(events)}",
                        "time_s": point["time_s"],
                        "type": kind or "CUSTOM",
                        "label": label if not kind else _MILESTONE_TEXT.get(kind, label).replace("_", " ").title(),
                        "value": point["bt"],
                    }
                )

    if not profile:
        raise RoastlogParseError("no readings were found in this file")
    return {"profile": profile, "events": events, "title": None, "beans": None, "weight_green_g": None, "weight_roasted_g": None, "notes": []}


def parse_roastlog_csv(text: str) -> dict:
    """Reads the shape ``save_roastlog_csv`` writes -- a metadata line, a
    header line, then data rows. Sniffs tab vs. comma from the header line, so
    a file saved with either delimiter is accepted."""
    lines = text.splitlines()
    if len(lines) < 3:
        raise RoastlogParseError("this file is too short to be a roast log table")
    delimiter = "\t" if "\t" in lines[1] else ","
    meta = _split_meta(lines[0], delimiter)
    headers = lines[1].split(delimiter)
    data_rows = [line.split(delimiter) for line in lines[2:] if line.strip()]
    return _parse_rows(meta, headers, data_rows)


def parse_roastlog_xlsx(data: bytes) -> dict:
    """Reads the shape ``save_roastlog_xlsx`` writes: metadata keys, metadata
    values, a blank row, headers, then data -- on whichever sheet has a
    recognizable header row (the first sheet, in a file this app wrote)."""
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    for ws in wb.worksheets:
        all_rows = [[("" if c is None else c) for c in row] for row in ws.iter_rows(values_only=True)]
        if len(all_rows) < 5:
            continue
        meta = {str(k): str(v) for k, v in zip(all_rows[0], all_rows[1]) if k}
        headers = [str(h) for h in all_rows[3]]
        try:
            return _parse_rows(meta, headers, all_rows[4:])
        except RoastlogParseError:
            continue
    raise RoastlogParseError("no roast log table was found in this spreadsheet")
