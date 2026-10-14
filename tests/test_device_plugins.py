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
from device_plugins.installed import example_serial_meter  # noqa: F401 -- registers "example_serial_meter" on import

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
    # Ships code-disabled on purpose (see its own module docstring) -- a
    # real, working example that's still never offered to an end user
    # until its author (whoever copies it to build a real plugin) says so.
    assert spec.enabled is False
    assert any(s.kind == "example_serial_meter" for s in device_plugins.list_plugins())  # still registered -- just not listed by the API (see below)


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


@pytest.fixture
def _visible_test_plugin():
    """A plugin shipped the ordinary way (PluginSpec.enabled defaults to
    True), registered directly rather than via example_serial_meter --
    which ships enabled=False on purpose (see its own module docstring) --
    so tests of the normal listing/toggle/delete path aren't coupled to
    that one's deliberately-hidden-by-default state."""
    def _connect(port, *, dry_end_c, fc_start_c, detect_milestones):
        raise NotImplementedError

    spec = device_plugins.PluginSpec(kind="test_visible_plugin", label="Test Visible Plugin", connect=_connect, needs_port=True, read_only=True)
    device_plugins.register(spec)
    yield spec
    device_plugins.base._REGISTRY.pop("test_visible_plugin", None)
    device_plugins.base._SOURCE_PATHS.pop("test_visible_plugin", None)


def test_device_plugins_api_lists_installed_plugins(client, _visible_test_plugin):
    resp = client.get("/api/v1/device-plugins")
    assert resp.status_code == 200
    kinds = {p["kind"]: p for p in resp.json()}
    assert "test_visible_plugin" in kinds
    assert kinds["test_visible_plugin"]["needs_port"] is True
    assert kinds["test_visible_plugin"]["read_only"] is True
    assert kinds["test_visible_plugin"]["enabled"] is True  # on by default -- nothing disabled it yet
    # example_serial_meter is real and registered (see test_registry_... above)
    # but ships enabled=False -- never surfaced by this endpoint on its own.
    assert "example_serial_meter" not in kinds


