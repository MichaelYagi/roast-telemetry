"""Read-only listing of installed device plugins -- see device_plugins/README.md.
Populates the New Roast form's "Plugin device" option, which only appears at
all once this returns something (nothing installed means nothing to pick)."""
from __future__ import annotations

import device_plugins
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/device-plugins", tags=["device-plugins"])


class DevicePluginInfo(BaseModel):
    kind: str
    label: str
    needs_port: bool
    read_only: bool
    port_hint: str


@router.get("", response_model=list[DevicePluginInfo])
def list_device_plugins() -> list[DevicePluginInfo]:
    return [
        DevicePluginInfo(kind=s.kind, label=s.label, needs_port=s.needs_port, read_only=s.read_only, port_hint=s.port_hint)
        for s in device_plugins.list_plugins()
    ]
