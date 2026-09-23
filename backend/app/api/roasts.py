"""Roast lifecycle, history and live-streaming endpoints."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from sse_starlette.sse import EventSourceResponse

from alog_playback.alog_io import _celsius_dict_to_fahrenheit, _nearest_index, load_alog
from alog_playback.roastlog import RoastlogParseError, _c_to_f, parse_roastlog_csv, parse_roastlog_xlsx, roast_to_json, save_roastlog_csv, save_roastlog_xlsx

from .. import auth, ollama_client, storage
from ..models import (
    ControlCommand,
    EventCreateRequest,
    EventUpdateRequest,
    FeedbackRequest,
    NoteCreateRequest,
    NoteUpdateRequest,
    OutcomeUpdate,
    RoastBeansUpdate,
    ProgramFromRoastRequest,
    ProgramRequest,
    ReviewStatus,
    Roast,
    RoastCreateRequest,
    RoastMode,
    RoastReview,
    RoastStats,
    RoastStatus,
    RoastSummary,
    TagsUpdateRequest,
    UserStatus,
)
from ..roast_control import program_from_profile
from ..roast_review import build_prompt, build_summary
from ..roast_session import RoastSessionError, session_manager
from ..roast_session.control import RoastControlError
from ..roast_stats import compute_roast_stats
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


def roast_to_csv(roast: Roast, temperature_unit: str = "c") -> str:
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
    simulator roast), so exact equality silently drops it.

    Column headers say which unit (bt_c/bt_f, ...) rather than leaving it
    ambiguous -- this format has no separate metadata line to carry a
    Unit tag the way roastlog.csv does. heater_pct/fan_pct/drum_speed_pct
    are percentages, never converted, regardless of temperature_unit."""
    to_native = (lambda c: c) if temperature_unit != "f" else _c_to_f
    to_rate = (lambda c: c) if temperature_unit != "f" else (lambda c: None if c is None else c * 9.0 / 5.0)
    suffix = "f" if temperature_unit == "f" else "c"
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
        [
            "time_s", "event", f"bt_{suffix}", f"et_{suffix}", f"dt_{suffix}", f"ror_bt_{suffix}", f"ror_et_{suffix}",
            "heater_pct", "fan_pct", "drum_speed_pct", f"burner_sv_{suffix}",
        ]
        + [f"extra_{label}_{suffix}" for label in extra_labels]
    )
    for p in roast.profile:
        writer.writerow(
            [
                p.time_s,
                "; ".join(events_by_time.get(p.time_s, [])),
                to_native(p.bt) if p.bt is not None else None,
                to_native(p.et) if p.et is not None else None,
                to_native(p.dt) if p.dt is not None else None,
                to_rate(p.ror_bt),
                to_rate(p.ror_et),
                p.heater_pct, p.fan_pct, p.drum_speed_pct,
                to_native(p.burner_sv_c) if p.burner_sv_c is not None else None,
            ]
            + [(to_native(p.extra.get(label)) if p.extra.get(label) is not None else None) for label in extra_labels]
        )
    return buffer.getvalue()


@router.get("", response_model=list[RoastSummary])
def list_roasts(
    mode: Optional[RoastMode] = None,
    status: Optional[RoastStatus] = None,
    tag: Optional[str] = None,
    q: Optional[str] = Query(default=None, description="Substring match against title/beans/tags"),
    created_by: Optional[str] = None,
    limit: int = Query(default=100, le=500),
    offset: int = 0,
) -> list[RoastSummary]:
    return session_manager.list_summaries(
        mode=mode.value if mode else None,
        status=status.value if status else None,
        tag=tag,
        q=q,
        created_by=created_by,
        limit=limit,
        offset=offset,
    )


@router.get("/tags")
def list_tags() -> list[dict]:
    return storage.list_distinct_tags()


@router.get("/roasters")
def list_roasters() -> list[dict]:
    return storage.list_distinct_roasters()


