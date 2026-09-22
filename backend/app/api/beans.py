"""Saved beans (green-coffee records). A roast can point at one, which lets
roasts be grouped and analysed by beans, origin, process and density."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from .. import storage
from ..models import Bean, BeanFields

router = APIRouter(prefix="/beans", tags=["beans"])


def _to_bean(row: dict) -> Bean:
    return Bean(**{k: row.get(k) for k in Bean.model_fields})


@router.get("", response_model=list[Bean])
def list_beans() -> list[Bean]:
    return [_to_bean(r) for r in storage.list_beans()]


@router.post("", response_model=Bean, status_code=201)
def create_bean(bean: BeanFields) -> Bean:
    record = {**bean.model_dump(), "id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc).isoformat()}
    storage.insert_bean(record)
    return _to_bean({**record, "roast_count": 0})


@router.put("/{bean_id}", response_model=Bean)
def update_bean(bean_id: str, bean: BeanFields) -> Bean:
    if storage.get_bean(bean_id) is None:
        raise HTTPException(status_code=404, detail="beans not found")
    storage.update_bean(bean_id, bean.model_dump())
    return next(_to_bean(r) for r in storage.list_beans() if r["id"] == bean_id)


@router.delete("/{bean_id}", status_code=204)
def delete_bean(bean_id: str) -> None:
    """Roasts that used it keep their own beans name and lose only the link."""
    if storage.get_bean(bean_id) is None:
        raise HTTPException(status_code=404, detail="beans not found")
    storage.delete_bean(bean_id)
