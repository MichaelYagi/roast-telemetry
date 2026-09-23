"""Pure helper functions in backend/app/api/roasts.py."""
from __future__ import annotations

import csv
import io

from backend.app.api.roasts import alog_filename, csv_filename, roast_to_csv
from backend.app.models import Roast, RoastEvent, RoastMode, RoastProfilePoint, RoastStatus


def test_alog_filename_basic():
    assert alog_filename("My Roast", "2026-03-05T14:32:10") == "My Roast_2026-03-05_1432.alog"


def test_alog_filename_sanitizes_unsafe_characters():
    name = alog_filename('Test / Lot "A": Batch?', "2026-03-05T14:32:10")
    assert name.endswith("_2026-03-05_1432.alog")
    for char in '/\\:*?"<>|':
        assert char not in name


def test_alog_filename_falls_back_when_title_is_empty():
    assert alog_filename("   ", "2026-03-05T14:32:10") == "roast_2026-03-05_1432.alog"


def test_csv_filename_basic():
    assert csv_filename("My Roast", "2026-03-05T14:32:10") == "My Roast_2026-03-05_1432.csv"


def test_filename_strips_embedded_crlf():
    # A title with an embedded CRLF used to survive straight into
    # download_csv's raw Content-Disposition header -- a real
    # header-injection surface, not just a filename cosmetic. Both
    # helpers share the same sanitizer, so one check covers both.
    name = csv_filename("Evil\r\nX-Injected: yes", "2026-03-05T14:32:10")
    assert "\r" not in name
    assert "\n" not in name
    name = alog_filename("Evil\r\nX-Injected: yes", "2026-03-05T14:32:10")
    assert "\r" not in name
    assert "\n" not in name


def _make_roast(profile, events=None) -> Roast:
    return Roast(
        id="r1", title="Test Roast", mode=RoastMode.SIMULATOR, status=RoastStatus.STOPPED,
        created_at="2026-01-01T00:00:00", profile=profile, events=events or [], notes=[],
    )


def test_roast_to_csv_header_and_rows():
    roast = _make_roast([
        RoastProfilePoint(time_s=0.0, bt=96.0, et=200.0, heater_pct=70.0, fan_pct=20.0, drum_speed_pct=50.0),
        RoastProfilePoint(time_s=1.0, bt=95.5, et=201.0, heater_pct=70.0, fan_pct=20.0, drum_speed_pct=50.0),
    ])
    rows = list(csv.reader(io.StringIO(roast_to_csv(roast))))
    assert rows[0] == [
        "time_s", "event", "bt_c", "et_c", "dt_c", "ror_bt_c", "ror_et_c",
        "heater_pct", "fan_pct", "drum_speed_pct", "burner_sv_c",
    ]
    assert rows[1][0] == "0.0"
    assert rows[1][2] == "96.0"
    assert len(rows) == 3  # header + 2 samples


def test_roast_to_csv_fahrenheit_converts_temps_not_percentages():
    roast = _make_roast([
        RoastProfilePoint(time_s=0.0, bt=100.0, et=200.0, heater_pct=70.0, fan_pct=20.0, drum_speed_pct=50.0, ror_bt=10.0),
    ])
    rows = list(csv.reader(io.StringIO(roast_to_csv(roast, "f"))))
    assert rows[0] == [
        "time_s", "event", "bt_f", "et_f", "dt_f", "ror_bt_f", "ror_et_f",
        "heater_pct", "fan_pct", "drum_speed_pct", "burner_sv_f",
    ]
    assert rows[1][2] == "212.0"  # 100C -> 212F
    assert rows[1][5] == "18.0"  # a rate: *1.8, no +32 offset
    assert rows[1][7] == "70.0"  # heater_pct untouched


def test_roast_to_csv_tags_the_row_a_milestone_landed_on():
    roast = _make_roast(
        [RoastProfilePoint(time_s=0.0, bt=96.0), RoastProfilePoint(time_s=1.0, bt=95.5)],
        events=[RoastEvent(id="e1", time_s=0.0, type="CHARGE", label="Charge")],
    )
    rows = list(csv.reader(io.StringIO(roast_to_csv(roast))))
    event_col = rows[0].index("event")
    assert rows[1][event_col] == "Charge"
    assert rows[2][event_col] == ""


def test_roast_to_csv_tags_the_nearest_row_when_no_exact_match():
    # A live-recorded simulator roast's CHARGE fires at time_s=0.0, but
    # its first real sample lands at time_s=1.0 -- confirmed live. The
    # nearest row, not "no row at all", should get tagged.
    roast = _make_roast(
        [RoastProfilePoint(time_s=1.0, bt=96.0), RoastProfilePoint(time_s=2.0, bt=95.9)],
        events=[RoastEvent(id="e1", time_s=0.0, type="CHARGE", label="Charge")],
    )
    rows = list(csv.reader(io.StringIO(roast_to_csv(roast))))
    event_col = rows[0].index("event")
    assert rows[1][event_col] == "Charge"
    assert rows[2][event_col] == ""


def test_roast_to_csv_adds_a_column_per_extra_channel():
    roast = _make_roast([
        RoastProfilePoint(time_s=0.0, bt=96.0, extra={"Flue": 120.0}),
        RoastProfilePoint(time_s=1.0, bt=95.5, extra={"Flue": 121.0}),
    ])
    rows = list(csv.reader(io.StringIO(roast_to_csv(roast))))
    assert rows[0][-1] == "extra_Flue_c"
    assert rows[1][-1] == "120.0"
