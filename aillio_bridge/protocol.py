"""The per-model adapter interface for Aillio Bullet roasters -- see
``aillio_bridge/engine.py``'s own module docstring for why this is a
swappable-strategy interface rather than a plain-data profile
(``modbus_bridge.DeviceProfile``'s own shape): R1 and R2 don't just
differ in register numbers, they differ in actual frame layout and
control semantics (R1's Drum is a direct set command, R2's is
relative INCR/DECR like Heater/Fan), so the thing that varies per
model is genuinely *behavior*, not just data.

Every method here is a pure function of bytes/numbers in, bytes/numbers
out -- no USB I/O of any kind -- so an adapter is fully unit-testable
with synthetic byte arrays, no hardware or even ``pyusb``/``libusb``
installed required. ``aillio_bridge/transport.py``'s ``UsbTransport``
is the only place actual device I/O happens; ``AillioEngine`` is the
glue between the two.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class AillioProtocol(ABC):
    """One instance per connected roast, so a stateful adapter (e.g. one
    that needs to remember something between calls) is possible, though
    neither shipped adapter needs to be stateful today -- both R1 and R2
    report their own last-commanded Heater/Fan/Drum value back in every
    status read, so ``AillioEngine`` diffs against *that*, not against
    any state kept here."""

    #: (vendor_id, product_id) pairs this model might enumerate as --
    #: more than one when a hardware revision changed the USB PID (see
    #: AillioR1Protocol's rev3 note) without changing the protocol.
    VID_PID_CANDIDATES: tuple[tuple[int, int], ...]

    #: Channels this model can actually control, in the shape
    #: apply_command()'s cmd dict uses -- lets AillioEngine warn (same
    #: "commanded but no matching channel" diagnostic ModbusEngine
    #: already has) instead of silently no-op'ing a channel this model
    #: doesn't support.
    CONTROLLABLE_CHANNELS: tuple[str, ...]

    @abstractmethod
    def poll_commands(self) -> list[tuple[list[int], int]]:
        """Returns [(command_bytes, reply_length), ...] -- sent in order
        once per tick(), replies concatenated and handed to
        parse_state() as one buffer. Two round trips for R1 (STATUS1 +
        STATUS2, 64 bytes each); may differ for other models."""

    @abstractmethod
    def parse_state(self, raw: bytes) -> Optional[dict]:
        """Decodes one polled reply buffer into the shared sample shape:
        bt/dt (°C, Optional[float]), heater_pct/fan_pct/drum_speed_pct
        (0-100, Optional[float], already remapped from this model's own
        native range), and an `extra` dict of any further named values
        (e.g. fan_rpm, voltage) for the EXTRA-channel display convention
        ModbusChannelRole.EXTRA already established. Returns None when
        the reply doesn't pass this model's own validity check (e.g.
        R1's byte-41 marker) -- AillioEngine holds last-known values in
        that case, same as every other live-bridge engine does on a
        transient read failure."""

    @abstractmethod
    def build_commands(self, channel: str, current: float, target: float) -> list[list[int]]:
        """Translates a 0-100% target for `channel` (one of
        CONTROLLABLE_CHANNELS) into the raw command packet(s) to send,
        given the channel's current 0-100% value (already remapped from
        this model's native range by the same mapping parse_state()
        used) -- e.g. R1's Heater/Fan diff current vs target in native
        units and return that many +1/-1 packets; R1's Drum returns one
        absolute-set packet regardless of current."""
