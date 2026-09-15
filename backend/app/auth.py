"""Session-cookie auth -- password hashing (stdlib PBKDF2, no extra
dependency needed) and the opaque-token session cookie that
`main.py`'s require_login middleware demands on every non-public
`/api/*` route.

Not a JWT: the cookie carries nothing but a random 32-byte token,
looked up against the `sessions` table on every request (see
storage.get_session_user). That's what makes an admin's Delete button
actually take effect immediately -- deleting the row revokes the
session right then, rather than waiting for a signed token to expire
on its own.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from fastapi import HTTPException, Request, Response

from . import storage

SESSION_COOKIE = "rt_session"
_PBKDF2_ITERATIONS = 260_000  # in line with OWASP's current PBKDF2-SHA256 guidance
# "Remember me" checked -- 400 days, the longest Max-Age Chrome will honor
# without silently capping it itself; as close to "until you sign out" as
# a cookie attribute can actually promise.
_REMEMBER_ME_MAX_AGE_S = 60 * 60 * 24 * 400


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, hex_digest = stored.split("$", 1)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS)
    return secrets.compare_digest(digest.hex(), hex_digest)


def start_session(response: Response, user_id: str, remember_me: bool = True) -> None:
    """remember_me=True (the default -- used for the auto-login on the
    very first, admin-creating registration, where there's no login form
    to have asked the question) sets a long-lived cookie that survives
    closing the browser. False (an explicit login with "Remember me"
    left unchecked) sets a plain session cookie instead -- no Max-Age/
    Expires attribute at all, so the browser itself drops it as soon as
    it closes; storage.get_session_user would otherwise still honor a
    stale row for a token no browser has anymore, but there's nothing
    left to send it with."""
    token = secrets.token_urlsafe(32)
    storage.insert_session(token, user_id, datetime.now(timezone.utc).isoformat())
    max_age = _REMEMBER_ME_MAX_AGE_S if remember_me else None
    # httponly -- JS never touches this, so it isn't readable by an XSS
    # payload; no `secure` flag since this app is plain http on a LAN (see
    # the getting-started LAN-exposure instructions) -- requiring https
    # here would just silently break the cookie on every real deployment.
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", path="/", max_age=max_age)


def end_session(request: Request, response: Response) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        storage.delete_session(token)
    response.delete_cookie(SESSION_COOKIE, path="/")


def get_user_for_token(token: str | None) -> dict | None:
    return storage.get_session_user(token) if token else None


def require_admin(request: Request) -> dict:
    """request.state.user is already guaranteed present (and ALLOWED) by
    the require_login gate for any route this is called from -- those
    never reach here otherwise -- so this only has to narrow it to admin."""
    user = request.state.user
    if user["role"] != "admin":
        raise HTTPException(403, "Admin access required")
    return user
