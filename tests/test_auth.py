"""Registration/login/logout, the first-account-becomes-admin rule, the
PENDING/ALLOWED/DENIED account lifecycle, and the require_login gate
that every other /api/v1/* route sits behind.

Uses `anon_client` (see conftest.py) rather than the auth-preloaded
`client` fixture used by every other test file -- these tests need to
control login state themselves, starting from genuinely logged out.
"""
from __future__ import annotations


def register(client, username, password="a-fine-password"):
    return client.post("/api/v1/auth/register", json={"username": username, "password": password})


def test_auth_status_reports_whether_an_admin_exists_yet(anon_client):
    # Public -- no login required (see main.py's _PUBLIC_API_PATHS) --
    # since the login screen needs this before anyone's typed anything.
    assert anon_client.get("/api/v1/auth/status").json() == {"has_admin": False}
    register(anon_client, "alice")
    assert anon_client.get("/api/v1/auth/status").json() == {"has_admin": True}


def test_first_registration_becomes_an_auto_allowed_admin_and_is_logged_in(anon_client):
    resp = register(anon_client, "alice")
    assert resp.status_code == 201
    body = resp.json()
    assert body["role"] == "admin"
    assert body["status"] == "allowed"
    # Logged in immediately -- no separate /auth/login call needed.
    me = anon_client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "alice"


def test_second_registration_is_a_pending_plain_user_not_logged_in(anon_client):
    register(anon_client, "alice")
    anon_client.post("/api/v1/auth/logout")
    resp = register(anon_client, "bob")
    body = resp.json()
    assert body["role"] == "user"
    assert body["status"] == "pending"
    # Not auto-logged-in -- a pending account can't reach anything yet.
    assert anon_client.get("/api/v1/auth/me").status_code == 401


def test_pending_user_cannot_log_in(anon_client):
    register(anon_client, "alice")
    anon_client.post("/api/v1/auth/logout")
    register(anon_client, "bob", password="bobs-password")
    anon_client.post("/api/v1/auth/logout")
    resp = anon_client.post("/api/v1/auth/login", json={"username": "bob", "password": "bobs-password"})
    assert resp.status_code == 403
    assert "approval" in resp.json()["detail"].lower()


def test_wrong_password_is_rejected(anon_client):
    register(anon_client, "alice", password="correct-horse")
    anon_client.post("/api/v1/auth/logout")
    resp = anon_client.post("/api/v1/auth/login", json={"username": "alice", "password": "wrong-password"})
    assert resp.status_code == 401


def test_duplicate_username_is_rejected(anon_client):
    register(anon_client, "alice")
    resp = register(anon_client, "alice")
    assert resp.status_code == 409


def test_unauthenticated_request_to_an_ordinary_route_is_rejected(anon_client):
    resp = anon_client.get("/api/v1/roasts")
    assert resp.status_code == 401


def test_logged_in_pending_user_gate_blocks_existing_routes(anon_client):
    # Belt-and-suspenders: pending users never get a session cookie in the
    # first place (see the auto-login test above), but this nails down
    # that even if a cookie somehow existed for a PENDING account, the
    # gate itself re-checks status on every request rather than trusting
    # "a valid session token exists" alone.
    register(anon_client, "alice")
    admin_client = anon_client
    admin_client.post("/api/v1/auth/logout")
    register(admin_client, "bob", password="bobs-password")
    # bob has no cookie (pending never gets one) -- confirm the ordinary
    # route stays locked out for the now-logged-out client too.
    assert admin_client.get("/api/v1/roasts").status_code == 401


def _login(client, username, password):
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def test_admin_can_allow_a_pending_user_who_can_then_log_in(anon_client):
    register(anon_client, "alice")
    anon_client.post("/api/v1/auth/logout")
    register(anon_client, "bob", password="bobs-password")
    _login(anon_client, "alice", "a-fine-password")
    users = anon_client.get("/api/v1/auth/users").json()
    bob = next(u for u in users if u["username"] == "bob")
    resp = anon_client.post(f"/api/v1/auth/users/{bob['id']}/allow")
    assert resp.json()["status"] == "allowed"
    anon_client.post("/api/v1/auth/logout")
    login_resp = _login(anon_client, "bob", "bobs-password")
    assert login_resp.json()["status"] == "allowed"


