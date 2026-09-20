"""Device status endpoints.

Every active roast owns exactly one ``MockDevice`` (see
``roast_session.RoastSession``), so "devices" here means the mock
hardware endpoints currently bound to roast sessions -- listing them is
how the frontend shows connection status per active roast.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..models import Device, DeviceStatus
from ..roast_session import session_manager

router = APIRouter(prefix="/devices", tags=["devices"])


def _to_device(session) -> Device:
    status = session.device.status()
    engine_status = status.get("engine_status") or {}
    # The MockDevice's own last_error only covers connect/read/write faults
    # at that shim layer; for live bridges (Modbus, etc.) the actually
    # useful error -- "port busy", "connection refused", etc. -- lives one
    # level down in the engine's own status. Surface that when present.
    # modbus_bridge specifically also tracks a separate control_last_error
    # (Air/Drum drive writes, on their own connection when control_port is
    # set) -- without this fallback, a rejected/errored drive write was
    # captured internally but never reached the API or UI at all, so a
    # wrong Air/Drum register looked identical to "wrote successfully."
    last_error = (
        status["last_error"] or engine_status.get("last_error") or engine_status.get("control_last_error")
    )
    return Device(
        id=session.id,
        name=f"{session.mode.value}:{session.id[:8]}",
        mode=session.mode,
        status=DeviceStatus(status["state"]),
        connected_at=status["connected_at"],
        last_error=last_error,
    )


@router.get("", response_model=list[Device])
def list_devices() -> list[Device]:
    return [_to_device(s) for s in session_manager.sessions.values()]


@router.get("/{roast_id}", response_model=Device)
def get_device(roast_id: str) -> Device:
    session = session_manager.get(roast_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"device for roast {roast_id!r} not found")
    return _to_device(session)


@router.post("/{roast_id}/fault", response_model=Device)
def inject_fault(roast_id: str, message: str = "simulated hardware fault") -> Device:
    """Testing hook to exercise error handling in the frontend/UI."""
    session = session_manager.get(roast_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"device for roast {roast_id!r} not found")
    session.device.simulate_fault(message)
    return _to_device(session)
