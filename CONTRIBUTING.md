# Contributing

Thanks for taking a look at Roast Telemetry. This is a hobby project, but
outside contributions are welcome — here's what to know before sending a PR.

## Getting set up

See [docs/getting-started.md](docs/getting-started.md) for the two ways
to run this locally (one-port demo build vs. two-port frontend dev with
hot reload). The short version:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt   # backend/requirements.txt + pytest
cd frontend && npm install && cd ..
```

## Running the backend tests

```bash
pip install -r requirements-dev.txt   # backend/requirements.txt + pytest
python -m pytest
```

Run from the repo root — `pytest.ini` puts it on `sys.path` (`pythonpath = .`,
matching how the app itself is always run: `PYTHONPATH=. uvicorn ...`).
Covers the `.alog` reader/writer (including real Artisan's own file
shape), `AlogPlayer`'s playback/interpolation, milestone event sequencing,
the modbus/ms6514 engines' register maps and lifecycle (connect vs.
record, detector reset on recording start), settings persistence, and the
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
  claiming to match real Artisan/hardware behavior, cite your source
  (Artisan's own GitHub source, a shipped `.aset` preset, a manufacturer
  manual) in a comment or the PR description — this codebase has been
  burned before by blog-sourced assumptions that turned out wrong once
  checked against Artisan's actual source. See
  [docs/modbus/fz-94-usb.md](docs/modbus/fz-94-usb.md) for the existing
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

## Reporting bugs / proposing features

Open a GitHub issue. For a bug, include: what you did, what you expected,
what happened instead, and which of the four roast-data-source modes
(simulator / alog_playback / modbus_live / ms6514_live) you were using,
since a lot of behavior is mode-specific.