def test_device_plugins_api_toggles_enabled(client, _visible_test_plugin):
    resp = client.put("/api/v1/device-plugins/test_visible_plugin", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    kinds = {p["kind"]: p for p in client.get("/api/v1/device-plugins").json()}
    assert kinds["test_visible_plugin"]["enabled"] is False

    resp = client.put("/api/v1/device-plugins/test_visible_plugin", json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True
    kinds = {p["kind"]: p for p in client.get("/api/v1/device-plugins").json()}
    assert kinds["test_visible_plugin"]["enabled"] is True


def test_device_plugins_api_404s_for_a_kind_that_isnt_installed(client):
    resp = client.put("/api/v1/device-plugins/no_such_plugin", json={"enabled": False})
    assert resp.status_code == 404


def test_a_code_disabled_plugin_is_excluded_from_the_listing(client):
    kinds = {p["kind"] for p in client.get("/api/v1/device-plugins").json()}
    assert "example_serial_meter" not in kinds


def test_a_code_disabled_plugin_cannot_be_toggled_or_deleted_through_the_api(client):
    assert client.put("/api/v1/device-plugins/example_serial_meter", json={"enabled": True}).status_code == 404
    assert client.delete("/api/v1/device-plugins/example_serial_meter").status_code == 404


def test_a_code_disabled_plugin_is_still_directly_connectable(isolated_db):
    # Deliberately still usable this way -- enabled=False only hides it
    # from the UI/listing endpoints above, so its own author can still
    # test it (by pointing a roast straight at its kind) before exposing
    # it to anyone else. example_serial_meter itself proves this for real,
    # rather than a synthetic stand-in.
    request = RoastCreateRequest(
        title="Test Roast", mode=RoastMode.PLUGIN_LIVE,
        plugin_kind="example_serial_meter", plugin_port=BOGUS_PORT,
    )
    session = RoastSession("plugin-code-disabled-test", request)
    assert session.plugin_kind == "example_serial_meter"


def test_unregister_removes_a_loaded_plugin_and_deletes_its_file(tmp_path):
    plugin_file = tmp_path / "throwaway_plugin.py"
    plugin_file.write_text(textwrap.dedent(
        """
        from device_plugins.base import PluginSpec, register

        def _connect(port, *, dry_end_c, fc_start_c, detect_milestones):
            raise NotImplementedError

        register(PluginSpec(kind="test_throwaway_plugin", label="Test Throwaway Plugin", connect=_connect))
        """
    ))
    device_plugins.load_installed(tmp_path)
    assert device_plugins.get("test_throwaway_plugin") is not None
    assert plugin_file.is_file()

    assert device_plugins.unregister("test_throwaway_plugin") is True

    assert device_plugins.get("test_throwaway_plugin") is None
    assert not plugin_file.is_file()  # deleted, not just unregistered


def test_unregister_a_directly_registered_plugin_leaves_no_file_to_touch(_visible_test_plugin):
    # Registered directly in the fixture above (not via load_installed()
    # scanning a real folder), so there's no on-disk file device_plugins/
    # base.py would know to delete -- confirms unregister() doesn't blow up
    # or try to delete anything just because a kind happens to match.
    assert device_plugins.unregister("test_visible_plugin") is True
    assert device_plugins.get("test_visible_plugin") is None


def test_unregister_returns_false_for_an_unknown_kind():
    assert device_plugins.unregister("no_such_plugin_at_all") is False


def test_device_plugins_api_delete_removes_it_immediately(client, _visible_test_plugin):
    resp = client.delete("/api/v1/device-plugins/test_visible_plugin")
    assert resp.status_code == 204

    kinds = {p["kind"] for p in client.get("/api/v1/device-plugins").json()}
    assert "test_visible_plugin" not in kinds  # gone, not just disabled-but-listed


def test_device_plugins_api_delete_404s_for_a_kind_that_isnt_installed(client):
    resp = client.delete("/api/v1/device-plugins/no_such_plugin")
    assert resp.status_code == 404


def test_plugin_live_rejects_connecting_a_disabled_plugin(isolated_db):
    isolated_db.set_plugin_enabled("example_serial_meter", False)
    request = RoastCreateRequest(
        title="Test Roast", mode=RoastMode.PLUGIN_LIVE,
        plugin_kind="example_serial_meter", plugin_port=BOGUS_PORT,
    )
    with pytest.raises(Exception) as excinfo:
        RoastSession("plugin-disabled-test", request)
    assert "disabled" in str(excinfo.value)


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


def test_ensure_example_copied_is_a_noop_from_a_source_checkout(monkeypatch):
    import device_plugins.base as dpb

    monkeypatch.setattr(dpb, "_DATA_DIR_OVERRIDE", None)
    dpb.ensure_example_copied()  # must not raise; nothing to assert -- genuinely nothing happens


def test_ensure_example_copied_copies_the_real_example_in_a_frozen_build(tmp_path, monkeypatch):
    import device_plugins.base as dpb

    installed_dir = tmp_path / "device_plugins" / "installed"
    installed_dir.mkdir(parents=True)
    monkeypatch.setattr(dpb, "_DATA_DIR_OVERRIDE", str(tmp_path))
    monkeypatch.setattr(dpb, "INSTALLED_DIR", installed_dir)

    dpb.ensure_example_copied()

    copied = installed_dir / "example_serial_meter.py"
    assert copied.is_file()
    assert copied.read_text() == (dpb._BUNDLED_INSTALLED_DIR / "example_serial_meter.py").read_text()


def test_plugin_live_requires_a_port_when_the_plugin_needs_one(isolated_db):
    async def body():
        request = RoastCreateRequest(title="Test Roast", mode=RoastMode.PLUGIN_LIVE, plugin_kind="example_serial_meter")
        with pytest.raises(Exception) as excinfo:
            RoastSession("plugin-noport-test", request)
        assert "Serial port" in str(excinfo.value)

    asyncio.run(body())
