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
"""Real USB transport for Aillio Bullet roasters, via ``pyusb``/
``libusb``. ``import usb.core``/``usb.util`` happen inside this class,
not at module top-level -- importing ``aillio_bridge.engine`` (and
running its tests, which inject a fake transport -- see
tests/test_aillio_engine.py) never requires ``pyusb`` or the native
``libusb`` shared library to be installed at all; only actually
connecting to real hardware does.

Endpoint/interface/configuration numbers below are the manufacturer's
own confirmed values (same sourcing note as ``r1.py``), common across
every known Bullet model -- only the VID/PID pairs to probe for differ
per model (``AillioProtocol.VID_PID_CANDIDATES``).
"""
from __future__ import annotations

from typing import Optional

ENDPOINT_WRITE = 0x03
ENDPOINT_READ = 0x81
INTERFACE = 0x01
CONFIGURATION = 0x01
TIMEOUT_MS = 1000


class UsbTransportError(RuntimeError):
    pass


class UsbTransport:
    """Injectable (see ``AillioEngine``'s ``transport_cls`` param) --
    exists so a fake transport can stand in for tests without any real
    USB/``libusb`` involved, the same seam ``ModbusEngine``'s
    ``client_cls`` already provides."""

    def __init__(self, vid_pid_candidates: tuple[tuple[int, int], ...]):
        import platform

        import usb.core
        import usb.util

        self._usb_util = usb.util
        # Windows has no system libusb the way Linux/macOS typically do --
        # libusb-package bundles one and provides its own find(), same as
        # the manufacturer's own driver does on this platform (see
        # backend/requirements.txt's own note on this dependency).
        if platform.system().startswith("Windows"):
            import libusb_package

            finder = libusb_package.find
        else:
            finder = usb.core.find
        self._handle = None
        for vid, pid in vid_pid_candidates:
            self._handle = finder(idVendor=vid, idProduct=pid)
            if self._handle is not None:
                break
        if self._handle is None:
            raise UsbTransportError(
                f"no Aillio device found for any of {vid_pid_candidates!r} "
                "(not plugged in, or the OS driver needs swapping -- see docs/aillio/bullet-r1.html)"
            )
        try:
            if self._handle.is_kernel_driver_active(INTERFACE):
                self._handle.detach_kernel_driver(INTERFACE)
        except NotImplementedError:
            pass  # platforms without kernel-driver detachment (e.g. Windows via libusb-win32) -- fine
        try:
            config = self._handle.get_active_configuration()
            if config is None or config.bConfigurationValue != CONFIGURATION:
                self._handle.set_configuration(CONFIGURATION)
            usb.util.claim_interface(self._handle, INTERFACE)
        except Exception as exc:  # pragma: no cover - depends on local hardware/OS
            self._handle = None
            raise UsbTransportError(f"could not claim the Aillio USB interface: {exc}") from exc

    def write(self, command: list[int]) -> None:
        if self._handle is None:
            raise UsbTransportError("not connected")
        self._handle.write(ENDPOINT_WRITE, command, timeout=TIMEOUT_MS)

    def read(self, length: int) -> bytes:
        if self._handle is None:
            raise UsbTransportError("not connected")
        return bytes(self._handle.read(ENDPOINT_READ, length, timeout=TIMEOUT_MS))

    def close(self) -> None:
        if self._handle is None:
            return
        try:
            self._usb_util.release_interface(self._handle, INTERFACE)
            self._usb_util.dispose_resources(self._handle)
        except Exception:  # pragma: no cover - best-effort cleanup
            pass
        self._handle = None
