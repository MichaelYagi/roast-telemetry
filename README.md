# Roast Telemetry

An API-first coffee roasting platform that reuses Artisan Scope's concepts
(BT/ET/RoR curves, roast events, `.alog` logs), focused entirely on
*direct* connections to a data source — no dependency on a separately
running Artisan instance for any of them. Every roast is driven by one of
four data sources:

1. **Artisan Simulator Mode** (`/simulator`) — a small thermal model that
   generates realistic BT/ET/RoR curves and auto-detects roast events
   (turning point, dry end, first crack, drop). No hardware, no Artisan.
2. **`.alog` Playback Engine** (`/alog_playback`) — loads a real roast log
   and replays it at real (or accelerated) speed, including its events
   and notes. Also verified to correctly read genuine Artisan-exported
   `.alog` files, not just this project's own — see "Assumptions" below.
3. **Direct Modbus Bridge** (`/modbus_bridge`) — talks straight to a real
   roaster's PLC over Modbus RTU (USB/serial), bypassing Artisan entirely.
   Can control the roaster (Burner/Air/Drum), since it's just standard
   Modbus register reads and writes. Ships configured for Coffee-Tech
   Engineering's plain **FZ-94** by default — not the EVO, which connects
   over Modbus TCP/Ethernet instead, a different connection method
   entirely. Every slave ID/register/baud rate below is confirmed against
   Artisan's own shipped machine preset and source for this exact model
   (see "Assumptions"), not guessed; every one is also a constructor
   argument, not hardcoded, so it can be repointed at a different Modbus
   roaster — every one (BT/ET/DT/Burner's own slave/register/divisor
   included, not just Air/Drum/Burner-range) is exposed as a per-roast
   override in the New Roast form's "Advanced Modbus register map":
   - One serial connection handles everything — BT/ET/DT
     (bean/environment/drum-space temperature) and Burner (a drum-temp
     *setpoint* the roaster's own PID bangs the 3 heating elements
     around, not a power %) plus the Air/Drum VFD drives — at 19200
     baud, 8N2, matching Artisan's own shipped preset exactly.
   - A genuinely separate second connection for Air/Drum is supported
     but only needed for unusual wiring; the default is one port.
   Mutually exclusive with Artisan (or anything else) also holding the
   same serial port(s) — pick one owner of each port at a time.
4. **Direct USB Bridge** (`/ms6514_bridge`) — reads a Mastech MS6514 dual
   K-type thermocouple meter straight over USB-serial, bypassing Artisan
   entirely. Read-only (the meter has no command channel at all); T1 →
   BT, T2 → ET. Protocol traced directly from Artisan's own driver
   (`MS6514temperature()` in `artisanlib/comm.py`).

The two live-hardware bridges (`modbus_bridge`, `ms6514_bridge`) share the
same independent event-auto-detection and RoR computation
(`/roast_heuristics`), since neither a raw PLC nor a thermocouple meter
carries roast-milestone events — Charge/Turning Point/Dry End/FC Start
are detected from the temperature curve itself, the same way a human
roaster would notice them from watching BT.

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
```

`backend` imports `simulator`, `mock_device`, `alog_playback`,
`modbus_bridge` and `ms6514_bridge` as plain top-level Python packages
(no cross-package inheritance — they share a small duck-typed contract:
`tick(dt)` / `get_new_events()` / `apply_command(cmd)` / `is_finished()`
/ `status()`), so each can be developed, tested, or reused independently
of the web backend. `modbus_bridge` and `ms6514_bridge` both depend on
`roast_heuristics` for the shared detection logic.

## Running it

### One port (recommended for demos)

Build the frontend once, then run only the backend — it serves the
compiled UI itself:

```bash
cd roast-telemetry
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
cd roast-telemetry
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

## Running the backend tests

```bash
pip install -r requirements-dev.txt   # backend/requirements.txt + pytest
python -m pytest
```

