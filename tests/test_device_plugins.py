"""The device-plugin registry (device_plugins/base.py), the worked example
plugin, the GET /device-plugins listing, and the generic plugin_live path
through RoastSession -- see test_roast_lifecycle.py's own
test_ms6514_live_connection_details_are_persisted_and_summarized, which this
mirrors for the generic path instead of one specific built-in bridge.
"""
from __future__ import annotations

import asyncio
import textwrap

import pytest

import device_plugins
from backend.app.models import RoastCreateRequest, RoastMode
from backend.app.roast_session.session import RoastSession
from device_plugins.examples import example_serial_meter  # noqa: F401 -- registers "example_serial_meter" on import

BOGUS_PORT = "/dev/nonexistent-for-tests"


@pytest.fixture(autouse=True)
def _example_plugin_registered():
    # The example module above already called register() once at import,
    # but a prior test in this file may have cleared the registry -- make
    # sure every test starts with it present regardless of order.
    device_plugins.register(device_plugins.get("example_serial_meter") or _reimport())
    yield


def _reimport():
    import importlib

    importlib.reload(example_serial_meter)
    return device_plugins.get("example_serial_meter")


async def _stop_background_task(session) -> None:
    if session._task is not None:
        session._task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await session._task


def test_registry_register_get_list_round_trip():
    spec = device_plugins.get("example_serial_meter")
    assert spec is not None
    assert spec.needs_port is True and spec.read_only is True
    assert any(s.kind == "example_serial_meter" for s in device_plugins.list_plugins())


def test_load_installed_imports_every_file_in_the_directory_and_skips_a_broken_one(tmp_path):
    good = tmp_path / "good_plugin.py"
    good.write_text(textwrap.dedent(
        """
        from device_plugins.base import PluginSpec, register

        def _connect(port, *, dry_end_c, fc_start_c, detect_milestones):
            raise NotImplementedError

        register(PluginSpec(kind="test_good_plugin", label="Test Good Plugin", connect=_connect))
        """
    ))
    broken = tmp_path / "broken_plugin.py"
    broken.write_text("this is not valid python (\n")
    underscored = tmp_path / "_skip_me.py"
    underscored.write_text("raise RuntimeError('must not be imported')")

    device_plugins.clear()
    try:
        device_plugins.load_installed(tmp_path)
        assert device_plugins.get("test_good_plugin") is not None
        assert device_plugins.get("broken_plugin") is None
    finally:
        device_plugins.clear()
        import importlib

        importlib.reload(example_serial_meter)


def test_device_plugins_api_lists_installed_plugins(client):
    resp = client.get("/api/v1/device-plugins")
    assert resp.status_code == 200
    kinds = {p["kind"]: p for p in resp.json()}
    assert "example_serial_meter" in kinds
    assert kinds["example_serial_meter"]["needs_port"] is True
    assert kinds["example_serial_meter"]["read_only"] is True


def test_plugin_live_connection_details_are_persisted_and_summarized(isolated_db):
    async def body():
        request = RoastCreateRequest(
            title="Test Roast", mode=RoastMode.PLUGIN_LIVE,
            plugin_kind="example_serial_meter", plugin_port=BOGUS_PORT,
        )
        session = RoastSession("plugin-persist-test", request)
        await session.connect()
        try:
            summary = session.summary()
            assert summary.plugin_kind == "example_serial_meter"
            assert summary.plugin_port == BOGUS_PORT
            # Read-only plugin: the control gate must say so, same check
            # real-time commands go through before ever reaching the engine.
            assert session.control.can_write is False

            await session.begin_recording()
            row = isolated_db.get_roast_row(session.id)
            assert row["plugin_kind"] == "example_serial_meter"
            assert row["plugin_port"] == BOGUS_PORT
        finally:
            await _stop_background_task(session)

    asyncio.run(body())


def test_plugin_live_rejects_an_unknown_plugin_kind(isolated_db):
    async def body():
        request = RoastCreateRequest(title="Test Roast", mode=RoastMode.PLUGIN_LIVE, plugin_kind="no_such_plugin", plugin_port=BOGUS_PORT)
        with pytest.raises(Exception) as excinfo:
            RoastSession("plugin-missing-test", request)
        assert "no_such_plugin" in str(excinfo.value)

    asyncio.run(body())


def test_plugin_live_requires_a_port_when_the_plugin_needs_one(isolated_db):
    async def body():
        request = RoastCreateRequest(title="Test Roast", mode=RoastMode.PLUGIN_LIVE, plugin_kind="example_serial_meter")
        with pytest.raises(Exception) as excinfo:
            RoastSession("plugin-noport-test", request)
        assert "Serial port" in str(excinfo.value)

    asyncio.run(body())
