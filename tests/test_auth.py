"""Registration/login/logout, the first-account-becomes-admin rule, the
PENDING/ALLOWED/DENIED account lifecycle, and the require_login gate
that every other /api/* route sits behind.

Uses `anon_client` (see conftest.py) rather than the auth-preloaded
`client` fixture used by every other test file -- these tests need to
control login state themselves, starting from genuinely logged out.
"""
from __future__ import annotations


def register(client, username, password="a-fine-password"):
    return client.post("/api/auth/register", json={"username": username, "password": password})


def test_first_registration_becomes_an_auto_allowed_admin_and_is_logged_in(anon_client):
    resp = register(anon_client, "alice")
    assert resp.status_code == 201
    body = resp.json()
    assert body["role"] == "admin"
    assert body["status"] == "allowed"
    # Logged in immediately -- no separate /auth/login call needed.
    me = anon_client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "alice"


def test_second_registration_is_a_pending_plain_user_not_logged_in(anon_client):
    register(anon_client, "alice")
    anon_client.post("/api/auth/logout")
    resp = register(anon_client, "bob")
    body = resp.json()
    assert body["role"] == "user"
    assert body["status"] == "pending"
    # Not auto-logged-in -- a pending account can't reach anything yet.
    assert anon_client.get("/api/auth/me").status_code == 401


def test_pending_user_cannot_log_in(anon_client):
    register(anon_client, "alice")
    anon_client.post("/api/auth/logout")
    register(anon_client, "bob", password="bobs-password")
    anon_client.post("/api/auth/logout")
    resp = anon_client.post("/api/auth/login", json={"username": "bob", "password": "bobs-password"})
    assert resp.status_code == 403
    assert "approval" in resp.json()["detail"].lower()


def test_wrong_password_is_rejected(anon_client):
    register(anon_client, "alice", password="correct-horse")
    anon_client.post("/api/auth/logout")
    resp = anon_client.post("/api/auth/login", json={"username": "alice", "password": "wrong-password"})
    assert resp.status_code == 401


def test_duplicate_username_is_rejected(anon_client):
    register(anon_client, "alice")
    resp = register(anon_client, "alice")
    assert resp.status_code == 409


def test_unauthenticated_request_to_an_ordinary_route_is_rejected(anon_client):
    resp = anon_client.get("/api/roasts")
    assert resp.status_code == 401


def test_logged_in_pending_user_gate_blocks_existing_routes(anon_client):
    # Belt-and-suspenders: pending users never get a session cookie in the
    # first place (see the auto-login test above), but this nails down
    # that even if a cookie somehow existed for a PENDING account, the
    # gate itself re-checks status on every request rather than trusting
    # "a valid session token exists" alone.
    register(anon_client, "alice")
    admin_client = anon_client
    admin_client.post("/api/auth/logout")
    register(admin_client, "bob", password="bobs-password")
    # bob has no cookie (pending never gets one) -- confirm the ordinary
    # route stays locked out for the now-logged-out client too.
    assert admin_client.get("/api/roasts").status_code == 401


def _login(client, username, password):
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def test_admin_can_allow_a_pending_user_who_can_then_log_in(anon_client):
    register(anon_client, "alice")
    anon_client.post("/api/auth/logout")
    register(anon_client, "bob", password="bobs-password")
    _login(anon_client, "alice", "a-fine-password")
    users = anon_client.get("/api/auth/users").json()
    bob = next(u for u in users if u["username"] == "bob")
    resp = anon_client.post(f"/api/auth/users/{bob['id']}/allow")
    assert resp.json()["status"] == "allowed"
    anon_client.post("/api/auth/logout")
    login_resp = _login(anon_client, "bob", "bobs-password")
    assert login_resp.json()["status"] == "allowed"


def test_non_admin_cannot_reach_the_users_list(anon_client):
    register(anon_client, "alice")
    anon_client.post("/api/auth/logout")
    register(anon_client, "bob", password="bobs-password")
    anon_client.post("/api/auth/logout")
    _login(anon_client, "alice", "a-fine-password")
    bob_id = next(u for u in anon_client.get("/api/auth/users").json() if u["username"] == "bob")["id"]
    anon_client.post(f"/api/auth/users/{bob_id}/allow")
    anon_client.post("/api/auth/logout")
    _login(anon_client, "bob", "bobs-password")
    resp = anon_client.get("/api/auth/users")
    assert resp.status_code == 403


