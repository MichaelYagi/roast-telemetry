# Artisan Web Roasting Platform

An API-first coffee roasting platform that reuses Artisan Scope's concepts
(BT/ET/RoR curves, roast events, `.alog` logs). Every roast is driven by
one of five data sources:

1. **Artisan Simulator Mode** (`/simulator`) — a small thermal model that
   generates realistic BT/ET/RoR curves and auto-detects roast events
   (turning point, dry end, first crack, drop). No hardware, no Artisan.
2. **Mock Device Layer** (`/mock_device`) — pretends to be USB/serial
   roaster hardware (`connect`/`disconnect`/`read`/`write`/`status`),
   backed transparently by whichever engine is active.
3. **`.alog` Playback Engine** (`/alog_playback`) — loads a real roast log
   and replays it at real (or accelerated) speed, including its events
   and notes. Also verified to correctly read genuine Artisan-exported
   `.alog` files, not just this project's own — see "Assumptions" below.
4. **Artisan Live Bridge** (`/artisan_bridge`) — mirrors a *real, running*
   Artisan instance connected to actual roaster hardware, so other people
   can watch the live BT/ET/RoR curve in a browser as the roast happens.
   Requires Artisan itself running somewhere with a real roaster attached
   and its WebLCDs feature enabled (Config → Curves → UI tab). View-only —
   Artisan exposes no channel for an external program to send it commands.
5. **Direct Modbus Bridge** (`/modbus_bridge`) — talks straight to a real
   roaster's PLC over Modbus RTU (USB/serial), bypassing Artisan entirely.
   Unlike the Artisan Live Bridge, this one *can* control the roaster
   (burner/air/drum), since it's just standard Modbus register reads and
   writes. Ships configured for Coffee-Tech Engineering's FZ94 EVO by
   default (register addresses taken from Artisan's own machine preset —
   see "Assumptions"), but every address is a constructor argument, not
   hardcoded, so it can be repointed at a different Modbus roaster.
   Mutually exclusive with Artisan (or anything else) also holding that
   same serial port — pick one owner of the port at a time.

Both live bridges share the same independent event-auto-detection and
RoR computation (`/roast_heuristics`), since neither WebLCDs nor a raw
PLC carries roast-milestone events — Charge/Turning Point/Dry End/FC
Start are detected from the temperature curve itself, the same way a
human roaster would notice them from watching BT.

## Layout

```
/backend           FastAPI app: REST + WebSocket/SSE API, roast session
                    orchestration, SQLite metadata store, machine catalog
/simulator          Standalone package: SimulatorEngine (thermal model)
/mock_device        Standalone package: MockDevice (fake serial/USB)
/alog_playback      Standalone package: .alog read/write + AlogPlayer
/artisan_bridge     Standalone package: ArtisanBridgeEngine (live mirror)
/modbus_bridge      Standalone package: ModbusEngine (direct roaster control)
/roast_heuristics   Standalone package: shared RoR + event auto-detection
/frontend           React + Vite + Chart.js single-page app
```

`backend` imports `simulator`, `mock_device`, `alog_playback`,
`artisan_bridge` and `modbus_bridge` as plain top-level Python packages
(no cross-package inheritance — they share a small duck-typed contract:
`tick(dt)` / `get_new_events()` / `apply_command(cmd)` / `is_finished()`
/ `status()`), so each can be developed, tested, or reused independently
of the web backend. `artisan_bridge` and `modbus_bridge` both depend on
`roast_heuristics` for the shared detection logic.

## Running it

### One port (recommended for demos)

Build the frontend once, then run only the backend — it serves the
compiled UI itself:

```bash
cd artisan-web-api
python3 -m venv .venv && source .venv/bin/activate   # first time only
pip install -r backend/requirements.txt               # first time only
cd frontend && npm install && npm run build && cd ..   # first time / after UI changes
PYTHONPATH=. uvicorn backend.app.main:app --port 8000
```

