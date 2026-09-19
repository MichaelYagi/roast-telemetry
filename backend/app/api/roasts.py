"""Roast lifecycle, history and live-streaming endpoints."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from sse_starlette.sse import EventSourceResponse

from alog_playback.alog_io import _nearest_index

from .. import auth, ollama_client, storage
from ..models import (
    ControlCommand,
    EventCreateRequest,
    EventUpdateRequest,
    NoteCreateRequest,
    Roast,
    RoastCreateRequest,
    RoastMode,
    RoastReview,
    RoastStatus,
    RoastSummary,
    TagsUpdateRequest,
    UserStatus,
)
from ..roast_review import build_prompt, build_summary
from ..roast_session import RoastSessionError, session_manager
from ..ws_manager import pubsub

router = APIRouter(prefix="/roasts", tags=["roasts"])

# \x00-\x1f (all C0 control characters, including \r\n) matters beyond
# just filesystem safety for csv_filename below -- that one goes straight
# into a raw Content-Disposition header string (download_csv), and an
# embedded CRLF there is a genuine header-injection surface, not just a
# cosmetic filename issue. alog_filename's own use (FileResponse's
# filename= kwarg) is already safe either way -- Starlette percent-encodes
# that internally -- but there's no reason for the two helpers sharing
# this regex to have different safety guarantees.
_UNSAFE_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def alog_filename(title: str, created_at: str) -> str:
    """"<Title>_<YYYY-MM-DD_HHMM>.alog" -- deliberately built by slicing
    the same ISO `created_at` string the frontend already has (not by
    reformatting a parsed datetime), so the frontend can trivially mirror
    this exact algorithm and show the same filename as a download link's
    label without a round trip, and without any timezone-conversion risk
    of the two sides disagreeing (see RoastDetailView.jsx's alogFilename)."""
    safe_title = _UNSAFE_FILENAME_CHARS.sub("_", title).strip() or "roast"
    timestamp = created_at[:16].replace("T", "_").replace(":", "")
    return f"{safe_title}_{timestamp}.alog"


def csv_filename(title: str, created_at: str) -> str:
    """Same "<Title>_<YYYY-MM-DD_HHMM>.csv" convention as alog_filename
    above, mirrored client-side the same way (see RoastDetailView.jsx's
    csvFilename)."""
    safe_title = _UNSAFE_FILENAME_CHARS.sub("_", title).strip() or "roast"
    timestamp = created_at[:16].replace("T", "_").replace(":", "")
    return f"{safe_title}_{timestamp}.csv"


def roast_to_csv(roast: Roast) -> str:
    """One row per recorded sample, time-aligned -- BT/ET/DT/RoR/Burner-
    Air-Drum-%/Burner-SV plus any role=EXTRA DeviceProfile channels this
    roast happens to have (collected as the union of every point's own
    `extra` keys, in first-seen order, same convention RoastChart.jsx's
    own extraSeriesDefs uses client-side). `event` is populated on
    whichever row is *closest* to each milestone's own time_s (reusing
    alog_io's own _nearest_index, the same alignment its .alog writer
    uses) rather than requiring an exact match -- CHARGE in particular
    can land at time_s=0.0 while a live-recorded roast's first real
    sample is at time_s=1.0 (confirmed empirically against a live
    simulator roast), so exact equality silently drops it."""
    extra_labels: list[str] = []
    for p in roast.profile:
        for label in p.extra or {}:
            if label not in extra_labels:
                extra_labels.append(label)

    sample_times = [p.time_s for p in roast.profile]
    events_by_time: dict[float, list[str]] = {}
    for e in roast.events:
        if not sample_times:
            break
        nearest_t = sample_times[_nearest_index(sample_times, e.time_s)]
        events_by_time.setdefault(nearest_t, []).append(e.label)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["time_s", "event", "bt", "et", "dt", "ror_bt", "ror_et", "heater_pct", "fan_pct", "drum_speed_pct", "burner_sv_c"]
        + [f"extra_{label}" for label in extra_labels]
    )
    for p in roast.profile:
        writer.writerow(
            [
                p.time_s,
                "; ".join(events_by_time.get(p.time_s, [])),
                p.bt, p.et, p.dt, p.ror_bt, p.ror_et, p.heater_pct, p.fan_pct, p.drum_speed_pct, p.burner_sv_c,
            ]
            + [p.extra.get(label) for label in extra_labels]
        )
    return buffer.getvalue()


@router.get("", response_model=list[RoastSummary])
def list_roasts(
    mode: Optional[RoastMode] = None,
    status: Optional[RoastStatus] = None,
    tag: Optional[str] = None,
    q: Optional[str] = Query(default=None, description="Substring match against title/beans/tags"),
    limit: int = Query(default=100, le=500),
    offset: int = 0,
) -> list[RoastSummary]:
    return session_manager.list_summaries(
        mode=mode.value if mode else None,
        status=status.value if status else None,
        tag=tag,
        q=q,
        limit=limit,
        offset=offset,
    )


@router.get("/tags")
def list_tags() -> list[dict]:
    return storage.list_distinct_tags()


