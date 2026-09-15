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
_COOKIE_MAX_AGE_S = 60 * 60 * 24 * 30  # 30 days -- a LAN roasting-log app, not a bank; long-lived is the right trade


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


def start_session(response: Response, user_id: str) -> None:
    token = secrets.token_urlsafe(32)
    storage.insert_session(token, user_id, datetime.now(timezone.utc).isoformat())
    # httponly -- JS never touches this, so it isn't readable by an XSS
    # payload; no `secure` flag since this app is plain http on a LAN (see
    # the getting-started LAN-exposure instructions) -- requiring https
    # here would just silently break the cookie on every real deployment.
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite="lax", path="/", max_age=_COOKIE_MAX_AGE_S
    )


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
