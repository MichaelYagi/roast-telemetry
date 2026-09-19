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
from backend.app.roast_session.session import RoastSession, RoastSessionError, RoastSessionManager
from backend.app.ws_manager import pubsub

# Used only by the stale-same-port-release tests below -- ModbusEngine's own
# __init__ catches the resulting connect failure internally (never raises),
# so constructing a RoastSession against this never touches real/virtual
# hardware and stays fast (same convention as test_modbus_register_overrides.py).
BOGUS_PORT = "/dev/nonexistent-for-tests"


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


def test_modbus_live_connection_details_are_persisted_and_summarized(isolated_db):
    """What a modbus_live roast was actually connected with (transport,
    port/host, device profile) should be visible later -- live summary()
    and the persisted DB row both -- not just live in the Configure Roast
    form's own transient state. See RoastSession.__init__.

    Constructs RoastSession directly (not manager.create()), same as the
    stale-same-port-release tests above -- BOGUS_PORT genuinely can't
    connect, and manager.create() itself refuses that (by design), but
    connect()/begin_recording() have no such gate, so this stays a fair,
    fast, no-real-hardware test of the persistence itself."""
    async def body():
        request = RoastCreateRequest(
            title="Test Roast", mode=RoastMode.MODBUS_LIVE,
            modbus_transport="serial", modbus_port=BOGUS_PORT,
        )
        session = RoastSession("modbus-persist-test", request)
        await session.connect()
        try:
            summary = session.summary()
            assert summary.modbus_transport == "serial"
            assert summary.modbus_port == BOGUS_PORT

            await session.begin_recording()
            row = isolated_db.get_roast_row(session.id)
            assert row["modbus_transport"] == "serial"
            assert row["modbus_port"] == BOGUS_PORT
        finally:
            await _stop_background_task(session)

    asyncio.run(body())


def test_ms6514_live_connection_details_are_persisted_and_summarized(isolated_db):
    async def body():
        request = RoastCreateRequest(title="Test Roast", mode=RoastMode.MS6514_LIVE, ms6514_port=BOGUS_PORT)
        session = RoastSession("ms6514-persist-test", request)
        await session.connect()
        try:
            assert session.summary().ms6514_port == BOGUS_PORT

            await session.begin_recording()
            row = isolated_db.get_roast_row(session.id)
            assert row["ms6514_port"] == BOGUS_PORT
        finally:
            await _stop_background_task(session)

    asyncio.run(body())


def test_set_weight_roasted_persists_to_the_db_row(isolated_db):
    # Real workflow: roast finishes, beans cool, *then* get weighed -- by
    # which point this session may be the only thing standing between the
    # entered weight and a backend restart losing it, since set_weight_roasted
    # used to only update the in-memory session, never storage (see its own
    # docstring in session.py).
    async def body():
        manager, request = make_manager_and_request()
        session = manager.create(request)
        await session.connect()
        try:
            await session.begin_recording()
            session.set_weight_roasted(1388.0)
            assert session.weight_roasted_g == 1388.0
            row = isolated_db.get_roast_row(session.id)
            assert row["weight_roasted_g"] == 1388.0
        finally:
            await _stop_background_task(session)

    asyncio.run(body())


def test_set_weight_green_persists_to_the_db_row(isolated_db):
    # Same reasoning as set_weight_roasted above -- green weight is usually
    # entered up front, but a typo/re-weigh should be correctable the same
    # way, and correcting it must survive a backend restart too.
    async def body():
        manager, request = make_manager_and_request()
        session = manager.create(request)
        await session.connect()
        try:
            await session.begin_recording()
            session.set_weight_green(1600.0)
            assert session.weight_green_g == 1600.0
            row = isolated_db.get_roast_row(session.id)
            assert row["weight_green_g"] == 1600.0
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


