"""Supported machine catalog endpoints."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..models import Machine

router = APIRouter(prefix="/machines", tags=["machines"])

_CATALOG_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "machines_catalog.json"
with open(_CATALOG_PATH, "r", encoding="utf-8") as f:
    _CATALOG: list[dict] = json.load(f)
_BY_ID = {m["id"]: m for m in _CATALOG}


@router.get("", response_model=list[Machine])
def list_machines(
    control_capable: Optional[bool] = Query(default=None),
    brand: Optional[str] = Query(default=None),
) -> list[Machine]:
    machines = _CATALOG
    if control_capable is not None:
        machines = [m for m in machines if m["control_capable"] == control_capable]
    if brand:
        machines = [m for m in machines if m["brand"].lower() == brand.lower()]
    return [Machine(**m) for m in machines]


@router.get("/{machine_id}", response_model=Machine)
def get_machine(machine_id: str) -> Machine:
    machine = _BY_ID.get(machine_id)
    if machine is None:
        raise HTTPException(status_code=404, detail=f"machine {machine_id!r} not found")
    return Machine(**machine)


def resolve_machine(machine_id: Optional[str]) -> Optional[dict]:
    if machine_id is None:
        return None
    return _BY_ID.get(machine_id)