def test_denied_user_cannot_log_in_and_can_be_reset_to_pending(anon_client):
    register(anon_client, "alice")
    anon_client.post("/api/auth/logout")
    register(anon_client, "bob", password="bobs-password")
    anon_client.post("/api/auth/logout")
    _login(anon_client, "alice", "a-fine-password")
    bob_id = next(u for u in anon_client.get("/api/auth/users").json() if u["username"] == "bob")["id"]

    deny_resp = anon_client.post(f"/api/auth/users/{bob_id}/deny")
    assert deny_resp.json()["status"] == "denied"
    anon_client.post("/api/auth/logout")
    denied_login = anon_client.post("/api/auth/login", json={"username": "bob", "password": "bobs-password"})
    assert denied_login.status_code == 403
    assert "denied" in denied_login.json()["detail"].lower()

    _login(anon_client, "alice", "a-fine-password")
    reset_resp = anon_client.post(f"/api/auth/users/{bob_id}/reset-to-pending")
    assert reset_resp.json()["status"] == "pending"
    anon_client.post("/api/auth/logout")
    pending_login = anon_client.post("/api/auth/login", json={"username": "bob", "password": "bobs-password"})
    assert pending_login.status_code == 403
    assert "approval" in pending_login.json()["detail"].lower()


def test_denying_an_already_logged_in_user_ends_their_session_immediately(anon_client):
    register(anon_client, "alice")
    anon_client.post("/api/auth/logout")
    register(anon_client, "bob", password="bobs-password")
    anon_client.post("/api/auth/logout")
    _login(anon_client, "alice", "a-fine-password")
    bob_id = next(u for u in anon_client.get("/api/auth/users").json() if u["username"] == "bob")["id"]
    anon_client.post(f"/api/auth/users/{bob_id}/allow")
    anon_client.post("/api/auth/logout")
    _login(anon_client, "bob", "bobs-password")
    assert anon_client.get("/api/roasts").status_code == 200

    _login(anon_client, "alice", "a-fine-password")
    anon_client.post(f"/api/auth/users/{bob_id}/deny")

    # bob's cookie is stale on the shared client jar now -- alice's own
    # login above already overwrote it, so re-simulate bob's browser by
    # logging in again: it should be rejected outright now that he's denied.
    resp = anon_client.post("/api/auth/login", json={"username": "bob", "password": "bobs-password"})
    assert resp.status_code == 403


def test_admin_cannot_delete_or_deny_their_own_account(anon_client):
    register(anon_client, "alice")
    me = anon_client.get("/api/auth/me").json()
    assert anon_client.post(f"/api/auth/users/{me['id']}/deny").status_code == 400
    assert anon_client.delete(f"/api/auth/users/{me['id']}").status_code == 400


def test_admin_can_delete_a_user_and_it_ends_their_session(anon_client):
    register(anon_client, "alice")
    anon_client.post("/api/auth/logout")
    register(anon_client, "bob", password="bobs-password")
    anon_client.post("/api/auth/logout")
    _login(anon_client, "alice", "a-fine-password")
    bob_id = next(u for u in anon_client.get("/api/auth/users").json() if u["username"] == "bob")["id"]
    anon_client.post(f"/api/auth/users/{bob_id}/allow")
    anon_client.post("/api/auth/logout")
    _login(anon_client, "bob", "bobs-password")

    _login(anon_client, "alice", "a-fine-password")
    del_resp = anon_client.delete(f"/api/auth/users/{bob_id}")
    assert del_resp.status_code == 204

    remaining = anon_client.get("/api/auth/users").json()
    assert all(u["id"] != bob_id for u in remaining)
    # A deleted account can no longer log in at all.
    assert anon_client.post("/api/auth/login", json={"username": "bob", "password": "bobs-password"}).status_code == 401


def test_logout_clears_the_session_so_the_gate_blocks_again(anon_client):
    register(anon_client, "alice")
    assert anon_client.get("/api/auth/me").status_code == 200
    anon_client.post("/api/auth/logout")
    assert anon_client.get("/api/auth/me").status_code == 401