@router.get("/count")
def count_roasts(
    mode: Optional[RoastMode] = None,
    status: Optional[RoastStatus] = None,
    tag: Optional[str] = None,
    q: Optional[str] = None,
    created_by: Optional[str] = None,
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
        created_by=created_by,
    )
    return {"total": total}


@router.get("/stats-batch")
def stats_batch(
    mode: Optional[RoastMode] = None,
    status: Optional[RoastStatus] = None,
    tag: Optional[str] = None,
    q: Optional[str] = None,
    created_by: Optional[str] = None,
    limit: int = Query(default=100, le=500),
    offset: int = 0,
) -> list[dict]:
    # Derived stats for History's "Show trends" toggle -- same filters as
    # list_roasts, same page (limit/offset) as whatever's currently shown
    # in the table, deliberately NOT the whole filtered set: each entry
    # here means a real .alog read + parse (get_roast_detail's cold path),
    # so this stays bounded to one page's worth of I/O, and is only ever
    # called when the user explicitly asks to see trends, never on a
    # plain History page load.
    summaries = session_manager.list_summaries(
        mode=mode.value if mode else None,
        status=status.value if status else None,
        tag=tag,
        q=q,
        created_by=created_by,
        limit=limit,
        offset=offset,
    )
    results = []
    for s in summaries:
        try:
            roast = session_manager.get_roast_detail(s.id)
        except RoastSessionError:
            continue  # a roast whose recording file is gone is left out, not a reason to fail the whole batch
        if roast is None:
            continue
        stats = compute_roast_stats(roast)
        results.append({"id": s.id, "title": s.title, **stats.model_dump()})
    return results


@router.post("", response_model=RoastSummary, status_code=201)
async def create_roast(request: RoastCreateRequest, http_request: Request) -> RoastSummary:
    """modbus_live/ms6514_live/aillio_live/tc4_live: this is the ON
    action -- connects and starts streaming live readings, but doesn't
    create a roast yet (see begin_recording below for that, the START
    action). Every other mode doesn't have a real connection worth
    verifying separately, so this still connects *and* starts recording
    in one step, exactly as before."""
    try:
        session = session_manager.create(request, created_by_username=http_request.state.user["username"])
        if request.mode in (RoastMode.MODBUS_LIVE, RoastMode.MS6514_LIVE, RoastMode.AILLIO_LIVE, RoastMode.TC4_LIVE):
            await session_manager.connect(session.id)
        else:
            await session_manager.start(session.id)
    except RoastSessionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    storage.log_activity(
        "roast", "create", username=http_request.state.user["username"], roast_id=session.id,
        roast_title=session.title, message=f'Created "{session.title}"',
    )
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
    try:
        roast = session_manager.get_roast_detail(roast_id)
    except RoastSessionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if roast is None:
        if storage.get_roast_row(roast_id) is not None:
            # In the list, but nothing was ever saved for it (the server stopped mid-roast, say).
            raise HTTPException(status_code=404, detail="this roast has no recording: it was interrupted before anything was saved. You can delete it from History")
        raise HTTPException(status_code=404, detail=f"roast {roast_id!r} not found")
    return roast


@router.get("/{roast_id}/stats", response_model=RoastStats)
def get_roast_stats(roast_id: str) -> RoastStats:
    # get_roast_detail already handles both a live session and a cold/
    # historical roast (same method the plain GET above uses) -- no
    # separate warm/cold plumbing needed here.
    roast = session_manager.get_roast_detail(roast_id)
    if roast is None:
        raise HTTPException(status_code=404, detail=f"roast {roast_id!r} not found")
    return compute_roast_stats(roast)


@router.delete("/{roast_id}", status_code=204)
def delete_roast(roast_id: str, http_request: Request) -> None:
    # Fetched before the delete -- session_manager.delete() removes the row
    # itself, and the activity log snapshots the title precisely so the
    # entry stays meaningful afterward (see storage.py's activity_log table).
    row = storage.get_roast_row(roast_id)
    try:
        session_manager.delete(roast_id)
    except RoastSessionError as exc:
        status = 409 if "still active" in str(exc) else 404
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    storage.log_activity(
        "roast", "delete", username=http_request.state.user["username"], roast_id=roast_id,
        roast_title=row["title"] if row else None, message=f'Deleted "{row["title"] if row else roast_id}"',
    )


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


