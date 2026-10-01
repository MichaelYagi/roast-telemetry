"""Accounts, login/logout, and the admin-only user-management endpoints.

Every other route in the app is gated by main.py's require_login
middleware -- once logged in as ALLOWED (admin or plain user), every
existing feature is available to everyone equally. The only thing
that's admin-only is this file's own /users* endpoints: viewing the
list of accounts and setting Allow/Deny/Delete on them.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response

from .. import auth, storage
from ..models import (
    ApiKeyCreateRequest,
    ApiKeyIssued,
    ApiKeyPublic,
    ChangePasswordRequest,
    LoginRequest,
    RegisterRequest,
    UserPublic,
    UserStatus,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _public(user: dict) -> UserPublic:
    return UserPublic(
        **{k: v for k, v in user.items() if k not in ("password_hash", "api_key_hash")},
    )


@router.get("/status")
def auth_status() -> dict:
    """Public (see main.py's _PUBLIC_API_PATHS) -- just enough for the
    login screen to know whether registering here will become the admin
    or land as a plain pending request, before the user has typed
    anything. Deliberately leaks nothing else (no usernames, no count)."""
    return {"has_admin": storage.count_users() > 0}


@router.post("/register", response_model=UserPublic, status_code=201)
def register(payload: RegisterRequest, request: Request, response: Response) -> UserPublic:
    username = payload.username.strip()
    if not username:
        raise HTTPException(400, "Username is required")
    user = {
        "id": str(uuid.uuid4()),
        "username": username,
        "password_hash": auth.hash_password(payload.password),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    # Checking-then-inserting as two separate calls let two concurrent
    # registrations both see "no admin yet" and both become admin, or
    # both pass a uniqueness pre-check for the same username before
    # either committed -- see storage.insert_user_and_check_first's own
    # docstring for the full race and why this has to be one atomic call.
    try:
        is_first = storage.insert_user_and_check_first(user)
    except storage.DuplicateUsernameError:
        raise HTTPException(409, "That username is already taken")
    user["role"] = "admin" if is_first else "user"
    user["status"] = UserStatus.ALLOWED.value if is_first else UserStatus.PENDING.value
    if is_first:
        # The very first account is auto-approved and logged straight in --
        # there's no admin yet to click Allow, so requiring approval here
        # would permanently lock the app.
        auth.start_session(response, user["id"])
        auth.log_sign_in_event(request, "login", user["username"], "password")
    return _public(user)


@router.post("/login", response_model=UserPublic)
def login(payload: LoginRequest, request: Request, response: Response) -> UserPublic:
    user = storage.get_user_by_username(payload.username.strip())
    if user is None or not auth.verify_password(payload.password, user["password_hash"]):
        raise HTTPException(401, "Incorrect username or password")
    if user["status"] == UserStatus.PENDING.value:
        raise HTTPException(403, "Your account is awaiting admin approval")
    if user["status"] == UserStatus.DENIED.value:
        raise HTTPException(403, "Your account has been denied access")
    auth.start_session(response, user["id"], remember_me=payload.remember_me)
    auth.log_sign_in_event(request, "login", user["username"], "password")
    return _public(user)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    # Deliberately outside the require_login gate (see main.py's
    # _PUBLIC_API_PATHS) -- clearing a stale/invalid cookie has to work
    # even when it no longer maps to a real session. Only a session that
    # still belonged to someone gets an activity-log entry -- there's no
    # username to attribute a stale cookie's logout to.
    user = auth.get_user_for_token(request.cookies.get(auth.SESSION_COOKIE))
    auth.end_session(request, response)
    if user is not None:
        auth.log_sign_in_event(request, "logout", user["username"], "password")


# API-key clients (the mobile app) have no session to start or end -- the
# key goes on every request -- so these exist purely so that "connected"/
# "disconnected" shows up in the activity log alongside browser logins.
# Called once when the user connects/disconnects, not on every app launch.
@router.post("/connect", status_code=204)
def connect(request: Request) -> None:
    auth.log_sign_in_event(request, "login", request.state.user["username"], request.state.auth_method)


@router.post("/disconnect", status_code=204)
def disconnect(request: Request) -> None:
    auth.log_sign_in_event(request, "logout", request.state.user["username"], request.state.auth_method)


@router.get("/me", response_model=UserPublic)
def me(request: Request) -> UserPublic:
    # request.state.user is already guaranteed set here -- /auth/me isn't
    # in the gate's public-path exemption list, so the middleware has
    # already resolved and attached it (or this route would never run).
    return _public(request.state.user)


@router.post("/change-password", status_code=204)
def change_password(payload: ChangePasswordRequest, request: Request) -> None:
    """Self-service password change (Account -> Change password). Requires
    the current password, not just a valid session. Every *other* active
    session for this account is ended on success (see
    storage.delete_other_sessions_for_user) -- an old password shouldn't
    keep working anywhere else, but the browser that just supplied it
    correctly stays signed in rather than being logged out too."""
    user = request.state.user
    if not auth.verify_password(payload.current_password, user["password_hash"]):
        raise HTTPException(401, "Current password is incorrect")
    storage.set_user_password_hash(user["id"], auth.hash_password(payload.new_password))
    token = request.cookies.get(auth.SESSION_COOKIE)
    if token:
        storage.delete_other_sessions_for_user(user["id"], token)


@router.get("/api-keys", response_model=list[ApiKeyPublic])
def list_api_keys(request: Request) -> list[dict]:
    return storage.list_api_keys(request.state.user["id"])


@router.post("/api-keys", response_model=ApiKeyIssued)
def create_api_key(payload: ApiKeyCreateRequest, request: Request) -> ApiKeyIssued:
    """One of possibly several named keys per account -- e.g. a phone and
    a script can each have their own, so revoking one never breaks the
    other. The plaintext key is returned exactly once, right here -- only
    its hash is ever stored (see auth.hash_api_key), so even this app
    itself can't show it again after this response."""
    user = request.state.user
    key = auth.generate_api_key()
    row = storage.create_api_key(user["id"], payload.name.strip() or "Unnamed key", auth.hash_api_key(key))
    return ApiKeyIssued(id=row["id"], name=row["name"], api_key=key)


@router.delete("/api-keys/{key_id}", status_code=204)
def delete_api_key(key_id: str, request: Request) -> None:
    user = request.state.user
    if not storage.delete_api_key(user["id"], key_id):
        raise HTTPException(404, "No such API key")


@router.get("/users", response_model=list[UserPublic])
def list_users(request: Request) -> list[UserPublic]:
    auth.require_admin(request)
    return [_public(u) for u in storage.list_users()]


def _get_target_or_404(user_id: str) -> dict:
    user = storage.get_user_by_id(user_id)
    if user is None:
        raise HTTPException(404, "No such user")
    return user


@router.post("/users/{user_id}/allow", response_model=UserPublic)
def allow_user(user_id: str, request: Request) -> UserPublic:
    auth.require_admin(request)
    user = _get_target_or_404(user_id)
    storage.set_user_status(user_id, UserStatus.ALLOWED.value)
    user["status"] = UserStatus.ALLOWED.value
    return _public(user)


@router.post("/users/{user_id}/deny", response_model=UserPublic)
def deny_user(user_id: str, request: Request) -> UserPublic:
    admin = auth.require_admin(request)
    if user_id == admin["id"]:
        raise HTTPException(400, "Can't deny your own admin account")
    user = _get_target_or_404(user_id)
    storage.set_user_status(user_id, UserStatus.DENIED.value)
    # Takes effect immediately, not just on their next login attempt -- an
    # already-logged-in user who gets denied is logged out right now.
    storage.delete_sessions_for_user(user_id)
    user["status"] = UserStatus.DENIED.value
    return _public(user)


@router.post("/users/{user_id}/reset-to-pending", response_model=UserPublic)
def reset_user_to_pending(user_id: str, request: Request) -> UserPublic:
    """The only way back from DENIED -- an admin reconsidering a denial
    sends the account back through the normal approval step (Allow)
    rather than being allowed directly from here."""
    auth.require_admin(request)
    user = _get_target_or_404(user_id)
    storage.set_user_status(user_id, UserStatus.PENDING.value)
    user["status"] = UserStatus.PENDING.value
    return _public(user)


@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: str, request: Request) -> None:
    admin = auth.require_admin(request)
    if user_id == admin["id"]:
        raise HTTPException(400, "Can't delete your own admin account")
    _get_target_or_404(user_id)
    storage.delete_user(user_id)