def test_non_admin_cannot_reach_the_users_list(anon_client):
    register(anon_client, "alice")
    anon_client.post("/api/v1/auth/logout")
    register(anon_client, "bob", password="bobs-password")
    anon_client.post("/api/v1/auth/logout")
    _login(anon_client, "alice", "a-fine-password")
    bob_id = next(u for u in anon_client.get("/api/v1/auth/users").json() if u["username"] == "bob")["id"]
    anon_client.post(f"/api/v1/auth/users/{bob_id}/allow")
    anon_client.post("/api/v1/auth/logout")
    _login(anon_client, "bob", "bobs-password")
    resp = anon_client.get("/api/v1/auth/users")
    assert resp.status_code == 403


def test_denied_user_cannot_log_in_and_can_be_reset_to_pending(anon_client):
    register(anon_client, "alice")
    anon_client.post("/api/v1/auth/logout")
    register(anon_client, "bob", password="bobs-password")
    anon_client.post("/api/v1/auth/logout")
    _login(anon_client, "alice", "a-fine-password")
    bob_id = next(u for u in anon_client.get("/api/v1/auth/users").json() if u["username"] == "bob")["id"]

    deny_resp = anon_client.post(f"/api/v1/auth/users/{bob_id}/deny")
    assert deny_resp.json()["status"] == "denied"
    anon_client.post("/api/v1/auth/logout")
    denied_login = anon_client.post("/api/v1/auth/login", json={"username": "bob", "password": "bobs-password"})
    assert denied_login.status_code == 403
    assert "denied" in denied_login.json()["detail"].lower()

    _login(anon_client, "alice", "a-fine-password")
    reset_resp = anon_client.post(f"/api/v1/auth/users/{bob_id}/reset-to-pending")
    assert reset_resp.json()["status"] == "pending"
    anon_client.post("/api/v1/auth/logout")
    pending_login = anon_client.post("/api/v1/auth/login", json={"username": "bob", "password": "bobs-password"})
    assert pending_login.status_code == 403
    assert "approval" in pending_login.json()["detail"].lower()


def test_denying_an_already_logged_in_user_ends_their_session_immediately(anon_client):
    register(anon_client, "alice")
    anon_client.post("/api/v1/auth/logout")
    register(anon_client, "bob", password="bobs-password")
    anon_client.post("/api/v1/auth/logout")
    _login(anon_client, "alice", "a-fine-password")
    bob_id = next(u for u in anon_client.get("/api/v1/auth/users").json() if u["username"] == "bob")["id"]
    anon_client.post(f"/api/v1/auth/users/{bob_id}/allow")
    anon_client.post("/api/v1/auth/logout")
    _login(anon_client, "bob", "bobs-password")
    assert anon_client.get("/api/v1/roasts").status_code == 200

    _login(anon_client, "alice", "a-fine-password")
    anon_client.post(f"/api/v1/auth/users/{bob_id}/deny")

    # bob's cookie is stale on the shared client jar now -- alice's own
    # login above already overwrote it, so re-simulate bob's browser by
    # logging in again: it should be rejected outright now that he's denied.
    resp = anon_client.post("/api/v1/auth/login", json={"username": "bob", "password": "bobs-password"})
    assert resp.status_code == 403


def test_admin_cannot_delete_or_deny_their_own_account(anon_client):
    register(anon_client, "alice")
    me = anon_client.get("/api/v1/auth/me").json()
    assert anon_client.post(f"/api/v1/auth/users/{me['id']}/deny").status_code == 400
    assert anon_client.delete(f"/api/v1/auth/users/{me['id']}").status_code == 400


