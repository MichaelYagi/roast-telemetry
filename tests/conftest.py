"""Shared fixtures. Run from the repo root (PYTHONPATH=. -- see pytest.ini's
`pythonpath = .`, matching how the app itself is always run, per README)."""
from __future__ import annotations

import pytest

from backend.app import storage


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Points backend.app.storage at a throwaway SQLite file for the
    duration of one test, instead of the real dev DB -- storage.DB_PATH is
    read fresh inside `_conn()` on every call, so monkeypatching the module
    attribute is enough; no connection-pooling/caching to worry about."""
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "test-roasts.db")
    storage.init_db()
    return storage


@pytest.fixture
def client(isolated_db):
    """A FastAPI TestClient wired to the isolated DB above. Imported lazily
    (after the DB_PATH monkeypatch) so the app's lifespan startup -- which
    calls storage.init_db()/abort_stale_roasts() -- runs against the
    throwaway DB, never the real one."""
    from fastapi.testclient import TestClient

    from backend.app.main import app

    with TestClient(app) as c:
        yield c