Run from the repo root — `pytest.ini` puts it on `sys.path` (`pythonpath = .`,
matching how the app itself is always run: `PYTHONPATH=. uvicorn ...`).
Covers the .alog reader/writer (including real Artisan's own file shape),
`AlogPlayer`'s playback/interpolation, milestone event sequencing
(`RoastSession.add_event`), settings persistence, and the settings API.
Each DB-touching test gets its own throwaway SQLite file (see
`tests/conftest.py`'s `isolated_db` fixture) — none of it touches
`backend/data/roasts.db`. There's no frontend test suite yet.

## Releasing

Versioning is git-tag-only — there's no VERSION file or committed version
number to keep in sync; the tag pushed to a commit *is* that commit's
version (`frontend/package.json`'s own `"version"` is cosmetic and not
enforced against the tag). `.github/workflows/ci.yml` runs backend tests
+ frontend build on every push/PR to `main`; `.github/workflows/release.yml`
re-runs both as a gate on tag push and only proceeds to package a release
if they pass — a tag on a broken commit can't produce one.

To cut a release:

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

That's the entire trigger. Once the gate passes, it zips the backend
(+ its standalone engine packages) and the built frontend together and
attaches `roast-telemetry-vX.Y.Z.zip` to a GitHub Release for that tag —
there's no hosting target configured yet, so this is a runnable snapshot,
not a deploy. To use it: unzip, `pip install -r backend/requirements.txt`,
then `PYTHONPATH=. uvicorn backend.app.main:app` from the unzipped root
(the frontend is already built into `frontend/dist`, which the backend
serves directly — see `backend/app/main.py`).

## Testing the hardware-dependent modes without real hardware

`simulator` and `alog_playback` need nothing extra — they're fully
self-contained. The two modes that talk to real hardware (`modbus_live`,
`ms6514_live`) can each be exercised end-to-end, through the app's actual
connection code, using a fake standing in for the real device. Both fakes
live in `/hardware_fakes` and share one thermal model
(`hardware_fakes/_thermal.py`, reusing `simulator.SimulatorEngine`) so
BT/ET behave like a real roast regardless of which mode you're testing.
Full details, including the MS6514 fake, are in `hardware_fakes/README.md`;
below is the quick-start for the Modbus one, since it's the only mode that can also
be *controlled* (Heater/Fan/Drum), not just read.

### Setting up the mock Modbus roaster

`ModbusSerialClient` (used by `modbus_bridge`) needs a real OS serial
port, so faking it over plain TCP isn't an option — instead, `socat`
creates a linked pair of virtual serial ports, one for the fake device,
one for the app to connect to, exactly as if a USB-RS485 cable joined
them.

```bash
# one-time
sudo apt install socat

# terminal 1 -- create the virtual pair, leave it running
socat -d -d pty,raw,echo=0,link=/tmp/ttyFAKE_ROASTER pty,raw,echo=0,link=/tmp/ttyFAKE_ROASTER_APP

# terminal 2 -- start the fake FZ-94 (one connection handles everything)
cd roast-telemetry && source .venv/bin/activate
python -m hardware_fakes.modbus_fz94 --port /tmp/ttyFAKE_ROASTER

# terminal 3 -- the app itself (see "Running it" above)
PYTHONPATH=. uvicorn backend.app.main:app --port 8000
```

In the app's **Live Roast → Configure Roast** form:
- **Data source**: `Direct Modbus (FZ-94, USB)`
- **Serial port**: `/tmp/ttyFAKE_ROASTER_APP`
- Leave baud rate at its default (19200) and the drive-port field blank
- Click **ON**, then **START**

BT/ET/DT should populate immediately and climb like a real roast; the
Heater/Fan/Drum sliders write real Modbus registers the fake decodes
and feeds back into its thermal model, so raising Heater visibly speeds
up ET/BT/DT.

The fake's thermal clock starts on the *first* request it receives, not
when the process launches -- so it's fine to leave it running for a
while (setting up socat, configuring the form) before you click START;
Charge happens right when the app actually connects, not whenever the
fake happened to start. You only need to restart the fake process
itself when you want a genuinely fresh roast (e.g. after one already
finished, or to reset mid-roast Heater/Fan/Drum changes).

Since re-entering the serial port / thresholds every time gets old
fast, use the **"Save this configuration as"** field at the bottom of
the form once you've got it dialed in — it persists to the backend
(`GET/POST/DELETE /api/presets`) and reappears in the **"Load saved
config"** dropdown next time, for this or any other mode. For
`simulator`/`modbus_live` (the controllable modes), the Heater/Fan/Drum
starting values shown in the form are saved too, and get auto-sent as
the roast's first command right after START when you load that preset
again — e.g. save a "Fake FZ94 test rig" preset with Heater=85%/Fan=15%
and loading it later both starts the roast *and* dials in those slider
positions immediately, no manual re-adjustment needed.

### Suggested manual test settings

Right after clicking START, set **Heater=85%, Fan=15%** (Drum at the
default 50%) in the Control Panel. This isn't a guess — it's the actual
thermal model run forward, so the timings below are exact for this
setting:

| Event | Type | Time | BT |
|---|---|---|---|
| Charge | auto | 0:00 | 96.0°C |
| Turning Point | auto | 0:45 | 82.0°C |
| Dry End | auto (threshold 160°C) | 3:35 | 160.1°C |
| FC Start | auto (threshold 196°C) | 5:27 | 196.1°C |
| FC End | **click it** | ~6:00 | ~205°C |
| SC Start | **click it** | ~6:30 | ~211°C |
| Drop | **click it, by** | ~6:50–6:54 | ~217–218°C |
| Cool End | **click it, then stop the roast** | by ~9:50 | falling |

Charge/Turning Point/Dry End/FC Start auto-fire from the BT curve
(`roast_heuristics.LiveRoastDetector`) — nothing to click. FC End, SC
Start, Drop and Cool End are judgment calls in real roasting too, so
they stay manual event buttons under the chart; the times above just
tell you *when the underlying fake's own physics hit those points*, so
your manual clicks land in the right place on the curve.

The **Drop** deadline matters more than the others: the fake's internal
physics hit its own drop threshold (218°C) at ~6:50 regardless of
whether you've clicked anything, and BT starts falling on its own after
that — click late and you'll mark Drop on an already-cooling curve.
After ~9:50 the fake's roast is fully finished and BT/ET just hold flat
at their final cooled-down value — click OFF around then to end your
recording cleanly. (An earlier version of the fake auto-restarted a
fresh Charge once finished, "to stay usable as a standing fixture" —
that silently reset BT/ET back to ~96°C/200°C mid-recording if you
didn't stop at exactly the right second, corrupting the tail of the
`.alog` with an unmarked second Charge. It's been removed for exactly
that reason; restart the fake process for a new test roast instead.)

### Saving to `.alog`

Automatic — nothing extra to do. Click **OFF** once you're done (right
after Cool End): that stops the roast and always writes the `.alog`
file to `backend/data/roasts/<roast_id>.alog`, regardless of how the
roast ends ("complete" if the engine itself signaled done, "stopped" if
you clicked OFF yourself — the only way any live-hardware mode ever
ends, since none of them have an automatic "done" signal — or "aborted"
if something actually went wrong). To get the file itself:

1. Click **View detail** (appears next to the toolbar once finished),
   or find the roast under **History**
2. Click **Download .alog** at the top of the detail page

## What's stored where

**Backend, `backend/data/roasts.db` (SQLite)** — shared across every
browser/device pointed at this server:

| Table | Holds |
|---|---|
| `roasts` | Per-roast metadata: title, mode, status, machine, beans, weights, duration, playback speed, and the path to its `.alog` file. The full per-second profile/events/notes live in that `.alog` file on disk (`backend/data/roasts/<id>.alog`), not in SQLite — this table just indexes it for fast history listing/filtering. |
| `roast_presets` | "Load saved config" entries — the full Configure Roast form plus its starting Heater/Fan/Drum values. |
| `settings` | Ollama URL/model (AI Roast Review), and which panels are enabled/ordered in the Big Readout Panel. |
| `roast_reviews` | The latest AI-generated review per roast (one row each, overwritten on regenerate). |

**Browser `localStorage`** — per-browser, never sent to the server, doesn't
sync across devices or tabs on a different machine:

| Key | Holds |
|---|---|
| `roast-telemetry:theme` | Selected theme (Light/Dark/Coffee/Croissant/Matcha) |
| `roast-telemetry:breakoutSplitWidth` | Dragged divider position in the Big Readout Panel's split-pane layout |

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
| GET | `/roasts` | Roast history (filter: `mode`, `status`) |
| POST | `/roasts` | Create **and start** a roast (`mode`: `simulator` \| `alog_playback`) |
| GET | `/roasts/{id}` | Full roast detail (profile, events, notes) — live or historical |
| DELETE | `/roasts/{id}` | Delete a roast + its `.alog` file (409 if still roasting/cooling) |
| POST | `/roasts/{id}/stop` | Deliberately stop a roast (status `stopped`), persist its `.alog` |
| POST | `/roasts/{id}/commands` | `heater_pct` / `fan_pct` / `drum_speed_pct` (simulator) or `speed` (playback) |
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
- **Direct Modbus Bridge register map**: confirmed against Artisan's own
  shipped machine preset for this exact model —
  [`FZ94.aset`](https://github.com/artisan-roaster-scope/artisan/blob/master/src/includes/Machines/Coffee-Tech/FZ94.aset)
  in [artisan-roaster-scope/artisan](https://github.com/artisan-roaster-scope/artisan)
  (the plain FZ-94, USB/RTU — not the EVO, which is Modbus TCP/Ethernet, a
  different connection method entirely) — and the source that interprets
  it, [`modbusport.py`](https://github.com/artisan-roaster-scope/artisan/blob/master/src/artisanlib/modbusport.py),
  not just paraphrased from blog write-ups, though those independently
  corroborate it:
  [BT/ET/DT](https://artisan-roasterscope.blogspot.com/2015/01/connecting-artisan-to-coffee-tech-fz-94.html),
  [Burner](https://artisan-roasterscope.blogspot.com/2016/08/fz-94-2-pushing-drum-heat-limit.html),
  Air/Drum control writes from
  [part 4](https://artisan-roasterscope.blogspot.com/2016/08/fz-94-4-taking-control.html)
  specifically, Air/Drum speed *feedback* from
  [part 3](https://artisan-roasterscope.blogspot.com/2016/08/fz-94-3-connecting-drives.html)
  specifically — different posts in the same 5-part series, not one post
  covering both directions.
  **One serial connection handles everything** — 19200 baud, 8N2, straight
  from the `.aset`'s own `[Modbus]` block (an earlier version of this
  assumed the temperature probes and Air/Drum drives needed two separate
  connections at different baud rates, based on one blog mid-series; that
  was a snapshot of that author's setup *before* they unified everything
  onto one bus, not Artisan's actual shipped configuration).
  BT/ET/DT are each their own Modbus slave device (11/13/12), all at
  register 0, function code 3, value = temperature×10; Burner shares DT's
  slave (12), register 5, same ×10 convention (confirmed in source, not
  just inferred — `modbusport.py`'s `setTarget()` maps the `.aset`'s
  `SVmultiplier=1` to an actual ×10 multiplier), but it's a
  drum-temperature *setpoint* the roaster's own bang-bang PID uses to
  switch its 3 heating elements — the FZ-94's own user manual
  independently confirms 3 separately-switched 1000W elements plus one
  "Drum heat limit controller," no per-element percentage control at all.
  This app maps its 0–100 `heater_pct` UI onto a configurable SV range
  (default 100–250°C, comfortably spanning Coffee-Tech's own
  factory-recommended 190°C starting point) as its own approximation, not
  something documented anywhere. Since it's a holding register, it's also
  *read* back (same address as the write) and reported as the PLC's actual
  current setpoint — not just an echo of this app's own last command.
  Air (slave 1) and Drum (slave 2) are
  Delta VFD-L drives needing two writes each (control register 8192 for
  run/stop, then frequency register 8193, value = percent×100), plus a
  separate read-only feedback register (8451, same ×100 convention) for
  the drive's *actual* current speed — used in place of just echoing the
  last command back, so a rejected/misrouted write doesn't silently look
  identical to a successful one. All of this is blog-sourced only, the
  `.aset`'s own `[Sliders]` block ships without a configured write
  command for it, so it's the least-confirmed piece here. `modbus_control_port` exists only for wiring that genuinely needs
  a *separate* second connection (uncommon) — the default is one port for
  everything. Tested against a mocked `pymodbus` client plus actual
  serial traffic against `hardware_fakes/modbus_fz94.py` (register math,
  control-command clamping, and failure handling all verified this way),
  not real FZ-94 hardware — none was available in this environment. The
  slave IDs/registers/baud rate themselves are from Artisan's own source;
  the wire-level RTU behavior against your specific unit is unverified
  until you try it. Because Heater/Fan/Drum are all genuinely read back
  (not echoed), connecting to a `modbus_live` roast never writes a
  "starting value" on your behalf — the Controls panel's sliders just show
  whatever the roaster is actually doing (an operator's own manual
  setting, or state left over from a previous session included), and
  nothing changes on the machine until you move a slider yourself. The
  "% at start" fields on the New Roast form only apply to `simulator`,
  which has no real device state to read.
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