@router.get("/count")
def count_roasts(
    mode: Optional[RoastMode] = None,
    status: Optional[RoastStatus] = None,
    tag: Optional[str] = None,
    q: Optional[str] = None,
) -> dict:
    # Separate from list_roasts above (rather than {items, total}) so
    # GET /roasts itself stays a plain array -- BackgroundProfilePicker.jsx/
    # RoastComparisonView.jsx/LiveRoastView.jsx's active-roast check all
    # already depend on that exact shape. Backs HistoryDashboard.jsx's
    # page count, same filters as list_roasts so the two always agree on
    # which rows match.
    total = session_manager.count_summaries(
        mode=mode.value if mode else None,
        status=status.value if status else None,
        tag=tag,
        q=q,
    )
    return {"total": total}


@router.post("", response_model=RoastSummary, status_code=201)
async def create_roast(request: RoastCreateRequest, http_request: Request) -> RoastSummary:
    """modbus_live/ms6514_live/aillio_live: this is the ON action --
    connects and starts streaming live readings, but doesn't create a
    roast yet (see begin_recording below for that, the START action).
    Every other mode doesn't have a real connection worth verifying
    separately, so this still connects *and* starts recording in one
    step, exactly as before."""
    try:
        session = session_manager.create(request, created_by_username=http_request.state.user["username"])
        if request.mode in (RoastMode.MODBUS_LIVE, RoastMode.MS6514_LIVE, RoastMode.AILLIO_LIVE):
            await session_manager.connect(session.id)
        else:
            await session_manager.start(session.id)
    except RoastSessionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return session.summary()


@router.post("/{roast_id}/start", response_model=RoastSummary)
async def begin_recording(roast_id: str) -> RoastSummary:
    """The START action for a session already connect()ed via POST
    /roasts (ON) -- begins actually recording the live stream that's
    already flowing into a persisted roast. No body: title/beans/weight
    were already captured when the connection was made."""
    try:
        session = await session_manager.begin_recording(roast_id)
    except RoastSessionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return session.summary()


@router.get("/{roast_id}", response_model=Roast)
def get_roast(roast_id: str) -> Roast:
    roast = session_manager.get_roast_detail(roast_id)
    if roast is None:
        raise HTTPException(status_code=404, detail=f"roast {roast_id!r} not found")
    return roast


@router.delete("/{roast_id}", status_code=204)
def delete_roast(roast_id: str) -> None:
    try:
        session_manager.delete(roast_id)
    except RoastSessionError as exc:
        status = 409 if "still active" in str(exc) else 404
        raise HTTPException(status_code=status, detail=str(exc)) from exc


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
    try:
        created = session.add_event(event)
    except RoastSessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await pubsub.publish(roast_id, {"type": "event", "roast_id": roast_id, "event": created})
    return created


def _require_roast_exists(roast_id: str) -> None:
    # Unlike add_event/add_note above, delete_event/retime_event work on
    # a roast whose in-memory session no longer exists too (a genuinely
    # historical roast from before the last backend restart), reading/
    # writing its .alog file directly -- so there's no single
    # session_manager.get() call to 404 on. Check both places explicitly
    # instead of string-matching exception messages for status codes.
    if session_manager.get(roast_id) is not None:
        return
    if storage.get_roast_row(roast_id) is not None:
        return
    raise HTTPException(status_code=404, detail=f"roast {roast_id!r} not found")


@router.delete("/{roast_id}/events/{event_id}", status_code=204)
async def delete_event(roast_id: str, event_id: str) -> None:
    _require_roast_exists(roast_id)
    try:
        session_manager.delete_event(roast_id, event_id)
    except RoastSessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await pubsub.publish(roast_id, {"type": "event_deleted", "roast_id": roast_id, "event_id": event_id})


@router.patch("/{roast_id}/events/{event_id}")
async def retime_event(roast_id: str, event_id: str, update: EventUpdateRequest) -> dict:
    _require_roast_exists(roast_id)
    try:
        updated = session_manager.retime_event(roast_id, event_id, update.time_s)
    except RoastSessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await pubsub.publish(roast_id, {"type": "event_updated", "roast_id": roast_id, "event": updated})
    return updated


@router.post("/{roast_id}/weight")
def set_weight(roast_id: str, grams: float) -> dict:
    # Works for any roast with a saved .alog, not just a still-live
    # in-memory session -- same warm/cold split as delete_event/retime_event
    # below, so this keeps working after a server restart, not just during
    # the same process the roast was recorded in.
    try:
        session_manager.set_weight_roasted(roast_id, grams)
    except RoastSessionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "weight_roasted_g": grams}


@router.post("/{roast_id}/weight-green")
def set_weight_green(roast_id: str, grams: float) -> dict:
    try:
        session_manager.set_weight_green(roast_id, grams)
    except RoastSessionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "weight_green_g": grams}


@router.put("/{roast_id}/tags")
def set_tags(roast_id: str, update: TagsUpdateRequest) -> dict:
    try:
        session_manager.set_tags(roast_id, update.tags)
    except RoastSessionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "tags": update.tags}


