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
    storage.log_activity("roast", "delete", platform="Chrome on Windows", username="alice", roast_id="r1", roast_title="Ethiopia #4", message='Deleted "Ethiopia #4"')
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
    storage.log_activity("roast", "delete", platform="Chrome on Windows", roast_id="r1", roast_title="Colombia", message='Deleted "Colombia"')
    storage.log_activity("safety", "safe_state", platform="Chrome on Windows", roast_id="r2", roast_title="Kenya", message='Safety stop on "Kenya": emergency stop')

    assert len(storage.list_activity(category="safety")) == 1
    assert storage.count_activity(category="safety") == 1
    assert len(storage.list_activity(action="delete")) == 1
    assert len(storage.list_activity(roast_id="r2")) == 1
    assert len(storage.list_activity(q="kenya")) == 1  # case-insensitive, matches roast_title
    assert len(storage.list_activity(q="emergency")) == 1  # matches message
    assert len(storage.list_activity(q="nothing matches this")) == 0


def test_pagination(isolated_db):
    for i in range(5):
        storage.log_activity("roast", "delete", platform="Chrome on Windows", roast_id=f"r{i}", roast_title=f"Roast {i}", message=f"Deleted Roast {i}")
    page1 = storage.list_activity(limit=2, offset=0)
    page2 = storage.list_activity(limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 2
    assert {r["id"] for r in page1}.isdisjoint({r["id"] for r in page2})
    assert storage.count_activity() == 5


def test_retention_trims_to_the_newest_rows(isolated_db, monkeypatch):
    monkeypatch.setattr(storage, "ACTIVITY_LOG_MAX_ROWS", 3)
    for i in range(5):
        storage.log_activity("roast", "delete", platform="Chrome on Windows", roast_id=f"r{i}", roast_title=f"Roast {i}", message=f"Deleted Roast {i}")
    rows = storage.list_activity(limit=100)
    assert len(rows) == 3
    # The newest 3 survive (Roast 2/3/4), the oldest 2 (Roast 0/1) are gone.
    titles = {r["roast_title"] for r in rows}
    assert titles == {"Roast 2", "Roast 3", "Roast 4"}


# -- API -----------------------------------------------------------------------------


def test_activity_endpoints_start_with_only_the_fixtures_own_login(client):
    # The `client` fixture registers the first admin, which logs straight
    # in -- that login is the only thing in a fresh log.
    [entry] = client.get("/api/v1/activity").json()
    assert (entry["category"], entry["action"], entry["username"]) == ("auth", "login", "test-admin")
    assert client.get("/api/v1/activity/count").json() == {"total": 1}


def test_export_csv_and_json(isolated_db, client):
    storage.log_activity("roast", "delete", platform="Chrome on Windows", username="test-admin", roast_id="r1", roast_title="Ethiopia #4", message='Deleted "Ethiopia #4"')

    # category=roast leaves out the fixture's own login entry.
    csv_res = client.get("/api/v1/activity/export.csv", params={"category": "roast"})
    assert csv_res.status_code == 200
    assert "text/csv" in csv_res.headers["content-type"]
    assert "attachment" in csv_res.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(csv_res.text)))
    assert len(rows) == 1
    assert rows[0]["roast_title"] == "Ethiopia #4"

    json_res = client.get("/api/v1/activity/export.json", params={"category": "roast"})
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
    storage.log_activity("roast", "delete", platform="Chrome on Windows", roast_id="r1", roast_title="Doesn't matter", message="Shouldn't raise")
    # No exception -- that's the entire assertion. Restore _conn explicitly
    # (not monkeypatch.undo(): isolated_db's own DB_PATH/ROASTS_DIR patches
    # share this same monkeypatch fixture instance, and undo() would revert
    # those too, pointing the assertion below at the real database) and
    # confirm nothing was actually written (the insert never completed).
    monkeypatch.setattr(storage, "_conn", real_conn)
    assert storage.count_activity() == 0


# -- logins/logouts (category "auth") -------------------------------------------------

_CHROME_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
_SAFARI_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/18.5 Mobile/15E148 Safari/604.1"
)


def _auth_entries(client):
    return client.get("/api/v1/activity", params={"category": "auth"}).json()


def test_first_admin_auto_login_is_logged_with_its_platform(anon_client):
    anon_client.post(
        "/api/v1/auth/register", json={"username": "alice", "password": "a-fine-password"},
        headers={"User-Agent": _CHROME_WINDOWS},
    )
    [entry] = _auth_entries(anon_client)
    assert entry["action"] == "login"
    assert entry["username"] == "alice"
    assert entry["message"] == "Logged in (Chrome on Windows)"
    assert entry["platform"] == "Chrome on Windows"
    assert entry["detail"]["method"] == "password"
    assert entry["detail"]["user_agent"] == _CHROME_WINDOWS


def test_login_and_logout_are_both_logged(anon_client):
    anon_client.post("/api/v1/auth/register", json={"username": "alice", "password": "a-fine-password"})
    anon_client.post("/api/v1/auth/logout", headers={"User-Agent": _SAFARI_IPHONE})
    anon_client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "a-fine-password"},
        headers={"User-Agent": _SAFARI_IPHONE},
    )

    newest_first = _auth_entries(anon_client)
    assert [e["action"] for e in newest_first] == ["login", "logout", "login"]
    assert newest_first[0]["message"] == "Logged in (Safari on iPhone)"
    assert newest_first[1]["message"] == "Logged out (Safari on iPhone)"
    assert all(e["username"] == "alice" for e in newest_first)


