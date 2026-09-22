"""Shared fixtures. Run from the repo root (PYTHONPATH=. -- see pytest.ini's
`pythonpath = .`, matching how the app itself is always run, per README)."""
from __future__ import annotations

import pytest

from backend.app import storage


@pytest.fixture
def isolated_db(tmp_path, tmp_path_factory, monkeypatch):
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
    anon_client.post("/api/auth/register", json={"username": "test-admin", "password": "test-password-1"})
    return anon_client