Open `http://localhost:8000` — that's it, one process, one port. The API
lives under `/api/*` on the same port (docs still at `/docs`), and any
other path falls back to the React app's `index.html` so client-side
routing (`/roasts/:id`, `/compare`, etc.) works on a hard refresh too.
Re-run `npm run build` after frontend changes; the backend doesn't
rebuild it for you, and this fallback only activates when
`frontend/dist/` exists — without it, the backend just runs as an
API-only server (handy for the two-port dev flow below).

### Two ports (for frontend development)

Use this instead when you're actively editing the frontend and want Vite's
hot-reload rather than rebuilding on every change.

#### Backend

```bash
cd artisan-web-api
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
PYTHONPATH=. uvicorn backend.app.main:app --reload --port 8000
```

Docs at `http://localhost:8000/docs`. Roast data persists to
`backend/data/roasts.db` (SQLite index) and `backend/data/roasts/*.alog`
(full profiles). A pre-baked example lives at
`backend/data/sample_roasts/demo_roast.alog` for exercising playback mode.

#### Frontend

```bash
cd frontend
npm install
npm run dev
```

Opens on `http://localhost:5173` and proxies `/api/*` (including
WebSockets) to the backend on port 8000 — see `frontend/vite.config.js`.

The Live Roast screen deliberately mirrors Artisan desktop's own layout:
the icon toolbar, ON/OFF device toggle + START recording button, digital
elapsed-time clock, DRY%/»DRY/»FCs milestone boxes, the banded "Roaster
Scope" chart with color-coded ET (crimson) / BT (blue) curves and a
matching ET/BT/ΔBT readout column, and the CHARGE/DRY END/FC START/FC
END/SC START/DROP manual event-marker buttons underneath the chart.

> If this repo lives under a Windows-mounted path in WSL (e.g.
> `/mnt/c/...`), filesystem change events don't propagate to Vite's
> watcher, so Hot Module Reload can silently serve stale code after an
> edit. `vite.config.js` already sets `server.watch.usePolling` to work
> around this — if you still see stale behavior, restart `npm run dev`.

## API summary

All routes live under `/api` (e.g. `/api/roasts`); omitted below for brevity.
`/docs`, `/redoc` and `/openapi.json` stay at the root, unprefixed.

