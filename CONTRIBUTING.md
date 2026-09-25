# Contributing

Thanks for taking a look at Roast Telemetry. This is a hobby project, but
outside contributions are welcome — here's what to know before sending a PR.

## Getting set up

See [docs/getting-started.html](docs/getting-started.html) for the plain
one-port way to run this (`scripts/install.sh` + `scripts/run-server.sh`).
For active frontend development, use two ports instead so Vite's hot
reload applies immediately instead of rebuilding on every change:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt   # backend/requirements.txt + pytest
cd frontend && npm install && cd ..
```

**Backend** (auto-restarts on backend code changes):

```bash
scripts/run-server.sh --skip-build --reload
```

`--skip-build` since Vite, not this backend, serves the frontend in this
flow. Docs at `http://localhost:8000/docs`. Roast data persists to
`backend/data/roasts.db` (SQLite index) and `backend/data/roasts/*.alog`
(full profiles); a pre-baked example lives at
`backend/data/sample_roasts/demo_roast.alog` for exercising playback mode.

**Frontend** (separate terminal):

```bash
cd frontend && npm run dev
```

Opens on `http://localhost:5173` and proxies `/api/v1/*` (including
WebSockets) to the backend on port 8000 — see `frontend/vite.config.js`.

If this repo lives under a Windows-mounted path in WSL (e.g.
`/mnt/c/...`), filesystem change events don't propagate to Vite's
watcher, so hot reload can silently serve stale code after an edit —
`vite.config.js` already sets `server.watch.usePolling` to work around
this; restart `npm run dev` if you still see stale behavior.

## Running the backend tests

```bash
pip install -r requirements-dev.txt   # backend/requirements.txt + pytest
python -m pytest
```

Run from the repo root — `pytest.ini` puts it on `sys.path` (`pythonpath = .`,
matching how the app itself is always run: `PYTHONPATH=. uvicorn ...`).
Covers the `.alog` reader/writer (including the native file
shape), `AlogPlayer`'s playback/interpolation, milestone event sequencing,
the modbus/ms6514 engines' register maps and lifecycle (connect vs.
record, detector reset on recording start), automation rules
(event/temperature/time triggers, one-shot ambient-trigger evaluation,
immediate/delayed firing, cancellation on abort, action failures never
breaking the milestone click), settings persistence, and the
settings/roasts/serial-ports APIs. Each DB-touching test gets its own
throwaway SQLite file (see `tests/conftest.py`'s `isolated_db` fixture) —
none of it touches `backend/data/roasts.db`. There's no frontend test
suite yet.

## Before opening a PR

- **Run the backend test suite** (above) and make sure it's green.
- **Build the frontend**: `cd frontend && npm run build` — this is part of
  CI and needs to succeed. There's no frontend test suite, so if you're
  touching the UI, test it manually in a browser (start both dev servers,
  exercise the actual feature) rather than relying on the build alone.
- Match the existing code style: no comment explaining *what* a line does
  (names should already say that) — comments are reserved for *why*
  something non-obvious is the way it is (a hidden constraint, a bug
  workaround, a source citation for a hardware register map). See any
  existing file for the tone.
- Keep PRs scoped to one change. A bug fix doesn't need an accompanying
  refactor.
- If you're touching `modbus_bridge/`, `ms6514_bridge/`, or anything
  claiming to match real hardware behavior, cite your source
  (a manufacturer manual, a published write-up, a real-hardware test) in
  a comment or the PR description — this codebase has been burned before
  by write-up-sourced assumptions that turned out wrong once checked
  against a real machine. Don't copy code, data files or saved settings
  from other projects (see [CLAUDE.md](CLAUDE.md)). See
  [docs/modbus/fz-94-usb.html](docs/modbus/fz-94-usb.html) for the existing
  citation style.

## What happens on a PR

`.github/workflows/ci.yml` runs backend tests and a frontend build on every
PR into `main`; both need to pass before merge.

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

## Publishing docs

`docs/` here isn't served directly -- what's live at
https://michaelyagi.github.io/roast-telemetry is a copy published to a
separate repo (`michaelyagi.github.io`) by `scripts/sync-docs.sh`. CI runs
it automatically whenever `docs/` changes on `main`; you can also run it by
hand (it clones/updates a local copy of that repo next to this one by
default, override with `MIRROR_DIR`), which copies everything over, strips
the couple of links that point back to this repo, and pushes.

## Reporting bugs / proposing features

Open a GitHub issue. For a bug, include: what you did, what you expected,
what happened instead, and which of the four roast-data-source modes
(simulator / alog_playback / modbus_live / ms6514_live) you were using,
since a lot of behavior is mode-specific.