def _active_session(roast_id: str):
    session = session_manager.get(roast_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"roast {roast_id!r} not found or not active")
    return session


@router.get("/{roast_id}/control")
def get_control(roast_id: str) -> dict:
    """What the roaster's automatic control is doing, the current safety
    limits, and whether a fail-safe has stopped things.

    Refreshes the limits from Settings before returning them -- a
    RoastControl only re-reads Settings on specific actions otherwise
    (starting automation, an emergency-stop attempt), so a roast that's
    been connected since before a Settings change would otherwise keep
    showing this poll's stale, in-memory copy indefinitely. This is the
    endpoint the Live Roast page's own display polls (AutoControlPanel,
    EmergencyStop) every few seconds specifically so a change made in
    Settings -- most importantly safety_disabled -- shows up there
    promptly, not just the next time something else happens to call
    refresh_limits() as a side effect."""
    session = _active_session(roast_id)
    session.control.refresh_limits()
    return session.control.status()


@router.post("/{roast_id}/emergency-stop")
async def emergency_stop(roast_id: str, http_request: Request) -> dict:
    """Heater off, fan to the safe level, all automation stopped. The roast
    itself keeps recording (as cooling) so nothing already logged is lost."""
    session = _active_session(roast_id)
    session.control.refresh_limits()
    written = await session.control.emergency_stop(username=http_request.state.user["username"])
    return {"ok": written, **session.control.status()}


@router.put("/{roast_id}/control/program")
async def start_program(roast_id: str, program: ProgramRequest, http_request: Request) -> dict:
    """Runs heater/fan/drum steps timed from Charge."""
    session = _active_session(roast_id)
    session.control.refresh_limits()
    try:
        session.control.start_program(program.steps, "custom program")
    except RoastControlError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    storage.log_activity(
        "safety", "automation_started", username=http_request.state.user["username"], roast_id=roast_id,
        roast_title=session.title, message=f'Started a custom program on "{session.title}"',
    )
    return session.control.status()


@router.post("/{roast_id}/control/program/from-roast")
async def start_program_from_roast(roast_id: str, body: ProgramFromRoastRequest, http_request: Request) -> dict:
    """Repeats another roast's heater/fan/drum settings, timed from Charge."""
    session = _active_session(roast_id)
    source = session_manager.get_roast_detail(body.source_roast_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"roast {body.source_roast_id!r} not found")
    charge = next((e for e in source.events if e.type.value == "CHARGE"), None)
    profile = [p.model_dump() for p in source.profile]
    steps = program_from_profile(profile, charge.time_s if charge else (profile[0]["time_s"] if profile else 0.0))
    if not steps:
        raise HTTPException(status_code=409, detail="that roast has no recorded heater, fan or drum settings to repeat")
    session.control.refresh_limits()
    try:
        session.control.start_program(steps, f"repeat of {source.title}")
    except RoastControlError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    storage.log_activity(
        "safety", "automation_started", username=http_request.state.user["username"], roast_id=roast_id,
        roast_title=session.title, message=f'Started a program on "{session.title}" repeating "{source.title}"',
    )
    return session.control.status()


@router.put("/{roast_id}/control/feedback")
async def start_feedback(roast_id: str, config: FeedbackRequest, http_request: Request) -> dict:
    """Holds a target (bean temperature or its rate of rise) by adjusting the heater."""
    session = _active_session(roast_id)
    session.control.refresh_limits()
    try:
        session.control.start_feedback(config)
    except RoastControlError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    storage.log_activity(
        "safety", "automation_started", username=http_request.state.user["username"], roast_id=roast_id,
        roast_title=session.title, message=f'Started target control on "{session.title}"',
    )
    return session.control.status()


