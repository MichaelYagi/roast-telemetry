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
device_plugins/examples/example_serial_meter.py for a complete, working one
to copy, and this package's README.md for the full how-to.
"""
from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Protocol

logger = logging.getLogger(__name__)

INSTALLED_DIR = Path(__file__).parent / "installed"


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


_REGISTRY: dict[str, PluginSpec] = {}


def register(spec: PluginSpec) -> None:
    """Called by a plugin module at import time. Registering the same kind
    twice replaces the first entry rather than erroring -- lets
    load_installed() be run again (tests do this) without needing a fresh
    process."""
    _REGISTRY[spec.kind] = spec


def get(kind: str) -> Optional[PluginSpec]:
    return _REGISTRY.get(kind)


def list_plugins() -> list[PluginSpec]:
    return sorted(_REGISTRY.values(), key=lambda s: s.label)


def clear() -> None:
    """Testing only -- empties the registry between tests that load their
    own plugin modules."""
    _REGISTRY.clear()


def load_installed(directory: Path = INSTALLED_DIR) -> None:
    """Imports every .py file directly inside `directory` (not
    subdirectories, and not dotfiles/__init__.py-style names) so each one's
    module-level register() call runs. The whole plugin contract is just
    that: a Python file that registers a PluginSpec when imported. A file
    that fails to import is logged and skipped -- one broken plugin can't
    take the rest of the server down.
    """
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        module_name = f"device_plugins.installed.{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError(f"couldn't build a module spec for {path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception:
            logger.exception("device plugin %s failed to load; skipping it", path.name)
