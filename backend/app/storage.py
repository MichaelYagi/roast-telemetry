"""Roast metadata persistence (SQLite) + on-disk .alog file layout.

Full per-second profile data lives in each roast's ``.alog`` file (read on
demand); SQLite only indexes the lightweight metadata needed to list and
filter roast history quickly. Swapping this for Postgres later is a
matter of changing the connection + a couple of ``?`` placeholders.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

# Defaults to the source-tree-relative path every dev/CI usage has
# always used (zero behavior change there -- this env var is never set
# in any of those). Only a packaged desktop build (see packaging/, and
# scripts/tray_app.py's own frozen-mode setup) sets it, to point at a
# real per-user writable directory instead of a path relative to
# wherever this file happens to be unpacked inside a frozen bundle,
# which may not even be writable (e.g. Program Files).
DATA_DIR = Path(os.environ["ROAST_TELEMETRY_DATA_DIR"]) if os.environ.get("ROAST_TELEMETRY_DATA_DIR") else (
    Path(__file__).resolve().parent.parent / "data"
)
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
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    beans TEXT,
    weight_green_g REAL,
    weight_roasted_g REAL,
    duration_s REAL,
    alog_path TEXT,
    source_alog_path TEXT,
    playback_speed REAL,
    created_by_username TEXT,
    modbus_transport TEXT,
    modbus_port TEXT,
    modbus_host TEXT,
    modbus_tcp_port INTEGER,
    modbus_device_profile_name TEXT,
    ms6514_port TEXT,
    aillio_model TEXT
);

CREATE TABLE IF NOT EXISTS roast_presets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    config_json TEXT NOT NULL,
    heater_pct REAL,
    fan_pct REAL,
    drum_speed_pct REAL,
    manufacturer TEXT,
    built_in INTEGER NOT NULL DEFAULT 0
);

-- A named Modbus register map (DeviceProfile) for one roaster brand/
-- model -- see backend/app/api/device_profiles.py. built_in=1 rows ship
-- with the app (seeded once, see seed_default_device_profiles below)
-- and the API refuses to PUT/DELETE them.
CREATE TABLE IF NOT EXISTS device_profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    config_json TEXT NOT NULL,
    built_in INTEGER NOT NULL DEFAULT 0
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

-- The first row ever inserted (by created_at) is the admin, auto-allowed;
-- every account after that starts 'pending' (see api/auth.py's register()).
-- api_key_hash is NULL until the user generates one (see api/auth.py's
-- POST/DELETE /auth/api-key) -- a hash, never the plaintext key itself,
-- same reasoning as password_hash. Uniqueness is a separate index below
-- (not an inline UNIQUE here) since SQLite's ALTER TABLE ADD COLUMN --
-- needed for the idempotent migration path, see init_db() -- can't add a
-- UNIQUE column after the fact, only a plain one.
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    api_key_hash TEXT
);

-- An opaque bearer token in an httponly cookie (see auth.py), not a JWT --
-- deleting a row here immediately revokes that one session, e.g. on
-- logout or when an admin deletes the account it belongs to.
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);

-- A normalized join table, not a comma-separated column on roasts --
-- needed for real WHERE tag = ? filtering and a distinct-tags listing
-- (see list_distinct_tags below), neither of which works cleanly
-- against a packed string column. No ALTER TABLE migration needed for
-- this one, unlike a new column on an existing table -- CREATE TABLE IF
-- NOT EXISTS is already idempotent on its own.
CREATE TABLE IF NOT EXISTS roast_tags (
    roast_id TEXT NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (roast_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_roast_tags_tag ON roast_tags(tag);
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
        # Idempotent migrations for DBs created before these columns
        # existed. machine_id/machine_label were dropped entirely (no
        # migration needed for those -- an existing DB just keeps its own
        # now-unused columns, harmless leftovers, nothing reads or writes
        # them anymore).
        existing_cols = {row[1] for row in c.execute("PRAGMA table_info(roasts)")}
        if "source_alog_path" not in existing_cols:
            c.execute("ALTER TABLE roasts ADD COLUMN source_alog_path TEXT")
        if "playback_speed" not in existing_cols:
            c.execute("ALTER TABLE roasts ADD COLUMN playback_speed REAL")
        # Username, not user_id -- usernames are immutable (no rename
        # endpoint exists) and this is meant as a permanent historical
        # record, so it should survive the account itself being deleted
        # later rather than going stale/dangling like a foreign key would.
        # NULL for every roast created before this column existed, and for
        # any deleted-user's old roasts if that matters someday -- both
        # read back as "no attribution", not an error.
        if "created_by_username" not in existing_cols:
            c.execute("ALTER TABLE roasts ADD COLUMN created_by_username TEXT")
        # Idempotent migration for DBs created before roasts carried what
        # they were actually connected with (modbus_live only -- see
        # RoastSession.__init__/summary()).
        if "modbus_transport" not in existing_cols:
            c.execute("ALTER TABLE roasts ADD COLUMN modbus_transport TEXT")
        if "modbus_port" not in existing_cols:
            c.execute("ALTER TABLE roasts ADD COLUMN modbus_port TEXT")
        if "modbus_host" not in existing_cols:
            c.execute("ALTER TABLE roasts ADD COLUMN modbus_host TEXT")
        if "modbus_tcp_port" not in existing_cols:
            c.execute("ALTER TABLE roasts ADD COLUMN modbus_tcp_port INTEGER")
        if "modbus_device_profile_name" not in existing_cols:
            c.execute("ALTER TABLE roasts ADD COLUMN modbus_device_profile_name TEXT")
        if "ms6514_port" not in existing_cols:
            c.execute("ALTER TABLE roasts ADD COLUMN ms6514_port TEXT")
        if "aillio_model" not in existing_cols:
            c.execute("ALTER TABLE roasts ADD COLUMN aillio_model TEXT")
        # Idempotent migration for DBs created before roast_presets carried
        # control-channel starting values.
        preset_cols = {row[1] for row in c.execute("PRAGMA table_info(roast_presets)")}
        for col in ("heater_pct", "fan_pct", "drum_speed_pct"):
            if col not in preset_cols:
                c.execute(f"ALTER TABLE roast_presets ADD COLUMN {col} REAL")
        # Idempotent migration for DBs created before roast_presets carried
        # manufacturer grouping / built-in-and-undeletable support (see
        # api/presets.py, DeviceProfile.built_in's own precedent).
        if "manufacturer" not in preset_cols:
            c.execute("ALTER TABLE roast_presets ADD COLUMN manufacturer TEXT")
        if "built_in" not in preset_cols:
            c.execute("ALTER TABLE roast_presets ADD COLUMN built_in INTEGER NOT NULL DEFAULT 0")
        # Idempotent migration for DBs created before users carried an API
        # key. The uniqueness index has to be created here, after the
        # column definitely exists (ADD COLUMN can't itself carry UNIQUE --
        # see the users table's own comment), rather than inside _SCHEMA
        # above, which only runs the bare CREATE TABLE on an existing DB.
        user_cols = {row[1] for row in c.execute("PRAGMA table_info(users)")}
        if "api_key_hash" not in user_cols:
            c.execute("ALTER TABLE users ADD COLUMN api_key_hash TEXT")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_api_key_hash ON users(api_key_hash)")


def alog_path_for(roast_id: str) -> str:
    return str(ROASTS_DIR / f"{roast_id}.alog")


def insert_roast(summary: dict) -> None:
    with _conn() as c:
        c.execute(
            """INSERT INTO roasts
               (id, title, mode, status, created_at, beans,
                weight_green_g, weight_roasted_g, duration_s, alog_path, source_alog_path, playback_speed,
                created_by_username, modbus_transport, modbus_port, modbus_host, modbus_tcp_port,
                modbus_device_profile_name, ms6514_port, aillio_model)
               VALUES (:id, :title, :mode, :status, :created_at, :beans,
                       :weight_green_g, :weight_roasted_g, :duration_s, :alog_path, :source_alog_path,
                       :playback_speed, :created_by_username, :modbus_transport, :modbus_port, :modbus_host,
                       :modbus_tcp_port, :modbus_device_profile_name, :ms6514_port, :aillio_model)""",
            {
                "source_alog_path": None, "playback_speed": None, "created_by_username": None,
                "modbus_transport": None, "modbus_port": None, "modbus_host": None, "modbus_tcp_port": None,
                "modbus_device_profile_name": None, "ms6514_port": None, "aillio_model": None, **summary,
            },
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


def _roast_filter_clauses(
    *,
    mode: Optional[str],
    status: Optional[str],
    tag: Optional[str],
    q: Optional[str],
    created_by: Optional[str] = None,
) -> tuple[list[str], dict]:
    """Shared between list_roast_rows and count_roast_rows -- the page
    of results and the total count behind it must always agree on
    exactly which rows match, so this is one place to keep in sync
    rather than two copies of the same WHERE logic drifting apart."""
    clauses, params = [], {}
    if mode:
        clauses.append("r.mode = :mode")
        params["mode"] = mode
    if status:
        clauses.append("r.status = :status")
        params["status"] = status
    if tag:
        clauses.append("t.tag = :tag")
        params["tag"] = tag
    if created_by:
        clauses.append("r.created_by_username = :created_by")
        params["created_by"] = created_by
    if q:
        # title/beans/tags only -- per-timestamp roast notes live inside
        # each roast's own .alog file, not a DB column, so searching
        # those would mean loading every .alog on every search. Not
        # worth it for this table's realistic scale (hundreds to
        # low-thousands of rows) -- plain LIKE, not FTS5, for the same
        # reason: no virtual-table/tokenizer setup earned at this size.
        clauses.append("(r.title LIKE :q OR r.beans LIKE :q OR t.tag LIKE :q)")
        params["q"] = f"%{q}%"
    return clauses, params


def list_roast_rows(
    *,
    mode: Optional[str] = None,
    status: Optional[str] = None,
    tag: Optional[str] = None,
    q: Optional[str] = None,
    created_by: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    clauses, params = _roast_filter_clauses(mode=mode, status=status, tag=tag, q=q, created_by=created_by)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params["limit"] = limit
    params["offset"] = offset
    with _conn() as c:
        rows = c.execute(
            f"""SELECT DISTINCT r.* FROM roasts r
                LEFT JOIN roast_tags t ON t.roast_id = r.id
                {where}
                ORDER BY r.created_at DESC LIMIT :limit OFFSET :offset""",
            params,
        ).fetchall()
        return [dict(r) for r in rows]


def count_roast_rows(
    *,
    mode: Optional[str] = None,
    status: Optional[str] = None,
    tag: Optional[str] = None,
    q: Optional[str] = None,
    created_by: Optional[str] = None,
) -> int:
    """Total matching rows regardless of limit/offset -- backs
    HistoryDashboard.jsx's page count, since GET /roasts itself stays a
    plain array (several other callers -- BackgroundProfilePicker.jsx,
    RoastComparisonView.jsx, LiveRoastView.jsx's active-roast check --
    already depend on that exact shape and would break if it became
    {items, total})."""
    clauses, params = _roast_filter_clauses(mode=mode, status=status, tag=tag, q=q, created_by=created_by)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _conn() as c:
        row = c.execute(
            f"""SELECT COUNT(DISTINCT r.id) FROM roasts r
                LEFT JOIN roast_tags t ON t.roast_id = r.id
                {where}""",
            params,
        ).fetchone()
        return row[0]


def list_distinct_roasters() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            """SELECT created_by_username, COUNT(*) as count FROM roasts
               WHERE created_by_username IS NOT NULL
               GROUP BY created_by_username ORDER BY count DESC, created_by_username ASC"""
        ).fetchall()
        return [dict(r) for r in rows]


def delete_roast_row(roast_id: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM roasts WHERE id = ?", (roast_id,))
        c.execute("DELETE FROM roast_tags WHERE roast_id = ?", (roast_id,))


def set_roast_tags(roast_id: str, tags: list[str]) -> None:
    """Replace-the-whole-set semantics, same 'load full, save full'
    precedent as AppSettings fields -- but scoped to one roast, so
    there's no cross-field clobbering risk the settings PUT landmine
    had (see chart_series_visible's own history)."""
    with _conn() as c:
        c.execute("DELETE FROM roast_tags WHERE roast_id = ?", (roast_id,))
        c.executemany(
            "INSERT INTO roast_tags (roast_id, tag) VALUES (?, ?)",
            [(roast_id, tag) for tag in dict.fromkeys(tags)],  # de-dupe, preserve order
        )


def get_tags_for_roasts(roast_ids: list[str]) -> dict[str, list[str]]:
    """One batched query (WHERE roast_id IN (...)), not N+1 -- used both
    for listing many roasts at once and for a single cold roast read
    (pass a one-element list)."""
    if not roast_ids:
        return {}
    with _conn() as c:
        placeholders = ",".join("?" * len(roast_ids))
        rows = c.execute(
            f"SELECT roast_id, tag FROM roast_tags WHERE roast_id IN ({placeholders}) ORDER BY tag",
            roast_ids,
        ).fetchall()
    result: dict[str, list[str]] = {}
    for row in rows:
        result.setdefault(row["roast_id"], []).append(row["tag"])
    return result


def list_distinct_tags() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT tag, COUNT(*) as count FROM roast_tags GROUP BY tag ORDER BY count DESC, tag ASC"
        ).fetchall()
        return [dict(r) for r in rows]


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


