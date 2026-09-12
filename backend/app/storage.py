"""Roast metadata persistence (SQLite) + on-disk .alog file layout.

Full per-second profile data lives in each roast's ``.alog`` file (read on
demand); SQLite only indexes the lightweight metadata needed to list and
filter roast history quickly. Swapping this for Postgres later is a
matter of changing the connection + a couple of ``?`` placeholders.
"""
from __future__ import annotations

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
    alog_path TEXT
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
        c.execute(_SCHEMA)
        # Idempotent migration for DBs created before machine_label existed.
        existing_cols = {row[1] for row in c.execute("PRAGMA table_info(roasts)")}
        if "machine_label" not in existing_cols:
            c.execute("ALTER TABLE roasts ADD COLUMN machine_label TEXT")


def alog_path_for(roast_id: str) -> str:
    return str(ROASTS_DIR / f"{roast_id}.alog")


def insert_roast(summary: dict) -> None:
    with _conn() as c:
        c.execute(
            """INSERT INTO roasts
               (id, title, mode, machine_id, machine_label, status, created_at, beans,
                weight_green_g, weight_roasted_g, duration_s, alog_path)
               VALUES (:id, :title, :mode, :machine_id, :machine_label, :status, :created_at, :beans,
                       :weight_green_g, :weight_roasted_g, :duration_s, :alog_path)""",
            summary,
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
