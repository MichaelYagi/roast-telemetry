"""modbus_fahrenheit_native used to be an independent checkbox on the
Configure Roast form (RoastCreateRequest.modbus_fahrenheit_native); it's now
derived automatically from the global Settings > Temperature Unit instead
(see RoastSession._fahrenheit_native_from_settings). Uses a nonexistent
serial port throughout: ModbusEngine.__init__ catches the resulting connect
failure internally (never raises), so this never touches real/virtual
hardware and stays fast -- same convention as test_modbus_register_overrides.py.
"""
from __future__ import annotations

from backend.app import storage
from backend.app.models import RoastCreateRequest, RoastMode
from backend.app.roast_session.session import RoastSession

BOGUS_PORT = "/dev/nonexistent-for-tests"


def test_modbus_live_follows_global_fahrenheit_setting(isolated_db):
    storage.set_settings(temperature_unit="f")
    request = RoastCreateRequest(title="Test Roast", mode=RoastMode.MODBUS_LIVE, modbus_port=BOGUS_PORT)
    session = RoastSession("test-roast-id", request)

    assert session.modbus_fahrenheit_native is True
    assert session.summary().modbus_fahrenheit_native is True


def test_modbus_live_follows_global_celsius_setting(isolated_db):
    storage.set_settings(temperature_unit="c")
    request = RoastCreateRequest(title="Test Roast", mode=RoastMode.MODBUS_LIVE, modbus_port=BOGUS_PORT)
    session = RoastSession("test-roast-id", request)

    assert session.modbus_fahrenheit_native is False
    assert session.summary().modbus_fahrenheit_native is False


def test_modbus_live_defaults_safely_when_settings_unreadable(monkeypatch):
    def _boom():
        raise RuntimeError("DB is locked")

    monkeypatch.setattr(storage, "get_settings", _boom)
    request = RoastCreateRequest(title="Test Roast", mode=RoastMode.MODBUS_LIVE, modbus_port=BOGUS_PORT)

    # Must not raise -- a bad/unreadable settings read should never stop a
    # roast from starting, same reasoning as RoastControl._load_limits.
    session = RoastSession("test-roast-id", request)

    assert session.modbus_fahrenheit_native is False


def test_non_modbus_modes_are_unaffected(isolated_db):
    storage.set_settings(temperature_unit="f")
    request = RoastCreateRequest(title="Test Roast", mode=RoastMode.SIMULATOR)
    session = RoastSession("test-roast-id", request)

    # Only modbus_live ever reads the setting at all -- every other mode
    # keeps the unconditional False set at the top of __init__.
    assert session.modbus_fahrenheit_native is False
