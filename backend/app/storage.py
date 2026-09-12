"""Roast metadata persistence (SQLite) + on-disk .alog file layout.

Full per-second profile data lives in each roast's ``.alog`` file (read on
demand); SQLite only indexes the lightweight metadata needed to list and
filter roast history quickly. Swapping this for Postgres later is a
matter of changing the connection + a couple of ``?`` placeholders.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ROASTS_DIR = DATA_DIR / "roasts"
SAMPLE_ROASTS_DIR = DATA_DIR / "sample_roasts"
DB_PATH = DATA_DIR / "roasts.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
ROASTS_DIR.mkdir(parents=True, exist_ok=True)
SAMPLE_ROASTS_DIR.mkdir(parents=True, exist_ok=True)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS roasts (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    mode TEXT NOT NULL,
    machine_id TEXT,
    machine_label TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    beans TEXT,
    weight_green_g REAL,
    weight_roasted_g REAL,
    duration_s REAL,
    alog_path TEXT,
    source_alog_path TEXT,
    playback_speed REAL
);

CREATE TABLE IF NOT EXISTS roast_presets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    config_json TEXT NOT NULL,
    heater_pct REAL,
    fan_pct REAL,
    drum_speed_pct REAL
);

-- Plain key/value store, currently just Ollama's base URL + model name.
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- One row per roast (REPLACE-d wholesale on regenerate, no history kept --
-- the card only ever shows the latest attempt).
CREATE TABLE IF NOT EXISTS roast_reviews (
    roast_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    review_text TEXT,
    error TEXT,
    model TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
"""


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as c:
        c.executescript(_SCHEMA)
        # Idempotent migration for DBs created before machine_label existed.
        existing_cols = {row[1] for row in c.execute("PRAGMA table_info(roasts)")}
        if "machine_label" not in existing_cols:
            c.execute("ALTER TABLE roasts ADD COLUMN machine_label TEXT")
        if "source_alog_path" not in existing_cols:
            c.execute("ALTER TABLE roasts ADD COLUMN source_alog_path TEXT")
        if "playback_speed" not in existing_cols:
            c.execute("ALTER TABLE roasts ADD COLUMN playback_speed REAL")
        # Idempotent migration for DBs created before roast_presets carried
        # control-channel starting values.
        preset_cols = {row[1] for row in c.execute("PRAGMA table_info(roast_presets)")}
        for col in ("heater_pct", "fan_pct", "drum_speed_pct"):
            if col not in preset_cols:
                c.execute(f"ALTER TABLE roast_presets ADD COLUMN {col} REAL")


def alog_path_for(roast_id: str) -> str:
    return str(ROASTS_DIR / f"{roast_id}.alog")


def insert_roast(summary: dict) -> None:
    with _conn() as c:
        c.execute(
            """INSERT INTO roasts
               (id, title, mode, machine_id, machine_label, status, created_at, beans,
                weight_green_g, weight_roasted_g, duration_s, alog_path, source_alog_path, playback_speed)
               VALUES (:id, :title, :mode, :machine_id, :machine_label, :status, :created_at, :beans,
                       :weight_green_g, :weight_roasted_g, :duration_s, :alog_path, :source_alog_path,
                       :playback_speed)""",
            {"source_alog_path": None, "playback_speed": None, **summary},
        )


def update_roast(roast_id: str, **fields) -> None:
    if not fields:
        return
    with _conn() as c:
        set_clause = ", ".join(f"{k} = :{k}" for k in fields)
        c.execute(f"UPDATE roasts SET {set_clause} WHERE id = :id", {**fields, "id": roast_id})


def get_roast_row(roast_id: str) -> Optional[dict]:
    with _conn() as c:
        row = c.execute("SELECT * FROM roasts WHERE id = ?", (roast_id,)).fetchone()
        return dict(row) if row else None


