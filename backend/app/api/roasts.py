"""Roast lifecycle, history and live-streaming endpoints."""
from __future__ import annotations

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

from ..models import (
    ControlCommand,
    EventCreateRequest,
    NoteCreateRequest,
    Roast,
    RoastCreateRequest,
    RoastMode,
    RoastStatus,
    RoastSummary,
)
from ..roast_session import RoastSessionError, session_manager
from ..ws_manager import pubsub
from .machines import resolve_machine

router = APIRouter(prefix="/roasts", tags=["roasts"])


@router.get("", response_model=list[RoastSummary])
def list_roasts(
    mode: Optional[RoastMode] = None,
    status: Optional[RoastStatus] = None,
    machine_id: Optional[str] = None,
    limit: int = Query(default=100, le=500),
    offset: int = 0,
) -> list[RoastSummary]:
    return session_manager.list_summaries(
        mode=mode.value if mode else None,
        status=status.value if status else None,
        machine_id=machine_id,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=RoastSummary, status_code=201)
async def create_roast(request: RoastCreateRequest) -> RoastSummary:
    machine = resolve_machine(request.machine_id)
    if request.machine_id and machine is None:
        raise HTTPException(status_code=404, detail=f"machine {request.machine_id!r} not found")
    try:
        session = session_manager.create(request, machine)
        await session_manager.start(session.id)
    except RoastSessionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return session.summary()


@router.get("/{roast_id}", response_model=Roast)
def get_roast(roast_id: str) -> Roast:
    roast = session_manager.get_roast_detail(roast_id)
    if roast is None:
        raise HTTPException(status_code=404, detail=f"roast {roast_id!r} not found")
    return roast


@router.post("/{roast_id}/stop", response_model=RoastSummary)
async def stop_roast(roast_id: str) -> RoastSummary:
    try:
        session = await session_manager.abort(roast_id)
    except RoastSessionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return session.summary()


@router.post("/{roast_id}/commands")
def send_command(roast_id: str, command: ControlCommand) -> dict:
    session = session_manager.get(roast_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"roast {roast_id!r} not found or not active")
    try:
        return session.apply_command(command)
    except RoastSessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{roast_id}/notes")
async def add_note(roast_id: str, note: NoteCreateRequest) -> dict:
    session = session_manager.get(roast_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"roast {roast_id!r} not found or not active")
    created = session.add_note(note)
    await pubsub.publish(roast_id, {"type": "note", "roast_id": roast_id, "note": created})
    return created


@router.post("/{roast_id}/events")
async def add_event(roast_id: str, event: EventCreateRequest) -> dict:
    session = session_manager.get(roast_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"roast {roast_id!r} not found or not active")
    created = session.add_event(event)
    await pubsub.publish(roast_id, {"type": "event", "roast_id": roast_id, "event": created})
    return created


@router.post("/{roast_id}/weight")
def set_weight(roast_id: str, grams: float) -> dict:
    session = session_manager.get(roast_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"roast {roast_id!r} not found or not active")
    session.set_weight_roasted(grams)
    return {"ok": True, "weight_roasted_g": grams}


@router.get("/{roast_id}/alog")
def download_alog(roast_id: str) -> FileResponse:
    roast = session_manager.get_roast_detail(roast_id)
    if roast is None or not roast.alog_path:
        raise HTTPException(status_code=404, detail="alog not available (roast still in progress or not found)")
    return FileResponse(roast.alog_path, filename=f"{roast.title}.alog", media_type="application/json")


@router.post("/import", response_model=RoastSummary, status_code=201)
def import_alog(path: str, title: Optional[str] = None) -> RoastSummary:
    try:
        return session_manager.import_alog(path, title)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.websocket("/{roast_id}/stream")
async def stream_roast_ws(websocket: WebSocket, roast_id: str) -> None:
    await websocket.accept()
    roast = session_manager.get_roast_detail(roast_id)
    if roast is None:
        await websocket.send_json({"type": "error", "message": f"roast {roast_id!r} not found"})
        await websocket.close()
        return

    await websocket.send_json({"type": "snapshot", "roast": roast.model_dump(mode="json")})
    queue = pubsub.subscribe(roast_id)
    try:
        while True:
            receive_task = asyncio.ensure_future(websocket.receive_text())
            queue_task = asyncio.ensure_future(queue.get())
            done, pending = await asyncio.wait({receive_task, queue_task}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            if receive_task in done:
                receive_task.result()  # raises WebSocketDisconnect on close
            if queue_task in done:
                await websocket.send_json(queue_task.result())
    except WebSocketDisconnect:
        pass
    finally:
        pubsub.unsubscribe(roast_id, queue)


@router.get("/{roast_id}/stream/sse")
async def stream_roast_sse(roast_id: str) -> EventSourceResponse:
    roast = session_manager.get_roast_detail(roast_id)
    if roast is None:
        raise HTTPException(status_code=404, detail=f"roast {roast_id!r} not found")

    async def event_generator():
        yield {"event": "snapshot", "data": roast.model_dump_json()}
        queue = pubsub.subscribe(roast_id)
        try:
            while True:
                message = await queue.get()
                yield {"event": message.get("type", "message"), "data": json.dumps(message)}
        finally:
            pubsub.unsubscribe(roast_id, queue)

    return EventSourceResponse(event_generator())
