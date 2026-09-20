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
"""Direct USB telemetry + control for a real Aillio Bullet roaster --
bypasses Artisan entirely, same as modbus_bridge/ms6514_bridge for
their own devices. Genuinely different from both: not Modbus, not even
a serial port -- a raw USB device (vendor-specific bulk-transfer
protocol), opened via aillio_bridge/transport.py's UsbTransport
(pyusb/libusb), not pyserial/pymodbus.

**Why a swappable protocol adapter, not a plain-data profile like
modbus_bridge.DeviceProfile.** Coffee-Tech's various Modbus roasters
only ever differ by *numbers* (register/slave ids, ranges) -- the read/
write mechanics are identical across every one of them, which is
exactly what makes a plain-data DeviceProfile the right fit there.
Aillio Bullet models don't share that property: R1 and R2 have
genuinely different frame layouts and even different control semantics
for the same channel (R1's Drum is a direct absolute-set command, R2's
is relative INCR/DECR like its Heater/Fan) -- see aillio_bridge/r1.py's
own docstring for R1's confirmed byte offsets/opcodes (traced from the
manufacturer's own shipped roaster-scope driver source, no official
spec exists). Forcing that into one shared numeric-register shape would
be a false elegance; a small strategy interface
(aillio_bridge/protocol.py's AillioProtocol) is the honest fit --
adding R2 later means writing one new adapter class from its own
research pass, not reshaping this engine.

**No internal polling thread**, unlike the manufacturer's own driver
(which runs a background thread + multiprocessing.Pipe purely to keep
its own synchronous main-thread GUI responsive). Unnecessary here:
RoastSession._run_loop() already calls every engine's tick() via
asyncio.to_thread, so this engine's tick() can do one plain synchronous
USB command+read round trip per call exactly as safely as
ModbusEngine._read_register's own blocking pymodbus call already does
-- porting that threading model would just be complexity this app's
own architecture already made unnecessary.

**Command diffing is against the device's own last reported state**,
not a value this engine tracks independently of what's actually been
read back -- matches the source driver's own set_heater/set_fan, which
read the device's current value before computing how many +1/-1
packets to send. If nothing has been successfully polled yet, a
command is refused (logged, not silently dropped or guessed) rather
than risk sending a run of relative nudges with no idea where they'd
land.
"""
from __future__ import annotations

import logging
from typing import Optional

from roast_heuristics import LiveRoastDetector

from .protocol import AillioProtocol
from .r1 import AillioR1Protocol
from .transport import UsbTransport

logger = logging.getLogger(__name__)

PROTOCOLS: dict[str, type[AillioProtocol]] = {
    "r1": AillioR1Protocol,
    # "r2": AillioR2Protocol,  -- follow-up: R2's frame layout needs its
    # own research pass (see docs/aillio/bullet-r1.html's own note, and
    # this session's plan) before it can be added here.
}


class AillioEngineError(RuntimeError):
    pass