@router.get("/{roast_id}/alog")
def download_alog(roast_id: str) -> FileResponse:
    """Real Artisan's own native format (Python-literal syntax +
    timeindex/computed/specialevents) -- File > Open in Artisan itself
    opens this directly. See roast_to_artisan_native_dict's docstring for
    why that's the only format this app writes."""
    roast = session_manager.get_roast_detail(roast_id)
    if roast is None or not roast.alog_path:
        raise HTTPException(status_code=404, detail="alog not available (roast still in progress or not found)")
    return FileResponse(
        roast.alog_path, filename=alog_filename(roast.title, roast.created_at), media_type="application/octet-stream"
    )


@router.get("/{roast_id}/csv")
def download_csv(roast_id: str) -> Response:
    """Plain spreadsheet-friendly export -- one row per recorded sample.
    Unlike the .alog download (only available once a roast has actually
    finished recording, since that's when it's written to disk -- see
    RoastSession._finish), this works for a roast still in progress too,
    reading straight from its live in-memory profile via the same
    get_roast_detail session_manager already uses everywhere else."""
    roast = session_manager.get_roast_detail(roast_id)
    if roast is None:
        raise HTTPException(status_code=404, detail=f"roast {roast_id!r} not found")
    return Response(
        content=roast_to_csv(roast),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{csv_filename(roast.title, roast.created_at)}"'},
    )


def _row_to_review(row: dict) -> RoastReview:
    return RoastReview(
        roast_id=row["roast_id"],
        status=row["status"],
        review_text=row.get("review_text"),
        error=row.get("error"),
        model=row.get("model"),
        created_at=row["created_at"],
        completed_at=row.get("completed_at"),
    )


async def _run_review(roast_id: str, url: str, model: str, created_at: str) -> None:
    """Runs in the background (kicked off by BackgroundTasks in
    request_review below) -- the whole point is the HTTP request doesn't
    wait for this; it can take anywhere from seconds to a couple minutes
    depending on the local model. Whatever finishes last wins the row."""
    try:
        roast = session_manager.get_roast_detail(roast_id)
        if roast is None:
            raise ValueError(f"roast {roast_id!r} no longer exists")
        summary = build_summary(roast)
        prompt = build_prompt(summary)
        text = await ollama_client.generate(url, model, prompt)
        storage.upsert_review_row({
            "roast_id": roast_id, "status": "ready", "review_text": text, "model": model,
            "created_at": created_at, "completed_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:  # noqa: BLE001 -- any failure here should land as a visible "failed" review, not a swallowed background-task crash
        storage.upsert_review_row({
            "roast_id": roast_id, "status": "failed", "error": str(exc), "model": model,
            "created_at": created_at, "completed_at": datetime.now(timezone.utc).isoformat(),
        })


@router.post("/{roast_id}/review", response_model=RoastReview, status_code=202)
async def request_review(roast_id: str, background_tasks: BackgroundTasks) -> RoastReview:
    roast = session_manager.get_roast_detail(roast_id)
    if roast is None:
        raise HTTPException(status_code=404, detail=f"roast {roast_id!r} not found")
    if roast.status in (RoastStatus.ROASTING, RoastStatus.COOLING):
        raise HTTPException(status_code=409, detail="roast is still active -- finish it before requesting a review")

    settings = storage.get_settings()
    url, model = settings.get("ollama_url"), settings.get("ollama_model")
    if not url or not model:
        raise HTTPException(status_code=400, detail="Ollama isn't configured -- set a URL and model in Settings")

    created_at = datetime.now(timezone.utc).isoformat()
    storage.upsert_review_row({"roast_id": roast_id, "status": "pending", "model": model, "created_at": created_at})
    background_tasks.add_task(_run_review, roast_id, url, model, created_at)
    return _row_to_review(storage.get_review_row(roast_id))


@router.get("/{roast_id}/review", response_model=RoastReview)
def get_review(roast_id: str) -> RoastReview:
    row = storage.get_review_row(roast_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no review for roast {roast_id!r} yet")
    return _row_to_review(row)


@router.post("/import", response_model=RoastSummary, status_code=201)
def import_alog(path: str, http_request: Request, title: Optional[str] = None) -> RoastSummary:
    try:
        return session_manager.import_alog(path, title, created_by_username=http_request.state.user["username"])
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.websocket("/{roast_id}/stream")
async def stream_roast_ws(websocket: WebSocket, roast_id: str) -> None:
    # main.py's require_login middleware is plain HTTP-scope only --
    # Starlette's BaseHTTPMiddleware never runs for a websocket upgrade,
    # so this route has to check the same session cookie itself instead
    # of inheriting the gate for free like every other route here does.
    # Same X-API-Key-then-cookie fallback as that middleware, so a script
    # holding an API key can also open this stream directly.
    user = None
    api_key = websocket.headers.get("x-api-key")
    if api_key:
        user = storage.get_user_by_api_key_hash(auth.hash_api_key(api_key))
    if user is None:
        user = auth.get_user_for_token(websocket.cookies.get(auth.SESSION_COOKIE))
    if user is None or user["status"] != UserStatus.ALLOWED.value:
        await websocket.close(code=4401)
        return
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
