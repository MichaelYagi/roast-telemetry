"""See base.py for the plugin interface, the registry and load_installed().
Re-exported here so callers write `from device_plugins import register,
PluginSpec` etc. instead of reaching into the submodule."""
from .base import DeviceEngine, PluginSpec, clear, get, list_plugins, load_installed, register

__all__ = ["DeviceEngine", "PluginSpec", "clear", "get", "list_plugins", "load_installed", "register"]