class AillioEngine:
    def __init__(
        self,
        model: str,
        dry_end_c: Optional[float] = 160.0,
        fc_start_c: Optional[float] = 196.0,
        # Opt-in -- off by default. See ModbusEngine's identical parameter
        # for the full rationale.
        detect_milestones: bool = False,
        transport_cls=UsbTransport,  # injectable for testing without real hardware
        protocol: Optional[AillioProtocol] = None,  # injectable -- overrides the model lookup, test use only
    ):
        if protocol is None and model not in PROTOCOLS:
            raise ValueError(f"unknown aillio model {model!r} (known: {sorted(PROTOCOLS)})")
        self.model = model
        self._protocol: AillioProtocol = protocol if protocol is not None else PROTOCOLS[model]()
        self._dry_end_c = dry_end_c
        self._fc_start_c = fc_start_c
        self._detect_milestones = detect_milestones
        self._detector = LiveRoastDetector(dry_end_c=dry_end_c, fc_start_c=fc_start_c, detect_milestones=detect_milestones)
        self._last_time_s = 0.0
        self._connected = False
        self._last_error: Optional[str] = None
        self._last_sample: Optional[dict] = None

        try:
            self._transport = transport_cls(self._protocol.VID_PID_CANDIDATES)
            self._connected = True
        except Exception as exc:  # pragma: no cover - depends on local hardware/OS
            self._transport = None
            self._last_error = str(exc)
            self._connected = False

    # -- USB I/O ---------------------------------------------------------
    def _poll(self) -> Optional[dict]:
        if self._transport is None:
            return None
        try:
            raw = bytearray()
            for command, reply_len in self._protocol.poll_commands():
                self._transport.write(command)
                raw += self._transport.read(reply_len)
            self._connected = True
            self._last_error = None
            return self._protocol.parse_state(bytes(raw))
        except Exception as exc:  # pragma: no cover - depends on local hardware/OS
            self._last_error = str(exc)
            self._connected = False
            return None

    # -- engine contract (matches simulator.SimulatorEngine / ModbusEngine / MS6514Engine) --
    def tick(self, dt: float) -> dict:
        self._last_time_s += dt
        time_s = self._last_time_s

        parsed = self._poll()
        if parsed is not None:
            self._last_sample = parsed
            logger.debug("aillio %s poll -> %s", self.model, parsed)

        bt = self._last_sample.get("bt") if self._last_sample else None
        sample = self._detector.observe(time_s, bt, self._last_sample.get("et") if self._last_sample else None)
        sample["dt"] = self._last_sample.get("dt") if self._last_sample else None
        sample["heater_pct"] = self._last_sample.get("heater_pct") if self._last_sample else None
        sample["fan_pct"] = self._last_sample.get("fan_pct") if self._last_sample else None
        sample["drum_speed_pct"] = self._last_sample.get("drum_speed_pct") if self._last_sample else None
        sample["burner_sv_c"] = None  # no SV-temperature concept on this device
        sample["extra"] = self._last_sample.get("extra", {}) if self._last_sample else {}
        return sample

    def get_new_events(self) -> list:
        return self._detector.get_new_events()

    def apply_command(self, cmd: dict) -> None:
        for channel in self._protocol.CONTROLLABLE_CHANNELS:
            target = cmd.get(channel)
            if target is None:
                continue
            current = self._last_sample.get(channel) if self._last_sample else None
            if current is None:
                logger.warning(
                    "commanded %s=%s but no Aillio state has been read yet -- ignored (current position unknown)",
                    channel, target,
                )
                continue
            try:
                for packet in self._protocol.build_commands(channel, current, float(target)):
                    self._transport.write(packet)
                self._connected = True
                self._last_error = None
            except Exception as exc:  # pragma: no cover - depends on local hardware/OS
                self._last_error = str(exc)
                self._connected = False
                logger.warning("write failed (aillio %s, channel=%s): %s", self.model, channel, exc)
        # Same "commanded but no matching channel" diagnostic ModbusEngine
        # already has (e.g. Drum on a hypothetical model without one).
        for key in ("heater_pct", "fan_pct", "drum_speed_pct"):
            if cmd.get(key) is not None and key not in self._protocol.CONTROLLABLE_CHANNELS:
                logger.warning("commanded %s but Aillio %s has no such channel -- ignored", key, self.model)

    def is_finished(self) -> bool:
        return False  # no end-of-roast signal surfaced here; stop manually from the UI

    def status(self) -> dict:
        return {
            "mode": "aillio_live",
            "model": self.model,
            "connected": self._connected,
            "last_error": self._last_error,
        }

    def close(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def reset_detection(self) -> None:
        """See ModbusEngine.reset_detection() -- identical reasoning and shape."""
        self._detector = LiveRoastDetector(dry_end_c=self._dry_end_c, fc_start_c=self._fc_start_c, detect_milestones=self._detect_milestones)
        self._last_time_s = 0.0

    def mark_milestone_fired(self, event_type: str) -> None:
        """See ModbusEngine.mark_milestone_fired's own docstring."""
        self._detector.mark_milestone_fired(event_type)

    def notify_manual_charge(self, time_s: float, bt: float) -> None:
        """See ModbusEngine.notify_manual_charge's own docstring."""
        self._detector.notify_manual_charge(time_s, bt)
