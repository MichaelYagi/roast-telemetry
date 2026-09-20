"""The starting point for the ``.alog`` files this app writes for Artisan.

Written from scratch for this project. It holds only:

* the field *names* Artisan's ``.alog`` format defines (a file format has to
  use its consumer's names -- there is no other way to be readable), and
* neutral values chosen here: empty strings, zeros, and a few defaults of
  our own. No values come from any real roast or from any other program's
  saved settings.

Fields Artisan's loader treats as optional (alarms, PID, energy accounting,
cupping, axis ranges, ...) are deliberately left out, so Artisan falls back
to its own defaults for them. Everything a roast actually says (curves,
milestones, events, weights, dates, notes) is filled in by
``alog_io.roast_to_artisan_native_dict``.

The extra-device slots are the one delicate part: Artisan indexes a dozen
per-device metadata lists by slot number, so they must all stay the same
length as the device lists. ``EXTRA_SLOTS`` is that length.
"""
from __future__ import annotations

# Burner/Air/Drum are the three "extra device" channels this app writes
# (bank 1); bank 2 carries DT and any extra probes. Both banks have this
# many slots. Anything past it doesn't round-trip through the export.
EXTRA_SLOTS = 3

# Artisan's per-slot LCD/curve flags cover its full set of LCD frames.
_FLAG_SLOTS = 10

# The names of the `computed` summary block Artisan's own reports read.
# Filled with zeros here; alog_io overwrites what it can actually compute.
_COMPUTED_FLOATS = (
    "CHARGE_ET", "CHARGE_BT", "TP_time", "TP_ET", "TP_BT", "MET",
    "DRY_time", "DRY_ET", "DRY_BT", "FCs_time", "FCs_ET", "FCs_BT",
    "DROP_time", "DROP_ET", "DROP_BT", "COOL_time", "COOL_ET", "COOL_BT",
    "totaltime", "dryphasetime", "midphasetime", "finishphasetime", "coolphasetime",
    "dry_phase_ror", "mid_phase_ror", "finish_phase_ror", "total_ror", "fcs_ror",
    "dry_phase_delta_temp", "mid_phase_delta_temp", "finish_phase_delta_temp",
    "weight_loss", "volumein", "volumeout", "weightin", "weightout",
    "roast_defects_weight", "total_yield", "total_loss",
    "BTU_preheat", "CO2_preheat", "BTU_bbp", "CO2_bbp", "BTU_cooling", "CO2_cooling",
    "BTU_LPG", "BTU_NG", "BTU_ELEC", "BTU_batch", "BTU_batch_per_green_kg",
    "BTU_roast", "BTU_roast_per_green_kg", "CO2_batch", "CO2_batch_per_green_kg",
    "CO2_roast", "CO2_roast_per_green_kg", "KWH_batch_per_green_kg",
    "KWH_roast_per_green_kg", "bbp_total_time", "bbp_bottom_temp",
    "bbp_begin_to_bottom_time", "bbp_bottom_to_charge_time",
    "bbp_begin_to_bottom_ror", "bbp_bottom_to_charge_ror",
)
_COMPUTED_INTS = (
    "TP_idx", "total_ts", "total_ts_ET", "total_ts_BT", "AUC", "AUCbase",
    "AUCfromeventflag", "dry_phase_AUC", "mid_phase_AUC", "finish_phase_AUC",
)


def new_profile_base() -> dict:
    """A fresh, empty profile dict (a new object on every call)."""
    computed: dict = {k: 0.0 for k in _COMPUTED_FLOATS}
    computed.update({k: 0 for k in _COMPUTED_INTS})
    computed["AUCbegin"] = ""

    n = EXTRA_SLOTS
    return {
        "mode": "C",
        # Identity and free text.
        "title": "",
        "beans": "",
        "roastingnotes": "",
        "cuppingnotes": "",
        "roastertype": "",
        "weight": [0.0, 0.0, "g"],
        "roastdate": "",
        "roastisodate": "",
        "roasttime": "",
        "roastepoch": 0,
        "roastUUID": "",
        # Curves, milestones, events (all filled in per roast).
        "timex": [],
        "temp1": [],
        "temp2": [],
        "timeindex": [0] * 8,
        "computed": computed,
        "etypes": [],
        "specialevents": [],
        "specialeventstype": [],
        "specialeventsvalue": [],
        "specialeventsStrings": [],
        # Temperature axis range; alog_io widens ymax to fit the roast.
        "ymin": 0,
        "ymax": 300,
        # Extra devices: `extradevices` 0 = "none" (no physical device is
        # implied), and every per-slot list below matches EXTRA_SLOTS.
        "extradevices": [0] * n,
        "extraname1": [""] * n,
        "extraname2": [""] * n,
        "extramathexpression1": [""] * n,
        "extramathexpression2": [""] * n,
        "extratimex": [[] for _ in range(n)],
        "extratemp1": [[] for _ in range(n)],
        "extratemp2": [[] for _ in range(n)],
        # Bank 1 is Burner/Air/Drum -- percentages, not temperatures, so
        # Artisan must not convert them when switching to Fahrenheit.
        "extraNoneTempHint1": [True] * n,
        "extraNoneTempHint2": [False] * n,
        "extradevicecolor1": ["#d9534f", "#5bc0de", "#8a6d3b"],
        "extradevicecolor2": ["#5cb85c", "#f0ad4e", "#777777"],
        "extraLCDvisibility1": [False] * _FLAG_SLOTS,
        "extraLCDvisibility2": [False] * _FLAG_SLOTS,
        "extraCurveVisibility1": [True] * n + [False] * (_FLAG_SLOTS - n),
        "extraCurveVisibility2": [False] * _FLAG_SLOTS,
        "extraDelta1": [False] * _FLAG_SLOTS,
        "extraDelta2": [False] * _FLAG_SLOTS,
        "extraFill1": [0] * _FLAG_SLOTS,
        "extraFill2": [0] * _FLAG_SLOTS,
        "extramarkersizes1": [6.0] * n,
        "extramarkersizes2": [6.0] * n,
        "extramarkers1": ["None"] * n,
        "extramarkers2": ["None"] * n,
        "extralinewidths1": [1.0] * n,
        "extralinewidths2": [1.0] * n,
        "extralinestyles1": ["-"] * n,
        "extralinestyles2": ["-"] * n,
        "extradrawstyles1": ["default"] * n,
        "extradrawstyles2": ["default"] * n,
    }
