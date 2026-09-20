# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is a derivative work. Its device logic -- USB identifiers,
# command and status byte layouts, and control semantics -- is ported from
# the Aillio R1 driver in Artisan (src/artisanlib/aillio_r1.py,
# https://github.com/artisan-roaster-scope/artisan):
#   Copyright (C) 2010-2026 The Artisan team, represented by Marko Luther
#   (maintainer) and all contributors; that driver by Rui Paulo, 2023.
#   Licensed under the GNU Affero General Public License, version 3 or
#   (at your option) any later version.
# Restructured as a protocol adapter for Roast Telemetry and modified in
# 2026 by Michael Yagi. Roast Telemetry as a whole is licensed under the
# same terms, AGPL-3.0-or-later (see LICENSE).
"""Aillio Bullet R1 protocol adapter -- byte offsets, opcodes, and
control semantics traced directly from the manufacturer's own shipped
roaster-scope driver source (a genuine USB device, not Modbus/serial --
see aillio_bridge/engine.py's module docstring), not guessed. No
official published spec exists for this device; this is a from-scratch
port of that driver's own confirmed logic, same sourcing standard
already applied to modbus_bridge/ms6514_bridge.

128-byte polled state (two 64-byte replies, STATUS1 then STATUS2,
concatenated) -- offsets into that combined buffer:
    0-3:   BT, °C, 32-bit float
    8-11:  DT (drum/bean-adjacent probe -- kept as `dt`, matching this
           app's own DT role, not the manual's own naming), 32-bit float
    16-19: exhaust temperature, °C, 32-bit float (exposed as `extra`)
    24:    elapsed minutes (device's own roast clock)
    25:    elapsed seconds
    26:    Fan, native range 1-12
    27:    Heater, native range 0-9
    28:    Drum, native range 1-9
    29:    roaster state byte (see STATE_LABELS) -- exposed as `extra`
           only; not wired into this app's own milestone auto-detection
           in this first pass (see engine.py's module docstring)
    32-35: IRT (infrared bean-surface probe), °C, 32-bit float, `extra`
    36-39: PCB temperature, °C, 32-bit float, `extra`
    41:    validity marker -- readings are only trusted when this byte
           equals 10; the device sends other, unrelated messages
           periodically that must be ignored, not decoded as zeros
    44-45: fan RPM, 16-bit int, `extra`
    48-49: supply voltage, 16-bit int, `extra`

Heater/Fan move via a run of relative +1/-1 command packets (the
device has no absolute-set command for either) -- build_commands()
below diffs the requested target against the *device's own last
reported value* (not a value this adapter tracks itself) and returns
that many packets, capped the same way the source driver caps it
(<=9 for Heater, <=11 for Fan) as a guard against a wildly desynced
diff producing an absurd packet run. Drum is the one exception, set
directly via one absolute packet.
"""
from __future__ import annotations

from struct import unpack
from typing import Optional

from .protocol import AillioProtocol

STATE_LABELS = {
    0x00: "off",
    0x02: "preheat",
    0x04: "charge",
    0x06: "roasting",
    0x08: "cooling",
    0x09: "shutdown",
}

# Native control ranges, straight from the source driver's own set_*() clamps.
HEATER_RANGE = (0, 9)
FAN_RANGE = (1, 12)
DRUM_RANGE = (1, 9)

_CMD_HEATER_INCR = [0x34, 0x01, 0xAA, 0xAA]
_CMD_HEATER_DECR = [0x34, 0x02, 0xAA, 0xAA]
_CMD_FAN_INCR = [0x31, 0x01, 0xAA, 0xAA]
_CMD_FAN_DECR = [0x31, 0x02, 0xAA, 0xAA]


def _pct_to_native(pct: float, native_range: tuple[int, int]) -> int:
    lo, hi = native_range
    pct = max(0.0, min(100.0, pct))
    return round(lo + (pct / 100.0) * (hi - lo))


def _native_to_pct(native: float, native_range: tuple[int, int]) -> float:
    lo, hi = native_range
    if hi == lo:
        return 0.0
    return max(0.0, min(100.0, (native - lo) / (hi - lo) * 100.0))


class AillioR1Protocol(AillioProtocol):
    VID_PID_CANDIDATES = (
        (0x0483, 0x5741),
        (0x0483, 0xA27E),  # hardware rev3 -- same protocol, different USB PID
    )
    CONTROLLABLE_CHANNELS = ("heater_pct", "fan_pct", "drum_speed_pct")

    def poll_commands(self) -> list[tuple[list[int], int]]:
        return [([0x30, 0x01], 64), ([0x30, 0x03], 64)]

    def parse_state(self, raw: bytes) -> Optional[dict]:
        if len(raw) < 50 or raw[41] != 10:
            return None
        bt = unpack("f", raw[0:4])[0]
        dt = unpack("f", raw[8:12])[0]
        exhaust = unpack("f", raw[16:20])[0]
        fan_native = raw[26]
        heater_native = raw[27]
        drum_native = raw[28]
        state = raw[29]
        irt = unpack("f", raw[32:36])[0]
        pcb_temp = unpack("f", raw[36:40])[0]
        fan_rpm = unpack("h", raw[44:46])[0]
        voltage = unpack("h", raw[48:50])[0]
        return {
            "bt": round(bt, 1),
            "dt": round(dt, 1),
            "heater_pct": round(_native_to_pct(heater_native, HEATER_RANGE), 1),
            "fan_pct": round(_native_to_pct(fan_native, FAN_RANGE), 1),
            "drum_speed_pct": round(_native_to_pct(drum_native, DRUM_RANGE), 1),
            "extra": {
                "Exhaust": round(exhaust, 1),
                "IRT": round(irt, 1),
                "PCB temp": round(pcb_temp, 1),
                "Fan RPM": fan_rpm,
                "Voltage": voltage,
                "State": STATE_LABELS.get(state, f"unknown (0x{state:02x})"),
            },
        }

    def build_commands(self, channel: str, current: float, target: float) -> list[list[int]]:
        if channel == "drum_speed_pct":
            native = _pct_to_native(target, DRUM_RANGE)
            return [[0x32, 0x01, native, 0x00]]

        if channel == "heater_pct":
            native_range, incr, decr, cap = HEATER_RANGE, _CMD_HEATER_INCR, _CMD_HEATER_DECR, 9
        elif channel == "fan_pct":
            native_range, incr, decr, cap = FAN_RANGE, _CMD_FAN_INCR, _CMD_FAN_DECR, 11
        else:
            return []

        current_native = _pct_to_native(current, native_range)
        target_native = _pct_to_native(target, native_range)
        diff = min(abs(target_native - current_native), cap)
        if diff == 0:
            return []
        step = decr if target_native < current_native else incr
        return [list(step) for _ in range(diff)]
