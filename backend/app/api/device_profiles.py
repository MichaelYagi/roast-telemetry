"""Saved Modbus register maps (DeviceProfile) -- lets a roast be
configured against a named machine definition instead of the 26 flat
modbus_* override fields (RoastCreateRequest.modbus_device_profile_id).
Mirrors presets.py's shape closely; the one real difference is built_in
rows (the profiles this app ships with -- see
modbus_bridge/device_profiles.py) can't be edited or deleted here."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from .. import storage
from ..models import DeviceProfile, DeviceProfileCreateRequest

router = APIRouter(prefix="/device-profiles", tags=["device-profiles"])


@router.get("", response_model=list[DeviceProfile])
def list_device_profiles() -> list[DeviceProfile]:
    return [DeviceProfile.from_row(r) for r in storage.list_device_profile_rows()]


@router.post("", response_model=DeviceProfile, status_code=201)
def create_device_profile(request: DeviceProfileCreateRequest) -> DeviceProfile:
    profile_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    storage.insert_device_profile({
        "id": profile_id,
        "name": request.name,
        "created_at": created_at,
        "config_json": request.model_dump_json(),
    })
    return DeviceProfile(id=profile_id, created_at=created_at, built_in=False, **request.model_dump())


@router.put("/{profile_id}", response_model=DeviceProfile)
def update_device_profile(profile_id: str, request: DeviceProfileCreateRequest) -> DeviceProfile:
    row = storage.get_device_profile_row(profile_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"device profile {profile_id!r} not found")
    if row["built_in"]:
        raise HTTPException(status_code=403, detail="built-in device profiles can't be edited")
    storage.update_device_profile_row(profile_id, {
        "name": request.name,
        "config_json": request.model_dump_json(),
    })
    return DeviceProfile(id=profile_id, created_at=row["created_at"], built_in=False, **request.model_dump())


@router.get("/{profile_id}", response_model=DeviceProfile)
def get_device_profile(profile_id: str) -> DeviceProfile:
    row = storage.get_device_profile_row(profile_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"device profile {profile_id!r} not found")
    return DeviceProfile.from_row(row)


@router.delete("/{profile_id}", status_code=204)
def delete_device_profile(profile_id: str) -> None:
    row = storage.get_device_profile_row(profile_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"device profile {profile_id!r} not found")
    if row["built_in"]:
        raise HTTPException(status_code=403, detail="built-in device profiles can't be deleted")
    storage.delete_device_profile_row(profile_id)