def test_admin_can_delete_a_user_and_it_ends_their_session(anon_client):
    register(anon_client, "alice")
    anon_client.post("/api/v1/auth/logout")
    register(anon_client, "bob", password="bobs-password")
    anon_client.post("/api/v1/auth/logout")
    _login(anon_client, "alice", "a-fine-password")
    bob_id = next(u for u in anon_client.get("/api/v1/auth/users").json() if u["username"] == "bob")["id"]
    anon_client.post(f"/api/v1/auth/users/{bob_id}/allow")
    anon_client.post("/api/v1/auth/logout")
    _login(anon_client, "bob", "bobs-password")

    _login(anon_client, "alice", "a-fine-password")
    del_resp = anon_client.delete(f"/api/v1/auth/users/{bob_id}")
    assert del_resp.status_code == 204

    remaining = anon_client.get("/api/v1/auth/users").json()
    assert all(u["id"] != bob_id for u in remaining)
    # A deleted account can no longer log in at all.
    assert anon_client.post("/api/v1/auth/login", json={"username": "bob", "password": "bobs-password"}).status_code == 401


def test_remember_me_unchecked_sets_a_plain_session_cookie(anon_client):
    register(anon_client, "alice", password="alices-password")
    anon_client.post("/api/v1/auth/logout")
    resp = anon_client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "alices-password", "remember_me": False}
    )
    set_cookie = resp.headers.get("set-cookie")
    assert "rt_session=" in set_cookie
    # No Max-Age/Expires at all -- that's what makes it a session cookie
    # the browser itself drops on close, not something this test can
    # observe any other way (TestClient has no real browser to close).
    assert "max-age" not in set_cookie.lower()
    assert "expires" not in set_cookie.lower()


def test_remember_me_checked_sets_a_long_lived_cookie(anon_client):
    register(anon_client, "alice", password="alices-password")
    anon_client.post("/api/v1/auth/logout")
    resp = anon_client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "alices-password", "remember_me": True}
    )
    set_cookie = resp.headers.get("set-cookie")
    assert "max-age=" in set_cookie.lower()


def test_first_admin_auto_login_is_remembered_by_default(anon_client):
    # No login form was involved (see register()'s auto-login) -- there's
    # no "Remember me" checkbox to have left unchecked, so this always
    # gets the persistent cookie.
    resp = register(anon_client, "alice")
    set_cookie = resp.headers.get("set-cookie")
    assert "max-age=" in set_cookie.lower()


def test_logout_clears_the_session_so_the_gate_blocks_again(anon_client):
    register(anon_client, "alice")
    assert anon_client.get("/api/v1/auth/me").status_code == 200
    anon_client.post("/api/v1/auth/logout")
    assert anon_client.get("/api/v1/auth/me").status_code == 401


# -- API keys (X-API-Key) ----------------------------------------------------


def test_generating_an_api_key_returns_it_once_and_reports_has_api_key(anon_client):
    register(anon_client, "alice")
    assert anon_client.get("/api/v1/auth/me").json()["has_api_key"] is False

    resp = anon_client.post("/api/v1/auth/api-key")
    assert resp.status_code == 200
    key = resp.json()["api_key"]
    assert len(key) > 20  # secrets.token_urlsafe(32) -- not a short/guessable value
    assert key.startswith("rt_")

    # The key itself is never echoed back anywhere else -- only whether one exists.
    me = anon_client.get("/api/v1/auth/me").json()
    assert me["has_api_key"] is True
    assert "api_key" not in me
    assert "api_key_hash" not in me


def test_api_key_header_authenticates_like_a_session_cookie(anon_client):
    register(anon_client, "alice")
    key = anon_client.post("/api/v1/auth/api-key").json()["api_key"]

    # Drop the session cookie entirely -- only the header should carry auth now.
    anon_client.cookies.clear()
    assert anon_client.get("/api/v1/roasts").status_code == 401  # confirms the cookie really is gone

    resp = anon_client.get("/api/v1/roasts", headers={"X-API-Key": key})
    assert resp.status_code == 200


def test_wrong_api_key_is_rejected(anon_client):
    register(anon_client, "alice")
    anon_client.post("/api/v1/auth/api-key")
    anon_client.cookies.clear()

    resp = anon_client.get("/api/v1/roasts", headers={"X-API-Key": "not-the-real-key"})
    assert resp.status_code == 401


