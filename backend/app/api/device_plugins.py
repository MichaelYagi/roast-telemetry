"""Listing + per-kind enable/disable for installed device plugins -- see
device_plugins/README.md. The New Roast form's "Plugin device" option only
appears at all once this returns something enabled (nothing installed, or
everything disabled, means nothing to pick).

A plugin whose own PluginSpec.enabled is False (the author's own code-level
switch, for one still being built) is excluded here entirely -- not listed,
not manageable through PUT/DELETE below either, same as if it didn't exist
from this API's point of view. That's deliberately different from, and
takes priority over, the enabled field below: *that* one is Settings' own
per-kind runtime checkbox, which only ever applies to a plugin the author
shipped as enabled=True."""
from __future__ import annotations

import device_plugins
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import storage

router = APIRouter(prefix="/device-plugins", tags=["device-plugins"])


class DevicePluginInfo(BaseModel):
    kind: str
    label: str
    needs_port: bool
    read_only: bool
    port_hint: str
    enabled: bool


def _visible_spec(kind: str):
    """device_plugins.get(kind), but treats a code-disabled plugin
    (PluginSpec.enabled=False) as not found -- see this module's own
    docstring for why."""
    spec = device_plugins.get(kind)
    return spec if spec is not None and spec.enabled else None


@router.get("", response_model=list[DevicePluginInfo])
def list_device_plugins() -> list[DevicePluginInfo]:
    disabled = set(storage.get_disabled_plugin_kinds())
    return [
        DevicePluginInfo(
            kind=s.kind, label=s.label, needs_port=s.needs_port, read_only=s.read_only,
            port_hint=s.port_hint, enabled=s.kind not in disabled,
        )
        for s in device_plugins.list_plugins() if s.enabled
    ]


class SetPluginEnabledRequest(BaseModel):
    enabled: bool


@router.put("/{kind}", response_model=DevicePluginInfo)
def set_plugin_enabled(kind: str, request: SetPluginEnabledRequest) -> DevicePluginInfo:
    spec = _visible_spec(kind)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"device plugin {kind!r} isn't installed")
    storage.set_plugin_enabled(kind, request.enabled)
    return DevicePluginInfo(
        kind=spec.kind, label=spec.label, needs_port=spec.needs_port, read_only=spec.read_only,
        port_hint=spec.port_hint, enabled=request.enabled,
    )


@router.delete("/{kind}", status_code=204)
def delete_device_plugin(kind: str) -> None:
    """Unlike disabling (a reversible visibility toggle, PUT above), this
    is permanent: removes the plugin from the running server immediately
    (no restart needed) and deletes its file from installed/, so
    load_installed() won't bring it back on the next one either."""
    if _visible_spec(kind) is None:
        raise HTTPException(status_code=404, detail=f"device plugin {kind!r} isn't installed")
    device_plugins.unregister(kind)