def list_roast_rows(
    *,
    mode: Optional[str] = None,
    status: Optional[str] = None,
    machine_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    clauses, params = [], {}
    if mode:
        clauses.append("mode = :mode")
        params["mode"] = mode
    if status:
        clauses.append("status = :status")
        params["status"] = status
    if machine_id:
        clauses.append("machine_id = :machine_id")
        params["machine_id"] = machine_id
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params["limit"] = limit
    params["offset"] = offset
    with _conn() as c:
        rows = c.execute(
            f"SELECT * FROM roasts {where} ORDER BY created_at DESC LIMIT :limit OFFSET :offset",
            params,
        ).fetchall()
        return [dict(r) for r in rows]


def delete_roast_row(roast_id: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM roasts WHERE id = ?", (roast_id,))


def abort_stale_roasts() -> list[str]:
    """Every roast's in-memory session (RoastSessionManager.sessions) is
    wiped on process restart, but its DB row's status isn't -- without
    this, a roast that was mid-flight when the backend restarted stays
    stuck showing "roasting"/"cooling" in history forever: unreachable
    (no session) and un-stoppable (no session to abort), since a
    restarted process can never resume the actual hardware connection
    anyway. Called once at startup; returns the ids it cleaned up."""
    with _conn() as c:
        rows = c.execute("SELECT id FROM roasts WHERE status IN ('roasting', 'cooling')").fetchall()
        ids = [r[0] for r in rows]
        if ids:
            c.execute(
                f"UPDATE roasts SET status = 'aborted' WHERE id IN ({','.join('?' * len(ids))})",
                ids,
            )
        return ids


def insert_preset(preset: dict) -> None:
    with _conn() as c:
        c.execute(
            """INSERT INTO roast_presets (id, name, created_at, config_json, heater_pct, fan_pct, drum_speed_pct)
               VALUES (:id, :name, :created_at, :config_json, :heater_pct, :fan_pct, :drum_speed_pct)""",
            {"heater_pct": None, "fan_pct": None, "drum_speed_pct": None, **preset},
        )


def update_preset_row(preset_id: str, preset: dict) -> None:
    with _conn() as c:
        c.execute(
            """UPDATE roast_presets
               SET name = :name, config_json = :config_json,
                   heater_pct = :heater_pct, fan_pct = :fan_pct, drum_speed_pct = :drum_speed_pct
               WHERE id = :id""",
            {"heater_pct": None, "fan_pct": None, "drum_speed_pct": None, **preset, "id": preset_id},
        )


def list_preset_rows() -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM roast_presets ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def get_preset_row(preset_id: str) -> Optional[dict]:
    with _conn() as c:
        row = c.execute("SELECT * FROM roast_presets WHERE id = ?", (preset_id,)).fetchone()
        return dict(row) if row else None


def delete_preset_row(preset_id: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM roast_presets WHERE id = ?", (preset_id,))


def get_settings() -> dict:
    with _conn() as c:
        rows = c.execute("SELECT key, value FROM settings").fetchall()
        values = {r["key"]: r["value"] for r in rows}
    try:
        broken_out_panels = json.loads(values["broken_out_panels"]) if values.get("broken_out_panels") else []
    except (json.JSONDecodeError, TypeError):
        broken_out_panels = []
    return {
        "ollama_url": values.get("ollama_url"),
        "ollama_model": values.get("ollama_model"),
        "broken_out_panels": broken_out_panels,
    }


def set_settings(**kv) -> None:
    if "broken_out_panels" in kv:
        kv["broken_out_panels"] = json.dumps(kv["broken_out_panels"])
    with _conn() as c:
        for key, value in kv.items():
            c.execute(
                "INSERT INTO settings (key, value) VALUES (:key, :value) "
                "ON CONFLICT(key) DO UPDATE SET value = :value",
                {"key": key, "value": value},
            )


def upsert_review_row(review: dict) -> None:
    with _conn() as c:
        c.execute(
            """INSERT INTO roast_reviews (roast_id, status, review_text, error, model, created_at, completed_at)
               VALUES (:roast_id, :status, :review_text, :error, :model, :created_at, :completed_at)
               ON CONFLICT(roast_id) DO UPDATE SET
                   status = :status, review_text = :review_text, error = :error,
                   model = :model, created_at = :created_at, completed_at = :completed_at""",
            {"review_text": None, "error": None, "model": None, "completed_at": None, **review},
        )


def get_review_row(roast_id: str) -> Optional[dict]:
    with _conn() as c:
        row = c.execute("SELECT * FROM roast_reviews WHERE roast_id = ?", (roast_id,)).fetchone()
        return dict(row) if row else None
