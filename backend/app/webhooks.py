"""Outbound notifications on roast events -- a plain HTTP POST to whatever
URL you give it (ntfy, a Discord/Slack incoming webhook, Home Assistant, a
script of your own). See backend/app/api/webhooks.py for the settings CRUD
and the "send a test" endpoint; the Settings page's Webhooks panel is the
usual way to manage these.
"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx

from . import storage

logger = logging.getLogger(__name__)

TIMEOUT_S = 10.0

# event key -> label, shown in the Settings page's per-webhook event picker.
# The milestone keys match RoastEventType's own values exactly (see
# models.py) so a milestone fires its webhook event under the same name it
# already has everywhere else in this app; "roast_finished" and "e_stop"
# aren't milestones, so they're spelled differently on purpose -- nothing
# to collide with.
EVENTS: dict[str, str] = {
    "CHARGE": "Charge",
    "TURNING_POINT": "Turning Point",
    "DRY_END": "Dry End",
    "FC_START": "First Crack Start",
    "FC_END": "First Crack End",
    "SC_START": "Second Crack Start",
    "SC_END": "Second Crack End",
    "DROP": "Drop",
    "COOL_END": "Cool End",
    "roast_finished": "Roast finished (complete, stopped, or aborted)",
    "e_stop": "Emergency stop / safety trip",
}


def _enabled_webhooks_for(event: str) -> list[dict]:
    """A webhook with an empty events_json list is subscribed to every
    event -- the "send me everything" default a first-time user would
    expect, not silence until they go pick events one by one."""
    rows = []
    for row in storage.list_webhook_rows():
        if not row["enabled"]:
            continue
        events = json.loads(row["events_json"]) if row["events_json"] else []
        if not events or event in events:
            rows.append(row)
    return rows


async def _post(client: httpx.AsyncClient, row: dict, payload: dict) -> None:
    try:
        resp = await client.post(row["url"], json=payload)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 -- a broken/slow/offline target must never affect the roast itself
        logger.warning("webhook %r (%s) failed for event %s: %s", row["name"], row["url"], payload.get("event"), exc)
        storage.log_activity(
            "webhook", "failed", platform=storage.AUTOMATIC_PLATFORM,
            roast_id=payload.get("roast_id"), roast_title=payload.get("roast_title"),
            message=f"Webhook \"{row['name']}\" failed on {payload.get('event')}: {exc}",
        )


async def fire(event: str, roast: dict) -> None:
    """Posts `roast` (see the call sites in roast_session/ for the exact
    shape -- always at least event/roast_id/roast_title/mode) as JSON to
    every enabled webhook subscribed to `event`. Every target is sent to
    concurrently and independently -- one slow or unreachable target never
    delays or blocks another."""
    rows = _enabled_webhooks_for(event)
    if not rows:
        return
    payload = {"event": event, **roast}
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        await asyncio.gather(*(_post(client, row, payload) for row in rows))


def fire_background(event: str, roast: dict) -> None:
    """Schedules fire() without making the caller (a roast's tick loop, or
    the safety/control path) wait on someone else's server. Silently does
    nothing outside a running event loop (e.g. a plain script/test context)
    -- there's nothing to schedule onto."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(fire(event, roast))
    # Nothing awaits this task (that's the whole point), so an exception in
    # fire() itself (not caught by _post's own try/except -- a bug, not a
    # network failure) would otherwise vanish as an "unhandled task
    # exception" asyncio warning with no real diagnostic. Logged instead.
    task.add_done_callback(lambda t: t.exception() and logger.exception("webhook dispatch crashed", exc_info=t.exception()))


async def send_test(url: str) -> dict:
    """Used by the Settings page's "Send test" button -- posts a single
    fixed payload straight to `url`, bypassing the webhooks table entirely
    (so it works before a webhook is even saved). Returns
    {"ok": bool, "status": int|None, "error": str|None}."""
    payload = {"event": "test", "message": "Test notification from Roast Telemetry"}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            resp = await client.post(url, json=payload)
        return {"ok": resp.is_success, "status": resp.status_code, "error": None if resp.is_success else resp.text[:300]}
    except Exception as exc:  # noqa: BLE001 -- reachability probe, same reasoning as ollama_client.check_connection
        return {"ok": False, "status": None, "error": str(exc)}