@router.delete("/{roast_id}/control/automation")
async def stop_automation(roast_id: str, http_request: Request) -> dict:
    """Stops any program or target control. The roaster keeps its current settings."""
    session = _active_session(roast_id)
    session.control.stop_automation("stopped by the operator")
    storage.log_activity(
        "safety", "automation_stopped", username=http_request.state.user["username"], roast_id=roast_id,
        roast_title=session.title, message=f'Stopped automation on "{session.title}"',
    )
    return session.control.status()


def _notes_message(roast_id: str) -> dict:
    # After an edit or delete other open pages get the whole list, so none
    # of them is left holding an id that no longer matches.
    roast = session_manager.get_roast_detail(roast_id)
    return {"type": "notes", "roast_id": roast_id, "notes": roast.notes if roast else []}


@router.post("/{roast_id}/notes")
async def add_note(roast_id: str, note: NoteCreateRequest, http_request: Request) -> dict:
    # Works during a roast and after it (a finished roast's notes are
    # rewritten into its .alog file), same warm/cold split as the weights.
    _require_roast_exists(roast_id)
    if not note.author:
        user = getattr(http_request.state, "user", None)
        note.author = user["username"] if user else None
    try:
        created = session_manager.add_note(roast_id, note)
    except RoastSessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await pubsub.publish(roast_id, {"type": "note", "roast_id": roast_id, "note": created})
    row = storage.get_roast_row(roast_id)
    storage.log_activity(
        "roast", "add_note", username=http_request.state.user["username"], roast_id=roast_id,
        roast_title=row["title"] if row else None, message=f'Added a note to "{row["title"] if row else roast_id}"',
    )
    return created


@router.patch("/{roast_id}/notes/{note_id}")
async def update_note(roast_id: str, note_id: str, update: NoteUpdateRequest, http_request: Request) -> dict:
    _require_roast_exists(roast_id)
    try:
        updated = session_manager.update_note(roast_id, note_id, update.text)
    except RoastSessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await pubsub.publish(roast_id, _notes_message(roast_id))
    row = storage.get_roast_row(roast_id)
    storage.log_activity(
        "roast", "update_note", username=http_request.state.user["username"], roast_id=roast_id,
        roast_title=row["title"] if row else None, message=f'Edited a note on "{row["title"] if row else roast_id}"',
    )
    return updated


@router.delete("/{roast_id}/notes/{note_id}", status_code=204)
async def delete_note(roast_id: str, note_id: str, http_request: Request) -> None:
    _require_roast_exists(roast_id)
    try:
        session_manager.delete_note(roast_id, note_id)
    except RoastSessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await pubsub.publish(roast_id, _notes_message(roast_id))
    row = storage.get_roast_row(roast_id)
    storage.log_activity(
        "roast", "delete_note", username=http_request.state.user["username"], roast_id=roast_id,
        roast_title=row["title"] if row else None, message=f'Deleted a note on "{row["title"] if row else roast_id}"',
    )


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
async def delete_event(roast_id: str, event_id: str, http_request: Request) -> None:
    _require_roast_exists(roast_id)
    try:
        session_manager.delete_event(roast_id, event_id)
    except RoastSessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await pubsub.publish(roast_id, {"type": "event_deleted", "roast_id": roast_id, "event_id": event_id})
    row = storage.get_roast_row(roast_id)
    storage.log_activity(
        "roast", "delete_event", username=http_request.state.user["username"], roast_id=roast_id,
        roast_title=row["title"] if row else None, message=f'Deleted a milestone on "{row["title"] if row else roast_id}"',
    )