def test_failed_login_is_not_logged(anon_client):
    # Deliberate: a burst of bad passwords would otherwise push real
    # history out of the retention-capped log (see auth.log_sign_in_event).
    anon_client.post("/api/v1/auth/register", json={"username": "alice", "password": "a-fine-password"})
    anon_client.post("/api/v1/auth/login", json={"username": "alice", "password": "wrong-password"})
    anon_client.post("/api/v1/auth/login", json={"username": "nobody", "password": "whatever-it-is"})
    assert [e["action"] for e in _auth_entries(anon_client)] == ["login"]  # just the registration's


def test_logout_with_no_live_session_is_not_logged(anon_client):
    anon_client.post("/api/v1/auth/register", json={"username": "alice", "password": "a-fine-password"})
    anon_client.post("/api/v1/auth/logout")
    anon_client.post("/api/v1/auth/logout")  # cookie already gone -- nobody to attribute it to
    assert [e["action"] for e in storage.list_activity(category="auth")] == ["logout", "login"]


def test_api_key_client_connect_and_disconnect_are_logged(anon_client):
    anon_client.post("/api/v1/auth/register", json={"username": "alice", "password": "a-fine-password"})
    key = anon_client.post("/api/v1/auth/api-key").json()["api_key"]
    anon_client.cookies.clear()  # the app has only its key, no session
    app = {"X-API-Key": key, "X-Client-Platform": "Roast Telemetry app 1.0.0 on Android 14", "User-Agent": "okhttp/4.12.0"}

    assert anon_client.post("/api/v1/auth/connect", headers=app).status_code == 204
    assert anon_client.post("/api/v1/auth/disconnect", headers=app).status_code == 204

    logout, login = [e for e in storage.list_activity(category="auth") if e["username"] == "alice"][:2]
    assert login["action"] == "login"
    assert login["message"] == "Logged in (Roast Telemetry app 1.0.0 on Android 14)"
    assert json.loads(login["detail_json"])["method"] == "api_key"
    assert logout["action"] == "logout"
    assert logout["message"] == "Logged out (Roast Telemetry app 1.0.0 on Android 14)"


def test_connect_requires_authentication(anon_client):
    anon_client.post("/api/v1/auth/register", json={"username": "alice", "password": "a-fine-password"})
    anon_client.cookies.clear()
    assert anon_client.post("/api/v1/auth/connect", headers={"X-API-Key": "rt_not-a-real-key"}).status_code == 401


def test_client_platform_labels():
    from starlette.requests import Request

    from backend.app.auth import client_platform

    def platform(headers):
        return client_platform(Request({"type": "http", "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]}))

    assert platform({"User-Agent": _CHROME_WINDOWS}) == "Chrome on Windows"
    assert platform({"User-Agent": _SAFARI_IPHONE}) == "Safari on iPhone"
    edge = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0"
    assert platform({"User-Agent": edge}) == "Edge on macOS"
    firefox_android = "Mozilla/5.0 (Android 14; Mobile; rv:131.0) Gecko/131.0 Firefox/131.0"
    assert platform({"User-Agent": firefox_android}) == "Firefox on Android"
    assert platform({"User-Agent": "curl/8.5.0"}) == "curl"
    assert platform({}) == "Unknown platform"
    # A declared client name wins over the User-Agent, and is sanitized/capped.
    assert platform({"X-Client-Platform": "My App\x07 on Linux", "User-Agent": _CHROME_WINDOWS}) == "My App on Linux"
    assert len(platform({"X-Client-Platform": "x" * 500})) == 80


# -- every entry carries user + platform ----------------------------------------------


def test_roast_edits_record_user_and_platform(client):
    roast_id = client.post("/api/v1/roasts", json={"title": "Who/Where", "mode": "simulator"}).json()["id"]
    client.put(f"/api/v1/roasts/{roast_id}/tags", json={"tags": ["x"]}, headers={"User-Agent": _SAFARI_IPHONE})
    [entry] = client.get("/api/v1/activity", params={"action": "set_tags"}).json()
    assert entry["username"] == "test-admin"
    assert entry["platform"] == "Safari on iPhone"


def test_manual_emergency_stop_records_user_and_platform(client):
    roast_id = client.post("/api/v1/roasts", json={"title": "E-stop Who", "mode": "simulator"}).json()["id"]
    client.post(f"/api/v1/roasts/{roast_id}/emergency-stop", headers={"X-Client-Platform": "Roast Telemetry app 1.0.0 on Android 14"})
    [entry] = client.get("/api/v1/activity", params={"action": "safe_state"}).json()
    assert entry["username"] == "test-admin"
    assert entry["platform"] == "Roast Telemetry app 1.0.0 on Android 14"


def test_autonomous_safety_stop_is_marked_automatic(client):
    import asyncio

    from backend.app.roast_session import session_manager

    roast_id = client.post("/api/v1/roasts", json={"title": "Fail-safe", "mode": "simulator"}).json()["id"]
    session = session_manager.get(roast_id)
    asyncio.run(session.control.enter_safe_state("lost the temperature reading during target control"))
    [entry] = client.get("/api/v1/activity", params={"action": "safe_state"}).json()
    assert entry["username"] is None
    assert entry["platform"] == storage.AUTOMATIC_PLATFORM


def test_csv_export_includes_platform(client):
    csv_res = client.get("/api/v1/activity/export.csv", params={"category": "auth"})
    [row] = list(csv.DictReader(io.StringIO(csv_res.text)))
    assert row["username"] == "test-admin"
    assert row["platform"] == "Unknown platform"  # TestClient's UA is just "testclient"