def test_sample_messages_carry_the_current_status_after_begin_recording(isolated_db):
    """Regression test for a real bug found live: the frontend's roast.status
    only ever got set once, from the WS "snapshot" message sent at connect
    time (always IDLE for modbus_live/ms6514_live, since connect() precedes
    START) -- nothing updated it afterward, so the UI stayed stuck showing
    "idle" forever post-START (elapsed time frozen, every milestone button
    permanently disabled) even though the backend was genuinely ROASTING
    and profile was genuinely filling in. Fix: "sample" pubsub messages now
    carry the live status_snapshot; this confirms that actually happens."""
    async def body():
        manager, request = make_manager_and_request()
        request.sample_interval_s = 0.05  # keep the test fast
        session = manager.create(request)
        queue = pubsub.subscribe(session.id)
        try:
            await session.connect()
            await session.begin_recording()

            message = await asyncio.wait_for(queue.get(), timeout=2.0)
            while message["type"] != "sample":
                message = await asyncio.wait_for(queue.get(), timeout=2.0)

            assert message["status"] == "roasting"
        finally:
            pubsub.unsubscribe(session.id, queue)
            await _stop_background_task(session)

    asyncio.run(body())


# -- RoastSessionManager._release_stale_same_port_session --------------------
# Real bug found live: a modbus_live/ms6514_live session that's merely
# connected (ON) but never started recording has no DB row and isn't found
# by the frontend's reconnectActiveRoast() (which only looks for roasting/
# cooling roasts) -- if the operator navigates away without clicking OFF
# first, it sits forever holding the real serial port with no way to
# discover or release it short of restarting the backend. A new connect
# attempt to the exact same port now releases that abandoned session first
# instead of failing against it.


def test_create_releases_a_stale_idle_unrecorded_session_on_the_same_port():
    manager = RoastSessionManager()
    stale_request = RoastCreateRequest(title="Forgotten", mode=RoastMode.MODBUS_LIVE, modbus_port=BOGUS_PORT)
    stale = RoastSession("stale-id", stale_request)
    manager.sessions["stale-id"] = stale
    assert stale.status == RoastStatus.IDLE
    assert not stale._recorded

    new_request = RoastCreateRequest(title="Retry", mode=RoastMode.MODBUS_LIVE, modbus_port=BOGUS_PORT)
    # BOGUS_PORT genuinely can't connect, so create() itself still raises --
    # this test is about the stale session being released regardless of
    # that, not about a bogus port successfully connecting.
    with pytest.raises(RoastSessionError):
        manager.create(new_request)

    assert "stale-id" not in manager.sessions


def test_does_not_release_an_actually_recording_session_on_the_same_port():
    manager = RoastSessionManager()
    active_request = RoastCreateRequest(title="In progress", mode=RoastMode.MODBUS_LIVE, modbus_port=BOGUS_PORT)
    active = RoastSession("active-id", active_request)
    active.status = RoastStatus.ROASTING
    active._recorded = True
    manager.sessions["active-id"] = active

    new_request = RoastCreateRequest(title="Retry", mode=RoastMode.MODBUS_LIVE, modbus_port=BOGUS_PORT)
    with pytest.raises(RoastSessionError):
        manager.create(new_request)

    assert "active-id" in manager.sessions


def test_does_not_release_a_stale_session_on_a_different_port():
    manager = RoastSessionManager()
    stale_request = RoastCreateRequest(title="Forgotten", mode=RoastMode.MODBUS_LIVE, modbus_port=BOGUS_PORT)
    stale = RoastSession("stale-id", stale_request)
    manager.sessions["stale-id"] = stale

    new_request = RoastCreateRequest(title="Retry", mode=RoastMode.MODBUS_LIVE, modbus_port="/dev/nonexistent-a-different-one")
    with pytest.raises(RoastSessionError):
        manager.create(new_request)

    assert "stale-id" in manager.sessions


def test_does_not_release_a_stale_session_of_a_different_mode():
    manager = RoastSessionManager()
    stale_request = RoastCreateRequest(title="Forgotten", mode=RoastMode.MS6514_LIVE, ms6514_port=BOGUS_PORT)
    stale = RoastSession("stale-id", stale_request)
    manager.sessions["stale-id"] = stale

    # Same literal port string, but modbus_live -- shouldn't collide with an
    # ms6514_live session on it (different protocols, different meaning).
    new_request = RoastCreateRequest(title="Retry", mode=RoastMode.MODBUS_LIVE, modbus_port=BOGUS_PORT)
    with pytest.raises(RoastSessionError):
        manager.create(new_request)

    assert "stale-id" in manager.sessions
