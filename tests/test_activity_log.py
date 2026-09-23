"""Who did what, when -- storage.py's activity_log table, GET /api/v1/activity
and its CSV/JSON exports, and a representative mutation from each category
(not every single call site -- see backend/app/storage.py's log_activity()
and the callers in api/roasts.py, roast_session/control.py and
roast_session/session.py)."""
from __future__ import annotations

import csv
import io
import json

from backend.app import storage


# -- storage.py ----------------------------------------------------------------------


def test_log_and_list_round_trip(isolated_db):
    storage.log_activity("roast", "delete", username="alice", roast_id="r1", roast_title="Ethiopia #4", message='Deleted "Ethiopia #4"')
    rows = storage.list_activity()
    assert len(rows) == 1
    row = rows[0]
    assert row["category"] == "roast"
    assert row["action"] == "delete"
    assert row["username"] == "alice"
    assert row["roast_id"] == "r1"
    assert row["roast_title"] == "Ethiopia #4"
    assert row["message"] == 'Deleted "Ethiopia #4"'
    assert storage.count_activity() == 1


def test_filters_narrow_correctly(isolated_db):
    storage.log_activity("roast", "delete", roast_id="r1", roast_title="Colombia", message='Deleted "Colombia"')
    storage.log_activity("safety", "safe_state", roast_id="r2", roast_title="Kenya", message='Safety stop on "Kenya": emergency stop')

    assert len(storage.list_activity(category="safety")) == 1
    assert storage.count_activity(category="safety") == 1
    assert len(storage.list_activity(action="delete")) == 1
    assert len(storage.list_activity(roast_id="r2")) == 1
    assert len(storage.list_activity(q="kenya")) == 1  # case-insensitive, matches roast_title
    assert len(storage.list_activity(q="emergency")) == 1  # matches message
    assert len(storage.list_activity(q="nothing matches this")) == 0


def test_pagination(isolated_db):
    for i in range(5):
        storage.log_activity("roast", "delete", roast_id=f"r{i}", roast_title=f"Roast {i}", message=f"Deleted Roast {i}")
    page1 = storage.list_activity(limit=2, offset=0)
    page2 = storage.list_activity(limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 2
    assert {r["id"] for r in page1}.isdisjoint({r["id"] for r in page2})
    assert storage.count_activity() == 5


def test_retention_trims_to_the_newest_rows(isolated_db, monkeypatch):
    monkeypatch.setattr(storage, "ACTIVITY_LOG_MAX_ROWS", 3)
    for i in range(5):
        storage.log_activity("roast", "delete", roast_id=f"r{i}", roast_title=f"Roast {i}", message=f"Deleted Roast {i}")
    rows = storage.list_activity(limit=100)
    assert len(rows) == 3
    # The newest 3 survive (Roast 2/3/4), the oldest 2 (Roast 0/1) are gone.
    titles = {r["roast_title"] for r in rows}
    assert titles == {"Roast 2", "Roast 3", "Roast 4"}


# -- API -----------------------------------------------------------------------------


def test_activity_endpoints_start_empty(client):
    assert client.get("/api/v1/activity").json() == []
    assert client.get("/api/v1/activity/count").json() == {"total": 0}


def test_export_csv_and_json(isolated_db, client):
    storage.log_activity("roast", "delete", username="test-admin", roast_id="r1", roast_title="Ethiopia #4", message='Deleted "Ethiopia #4"')

    csv_res = client.get("/api/v1/activity/export.csv")
    assert csv_res.status_code == 200
    assert "text/csv" in csv_res.headers["content-type"]
    assert "attachment" in csv_res.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(csv_res.text)))
    assert len(rows) == 1
    assert rows[0]["roast_title"] == "Ethiopia #4"

    json_res = client.get("/api/v1/activity/export.json")
    assert json_res.status_code == 200
    assert "application/json" in json_res.headers["content-type"]
    assert "attachment" in json_res.headers["content-disposition"]
    entries = json.loads(json_res.text)
    assert len(entries) == 1
    assert entries[0]["roast_title"] == "Ethiopia #4"


# -- representative mutations, one per category --------------------------------------


def test_deleting_a_roast_logs_it_and_the_snapshot_survives(client):
    roast_id = client.post("/api/v1/roasts", json={"title": "To Delete", "mode": "simulator"}).json()["id"]
    client.post(f"/api/v1/roasts/{roast_id}/stop")

    res = client.delete(f"/api/v1/roasts/{roast_id}")
    assert res.status_code == 204

    entries = client.get("/api/v1/activity", params={"category": "roast", "action": "delete"}).json()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["roast_id"] == roast_id
    assert entry["roast_title"] == "To Delete"  # the snapshot, not a join -- the roast row is gone now
    assert entry["username"] == "test-admin"
    assert "To Delete" in entry["message"]

    # The roast itself really is gone -- the log entry doesn't keep it alive.
    assert client.get(f"/api/v1/roasts/{roast_id}").status_code == 404


def test_emergency_stop_logs_a_safety_entry(client):
    roast_id = client.post("/api/v1/roasts", json={"title": "E-stop Test", "mode": "simulator"}).json()["id"]
    client.post(f"/api/v1/roasts/{roast_id}/emergency-stop")

    entries = client.get("/api/v1/activity", params={"category": "safety", "action": "safe_state"}).json()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["roast_id"] == roast_id
    assert entry["username"] == "test-admin"
    assert "emergency stop" in entry["message"]


def test_activity_log_filters_by_category(client):
    roast_id = client.post("/api/v1/roasts", json={"title": "Mixed", "mode": "simulator"}).json()["id"]
    client.post(f"/api/v1/roasts/{roast_id}/emergency-stop")
    client.put(f"/api/v1/roasts/{roast_id}/tags", json={"tags": ["espresso"]})

    roast_entries = client.get("/api/v1/activity", params={"category": "roast"}).json()
    safety_entries = client.get("/api/v1/activity", params={"category": "safety"}).json()
    assert any(e["action"] == "set_tags" for e in roast_entries)
    assert any(e["action"] == "safe_state" for e in safety_entries)
    assert not any(e["category"] == "safety" for e in roast_entries)


# -- failure isolation: the point of doing this inside log_activity itself -----------


def test_log_activity_never_raises_even_when_its_own_db_access_fails(isolated_db, monkeypatch):
    """The try/except lives inside log_activity() itself, not at each of
    the ~15 call sites (see its own docstring) -- this is what actually
    proves that decision: make log_activity's own connection fail and
    confirm it swallows the error instead of propagating it. (Call sites
    plainly have no try/except of their own -- see e.g. delete_roast in
    api/roasts.py -- so if this guarantee didn't hold here, every one of
    them would be one DB hiccup away from turning a successful mutation
    into a 500.)"""

    real_conn = storage._conn

    def boom():
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(storage, "_conn", boom)
    storage.log_activity("roast", "delete", roast_id="r1", roast_title="Doesn't matter", message="Shouldn't raise")
    # No exception -- that's the entire assertion. Restore _conn explicitly
    # (not monkeypatch.undo(): isolated_db's own DB_PATH/ROASTS_DIR patches
    # share this same monkeypatch fixture instance, and undo() would revert
    # those too, pointing the assertion below at the real database) and
    # confirm nothing was actually written (the insert never completed).
    monkeypatch.setattr(storage, "_conn", real_conn)
    assert storage.count_activity() == 0