def seed_default_presets(seeds: list[dict]) -> None:
    """Ships a couple of ready-to-use starter presets (see main.py's
    lifespan for what) instead of an empty "Load saved config" dropdown on
    first run. Runs on *every* startup but only actually inserts a given
    preset once ever -- tracked per preset id via its own marker row in
    `settings` (not "does a row with this id still exist"), so deleting a
    seeded preset stays deleted; this won't silently resurrect it just
    because its own row is gone next time the app starts. Per-id (not one
    marker for the whole batch) so adding a *new* default preset in a
    later version still gets seeded for existing installs that already
    have the marker for earlier ones. Also re-syncs built_in/manufacturer
    on already-seeded rows every startup (see the loop below) -- both are
    always app-managed (the API blocks editing a built_in row), so an
    install that seeded a preset before those columns existed gets them
    backfilled instead of staying stuck at built_in=0/manufacturer=NULL
    forever."""
    with _conn() as c:
        for preset in seeds:
            marker_key = f"seeded_default_preset:{preset['id']}"
            already_seeded = c.execute("SELECT 1 FROM settings WHERE key = ?", (marker_key,)).fetchone()
            if already_seeded is None:
                # OR IGNORE, not a plain INSERT: an earlier version of this
                # function tracked seeding with one marker for the whole
                # batch, so on an install that already ran that version,
                # this preset's row can already exist even though its own
                # new per-id marker doesn't -- a plain INSERT there would
                # hit the existing primary key and crash startup.
                c.execute(
                    """INSERT OR IGNORE INTO roast_presets
                       (id, name, created_at, config_json, heater_pct, fan_pct, drum_speed_pct, manufacturer, built_in)
                       VALUES (:id, :name, :created_at, :config_json, :heater_pct, :fan_pct, :drum_speed_pct, :manufacturer, :built_in)""",
                    {"heater_pct": None, "fan_pct": None, "drum_speed_pct": None, "manufacturer": None, "built_in": 1, **preset},
                )
                c.execute("INSERT INTO settings (key, value) VALUES (:key, :value)", {"key": marker_key, "value": "1"})
                continue
            # Already seeded (marker exists) -- but built_in/manufacturer
            # were added to this table after the very first default
            # presets shipped, so an install that seeded e.g.
            # "default-fz94-usb" before then still has built_in=0,
            # manufacturer=NULL on that row forever, since the block
            # above never runs again for it. These two fields are always
            # app-managed (the API blocks PUT on a built_in row, so a
            # user could never have set them to anything else), so it's
            # safe to keep them in sync with the current seed definition
            # on every startup -- WHERE id = ... makes this a no-op if
            # the row doesn't exist (e.g. deleted before built_in
            # protection existed), which correctly leaves it deleted
            # rather than resurrecting it.
            c.execute(
                "UPDATE roast_presets SET built_in = :built_in, manufacturer = :manufacturer WHERE id = :id",
                {"built_in": 1, "manufacturer": preset.get("manufacturer"), "id": preset["id"]},
            )