@router.patch("/{roast_id}/events/{event_id}")
async def retime_event(roast_id: str, event_id: str, update: EventUpdateRequest, http_request: Request) -> dict:
    _require_roast_exists(roast_id)
    try:
        updated = session_manager.retime_event(roast_id, event_id, update.time_s)
    except RoastSessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await pubsub.publish(roast_id, {"type": "event_updated", "roast_id": roast_id, "event": updated})
    row = storage.get_roast_row(roast_id)
    storage.log_activity(
        "roast", "retime_event", username=http_request.state.user["username"], roast_id=roast_id,
        roast_title=row["title"] if row else None,
        message=f'Retimed a milestone to {update.time_s:.0f}s on "{row["title"] if row else roast_id}"',
    )
    return updated


@router.post("/{roast_id}/weight")
def set_weight(roast_id: str, grams: float, http_request: Request) -> dict:
    # Works for any roast with a saved .alog, not just a still-live
    # in-memory session -- same warm/cold split as delete_event/retime_event
    # below, so this keeps working after a server restart, not just during
    # the same process the roast was recorded in.
    try:
        session_manager.set_weight_roasted(roast_id, grams)
    except RoastSessionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _log_weight_change(roast_id, http_request, f"Set roasted weight to {grams:g} g")
    return {"ok": True, "weight_roasted_g": grams}


@router.post("/{roast_id}/weight-green")
def set_weight_green(roast_id: str, grams: float, http_request: Request) -> dict:
    try:
        session_manager.set_weight_green(roast_id, grams)
    except RoastSessionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _log_weight_change(roast_id, http_request, f"Set green weight to {grams:g} g")
    return {"ok": True, "weight_green_g": grams}


@router.delete("/{roast_id}/weight")
def delete_weight(roast_id: str, http_request: Request) -> dict:
    # Same set_weight_roasted method as the POST above, just with
    # grams=None -- see that method's own comment for why one method
    # handles both instead of a separate clearing code path.
    try:
        session_manager.set_weight_roasted(roast_id, None)
    except RoastSessionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _log_weight_change(roast_id, http_request, "Cleared roasted weight")
    return {"ok": True, "weight_roasted_g": None}


@router.delete("/{roast_id}/weight-green")
def delete_weight_green(roast_id: str, http_request: Request) -> dict:
    try:
        session_manager.set_weight_green(roast_id, None)
    except RoastSessionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _log_weight_change(roast_id, http_request, "Cleared green weight")
    return {"ok": True, "weight_green_g": None}


def _log_weight_change(roast_id: str, http_request: Request, action_text: str) -> None:
    row = storage.get_roast_row(roast_id)
    title = row["title"] if row else roast_id
    storage.log_activity(
        "roast", "set_weight", username=http_request.state.user["username"], roast_id=roast_id,
        roast_title=row["title"] if row else None, message=f'{action_text} on "{title}"',
    )


@router.put("/{roast_id}/outcome")
def set_outcome(roast_id: str, update: OutcomeUpdate, http_request: Request) -> dict:
    """What the roast turned out like: color (Agtron), cupping score, a 1-5
    rating, tasting notes. A field left out is unchanged; null clears it."""
    row = storage.get_roast_row(roast_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"roast {roast_id!r} not found")
    fields = {k: getattr(update, k) for k in update.model_fields_set}
    if "tasting_notes" in fields and fields["tasting_notes"] is not None:
        fields["tasting_notes"] = fields["tasting_notes"].strip() or None
    storage.update_roast(roast_id, **fields)
    storage.log_activity(
        "roast", "set_outcome", username=http_request.state.user["username"], roast_id=roast_id,
        roast_title=row["title"], message=f'Updated how "{row["title"]}" turned out',
    )
    row = storage.get_roast_row(roast_id)
    return {k: row.get(k) for k in ("color_agtron", "cupping_score", "rating", "tasting_notes")}


