"""Looking across roasts: one row of numbers per roast, totals and spread per
group, and bulk export (a zip of every .alog plus one summary CSV)."""
from __future__ import annotations

import asyncio
import math
import os
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from .. import analysis_insights, ollama_client, storage
from ..roast_metrics import METRIC_KEYS, METRICS, alog_metrics, build_row, row_source
from alog_playback.alog_io import load_alog
from alog_playback.roastlog import roast_to_json, save_roastlog_csv, save_roastlog_xlsx

from ..roast_session import RoastSessionError, session_manager
from .roasts import alog_filename, json_filename, roastlog_csv_filename, xlsx_filename

router = APIRouter(prefix="/analysis", tags=["analysis"])

# A roast counts once it has ended; ones still recording have no finished curve yet.
FINISHED = ("complete", "stopped")
MAX_ROWS = 2000

GROUP_BYS = ("none", "beans", "tag", "month", "roaster", "mode", "source", "origin", "process")

# What is worked out from a roast's .alog only changes when the file does.
_CURVE_CACHE: dict[tuple, dict] = {}


def _curve_metrics(row: dict) -> Optional[dict]:
    path = row.get("alog_path")
    if not path or not os.path.exists(path):
        return None
    stat = os.stat(path)
    key = (row["id"], stat.st_mtime_ns, stat.st_size)
    cached = _CURVE_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        roast = session_manager.get_roast_detail(row["id"])
    except RoastSessionError:
        return None  # unreadable recording: left out, and counted by the caller
    if roast is None:
        return None
    result = alog_metrics(roast)
    # Drop older entries for the same roast so the cache doesn't grow with edits.
    for old in [k for k in _CURVE_CACHE if k[0] == row["id"]]:
        del _CURVE_CACHE[old]
    _CURVE_CACHE[key] = result
    return result


def _rows(
    *,
    mode: Optional[str],
    status: Optional[str],
    tag: Optional[str],
    q: Optional[str],
    created_by: Optional[str],
    created_from: Optional[str],
    created_to: Optional[str],
    bean_id: Optional[str],
    include_simulated: bool,
    include_replays: bool = False,
    source: Optional[str] = None,
) -> dict:
    db_rows = storage.list_roast_rows(
        mode=mode, status=status, tag=tag, q=q, created_by=created_by,
        created_from=created_from, created_to=created_to, bean_id=bean_id,
        limit=100000,
    )
    if not status:
        db_rows = [r for r in db_rows if r["status"] in FINISHED]
    tag_map = storage.get_tags_for_roasts([r["id"] for r in db_rows])
    beans = {b["id"]: b for b in storage.list_beans()}

    rows, excluded, missing = [], 0, 0
    for r in db_rows:
        tags = tag_map.get(r["id"], [])
        if "simulated" in tags and not include_simulated:
            excluded += 1
            continue
        kind = row_source(r)
        if source:
            if kind != source:
                continue
        elif kind == "replay" and not include_replays:
            continue  # a replay just repeats an existing log's data
        curve = _curve_metrics(r)
        if curve is None:
            missing += 1
            continue
        rows.append(build_row(r, curve, beans.get(r.get("bean_id")), tags))
    total = len(rows)
    return {
        "rows": rows[:MAX_ROWS], "total": total, "truncated": total > MAX_ROWS,
        "simulated_excluded": excluded, "missing_recording": missing,
    }


def _filters(
    mode: Optional[str] = None,
    status: Optional[str] = None,
    tag: Optional[str] = None,
    q: Optional[str] = Query(default=None, description="Substring match against title/beans/tags"),
    created_by: Optional[str] = None,
    created_from: Optional[str] = Query(default=None, description="ISO date/time, inclusive"),
    created_to: Optional[str] = Query(default=None, description="ISO date/time, inclusive; a bare date means the whole day"),
    bean_id: Optional[str] = None,
    include_simulated: bool = False,
    include_replays: bool = Query(default=False, description="Also count roasts made by replaying a saved log (they repeat that log's data)"),
    source: Optional[str] = Query(default=None, pattern="^(recorded|uploaded|replay)$", description="Only roasts recorded from a device, uploaded from a log file, or replayed from one"),
) -> dict:
    return dict(
        mode=mode, status=status, tag=tag, q=q, created_by=created_by,
        created_from=created_from, created_to=created_to, bean_id=bean_id,
        include_simulated=include_simulated, include_replays=include_replays, source=source,
    )


