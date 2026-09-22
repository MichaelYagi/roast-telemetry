"""Turning the text a log file carries in its "beans" field into a clean beans
record.

Some programs save a whole description there ("Caturra\\nEthiopia\\nFarm: ...\\nProcess
Natural\\nVariety Caturra\\nAltitude: 1850m\\nSCA: 87.0\\nuuid: ..."), and some save its
line breaks and accented letters as escape codes (a literal backslash and n, or
"\\xed") instead of the characters themselves. `parse_beans_description` decodes
those and splits the description into a name and the fields it can find, keeping
every line in the notes so nothing is lost.
"""
from __future__ import annotations

import re
from typing import Optional

_ESCAPE = re.compile(r"\\(n|r|t|x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4})")

_LABELLED = {
    "supplier": re.compile(r"^(?:farm|producer|farmer|estate)\s*[:\-]\s*(.+)$", re.I),
    "process": re.compile(r"^process(?:ing)?\s*[:\-]?\s*(.+)$", re.I),
    "variety": re.compile(r"^variet(?:y|ies)\s*[:\-]?\s*(.+)$", re.I),
}
_ALTITUDE = re.compile(r"^altitude\s*[:\-]?\s*(\d[\d.,]*)\s*(?:m|masl|meters?|metres?)?\b", re.I)
_LABELLED_ANY = re.compile(r"^[A-Za-z][A-Za-z ]{0,30}\s*[:\-]")

MAX_NAME = 200
MAX_NOTES = 2000


def unescape_text(text: str) -> str:
    """Turns literal escape codes (a backslash followed by n, x-and-two-digits, ...)
    into the characters they stand for."""

    def replace(m: "re.Match[str]") -> str:
        code = m.group(1)
        if code == "n":
            return "\n"
        if code == "r":
            return ""
        if code == "t":
            return " "
        return chr(int(code[1:], 16))

    return _ESCAPE.sub(replace, text)


def parse_beans_description(text: Optional[str]) -> dict:
    """{"name": ..., plus origin/process/variety/supplier/altitude_m/notes when found}.

    A plain one-line name comes back as just {"name": ...}. For a multi-line
    description the name is the first line (with the origin after it, when one
    is found, so different coffees don't share a name), and the whole description,
    one line each, is kept in the notes."""
    cleaned = unescape_text(text or "").replace(" ", " ")
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if not lines:
        return {"name": ""}
    if len(lines) == 1:
        return {"name": lines[0][:MAX_NAME]}

    found: dict = {}
    origin = None
    for line in lines[1:]:
        altitude = _ALTITUDE.match(line)
        if altitude:
            try:
                found["altitude_m"] = float(altitude.group(1).replace(",", ""))
            except ValueError:
                pass
            continue
        for field, pattern in _LABELLED.items():
            m = pattern.match(line)
            if m:
                found.setdefault(field, m.group(1).strip())
                break
        else:
            # A bare word or two with no label or digits, right after the name, is usually the origin.
            if origin is None and not _LABELLED_ANY.match(line) and not re.search(r"\d", line) and len(line) <= 60:
                origin = line
    first = lines[0]
    name = f"{first} · {origin}" if origin else first
    result = {"name": name[:MAX_NAME], "notes": "\n".join(lines)[:MAX_NOTES], **found}
    if origin:
        result["origin"] = origin
    return result