@router.put("/{roast_id}/beans")
def set_beans(roast_id: str, update: RoastBeansUpdate, http_request: Request) -> dict:
    """Sets the roast's beans. A name that matches a saved beans record
    (ignoring case) links the roast to it; any other name is added to the
    Beans list and linked; null or blank clears both."""
    row = storage.get_roast_row(roast_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"roast {roast_id!r} not found")
    name = (update.name or "").strip() or None
    saved = storage.ensure_bean(name)
    fields = {"beans": saved["name"] if saved else None, "bean_id": saved["id"] if saved else None}
    storage.update_roast(roast_id, **fields)
    session = session_manager.get(roast_id)
    if session is not None:
        session.beans, session.bean_id = fields["beans"], fields["bean_id"]
    storage.log_activity(
        "roast", "set_beans", username=http_request.state.user["username"], roast_id=roast_id,
        roast_title=row["title"], message=f'Set beans on "{row["title"]}" to "{fields["beans"] or "(none)"}"',
    )
    return fields


@router.put("/{roast_id}/tags")
def set_tags(roast_id: str, update: TagsUpdateRequest, http_request: Request) -> dict:
    try:
        session_manager.set_tags(roast_id, update.tags)
    except RoastSessionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    row = storage.get_roast_row(roast_id)
    tags_text = ", ".join(update.tags) or "(none)"
    storage.log_activity(
        "roast", "set_tags", username=http_request.state.user["username"], roast_id=roast_id,
        roast_title=row["title"] if row else None,
        message=f'Set tags on "{row["title"] if row else roast_id}" to {tags_text}',
    )
    return {"ok": True, "tags": update.tags}


@router.get("/{roast_id}/alog")
def download_alog(roast_id: str) -> Response:
    """The native .alog format (Python-literal syntax +
    timeindex/computed/specialevents), which software that reads .alog
    opens directly. See roast_to_native_alog_dict's docstring for
    why that's the only format this app writes.

    Serves the stored file byte-for-byte when the Temperature Unit
    setting is Celsius (the common case -- no read/convert/reserialize
    cost). Fahrenheit reads the file, converts a copy of the already-built
    dict (never rebuilds it -- see download_json's own docstring for why
    rebuilding from roast.profile/events here would silently shift
    Charge), and serves that instead."""
    roast = session_manager.get_roast_detail(roast_id)
    if roast is None or not roast.alog_path:
        raise HTTPException(status_code=404, detail="alog not available (roast still in progress or not found)")
    filename = alog_filename(roast.title, roast.created_at)
    unit = storage.get_settings().get("temperature_unit") or "c"
    if unit != "f":
        return FileResponse(roast.alog_path, filename=filename, media_type="application/octet-stream")
    data = _celsius_dict_to_fahrenheit(load_alog(roast.alog_path))
    return Response(
        content=repr(data),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
    unit = storage.get_settings().get("temperature_unit") or "c"
    return Response(
        content=roast_to_csv(roast, unit),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{csv_filename(roast.title, roast.created_at)}"'},
    )


def json_filename(title: str, created_at: str) -> str:
    safe_title = _UNSAFE_FILENAME_CHARS.sub("_", title).strip() or "roast"
    timestamp = created_at[:16].replace("T", "_").replace(":", "")
    return f"{safe_title}_{timestamp}.json"


def roastlog_csv_filename(title: str, created_at: str) -> str:
    """The same convention, distinguishing this from csv_filename's own
    (different-shaped) CSV download so a browser's download list never shows
    two files with the same name."""
    safe_title = _UNSAFE_FILENAME_CHARS.sub("_", title).strip() or "roast"
    timestamp = created_at[:16].replace("T", "_").replace(":", "")
    return f"{safe_title}_{timestamp}_log.csv"


def xlsx_filename(title: str, created_at: str) -> str:
    safe_title = _UNSAFE_FILENAME_CHARS.sub("_", title).strip() or "roast"
    timestamp = created_at[:16].replace("T", "_").replace(":", "")
    return f"{safe_title}_{timestamp}.xlsx"


@router.get("/{roast_id}/json")
def download_json(roast_id: str) -> Response:
    """The same data as the .alog download, as valid JSON instead of that
    file's Python-literal syntax -- opens the same way in this app (see
    load_alog), and in other software that accepts JSON for this shape.

    Reads and re-serializes the roast's own saved .alog file directly, rather
    than rebuilding it from roast.profile/events through
    roast_to_native_alog_dict -- that function's own Charge-index handling
    assumes its input starts right at Charge (true for a roast as this app
    first records it, no longer true once roast.profile already carries a
    once-added synthetic lead-in sample read back from disk); rebuilding from
    it a second time would silently shift Charge by a tick."""
    roast = session_manager.get_roast_detail(roast_id)
    if roast is None or not roast.alog_path:
        raise HTTPException(status_code=404, detail="not available (roast still in progress or not found)")
    data = load_alog(roast.alog_path)
    unit = storage.get_settings().get("temperature_unit") or "c"
    if unit == "f":
        data = _celsius_dict_to_fahrenheit(data)
    return Response(
        content=roast_to_json(data),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{json_filename(roast.title, roast.created_at)}"'},
    )


@router.get("/{roast_id}/roastlog.csv")
def download_roastlog_csv(roast_id: str) -> Response:
    """A second, table-shaped export -- Time1/Time2/ET/BT/.../Heater columns,
    tab-separated (kept as a .csv extension, matching real-world files of
    this shape). See alog_playback/roastlog.py."""
    roast = session_manager.get_roast_detail(roast_id)
    if roast is None:
        raise HTTPException(status_code=404, detail=f"roast {roast_id!r} not found")
    unit = storage.get_settings().get("temperature_unit") or "c"
    content = save_roastlog_csv(
        profile=[p.model_dump() for p in roast.profile], events=[e.model_dump() for e in roast.events], temperature_unit=unit
    )
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{roastlog_csv_filename(roast.title, roast.created_at)}"'},
    )


