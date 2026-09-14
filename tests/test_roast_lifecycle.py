"""RoastSession.connect()/begin_recording() -- the ON-connects/START-records
split (modbus_live/ms6514_live only; see api/roasts.py's create_roast route
for the mode branch that actually calls these vs. the older start()).

These exercise the two new lifecycle methods directly, via a SIMULATOR-mode
session -- SimulatorEngine "connects" trivially (no real hardware, no
client_cls injection point to fake a modbus/ms6514 connection through this
constructor path), and connect()/begin_recording() are themselves
mode-agnostic (only the API route decides which mode calls which), so this
is a fair, isolated test of their own state-machine logic: DB-row timing,
status transitions, and the light-teardown path -- independent of whichever
mode happens to route through them in the real app.

No pytest-asyncio in this project -- each test drives its own async body
via asyncio.run() from an ordinary sync test function, and any test that
calls connect() (which spawns a background _run_loop() task) cancels and
awaits that task before its own asyncio.run() call returns, so nothing is
left running against a closed event loop afterward.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.app.models import ControlCommand, RoastCreateRequest, RoastMode, RoastStatus
from backend.app.roast_session.session import RoastSessionManager


async def _stop_background_task(session) -> None:
    if session._task is not None:
        session._task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await session._task


def make_manager_and_request() -> tuple[RoastSessionManager, RoastCreateRequest]:
    manager = RoastSessionManager()
    request = RoastCreateRequest(title="Test Roast", mode=RoastMode.SIMULATOR)
    return manager, request


def test_connect_does_not_persist_a_roast_row(isolated_db):
    async def body():
        manager, request = make_manager_and_request()
        session = manager.create(request)
        await session.connect()
        try:
            assert session.status == RoastStatus.IDLE
            assert isolated_db.get_roast_row(session.id) is None
        finally:
            await _stop_background_task(session)

    asyncio.run(body())


def test_begin_recording_persists_the_roast_row_and_sets_roasting(isolated_db):
    async def body():
        manager, request = make_manager_and_request()
        session = manager.create(request)
        await session.connect()
        try:
            await session.begin_recording()
            assert session.status == RoastStatus.ROASTING
            row = isolated_db.get_roast_row(session.id)
            assert row is not None
            assert row["status"] == "roasting"
        finally:
            await _stop_background_task(session)

    asyncio.run(body())


def test_apply_command_allowed_while_connected_but_not_recording(isolated_db):
    """Air/Drum/Burner sliders (or Testing Mode's write check) need to work
    during the armed-not-recording window too, matching Artisan's own
    control-before-record model -- not just once ROASTING."""
    async def body():
        manager, request = make_manager_and_request()
        session = manager.create(request)
        await session.connect()
        try:
            result = session.apply_command(ControlCommand(heater_pct=50.0))
            assert result["ok"] is True
        finally:
            await _stop_background_task(session)

    asyncio.run(body())


def test_abort_before_recording_touches_no_storage_and_removes_the_session(isolated_db):
    async def body():
        manager, request = make_manager_and_request()
        session = manager.create(request)
        roast_id = session.id
        await session.connect()

        await manager.abort(roast_id)

        assert isolated_db.get_roast_row(roast_id) is None
        assert manager.get(roast_id) is None

    asyncio.run(body())


def test_abort_after_recording_keeps_normal_stop_behavior(isolated_db):
    """Sanity check that the new IDLE-vs-recorded branch in abort() didn't
    regress the existing, already-working "stop a real roast" path."""
    async def body():
        manager, request = make_manager_and_request()
        session = manager.create(request)
        roast_id = session.id
        await session.connect()
        await session.begin_recording()

        await manager.abort(roast_id)

        row = isolated_db.get_roast_row(roast_id)
        assert row is not None
        assert row["status"] == "stopped"
        # A recorded/finished session isn't evicted from the manager today
        # (existing behavior, unchanged by this refactor) -- only the
        # never-recorded case is.
        assert manager.get(roast_id) is not None

    asyncio.run(body())