def insert_preset(preset: dict) -> None:
    with _conn() as c:
        c.execute(
            """INSERT INTO roast_presets (id, name, created_at, config_json, heater_pct, fan_pct, drum_speed_pct, manufacturer, built_in)
               VALUES (:id, :name, :created_at, :config_json, :heater_pct, :fan_pct, :drum_speed_pct, :manufacturer, 0)""",
            {"heater_pct": None, "fan_pct": None, "drum_speed_pct": None, "manufacturer": None, **preset},
        )


def update_preset_row(preset_id: str, preset: dict) -> None:
    with _conn() as c:
        c.execute(
            """UPDATE roast_presets
               SET name = :name, config_json = :config_json,
                   heater_pct = :heater_pct, fan_pct = :fan_pct, drum_speed_pct = :drum_speed_pct,
                   manufacturer = :manufacturer
               WHERE id = :id""",
            {"heater_pct": None, "fan_pct": None, "drum_speed_pct": None, "manufacturer": None, **preset, "id": preset_id},
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


def seed_default_device_profiles(seeds: list[dict]) -> None:
    """Same per-id marker-row idempotence as seed_default_presets above --
    see that docstring for the full rationale. Ships the built-in FZ-94
    profile (see modbus_bridge/device_profiles.py) once; deleting it
    stays deleted rather than resurrecting on the next startup. (Not
    that the API actually allows deleting a built_in=1 row today -- this
    still matters if that ever changes, or for a future built-in profile
    added after this one.)"""
    with _conn() as c:
        for profile in seeds:
            marker_key = f"seeded_default_device_profile:{profile['id']}"
            already_seeded = c.execute("SELECT 1 FROM settings WHERE key = ?", (marker_key,)).fetchone()
            if already_seeded is not None:
                continue
            c.execute(
                """INSERT OR IGNORE INTO device_profiles (id, name, created_at, config_json, built_in)
                   VALUES (:id, :name, :created_at, :config_json, :built_in)""",
                {"built_in": 1, **profile},
            )
            c.execute("INSERT INTO settings (key, value) VALUES (:key, :value)", {"key": marker_key, "value": "1"})


def insert_device_profile(profile: dict) -> None:
    with _conn() as c:
        c.execute(
            """INSERT INTO device_profiles (id, name, created_at, config_json, built_in)
               VALUES (:id, :name, :created_at, :config_json, :built_in)""",
            {"built_in": 0, **profile},
        )


def update_device_profile_row(profile_id: str, profile: dict) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE device_profiles SET name = :name, config_json = :config_json WHERE id = :id",
            {**profile, "id": profile_id},
        )


