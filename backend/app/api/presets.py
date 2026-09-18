"""Saved roast configurations -- lets the "Configure Roast" form be
filled in once and reused, instead of re-entering mode/host/port/
threshold fields every time (especially painful for the direct-hardware
modes, which have several fields with no sensible universal default)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from .. import storage
from ..models import RoastCreateRequest, RoastPreset, RoastPresetCreateRequest

router = APIRouter(prefix="/presets", tags=["presets"])


def _row_to_preset(row: dict) -> RoastPreset:
    return RoastPreset(
        id=row["id"],
        name=row["name"],
        created_at=row["created_at"],
        config=RoastCreateRequest.model_validate_json(row["config_json"]),
        heater_pct=row.get("heater_pct"),
        fan_pct=row.get("fan_pct"),
        drum_speed_pct=row.get("drum_speed_pct"),
        manufacturer=row.get("manufacturer"),
        built_in=bool(row.get("built_in")),
    )


@router.get("", response_model=list[RoastPreset])
def list_presets() -> list[RoastPreset]:
    return [_row_to_preset(r) for r in storage.list_preset_rows()]


@router.post("", response_model=RoastPreset, status_code=201)
def create_preset(request: RoastPresetCreateRequest) -> RoastPreset:
    preset_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    storage.insert_preset({
        "id": preset_id,
        "name": request.name,
        "created_at": created_at,
        "config_json": request.config.model_dump_json(),
        "heater_pct": request.heater_pct,
        "fan_pct": request.fan_pct,
        "drum_speed_pct": request.drum_speed_pct,
        "manufacturer": request.manufacturer,
    })
    return RoastPreset(
        id=preset_id, name=request.name, created_at=created_at, config=request.config,
        heater_pct=request.heater_pct, fan_pct=request.fan_pct, drum_speed_pct=request.drum_speed_pct,
        manufacturer=request.manufacturer, built_in=False,
    )


@router.put("/{preset_id}", response_model=RoastPreset)
def update_preset(preset_id: str, request: RoastPresetCreateRequest) -> RoastPreset:
    row = storage.get_preset_row(preset_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"preset {preset_id!r} not found")
    if row["built_in"]:
        raise HTTPException(status_code=403, detail="built-in presets can't be edited")
    storage.update_preset_row(preset_id, {
        "name": request.name,
        "config_json": request.config.model_dump_json(),
        "heater_pct": request.heater_pct,
        "fan_pct": request.fan_pct,
        "drum_speed_pct": request.drum_speed_pct,
        "manufacturer": request.manufacturer,
    })
    return RoastPreset(
        id=preset_id, name=request.name, created_at=row["created_at"], config=request.config,
        heater_pct=request.heater_pct, fan_pct=request.fan_pct, drum_speed_pct=request.drum_speed_pct,
        manufacturer=request.manufacturer, built_in=False,
    )


@router.get("/{preset_id}", response_model=RoastPreset)
def get_preset(preset_id: str) -> RoastPreset:
    row = storage.get_preset_row(preset_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"preset {preset_id!r} not found")
    return _row_to_preset(row)


@router.delete("/{preset_id}", status_code=204)
def delete_preset(preset_id: str) -> None:
    row = storage.get_preset_row(preset_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"preset {preset_id!r} not found")
    if row["built_in"]:
        raise HTTPException(status_code=403, detail="built-in presets can't be deleted")
    storage.delete_preset_row(preset_id)
