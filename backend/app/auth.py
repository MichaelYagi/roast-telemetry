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


# API keys (X-API-Key header, see main.py's require_login) -- a second,
# independent credential a user can generate for themselves (Account ->
# API key) to call the API directly (a script, a Home Assistant
# integration, curl) without going through the browser's cookie-based
# login at all. Deliberately NOT hashed with hash_password's PBKDF2 above
# -- that expense exists specifically to slow down brute-forcing a
# low-entropy human password; an API key is already 32 cryptographically
# random bytes (secrets.token_urlsafe), so a plain fast hash is both
# sufficient and the standard choice for this exact case (this is how
# GitHub hashes personal access tokens too).
_API_KEY_PREFIX = "rt_"


def generate_api_key() -> str:
    # rt_ prefix -- recognizable at a glance (which secret is this?) and
    # also sidesteps token_urlsafe occasionally starting with "_" or "-"
    # on its own, which reads oddly as the very first character of a key.
    return _API_KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def require_admin(request: Request) -> dict:
    """request.state.user is already guaranteed present (and ALLOWED) by
    the require_login gate for any route this is called from -- those
    never reach here otherwise -- so this only has to narrow it to admin."""
    user = request.state.user
    if user["role"] != "admin":
        raise HTTPException(403, "Admin access required")
    return user


# -- Sign-in/sign-out activity log entries ------------------------------------

# Sent by non-browser clients that want to name themselves in the activity
# log (the Roast Telemetry mobile app sends e.g. "Roast Telemetry app 1.0.0
# on Android 14") -- a browser's User-Agent is parsed instead. Client-
# supplied either way, so it's a label for the log, never trusted for
# anything else.
CLIENT_PLATFORM_HEADER = "X-Client-Platform"
_MAX_PLATFORM_LEN = 80
_MAX_USER_AGENT_LEN = 300

# First match wins, so the more specific tokens come first: Edge and Opera
# UAs also say "Chrome", Chrome's also says "Safari", Chrome/Firefox on iOS
# say "CriOS"/"FxiOS" instead of their usual names.
_BROWSERS = (
    ("Edg", "Edge"), ("OPR/", "Opera"), ("FxiOS", "Firefox"), ("Firefox/", "Firefox"),
    ("CriOS", "Chrome"), ("SamsungBrowser", "Samsung Internet"), ("Chrome/", "Chrome"),
    ("Safari/", "Safari"), ("curl/", "curl"), ("python-requests", "Python"), ("okhttp", "Android app"),
)
_OSES = (
    ("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Windows", "Windows"),
    ("CrOS", "ChromeOS"), ("Mac OS X", "macOS"), ("Macintosh", "macOS"), ("Linux", "Linux"),
)


def _clean(value: str, limit: int) -> str:
    return "".join(ch for ch in value if ch.isprintable()).strip()[:limit]


def client_platform(request: Request) -> str:
    """A short human label for what's on the other end of this request --
    "Chrome on Windows", "Safari on iPhone", "Roast Telemetry app 1.0.0 on
    Android 14" -- for the activity log's login/logout entries."""
    declared = _clean(request.headers.get(CLIENT_PLATFORM_HEADER, ""), _MAX_PLATFORM_LEN)
    if declared:
        return declared
    ua = request.headers.get("User-Agent", "")
    browser = next((name for token, name in _BROWSERS if token in ua), None)
    os_name = next((name for token, name in _OSES if token in ua), None)
    if browser and os_name:
        return f"{browser} on {os_name}"
    return browser or os_name or "Unknown platform"


def actor(request: Request) -> dict:
    """username + platform for an activity_log row caused by this request --
    `storage.log_activity(..., **auth.actor(request), ...)`. Only for routes
    behind main.py's require_login gate (request.state.user is set there)."""
    return {"username": request.state.user["username"], "platform": client_platform(request)}


def log_sign_in_event(request: Request, action: str, username: str, method: str) -> None:
    """One activity_log row per successful login/logout (category "auth").
    Failed attempts are deliberately not logged: the log only keeps the
    newest storage.ACTIVITY_LOG_MAX_ROWS entries, so a burst of bad
    passwords would push real safety/roast history out of it."""
    platform = client_platform(request)
    storage.log_activity(
        "auth",
        action,
        username=username,
        platform=platform,
        message=f"{'Logged in' if action == 'login' else 'Logged out'} ({platform})",
        detail={
            "method": method,
            "user_agent": _clean(request.headers.get("User-Agent", ""), _MAX_USER_AGENT_LEN) or None,
        },
    )
