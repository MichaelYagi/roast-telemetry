"""Who did what, when -- roast deletes/edits, safety-critical control
events (Emergency Stop, fail-safe trips, automation start/stop/fire), and
logins/logouts with the platform they came from (auth.log_sign_in_event). See
storage.py's activity_log table and log_activity() for how rows get written;
this module only reads and exports them."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Response

from .. import storage
from ..models import ActivityLogEntry

router = APIRouter(prefix="/activity", tags=["activity"])

_COLUMNS = ["created_at", "category", "action", "username", "platform", "roast_id", "roast_title", "message", "detail_json"]


def _to_entry(row: dict) -> ActivityLogEntry:
    return ActivityLogEntry(
        id=row["id"], created_at=row["created_at"], category=row["category"], action=row["action"],
        username=row.get("username"), platform=row.get("platform"), roast_id=row.get("roast_id"), roast_title=row.get("roast_title"),
        message=row["message"], detail=json.loads(row["detail_json"]) if row.get("detail_json") else None,
    )


@router.get("", response_model=list[ActivityLogEntry])
def list_activity(
    category: Optional[str] = None, action: Optional[str] = None, roast_id: Optional[str] = None,
    q: Optional[str] = None, limit: int = 100, offset: int = 0,
) -> list[ActivityLogEntry]:
    return [_to_entry(r) for r in storage.list_activity(category, action, roast_id, q, limit, offset)]


@router.get("/count")
def count_activity(
    category: Optional[str] = None, action: Optional[str] = None, roast_id: Optional[str] = None, q: Optional[str] = None
) -> dict:
    return {"total": storage.count_activity(category, action, roast_id, q)}


def _export_filename(ext: str) -> str:
    return f"activity-log_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.{ext}"


@router.get("/export.csv")
def export_csv(
    category: Optional[str] = None, action: Optional[str] = None, roast_id: Optional[str] = None, q: Optional[str] = None
) -> Response:
    """The whole filtered set, not just one page -- no limit/offset, same as
    the roast bulk-zip export. Implicitly capped by activity_log's own
    retention trim (storage.ACTIVITY_LOG_MAX_ROWS), so this is a snapshot of
    currently retained history, not an unbounded archive."""
    rows = storage.list_activity(category, action, roast_id, q, limit=storage.ACTIVITY_LOG_MAX_ROWS, offset=0)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_COLUMNS)
    for r in rows:
        writer.writerow([r.get(c, "") or "" for c in _COLUMNS])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{_export_filename("csv")}"'},
    )


@router.get("/export.json")
def export_json(
    category: Optional[str] = None, action: Optional[str] = None, roast_id: Optional[str] = None, q: Optional[str] = None
) -> Response:
    rows = storage.list_activity(category, action, roast_id, q, limit=storage.ACTIVITY_LOG_MAX_ROWS, offset=0)
    entries = [_to_entry(r).model_dump() for r in rows]
    return Response(
        content=json.dumps(entries, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{_export_filename("json")}"'},
    )
