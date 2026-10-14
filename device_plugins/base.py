"""The interface a device plugin's engine must implement, and the registry
plugins add themselves to at import time.

A plugin is for a protocol family this app doesn't already speak. That's
*not* the same gap as a new Modbus roaster -- one of those needs no code at
all, just a DeviceProfile (register facts as data, see
modbus_bridge/device_profiles.py) -- and it's not one of the other built-in
live bridges either (ms6514_bridge, aillio_bridge, tc4_bridge). It's for
something genuinely new underneath: a different serial protocol, a raw-USB
device, Bluetooth, anything.

A plugin is one Python file dropped in device_plugins/installed/ that calls
register() at import time with a PluginSpec. See
device_plugins/installed/example_serial_meter.py for a complete, working one
to copy (shipped there directly, registered with enabled=False so it never
shows up on its own -- see PluginSpec.enabled below), and this package's
README.md for the full how-to.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Protocol

logger = logging.getLogger(__name__)

# The source tree's own installed/ folder -- in a source checkout this is
# the one and only installed/ (identical to INSTALLED_DIR below), so
# ensure_example_copied() is a no-op there; nothing to copy that isn't
# already exactly where it needs to be. In a frozen desktop build it's
# instead wherever PyInstaller unpacked it this launch (sys._MEIPASS/
# device_plugins/installed/ -- see packaging/roast-telemetry.spec's own
# datas entry), distinct from the real, persistent INSTALLED_DIR below --
# the one place a bundled, always-current copy of example_serial_meter.py
# can be found to copy from.
_BUNDLED_INSTALLED_DIR = Path(__file__).parent / "installed"

# Mirrors backend/app/storage.py's own DATA_DIR override exactly, and for
# the same reason: Path(__file__).parent is a source-tree-relative path
# that only means something when running from a checkout. In a frozen
# desktop build it instead resolves inside PyInstaller's own extraction
# folder (sys._MEIPASS) -- not writable in every install location, and for
# the onefile package specifically, wiped and recreated fresh on every
# single launch, so anything dropped there would vanish the next time the
# app opened. scripts/tray_app.py sets this same env var for the server
# subprocess, pointed at the real per-user data directory (where
# roasts.db/roasts/ already live, reachable via the tray's own "Open data
# folder"), so a plugin dropped in there actually persists and is
# discoverable. Unset (the normal dev/CI/source-checkout case): behavior
# is unchanged, same source-tree-relative installed/ folder as always.
_DATA_DIR_OVERRIDE = os.environ.get("ROAST_TELEMETRY_DATA_DIR")
INSTALLED_DIR = (
    Path(_DATA_DIR_OVERRIDE) / "device_plugins" / "installed" if _DATA_DIR_OVERRIDE
    else _BUNDLED_INSTALLED_DIR
)
if _DATA_DIR_OVERRIDE:
    INSTALLED_DIR.mkdir(parents=True, exist_ok=True)


class DeviceEngine(Protocol):
    """What a RoastSession drives a connected device through once
    PluginSpec.connect() hands one back. Every built-in live bridge
    (ModbusEngine, MS6514Engine, AillioEngine, TC4Engine) already has this
    exact shape -- ms6514_bridge/engine.py's MS6514Engine is the simplest
    real one to read alongside this.
    """

    def tick(self, dt: float) -> dict:
        """Advances by dt seconds and returns this moment's sample: at least
        bt/et/heater_pct/fan_pct/drum_speed_pct keys (None for anything this
        device doesn't have -- a read-only meter returns None for all three
        control keys, see PluginSpec.read_only)."""
        ...

    def get_new_events(self) -> list:
        """Milestones detected since the last call (Charge/Turning Point/
        Dry End/FC Start/...) -- [] if this plugin doesn't auto-detect any
        (the operator's own manual event buttons still work either way)."""
        ...

    def apply_command(self, cmd: dict) -> None:
        """A control write (heater_pct/fan_pct/drum_speed_pct) -- a no-op for
        a read-only meter (PluginSpec.read_only=True)."""
        ...

    def is_finished(self) -> bool:
        """True if the device itself signaled the roast is over. Most
        protocols have no such signal -- return False always and let the
        operator stop the roast by hand, same as every built-in bridge."""
        ...

    def status(self) -> dict:
        """At least {"connected": bool, "last_error": str|None}."""
        ...

    def reset_detection(self) -> None:
        """Discards accumulated milestone-detection state and resets the
        elapsed-time clock -- called when recording actually starts, after
        however long the roast sat connected-but-idle first."""
        ...

    def mark_milestone_fired(self, event_type: str) -> None:
        """Forwarded when the operator manually marks a milestone this
        engine also auto-detects, so it doesn't fire the same one again."""
        ...

    def notify_manual_charge(self, time_s: float, bt: float) -> None:
        """Forwarded when Charge is marked manually, so time-from-charge
        detection has a real starting point."""
        ...

    def close(self) -> None:
        """Releases whatever this engine opened (a serial port, a USB
        handle, ...). Must be safe to call even if connecting failed."""
        ...


@dataclass(frozen=True)
class PluginSpec:
    kind: str  # unique id -- RoastCreateRequest.plugin_kind, also this plugin's URL-safe identifier
    label: str  # shown in the New Roast form's plugin dropdown
    # connect(port, *, dry_end_c, fc_start_c, detect_milestones) -> DeviceEngine
    # `port` is whatever needs_port calls for below (None when it's False).
    # The three keyword args mirror every built-in bridge's own constructor
    # -- forward them to roast_heuristics.LiveRoastDetector the same way
    # ms6514_bridge/engine.py's MS6514Engine does, or ignore them if this
    # protocol has no milestone auto-detection.
    connect: Callable[..., DeviceEngine]
    needs_port: bool = True  # False only for a raw-USB device with nothing to configure (see aillio_bridge)
    read_only: bool = False  # True for a meter with no command channel (see ms6514_bridge) -- heater/fan/drum controls stay hidden
    port_hint: str = "Serial port"  # field label and error text when needs_port is True, e.g. "Serial port" or "Host / IP address"
    # The plugin author's own code-level switch -- set False while still
    # building one, to keep it off the New Roast form and Settings >
    # Device plugins entirely (not listed, not connectable) without
    # removing the file. Distinct from -- and takes priority over --
    # Settings' own per-kind enable/disable checkbox, which only ever
    # applies to a plugin shipped with enabled=True here: that's a
    # reversible, no-restart-needed runtime toggle for something already
    # finished and installed, not a replacement for this one.
    enabled: bool = True


_REGISTRY: dict[str, PluginSpec] = {}
# kind -> the installed/*.py file it was loaded from, set only while
# load_installed() itself is importing that file (see _currently_loading
# below) -- a plugin registered any other way (directly by a test, or by
# some future non-file source) has no entry here, so unregister() below
# knows it has nothing on disk it's safe to delete.
_SOURCE_PATHS: dict[str, Path] = {}
_currently_loading: Optional[Path] = None


def register(spec: PluginSpec) -> None:
    """Called by a plugin module at import time. Registering the same kind
    twice replaces the first entry rather than erroring -- lets
    load_installed() be run again (tests do this) without needing a fresh
    process."""
    _REGISTRY[spec.kind] = spec
    if _currently_loading is not None:
        _SOURCE_PATHS[spec.kind] = _currently_loading
    else:
        _SOURCE_PATHS.pop(spec.kind, None)


def get(kind: str) -> Optional[PluginSpec]:
    return _REGISTRY.get(kind)


def list_plugins() -> list[PluginSpec]:
    return sorted(_REGISTRY.values(), key=lambda s: s.label)


def clear() -> None:
    """Testing only -- empties the registry between tests that load their
    own plugin modules."""
    _REGISTRY.clear()
    _SOURCE_PATHS.clear()


def unregister(kind: str) -> bool:
    """Removes a plugin from the live registry immediately -- no server
    restart needed, unlike just disabling it (see
    backend/app/storage.set_plugin_enabled, a reversible visibility toggle
    that leaves the registry and the file alone). If `kind` was loaded from
    a real file in installed/ (the normal case -- anything else means it
    was registered directly, e.g. by a test), that file is deleted too, so
    load_installed() won't bring it back on the next restart either.
    Returns whether anything was actually registered under `kind`.
    """
    existed = _REGISTRY.pop(kind, None) is not None
    path = _SOURCE_PATHS.pop(kind, None)
    if path is not None and path.is_file():
        path.unlink()
    return existed


def load_installed(directory: Path = INSTALLED_DIR) -> None:
    """Imports every .py file directly inside `directory` (not
    subdirectories, and not dotfiles/__init__.py-style names) so each one's
    module-level register() call runs. The whole plugin contract is just
    that: a Python file that registers a PluginSpec when imported. A file
    that fails to import is logged and skipped -- one broken plugin can't
    take the rest of the server down.
    """
    global _currently_loading
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        module_name = f"device_plugins.installed.{path.stem}"
        _currently_loading = path
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError(f"couldn't build a module spec for {path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception:
            logger.exception("device plugin %s failed to load; skipping it", path.name)
        finally:
            _currently_loading = None


def ensure_example_copied() -> None:
    """Keeps example_serial_meter.py actually sitting in installed/ --
    genuinely loaded by load_installed() below, proving the whole mechanism
    end to end, rather than just a reference file someone has to go copy
    themselves. It ships registered with enabled=False (see its own module
    docstring), so this being real and loaded doesn't mean it's visible --
    Settings > Device plugins and the New Roast form never show it on their
    own, same as any other code-disabled plugin.

    In a source checkout this is a no-op: _BUNDLED_INSTALLED_DIR and
    INSTALLED_DIR are the exact same folder, so the file is already right
    there with nothing to copy. In a frozen desktop build, the bundled copy
    only lives inside PyInstaller's own extraction folder (see this
    module's INSTALLED_DIR comment -- gone entirely between launches of a
    onefile build), so this copies it into the real, persistent
    INSTALLED_DIR instead, overwriting on every startup so it can't drift
    from the app's own shipped version. That overwrite is also why this
    file's own docstring says to copy it to a new name rather than edit it
    directly -- an in-place edit would otherwise vanish on the next launch.
    """
    if not _DATA_DIR_OVERRIDE:
        return
    source = _BUNDLED_INSTALLED_DIR / "example_serial_meter.py"
    if not source.is_file():
        return
    shutil.copyfile(source, INSTALLED_DIR / source.name)
