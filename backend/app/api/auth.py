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
from ..models import LoginRequest, RegisterRequest, UserPublic, UserStatus

router = APIRouter(prefix="/auth", tags=["auth"])


def _public(user: dict) -> UserPublic:
    return UserPublic(**{k: v for k, v in user.items() if k != "password_hash"})


@router.post("/register", response_model=UserPublic, status_code=201)
def register(payload: RegisterRequest, response: Response) -> UserPublic:
    username = payload.username.strip()
    if not username:
        raise HTTPException(400, "Username is required")
    if storage.get_user_by_username(username):
        raise HTTPException(409, "That username is already taken")
    is_first = storage.count_users() == 0
    user = {
        "id": str(uuid.uuid4()),
        "username": username,
        "password_hash": auth.hash_password(payload.password),
        "role": "admin" if is_first else "user",
        "status": UserStatus.ALLOWED.value if is_first else UserStatus.PENDING.value,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    storage.insert_user(user)
    if is_first:
        # The very first account is auto-approved and logged straight in --
        # there's no admin yet to click Allow, so requiring approval here
        # would permanently lock the app.
        auth.start_session(response, user["id"])
    return _public(user)


@router.post("/login", response_model=UserPublic)
def login(payload: LoginRequest, response: Response) -> UserPublic:
    user = storage.get_user_by_username(payload.username.strip())
    if user is None or not auth.verify_password(payload.password, user["password_hash"]):
        raise HTTPException(401, "Incorrect username or password")
    if user["status"] == UserStatus.PENDING.value:
        raise HTTPException(403, "Your account is awaiting admin approval")
    if user["status"] == UserStatus.DENIED.value:
        raise HTTPException(403, "Your account has been denied access")
    auth.start_session(response, user["id"])
    return _public(user)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    # Deliberately outside the require_login gate (see main.py's
    # _PUBLIC_API_PATHS) -- clearing a stale/invalid cookie has to work
    # even when it no longer maps to a real session.
    auth.end_session(request, response)


@router.get("/me", response_model=UserPublic)
def me(request: Request) -> UserPublic:
    # request.state.user is already guaranteed set here -- /auth/me isn't
    # in the gate's public-path exemption list, so the middleware has
    # already resolved and attached it (or this route would never run).
    return _public(request.state.user)


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