@router.get("/metrics")
def list_metrics() -> list[dict]:
    """Every number that can be charted or exported, with its label and unit."""
    return METRICS


@router.get("/roasts/{roast_id}")
def one_roast(roast_id: str) -> dict:
    """The same record as one row of the table, for a single roast (a
    simulated or replayed one too)."""
    row = storage.get_roast_row(roast_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"roast {roast_id!r} not found")
    curve = _curve_metrics(row)
    if curve is None:
        detail = "this roast has no saved recording to work out numbers from yet"
        if row.get("alog_path") and not os.path.exists(row["alog_path"]):
            detail = "the recording file for this roast is missing from the data folder"
        raise HTTPException(status_code=404, detail=detail)
    tags = storage.get_tags_for_roasts([roast_id]).get(roast_id, [])
    bean = storage.get_bean(row["bean_id"]) if row.get("bean_id") else None
    return build_row(row, curve, bean, tags)


@router.get("/table")
def table(filters: dict = Depends(_filters)) -> dict:
    """One row per finished roast: who/what/when plus every metric."""
    return _rows(**filters)


# -- summaries ---------------------------------------------------------------------


def _stats(values: list[float]) -> dict:
    n = len(values)
    if n == 0:
        return {"n": 0, "mean": None, "sd": None, "min": None, "max": None}
    mean = sum(values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1)) if n > 1 else None
    return {
        "n": n,
        "mean": round(mean, 2),
        "sd": None if sd is None else round(sd, 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
    }


def _group_keys(row: dict, group_by: str) -> list[str]:
    if group_by == "none":
        return ["All roasts"]
    if group_by == "tag":
        tags = [t for t in row["tags"] if t != "simulated"]
        return tags or ["(no tag)"]
    if group_by == "month":
        return [row["created_at"][:7]]
    value = row.get(group_by)
    return [value if value else "(none)"]


def _summarise(rows: list[dict], group_by: str) -> list[dict]:
    """Count, average, spread and range of every metric per group, largest group first."""
    buckets: dict[str, list[dict]] = {}
    for row in rows:
        for key in _group_keys(row, group_by):
            buckets.setdefault(key, []).append(row)

    groups = []
    for key, members in buckets.items():
        metrics = {}
        for m in METRIC_KEYS:
            values = [r["metrics"][m] for r in members if r["metrics"].get(m) is not None]
            if values:
                metrics[m] = _stats(values)
        flagged = sum(
            1 for r in members
            if sum((r["metrics"].get(k) or 0) for k in ("ror_crashes", "ror_flatlines", "ror_flicks")) > 0
        )
        groups.append({"key": key, "count": len(members), "flagged_ror": flagged, "metrics": metrics})
    groups.sort(key=lambda g: (-g["count"], g["key"]))
    return groups


@router.get("/summary")
def summary(group_by: str = "none", filters: dict = Depends(_filters)) -> dict:
    """Count, average, spread (standard deviation) and range of every metric,
    per group. Roasts with several tags count once under each tag."""
    if group_by not in GROUP_BYS:
        raise HTTPException(status_code=422, detail=f"group_by must be one of {', '.join(GROUP_BYS)}")
    data = _rows(**filters)
    groups = _summarise(data["rows"], group_by)
    return {
        "group_by": group_by,
        "groups": groups,
        "total": data["total"],
        "truncated": data["truncated"],
        "simulated_excluded": data["simulated_excluded"],
        "missing_recording": data["missing_recording"],
    }


# -- export --------------------------------------------------------------------------

# One writer per extra format the zip can include besides .alog (always
# included). Each takes a loaded Roast and returns (bytes, filename_fn);
# needs a live Roast (profile/events), not just the database row, so these
# cost one .alog read per roast per format asked for -- fine for the "tick a
# couple of extra formats" case this is for, not meant for every format on a
# large history at once.
def _zip_json_bytes(roast, row) -> bytes:
    return roast_to_json(load_alog(row["alog_path"])).encode("utf-8")


def _zip_roastlog_csv_bytes(roast, row, unit: str) -> bytes:
    return save_roastlog_csv(
        profile=[p.model_dump() for p in roast.profile], events=[e.model_dump() for e in roast.events], temperature_unit=unit
    ).encode("utf-8")


def _zip_xlsx_bytes(roast, row, unit: str) -> bytes:
    return save_roastlog_xlsx(
        profile=[p.model_dump() for p in roast.profile], events=[e.model_dump() for e in roast.events], temperature_unit=unit
    )


_EXTRA_FORMATS = {
    "json": ("json", json_filename, lambda roast, row, unit: _zip_json_bytes(roast, row)),
    "roastlog_csv": ("roastlog_csv", roastlog_csv_filename, _zip_roastlog_csv_bytes),
    "xlsx": ("xlsx", xlsx_filename, _zip_xlsx_bytes),
}


@router.get("/export.zip")
def export_zip(
    formats: str = Query(default="", description="Extra formats to include besides .alog: json, roastlog_csv, xlsx (comma-separated)"),
    filters: dict = Depends(_filters),
) -> FileResponse:
    """Every matching roast's .alog file, in one zip. `formats` adds a
    matching folder for each of json/roastlog_csv/xlsx too."""
    extra = [f.strip() for f in formats.split(",") if f.strip()]
    unknown = [f for f in extra if f not in _EXTRA_FORMATS]
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown format(s): {', '.join(unknown)} -- choose from {', '.join(_EXTRA_FORMATS)}")
    data = _rows(**filters)
    if not data["rows"]:
        raise HTTPException(status_code=404, detail="no roasts match those filters")
    unit = storage.get_settings().get("temperature_unit") or "c"
    handle = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    handle.close()
    try:
        with zipfile.ZipFile(handle.name, "w", zipfile.ZIP_DEFLATED) as zf:
            used: dict[str, set[str]] = {folder: set() for folder in ["alog", *extra]}
            for r in data["rows"]:
                row = storage.get_roast_row(r["id"])
                if not row or not row.get("alog_path") or not os.path.exists(row["alog_path"]):
                    continue
                name = alog_filename(r["title"], r["created_at"])
                if name in used["alog"]:
                    name = name[: -len(".alog")] + f"_{r['id'][:8]}.alog"
                used["alog"].add(name)
                zf.write(row["alog_path"], f"alog/{name}")

                if not extra:
                    continue
                roast = session_manager.get_roast_detail(r["id"])
                if roast is None:
                    continue
                for key in extra:
                    folder, filename_fn, writer = _EXTRA_FORMATS[key]
                    try:
                        content = writer(roast, row, unit)
                    except Exception:  # noqa: BLE001 -- one roast's extra-format hiccup shouldn't drop the whole export
                        continue
                    fname = filename_fn(r["title"], r["created_at"])
                    if fname in used[folder]:
                        stem, dot, ext = fname.rpartition(".")
                        fname = f"{stem}_{r['id'][:8]}.{ext}" if dot else f"{fname}_{r['id'][:8]}"
                    used[folder].add(fname)
                    zf.writestr(f"{folder}/{fname}", content)
    except Exception:
        os.unlink(handle.name)
        raise
    return FileResponse(
        handle.name,
        media_type="application/zip",
        filename="roast-telemetry-export.zip",
        background=BackgroundTask(os.unlink, handle.name),
    )


# -- AI insights (only when Ollama is set up) ---------------------------------------

# Finished and running jobs, newest last. Kept in memory: a comment on a set of
# roasts is cheap to ask for again, so nothing here needs to survive a restart.
_INSIGHT_JOBS: dict[str, dict] = {}
_MAX_JOBS = 20


class InsightRequest(BaseModel):
    group_by: str = "none"
    question: Optional[str] = Field(default=None, max_length=500)
    mode: Optional[str] = None
    status: Optional[str] = None
    tag: Optional[str] = None
    q: Optional[str] = None
    created_by: Optional[str] = None
    created_from: Optional[str] = None
    created_to: Optional[str] = None
    bean_id: Optional[str] = None
    include_simulated: bool = False
    include_replays: bool = False
    source: Optional[str] = Field(default=None, pattern="^(recorded|uploaded|replay)$")


def _describe_filters(req: InsightRequest) -> str:
    parts = []
    for label, value in (
        ("search", req.q), ("tag", req.tag), ("source", req.source), ("from", req.created_from), ("to", req.created_to),
    ):
        if value:
            parts.append(f"{label} = {value}")
    if req.bean_id:
        bean = storage.get_bean(req.bean_id)
        parts.append(f"beans = {bean['name'] if bean else req.bean_id}")
    return "; ".join(parts)


async def _run_insight(job_id: str, req: InsightRequest, url: str, model: str) -> None:
    job = _INSIGHT_JOBS[job_id]
    try:
        filters = req.model_dump(exclude={"group_by", "question"})
        # Reading every roast's file is blocking work, so keep it off the event loop.
        data = await asyncio.to_thread(_rows, **filters)
        if not data["rows"]:
            raise ValueError("no finished roasts match those filters")
        bucketed = await asyncio.to_thread(_summarise, data["rows"], req.group_by)
        prompt = analysis_insights.build_prompt(
            groups=bucketed, rows=data["rows"], group_by=req.group_by, total=data["total"],
            truncated=data["truncated"], filters_text=_describe_filters(req), question=req.question,
        )
        text = await ollama_client.generate(url, model, prompt, options={"num_ctx": analysis_insights.NUM_CTX})
        job.update(status="ready", text=text, completed_at=datetime.now(timezone.utc).isoformat())
    except Exception as exc:  # noqa: BLE001 -- any failure lands as a visible "failed" result
        job.update(status="failed", error=str(exc) or exc.__class__.__name__, completed_at=datetime.now(timezone.utc).isoformat())


@router.post("/insights", status_code=202)
async def request_insight(req: InsightRequest) -> dict:
    """Asks the configured Ollama model to comment on the roasts these filters
    match. Returns at once with a job id to poll (a local model can take a
    minute or two)."""
    if req.group_by not in GROUP_BYS:
        raise HTTPException(status_code=422, detail=f"group_by must be one of {', '.join(GROUP_BYS)}")
    settings = storage.get_settings()
    url, model = settings.get("ollama_url"), settings.get("ollama_model")
    if not url or not model:
        raise HTTPException(status_code=400, detail="Ollama isn't configured -- set a URL and model in Settings")

    job_id = str(uuid.uuid4())
    _INSIGHT_JOBS[job_id] = {
        "id": job_id, "status": "pending", "text": None, "error": None, "model": model,
        "created_at": datetime.now(timezone.utc).isoformat(), "completed_at": None,
    }
    while len(_INSIGHT_JOBS) > _MAX_JOBS:
        del _INSIGHT_JOBS[next(iter(_INSIGHT_JOBS))]
    asyncio.get_running_loop().create_task(_run_insight(job_id, req, url, model))
    return _INSIGHT_JOBS[job_id]


@router.get("/insights/{job_id}")
def get_insight(job_id: str) -> dict:
    job = _INSIGHT_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="that analysis is no longer available -- ask again")
    return job
