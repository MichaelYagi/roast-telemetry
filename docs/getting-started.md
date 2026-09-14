# Getting started

## One port (recommended for demos)

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

## Two ports (for frontend development)

Use this instead when you're actively editing the frontend and want Vite's
hot-reload rather than rebuilding on every change.

### Backend

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

### Frontend

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

## The four data sources

Every roast is driven by one of four modes, picked from the Configure
Roast form's "Data source" dropdown:

1. **Artisan Simulator** — a small thermal model that generates realistic
   BT/ET/RoR curves and auto-detects roast events (turning point, dry
   end, first crack, drop). No hardware needed.
2. **`.alog` Playback** — loads a real roast log and replays it at real
   (or accelerated) speed, including its events and notes. Also reads
   genuine Artisan-exported `.alog` files, not just this project's own.
3. **Direct Modbus (USB)** — talks straight to a real roaster's PLC over
   Modbus RTU, bypassing Artisan entirely. Ships with defaults for
   Coffee-Tech's FZ-94; every register is overridable for a different
   Modbus roaster. See [docs/modbus/](modbus/README.md).
4. **Direct USB (thermocouple meter)** — reads a Mastech MS6514 dual
   K-type thermocouple meter straight over USB-serial. Read-only (the
   meter has no command channel); T1 → BT, T2 → ET.

Clicking **ON** connects to the device and starts streaming live
readings (nothing recorded yet); **START** begins actually recording
that stream into a roast — for `modbus_live`/`ms6514_live` these are two
genuinely separate steps (verify the connection before committing to a
roast), matching real Artisan's own connect-then-record model. For
`simulator`/`alog_playback`, connecting and recording happen together in
one step, since there's no real external connection to verify first.

## Saving a roast

Automatic — nothing extra to do. Click **OFF** once you're done: that
stops the roast and writes its `.alog` file to
`backend/data/roasts/<roast_id>.alog`, regardless of how it ends
("complete" if the engine itself signaled done, "stopped" if you clicked
OFF yourself, "aborted" if something actually went wrong). To get the
file:

1. Click **View detail** (next to the toolbar once finished), or find
   the roast under **History**.
2. Click **Download .alog** at the top of the detail page.

## Testing without real hardware

`simulator` and `alog_playback` need nothing extra — fully
self-contained. The two modes that talk to real hardware (`modbus_live`,
`ms6514_live`) can each be exercised end-to-end, through the app's actual
connection code, using a fake standing in for the real device — see
`hardware_fakes/README.md` for the MS6514 fake, and
[docs/modbus/fz-94-usb.md](modbus/fz-94-usb.md#testing-without-real-hardware)
for the FZ-94 one specifically, since it's the one that can also be
*controlled* (Burner/Air/Drum), not just read.

## Next steps

- Setting up and running the backend test suite, and cutting a release:
  see [CONTRIBUTING.md](../CONTRIBUTING.md).
- What each data source actually stores, and the full API surface: see
  [Architecture reference](architecture.md#whats-stored-where).
