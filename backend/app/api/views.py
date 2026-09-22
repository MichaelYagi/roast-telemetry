"""Saved comparisons and analyses -- a page's own settings, kept under a name."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response

from .. import storage
from ..models import SavedView, SavedViewCreate

router = APIRouter(prefix="/views", tags=["views"])


def _to_view(row: dict) -> SavedView:
    return SavedView(
        id=row["id"], kind=row["kind"], name=row["name"], config=json.loads(row["config_json"]),
        created_at=row["created_at"], created_by_username=row.get("created_by_username"),
    )


@router.get("", response_model=list[SavedView])
def list_views(kind: Optional[str] = None) -> list[SavedView]:
    return [_to_view(r) for r in storage.list_saved_views(kind)]


@router.post("", response_model=SavedView, status_code=201)
def create_view(view: SavedViewCreate, request: Request, response: Response) -> SavedView:
    """Saves a view under a name. Saving under a name that already exists (for
    the same page, ignoring case) updates that view instead of adding a second."""
    existing = storage.find_saved_view(view.kind, view.name)
    if existing is not None:
        storage.update_saved_view(existing["id"], json.dumps(view.config))
        response.status_code = 200
        return _to_view({**existing, "config_json": json.dumps(view.config)})
    user = getattr(request.state, "user", None)
    record = {
        "id": str(uuid.uuid4()), "kind": view.kind, "name": view.name.strip(),
        "config_json": json.dumps(view.config), "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by_username": user["username"] if user else None,
    }
    storage.insert_saved_view(record)
    return _to_view(record)


@router.delete("/{view_id}", status_code=204)
def delete_view(view_id: str) -> None:
    if not storage.delete_saved_view(view_id):
        raise HTTPException(status_code=404, detail="saved view not found")
