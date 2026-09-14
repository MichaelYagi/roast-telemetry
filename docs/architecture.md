# Architecture reference

## Layout

```
/backend           FastAPI app: REST + WebSocket/SSE API, roast session
                    orchestration, SQLite metadata store
/simulator          Standalone package: SimulatorEngine (thermal model)
/mock_device        Standalone package: MockDevice (fake serial/USB)
/alog_playback      Standalone package: .alog read/write + AlogPlayer
/modbus_bridge      Standalone package: ModbusEngine (direct roaster control)
/ms6514_bridge      Standalone package: MS6514Engine (direct USB meter reader)
/roast_heuristics   Standalone package: shared RoR + event auto-detection
/frontend           React + Vite + Chart.js single-page app
/docs               Reference documentation (getting started, per-model hardware notes)
```

`backend` imports `simulator`, `mock_device`, `alog_playback`,
`modbus_bridge` and `ms6514_bridge` as plain top-level Python packages
(no cross-package inheritance — they share a small duck-typed contract:
`tick(dt)` / `get_new_events()` / `apply_command(cmd)` / `is_finished()`
/ `status()`), so each can be developed, tested, or reused independently
of the web backend. `modbus_bridge` and `ms6514_bridge` both depend on
`roast_heuristics` for the shared detection logic.

## What's stored where

**Backend, `backend/data/roasts.db` (SQLite)** — shared across every
browser/device pointed at this server:

| Table | Holds |
|---|---|
| `roasts` | Per-roast metadata: title, mode, status, beans, weights, duration, playback speed, and the path to its `.alog` file. The full per-second profile/events/notes live in that `.alog` file on disk (`backend/data/roasts/<id>.alog`), not in SQLite — this table just indexes it for fast history listing/filtering. |
| `roast_presets` | "Load saved config" entries — the full Configure Roast form plus its starting Burner/Air/Drum values. |
| `settings` | Ollama URL/model (AI Roast Review), and which panels are enabled/ordered in the Big Readout Panel / Small Readout column. |
| `roast_reviews` | The latest AI-generated review per roast (one row each, overwritten on regenerate). |

**Browser `localStorage`** — per-browser, never sent to the server, doesn't
sync across devices or tabs on a different machine:

| Key | Holds |
|---|---|
| `roast-telemetry:theme` | Selected theme (Light/Dark/Coffee/Croissant/Matcha) |
| `roast-telemetry:breakoutSplitWidth` | Dragged divider position in the Big Readout Panel's split-pane layout |
| `roast-telemetry:chartHeight` | Dragged chart height |

Rule of thumb: anything that should look the same for anyone opening the
app, or that a roast's own record needs, goes in the DB. Anything that's
just "how do I like my own browser to look right now" goes in
`localStorage`.

## API summary

All routes live under `/api` (e.g. `/api/roasts`); omitted below for brevity.
`/docs`, `/redoc` and `/openapi.json` stay at the root, unprefixed.

| Method | Path | Purpose |
|---|---|---|
| GET | `/devices` | Mock devices currently bound to active roasts |
| POST | `/devices/{id}/fault` | Testing hook: force a device into an error state |
| GET | `/serial-ports` | Serial ports currently visible to the OS, for the Configure Roast form's port picker |
| GET | `/roasts` | Roast history (filter: `mode`, `status`) |
| POST | `/roasts` | `simulator`/`alog_playback`: create **and start** a roast. `modbus_live`/`ms6514_live`: **connect only** (ON) — see `/roasts/{id}/start` |
| POST | `/roasts/{id}/start` | `modbus_live`/`ms6514_live` only: begin recording a connection already made via `POST /roasts` (START) |
| GET | `/roasts/{id}` | Full roast detail (profile, events, notes) — live or historical |
| DELETE | `/roasts/{id}` | Delete a roast + its `.alog` file (409 if still roasting/cooling) |
| POST | `/roasts/{id}/stop` | Deliberately stop a roast (status `stopped`), persist its `.alog` |
| POST | `/roasts/{id}/commands` | `heater_pct` / `fan_pct` / `drum_speed_pct` (simulator, modbus_live) or `speed` (playback) |
| POST | `/roasts/{id}/notes` | Append a timestamped note |
| POST | `/roasts/{id}/events` | Append a custom event marker |
| GET | `/roasts/{id}/alog` | Download the roast's `.alog` file |
| POST | `/roasts/import` | Import an existing `.alog` file as history (`path`, optional `title`) |
| WS | `/roasts/{id}/stream` | Live sample/event stream (sends a `snapshot` first) |
| GET | `/roasts/{id}/stream/sse` | Same stream over Server-Sent Events |
| GET | `/presets` | List saved roast configurations |
| POST | `/presets` | Save the current Configure-Roast form as a named preset |
| GET | `/presets/{id}` | One saved preset's full config |
| DELETE | `/presets/{id}` | Delete a saved preset |

## Assumptions worth knowing about

- **`.alog` schema**: reading real Artisan files is now verified, not
  guessed — `alog_playback/alog_io.py` was checked against an actual
  Kaleido-exported `.alog`. Two things worth knowing: (1) real Artisan
  writes the file as a Python dict literal (`str(dict)`, single quotes,
  meant to be `eval`'d) rather than strict JSON — the loader tries `json`
  first and falls back to `ast.literal_eval` (safe, no code execution);
  (2) named roast-phase markers (Charge/Dry End/FC start&end/SC
  start&end/Drop/Cool End) live in an 8-element `timeindex` array of
  `timex` indices (0 = not recorded), with Turning Point computed
  separately at `computed['TP_idx']` — confirmed by cross-checking every
  populated index's ET/BT against the file's own `computed` block, so
  this isn't a guess. Manual burner/air/damper/drum adjustments come
  through as `CUSTOM` events from the `specialevents`/`specialeventstype`/
  `specialeventsvalue`/`specialeventsStrings` parallel arrays. Writing
  still uses this project's own simpler JSON shape (events/notes as plain
  objects) — round-trips perfectly within itself, but isn't byte-for-byte
  what Artisan's own exporter would produce. Real Artisan files also
  don't store a per-sample RoR array (only phase-average RoR in
  `computed`) — `alog_io.py` reconstructs RoR from the BT/ET curve itself
  (24s trailing-window derivative) whenever the source doesn't provide
  one, so RoR is always available regardless of source.
- **Direct Modbus Bridge register map**: see
  [docs/modbus/fz-94-usb.md](modbus/fz-94-usb.md) for the full citation
  trail and what's actually been verified vs. blog-sourced.
- **Simulator physics**: `simulator/engine.py` is a first-order thermal
  model (ET chases a heater-driven setpoint; BT lags ET with an explicit
  charge-dip/turning-point phase) tuned to produce a ~10-11 minute roast
  with realistic event timing, not a reproduction of Artisan's actual
  simulator internals (which we don't have source access to).
- **Persistence**: SQLite indexes roast metadata for fast history
  listing/filtering; full per-second profiles live in each roast's
  `.alog` file and are read from disk on demand. Swapping to Postgres is
  isolated to `backend/app/storage.py`.
- **`.alog` import** takes a server-side file path rather than a
  multipart upload, to keep the skeleton simple — swap
  `POST /roasts/import` for a multipart endpoint if the frontend needs to
  upload files directly from a client machine.