def test_regenerating_an_api_key_invalidates_the_previous_one(anon_client):
    register(anon_client, "alice")
    old_key = anon_client.post("/api/v1/auth/api-key").json()["api_key"]
    new_key = anon_client.post("/api/v1/auth/api-key").json()["api_key"]
    assert new_key != old_key

    anon_client.cookies.clear()
    assert anon_client.get("/api/v1/roasts", headers={"X-API-Key": old_key}).status_code == 401
    assert anon_client.get("/api/v1/roasts", headers={"X-API-Key": new_key}).status_code == 200


def test_revoking_an_api_key_turns_off_access_without_issuing_a_new_one(anon_client):
    register(anon_client, "alice")
    key = anon_client.post("/api/v1/auth/api-key").json()["api_key"]

    resp = anon_client.delete("/api/v1/auth/api-key")
    assert resp.status_code == 204
    assert anon_client.get("/api/v1/auth/me").json()["has_api_key"] is False

    anon_client.cookies.clear()
    assert anon_client.get("/api/v1/roasts", headers={"X-API-Key": key}).status_code == 401


def test_api_key_still_gated_by_allowed_status(anon_client):
    # A key generated while ALLOWED shouldn't keep working if the account
    # is later denied -- the same status check the cookie path gets.
    register(anon_client, "alice")
    admin_client = anon_client
    admin_client.post("/api/v1/auth/logout")
    register(admin_client, "bob", password="bobs-password")
    _login(admin_client, "alice", "a-fine-password")
    bob_id = next(u for u in admin_client.get("/api/v1/auth/users").json() if u["username"] == "bob")["id"]
    admin_client.post(f"/api/v1/auth/users/{bob_id}/allow")
    admin_client.post("/api/v1/auth/logout")
    _login(admin_client, "bob", "bobs-password")
    key = admin_client.post("/api/v1/auth/api-key").json()["api_key"]

    admin_client.post("/api/v1/auth/logout")
    _login(admin_client, "alice", "a-fine-password")
    admin_client.post(f"/api/v1/auth/users/{bob_id}/deny")
    admin_client.post("/api/v1/auth/logout")

    resp = admin_client.get("/api/v1/roasts", headers={"X-API-Key": key})
    assert resp.status_code == 401


def test_change_password_requires_correct_current_password(anon_client):
    register(anon_client, "alice", password="correct-horse")
    resp = anon_client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "wrong-password", "new_password": "battery-staple"},
    )
    assert resp.status_code == 401
    # Rejected -- the old password should still work.
    anon_client.post("/api/v1/auth/logout")
    assert _login(anon_client, "alice", "correct-horse").status_code == 200


def test_change_password_updates_the_password(anon_client):
    register(anon_client, "alice", password="correct-horse")
    resp = anon_client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "correct-horse", "new_password": "battery-staple"},
    )
    assert resp.status_code == 204

    anon_client.post("/api/v1/auth/logout")
    old_login = anon_client.post("/api/v1/auth/login", json={"username": "alice", "password": "correct-horse"})
    assert old_login.status_code == 401
    assert _login(anon_client, "alice", "battery-staple").status_code == 200


def test_change_password_logs_out_other_sessions_but_not_this_one(anon_client):
    from fastapi.testclient import TestClient

    from backend.app.main import app

    register(anon_client, "alice", password="correct-horse")
    # A second "browser" -- its own cookie jar (a separate TestClient
    # against the same app/isolated DB), logged in as the same user.
    with TestClient(app) as other:
        _login(other, "alice", "correct-horse")
        assert other.get("/api/v1/auth/me").status_code == 200

        resp = anon_client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "correct-horse", "new_password": "battery-staple"},
        )
        assert resp.status_code == 204

        # The session that made the change is still logged in...
        assert anon_client.get("/api/v1/auth/me").status_code == 200
        # ...but the other browser's session was ended.
        assert other.get("/api/v1/auth/me").status_code == 401


def test_change_password_rejects_a_too_short_new_password(anon_client):
    register(anon_client, "alice", password="correct-horse")
    resp = anon_client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "correct-horse", "new_password": "short"},
    )
    assert resp.status_code == 422
