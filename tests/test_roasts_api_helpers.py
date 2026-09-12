"""Pure helper functions in backend/app/api/roasts.py."""
from __future__ import annotations

from backend.app.api.roasts import alog_filename


def test_alog_filename_basic():
    assert alog_filename("My Roast", "2026-03-05T14:32:10") == "My Roast_2026-03-05_1432.alog"


def test_alog_filename_sanitizes_unsafe_characters():
    name = alog_filename('Guatemala / Finca "Rosma": Batch?', "2026-03-05T14:32:10")
    assert name.endswith("_2026-03-05_1432.alog")
    for char in '/\\:*?"<>|':
        assert char not in name


def test_alog_filename_falls_back_when_title_is_empty():
    assert alog_filename("   ", "2026-03-05T14:32:10") == "roast_2026-03-05_1432.alog"