@router.get("/{roast_id}/xlsx")
def download_xlsx(roast_id: str) -> Response:
    """The same table as an Excel workbook."""
    roast = session_manager.get_roast_detail(roast_id)
    if roast is None:
        raise HTTPException(status_code=404, detail=f"roast {roast_id!r} not found")
    unit = storage.get_settings().get("temperature_unit") or "c"
    content = save_roastlog_xlsx(
        profile=[p.model_dump() for p in roast.profile], events=[e.model_dump() for e in roast.events], temperature_unit=unit
    )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{xlsx_filename(roast.title, roast.created_at)}"'},
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
    if row is not None:
        return _row_to_review(row)
    # No review row yet -- the default, common state (nobody's clicked
    # "Generate review"), not an error. 404 is reserved for the roast
    # itself not existing at all -- see ReviewStatus.NONE's own comment
    # for why this distinction matters: a 404 here used to fire on every
    # single unreviewed roast's detail page load, showing up as a failed
    # network request in the browser console during completely normal
    # browsing, not just when something was actually wrong.
    if session_manager.get_roast_detail(roast_id) is None:
        raise HTTPException(status_code=404, detail=f"roast {roast_id!r} not found")
    return RoastReview(roast_id=roast_id, status=ReviewStatus.NONE)


@router.post("/import", response_model=RoastSummary, status_code=201)
def import_alog(path: str, http_request: Request, title: Optional[str] = None) -> RoastSummary:
    """A path on the server's own computer -- the file extension picks the
    reader: .alog/.json go through the native reader (see load_alog), .csv/
    .tsv/.xlsx through the roast-log table reader (see alog_playback/roastlog.py)."""
    user = http_request.state.user["username"]
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".xlsx":
            with open(path, "rb") as f:
                parsed = parse_roastlog_xlsx(f.read())
            summary = session_manager.import_table(parsed=parsed, title=title, created_by_username=user)
        elif ext in (".csv", ".tsv"):
            with open(path, "r", encoding="utf-8-sig") as f:
                parsed = parse_roastlog_csv(f.read())
            summary = session_manager.import_table(parsed=parsed, title=title, created_by_username=user)
        else:
            summary = session_manager.import_alog(path, title, created_by_username=user)
    except (FileNotFoundError, ValueError, RoastlogParseError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    storage.log_activity(
        "roast", "import", username=user, roast_id=summary.id, roast_title=summary.title,
        message=f'Imported "{summary.title}" from {os.path.basename(path)}',
    )
    return summary


# An .alog is a few hundred KB at most (a long roast sampled every second); this
# only exists so a wrong file can't fill memory or disk.
MAX_ALOG_UPLOAD_BYTES = 25 * 1024 * 1024


def _import_uploaded_bytes(data: bytes, *, filename: Optional[str], title: Optional[str], created_by_username: str) -> RoastSummary:
    """The shared tail of import-upload, run in a worker thread: picks a reader
    by the uploaded file's own extension (falling back to content for a missing
    or unrecognized one), same split as the server-path /import above."""
    label = os.path.basename(filename) if filename else "The file"
    ext = os.path.splitext(filename or "")[1].lower()

    if ext == ".xlsx" or (not ext and data[:2] == b"PK"):
        try:
            parsed = parse_roastlog_xlsx(data)
        except RoastlogParseError as exc:
            raise HTTPException(status_code=400, detail=f"{label} doesn't look like a roast log spreadsheet ({exc}).") from exc
        summary = session_manager.import_table(parsed=parsed, title=title, created_by_username=created_by_username)
        _log_import(summary, label, created_by_username)
        return summary

    if ext in (".csv", ".tsv"):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"{label} isn't readable text.") from exc
        try:
            parsed = parse_roastlog_csv(text)
        except RoastlogParseError as exc:
            raise HTTPException(status_code=400, detail=f"{label} doesn't look like a roast log table ({exc}).") from exc
        summary = session_manager.import_table(parsed=parsed, title=title, created_by_username=created_by_username)
        _log_import(summary, label, created_by_username)
        return summary

    # .alog, .json, or an extension-less/unrecognized file -- the native
    # reader accepts either syntax (see load_alog), so try that.
    fd, tmp_path = tempfile.mkstemp(suffix=".alog")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        summary = session_manager.import_alog(tmp_path, title, created_by_username=created_by_username)
        _log_import(summary, label, created_by_username)
        return summary
    except (FileNotFoundError, ValueError, SyntaxError, UnicodeDecodeError) as exc:
        # Our own explanations (e.g. a missing required field) are worth showing;
        # the parser's internals ("malformed node ... <ast.Name object at 0x...>") are not.
        reason = str(exc)
        detail = f"{label} doesn't look like a valid .alog or .json file."
        missing = re.search(r"missing required field ('[^']+')", reason)
        if missing:  # (the throwaway file's path is left out of what the user sees)
            detail = f"{label} doesn't look like a valid .alog file (it is missing the {missing.group(1)} field)."
        raise HTTPException(status_code=400, detail=detail) from exc
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def _log_import(summary: RoastSummary, label: str, username: str) -> None:
    storage.log_activity(
        "roast", "import", username=username, roast_id=summary.id, roast_title=summary.title,
        message=f'Imported "{summary.title}" from {label}',
    )


@router.post("/import-upload", response_model=RoastSummary, status_code=201)
async def import_alog_upload(
    request: Request, filename: Optional[str] = None, title: Optional[str] = None
) -> RoastSummary:
    """Imports a roast log sent from the browser's own computer (the file
    itself as the request body), as opposed to POST /import, which reads a
    path on the server. Accepts .alog, .json, .csv, .tsv and .xlsx -- picked
    by ``filename``'s own extension (see _import_uploaded_bytes).

    The body is the raw file rather than a multipart form -- one file per
    request, no extra dependency. ``filename`` also names the format and
    appears in error messages; it never touches the filesystem."""
    label = os.path.basename(filename) if filename else "The file"
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_ALOG_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"{label} is too large to import.")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_ALOG_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"{label} is too large to import.")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data.strip():
        raise HTTPException(status_code=400, detail=f"{label} is empty.")
    return await run_in_threadpool(
        _import_uploaded_bytes, data, filename=filename, title=title, created_by_username=request.state.user["username"]
    )


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
