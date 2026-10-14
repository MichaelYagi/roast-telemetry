"""See base.py for the plugin interface, the registry and load_installed().
Re-exported here so callers write `from device_plugins import register,
PluginSpec` etc. instead of reaching into the submodule."""
from .base import DeviceEngine, PluginSpec, clear, ensure_example_copied, get, list_plugins, load_installed, register, unregister

__all__ = ["DeviceEngine", "PluginSpec", "clear", "ensure_example_copied", "get", "list_plugins", "load_installed", "register", "unregister"]
