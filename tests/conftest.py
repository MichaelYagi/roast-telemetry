"""Shared fixtures. Run from the repo root (PYTHONPATH=. -- see pytest.ini's
`pythonpath = .`, matching how the app itself is always run, per README)."""
from __future__ import annotations

import pytest

from backend.app import storage
from backend.app.roast_session import session_manager


@pytest.fixture(autouse=True)
def _default_db_isolation(tmp_path, monkeypatch):
    """Every test gets this, not just ones that explicitly request
    isolated_db below. A plain unit test that only ever constructs a
    RoastSession/RoastControl object directly (most of this suite) used to
    be safe by construction -- nothing it touched wrote to storage at all.
    That stopped being true once activity-log calls landed deep inside
    control.py/session.py (enter_safe_state, _fire_rule): a bare
    RoastControl built with no DB isolation at all now reaches
    storage.log_activity on a plain emergency_stop() call, and without this
    fixture that meant the *real* backend/data/roasts.db (confirmed: 18
    rows from one pytest run, cleaned up after finding this). Redirecting
    DB_PATH/ROASTS_DIR here, unconditionally, for every single test, is
    what makes "never touch the real DB" a structural guarantee instead of
    something each new test file -- or each new storage-touching code path
    added to already-existing code -- has to remember to opt into.

    isolated_db explicitly depends on this fixture (see its own params) so
    its own monkeypatch.setattr calls, plus a real init_db(), always run
    strictly after this one and take final effect -- this is just the
    baseline safety net underneath it, not a competing setup.

    Also clears the process-wide session_manager.sessions dict before every
    test -- it's a plain module-level dict with no per-test reset of its
    own, so a session left CONNECTED/STREAMING by one test (most never call
    stop/OFF, since their assertions don't need to) used to still be sitting
    there when a later, unrelated test ran -- e.g. GET /api/v1/health's new
    roaster_connected field (main.py) reading True in a test that created no
    session of its own at all."""
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "unused-default-test.db")
    monkeypatch.setattr(storage, "ROASTS_DIR", tmp_path / "unused-default-roasts")
    session_manager.sessions.clear()


@pytest.fixture
def isolated_db(tmp_path, tmp_path_factory, monkeypatch, _default_db_isolation):
    """Points backend.app.storage at a throwaway SQLite file for the
    duration of one test, instead of the real dev DB -- storage.DB_PATH is
    read fresh inside `_conn()` on every call, so monkeypatching the module
    attribute is enough; no connection-pooling/caching to worry about."""
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "test-roasts.db")
    # The roasts folder too: without this every test that records or imports a
    # roast leaves its .alog file behind in the real data folder.
    # (Outside tmp_path itself, which some tests list.)
    monkeypatch.setattr(storage, "ROASTS_DIR", tmp_path_factory.mktemp("roasts"))
    storage.init_db()
    return storage


@pytest.fixture
def anon_client(isolated_db):
    """A FastAPI TestClient wired to the isolated DB above, with no
    account registered and no session cookie -- for auth tests that need
    to exercise the logged-out gate itself. Imported lazily (after the
    DB_PATH monkeypatch) so the app's lifespan startup -- which calls
    storage.init_db()/abort_stale_roasts() -- runs against the throwaway
    DB, never the real one."""
    from fastapi.testclient import TestClient

    from backend.app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def client(anon_client):
    """Every other test file in this suite predates login existing at
    all and has no reason to exercise it -- this registers the first
    (thus admin, thus auto-allowed) account and keeps its session cookie
    on the TestClient's own cookie jar for the rest of the test, so every
    existing call through `client` stays authenticated exactly like a
    real logged-in browser, without every one of those tests needing its
    own login boilerplate."""
    anon_client.post("/api/v1/auth/register", json={"username": "test-admin", "password": "test-password-1"})
    return anon_client