def list_device_profile_rows() -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM device_profiles ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def get_device_profile_row(profile_id: str) -> Optional[dict]:
    with _conn() as c:
        row = c.execute("SELECT * FROM device_profiles WHERE id = ?", (profile_id,)).fetchone()
        return dict(row) if row else None


def delete_device_profile_row(profile_id: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM device_profiles WHERE id = ?", (profile_id,))


def get_settings() -> dict:
    with _conn() as c:
        rows = c.execute("SELECT key, value FROM settings").fetchall()
        values = {r["key"]: r["value"] for r in rows}
    try:
        broken_out_panels = json.loads(values["broken_out_panels"]) if values.get("broken_out_panels") else []
    except (json.JSONDecodeError, TypeError):
        broken_out_panels = []
    try:
        breakout_panel_colors = (
            json.loads(values["breakout_panel_colors"]) if values.get("breakout_panel_colors") else {}
        )
    except (json.JSONDecodeError, TypeError):
        breakout_panel_colors = {}
    try:
        # Falls back to the fixed ET/BT/DT/deltaBT legend this replaced,
        # but only when the key was truly never saved (values.get returns
        # None, not the string "[]" json.dumps([]) would have produced) --
        # an install that explicitly saves an empty Small Readout list
        # gets to keep it empty on the next load, not have this default
        # forced back.
        small_readout_panels = (
            json.loads(values["small_readout_panels"])
            if values.get("small_readout_panels")
            else ["et", "bt", "dt", "ror_bt"]
        )
    except (json.JSONDecodeError, TypeError):
        small_readout_panels = ["et", "bt", "dt", "ror_bt"]
    try:
        # Same never-saved-vs-explicitly-set distinction as
        # small_readout_panels above -- seeds to what the old always-on
        # Controls panel showed (Drum, Fan, Burner, each siloed) only the
        # first time; an install that explicitly saves a different layout
        # keeps it.
        vertical_control_layout = (
            json.loads(values["vertical_control_layout"])
            if values.get("vertical_control_layout")
            else [["drum_speed_pct"], ["fan_pct"], ["heater_pct"]]
        )
    except (json.JSONDecodeError, TypeError):
        vertical_control_layout = [["drum_speed_pct"], ["fan_pct"], ["heater_pct"]]
    try:
        vertical_control_arrows = (
            json.loads(values["vertical_control_arrows"]) if values.get("vertical_control_arrows") else {}
        )
        # Migrates a pre-existing row saved back when this was a plain
        # bool (arrows shown or not, always stepping by a hardcoded 1,
        # not the per-channel step size it is now -- see AppSettings'
        # own comment) -- True becomes step=1 (arrows were on, so this
        # keeps them on at the step they always actually used), False is
        # dropped entirely (same as never having been set, both mean
        # off). Explicit here rather than leaning on Pydantic's own
        # bool-to-float coercion for a dict field, which isn't something
        # worth trusting to stay consistent across versions.
        vertical_control_arrows = {
            k: (1.0 if v is True else v) for k, v in vertical_control_arrows.items() if v is not False
        }
    except (json.JSONDecodeError, TypeError):
        vertical_control_arrows = {}
    try:
        chart_series_visible = (
            json.loads(values["chart_series_visible"]) if values.get("chart_series_visible") else {}
        )
    except (json.JSONDecodeError, TypeError):
        chart_series_visible = {}
    try:
        history_page_size = int(values["history_page_size"]) if values.get("history_page_size") else 100
    except (TypeError, ValueError):
        history_page_size = 100
    return {
        "ollama_url": values.get("ollama_url"),
        "ollama_model": values.get("ollama_model"),
        "broken_out_panels": broken_out_panels,
        "breakout_panel_colors": breakout_panel_colors,
        "small_readout_panels": small_readout_panels,
        "temperature_unit": values.get("temperature_unit") or "c",
        "vertical_control_layout": vertical_control_layout,
        "vertical_control_arrows": vertical_control_arrows,
        "chart_series_visible": chart_series_visible,
        "history_page_size": history_page_size,
    }


def set_settings(**kv) -> None:
    if "broken_out_panels" in kv:
        kv["broken_out_panels"] = json.dumps(kv["broken_out_panels"])
    if "breakout_panel_colors" in kv:
        kv["breakout_panel_colors"] = json.dumps(kv["breakout_panel_colors"])
    if "small_readout_panels" in kv:
        kv["small_readout_panels"] = json.dumps(kv["small_readout_panels"])
    if "vertical_control_layout" in kv:
        kv["vertical_control_layout"] = json.dumps(kv["vertical_control_layout"])
    if "vertical_control_arrows" in kv:
        kv["vertical_control_arrows"] = json.dumps(kv["vertical_control_arrows"])
    if "chart_series_visible" in kv:
        kv["chart_series_visible"] = json.dumps(kv["chart_series_visible"])
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


def insert_user(user: dict) -> None:
    with _conn() as c:
        c.execute(
            """INSERT INTO users (id, username, password_hash, role, status, created_at)
               VALUES (:id, :username, :password_hash, :role, :status, :created_at)""",
            user,
        )


class DuplicateUsernameError(Exception):
    pass


def insert_user_and_check_first(user: dict) -> bool:
    """Atomically inserts a new user (`user` without role/status -- this
    fills those in itself once it knows) and reports whether it was the
    very first account ever (thus admin, auto-allowed).

    Deliberately NOT "count_users() == 0, then separately insert_user()"
    -- two of those as separate calls/transactions race: two concurrent
    registrations can both read count()==0 before either commits its
    insert, so both become admin. `_conn()`'s default deferred-transaction
    mode doesn't close this either (the SELECT itself doesn't take a
    write lock, so it still runs before either connection contends for
    one) -- BEGIN IMMEDIATE below acquires the write lock *before* the
    SELECT, so a second concurrent call genuinely blocks at its own BEGIN
    IMMEDIATE until this whole read-decide-write sequence has committed,
    and then correctly sees the first call's row already there.

    Also closes a second race the same way: two concurrent registrations
    with the *same* username used to both pass an earlier, separate
    get_user_by_username() pre-check before either inserted, so the
    second's INSERT hit the UNIQUE constraint as an unhandled
    IntegrityError (a 500, not the intended 409) -- now caught here and
    raised as DuplicateUsernameError for the API layer to translate."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        is_first = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        try:
            conn.execute(
                """INSERT INTO users (id, username, password_hash, role, status, created_at)
                   VALUES (:id, :username, :password_hash, :role, :status, :created_at)""",
                {
                    **user,
                    "role": "admin" if is_first else "user",
                    "status": "allowed" if is_first else "pending",
                },
            )
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise DuplicateUsernameError(str(exc)) from exc
        conn.commit()
        return is_first
    finally:
        conn.close()


def count_users() -> int:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def get_user_by_username(username: str) -> Optional[dict]:
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: str) -> Optional[dict]:
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def list_users() -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM users ORDER BY created_at ASC").fetchall()
        return [dict(r) for r in rows]


def set_user_status(user_id: str, status: str) -> None:
    with _conn() as c:
        c.execute("UPDATE users SET status = ? WHERE id = ?", (status, user_id))


def set_user_api_key_hash(user_id: str, api_key_hash: Optional[str]) -> None:
    """None clears it (revoke) -- see api/auth.py's DELETE /auth/api-key.
    A regenerate is just this same call with a new hash, no separate
    "clear first" step needed."""
    with _conn() as c:
        c.execute("UPDATE users SET api_key_hash = ? WHERE id = ?", (api_key_hash, user_id))


def get_user_by_api_key_hash(api_key_hash: str) -> Optional[dict]:
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE api_key_hash = ?", (api_key_hash,)).fetchone()
        return dict(row) if row else None


def set_user_password_hash(user_id: str, password_hash: str) -> None:
    with _conn() as c:
        c.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))


def delete_other_sessions_for_user(user_id: str, keep_token: str) -> None:
    """Kills every other active session for this account, keeping only
    the one that just proved it knows the (soon-to-be-old) password --
    see api/auth.py's change_password. Used instead of
    delete_sessions_for_user so changing your own password doesn't log
    the browser you just changed it from back out."""
    with _conn() as c:
        c.execute("DELETE FROM sessions WHERE user_id = ? AND token != ?", (user_id, keep_token))


def delete_sessions_for_user(user_id: str) -> None:
    """Kills every active session for one account -- without this, a
    browser holding a still-valid cookie for a just-denied/deleted user
    would keep passing the auth gate until that row happened to expire on
    its own (it never does -- sessions have no TTL today). Called on
    delete_user, and separately from api/auth.py's deny_user (denying an
    already-logged-in user has to end their session too, not just block
    future logins)."""
    with _conn() as c:
        c.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def delete_user(user_id: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM users WHERE id = ?", (user_id,))
    delete_sessions_for_user(user_id)


def insert_session(token: str, user_id: str, created_at: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
            (token, user_id, created_at),
        )


def get_session_user(token: str) -> Optional[dict]:
    with _conn() as c:
        row = c.execute(
            "SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id WHERE sessions.token = ?",
            (token,),
        ).fetchone()
        return dict(row) if row else None


def delete_session(token: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM sessions WHERE token = ?", (token,))