| Method | Path | Purpose |
|---|---|---|
| GET | `/machines` | Supported machine catalog (filter: `brand`, `control_capable`) |
| GET | `/machines/{id}` | One machine's capabilities/connection type |
| GET | `/devices` | Mock devices currently bound to active roasts |
| POST | `/devices/{id}/fault` | Testing hook: force a device into an error state |
| GET | `/roasts` | Roast history (filter: `mode`, `status`, `machine_id`) |
| POST | `/roasts` | Create **and start** a roast (`mode`: `simulator` \| `alog_playback`) |
| GET | `/roasts/{id}` | Full roast detail (profile, events, notes) — live or historical |
| POST | `/roasts/{id}/stop` | Abort/finish a roast, persist its `.alog` |
| POST | `/roasts/{id}/commands` | `heater_pct` / `fan_pct` / `drum_speed_pct` (simulator) or `speed` (playback) |
| POST | `/roasts/{id}/notes` | Append a timestamped note |
| POST | `/roasts/{id}/events` | Append a custom event marker |
| GET | `/roasts/{id}/alog` | Download the roast's `.alog` file |
| POST | `/roasts/import` | Import an existing `.alog` file as history (`path`, optional `title`) |
| WS | `/roasts/{id}/stream` | Live sample/event stream (sends a `snapshot` first) |
| GET | `/roasts/{id}/stream/sse` | Same stream over Server-Sent Events |

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
- **Artisan Live Bridge protocol**: verified against Artisan's actual
  source on GitHub (`artisanlib/weblcds.py` and the `updateWebLCDs()`
  call site in `artisanlib/canvas.py`), not guessed — and checked across
  versions: v2.4.2 (2020, a completely different internal server —
  bottle+gevent multiprocess WSGI vs. current aiohttp/asyncio-in-thread)
  and current v4.2.0 use the byte-for-byte identical wire protocol.
  Artisan's WebLCDs WebSocket server (`ws://<host>:<port>/websocket`,
  default port 8080) pushes partial JSON frames like `{"data": {"bt":
  "189.5", "et": "204.8", "time": "04:12"}}` as the roast progresses;
  sending an empty frame right after connecting returns the last known
  state immediately on current versions (v2.4.2's server ignores this —
  harmless, just a possible brief wait for the first reading on old
  versions). Also checked Autosave (`canvas.py`'s `OffRecorder`, only
  writes once, after Drop), the WebSocket-JSON device protocol, and MQTT
  support (`mqttport.py`) as possible event/control channels — all three
  are Artisan pulling data *in* from a device, never accepting commands
  from an external actor, and Autosave isn't continuous during the
  roast. So WebLCDs carries no RoR (`roast_heuristics` computes it) and
  no roast-milestone events or control channel — not an integration gap,
  a real absence, confirmed by checking every plausible channel rather
  than assuming. `roast_heuristics.LiveRoastDetector` auto-detects
  Charge (sharp BT drop) and Turning Point (BT minimum after) from the
  live stream itself, plus Dry End/FC Start at configurable BT
  thresholds; Drop/Cool End stay manual (this platform's event buttons)
  since they're roast-level judgment calls, not thresholds — same as a
  human roaster.
- **Direct Modbus Bridge register map**: also verified, not guessed —
  pulled directly from Artisan's own machine preset for this exact model
  (`src/includes/Machines/Coffee-Tech/FZ94_EVO.aset` in Artisan's repo).
  BT is holding register 100, DT (this machine's ET-equivalent) is
  register 80, both function code 3 (read holding registers). Control
  writes use function code 6 (write single register): register 20 (range
  30–70), register 16 (range 30–70), register 35 (range 30–100). The
  channel *names* — Air=20, Drum=16, Burner=35 — are inferred, not
  confirmed against FZ94 EVO's manual: they follow Artisan's standard
  slider ordering convention (Air, Drum, Damper, Burner — the same order
  seen in a real Artisan `.alog`'s `etypes` field, checked independently
  elsewhere in this project) combined with which slider slot the preset
  disables (slot 3 = Damper, absent here, consistent with a
  burner/air/drum-only machine). Confirm which slider moves which
  physical actuator on the real hardware before roasting with it —
  swap the register numbers in `modbus_bridge/engine.py`'s constructor
  defaults if it turns out to be wrong. Tested against a mocked
  `pymodbus` client (register math, control-command clamping, and
  failure handling all verified this way), not real FZ94 EVO hardware —
  none was available in this environment. The register addresses
  themselves are real; the wire-level RTU behavior against your specific
  unit is unverified until you try it.
- **Simulator physics**: `simulator/engine.py` is a first-order thermal
  model (ET chases a heater-driven setpoint; BT lags ET with an explicit
  charge-dip/turning-point phase) tuned to produce a ~10-11 minute roast
  with realistic event timing, not a reproduction of Artisan's actual
  simulator internals (which we don't have source access to).
- **Machine catalog**: `backend/data/machines_catalog.json` is generated
  from the brand list and control-capable subset in the spec
  (`scripts` used to build it aren't part of the running app). Most
  brands don't have a verified real model name, so they get a
  `"Generic / All Models"` placeholder — a handful of well-known brands
  (Aillio, Hottop, Giesen, Probat, Loring, IKAWA, …) have real model
  names filled in. `connection_type` is a plausible default
  (`Serial (RS-232 / USB-serial adapter)`) overridden for brands with a
  well-known modern interface (IKAWA/ROEST via WiFi, Loring/Giesen/Probat
  via Modbus, etc.) — treat it as documentation, not a verified spec sheet.
- **Persistence**: SQLite indexes roast metadata for fast history
  listing/filtering; full per-second profiles live in each roast's
  `.alog` file and are read from disk on demand. Swapping to Postgres is
  isolated to `backend/app/storage.py`.
- **`.alog` import** takes a server-side file path rather than a
  multipart upload, to keep the skeleton simple — swap
  `POST /roasts/import` for a multipart endpoint if the frontend needs to
  upload files directly from a client machine.
