# Contributing

Thanks for taking a look at Roast Telemetry. This is a hobby project, but
outside contributions are welcome — here's what to know before sending a PR.

## Getting set up

See the README's "Running it" section for the two ways to run this locally
(one-port demo build vs. two-port frontend dev with hot reload). The short
version:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt   # backend/requirements.txt + pytest
cd frontend && npm install && cd ..
```

## Before opening a PR

- **Run the backend test suite**: `PYTHONPATH=. pytest -q` from the repo
  root (see the README's "Running the backend tests" section for what it
  covers). There's no frontend test suite yet — if you're touching the UI,
  test it manually in a browser (start both dev servers, exercise the
  actual feature) rather than relying on `npm run build` succeeding.
- **Build the frontend**: `cd frontend && npm run build` — this is part of
  CI and needs to succeed.
- Match the existing code style: no comment explaining *what* a line does
  (names should already say that) — comments are reserved for *why*
  something non-obvious is the way it is (a hidden constraint, a bug
  workaround, a source citation for a hardware register map). See any
  existing file for the tone.
- Keep PRs scoped to one change. A bug fix doesn't need an accompanying
  refactor.
- If you're touching `modbus_bridge/`, `artisan_bridge/`, or anything
  claiming to match real Artisan/hardware behavior, cite your source
  (Artisan's own GitHub source, a shipped `.aset` preset, a manufacturer
  manual) in a comment or the PR description — this codebase has been
  burned before by blog-sourced assumptions that turned out wrong once
  checked against Artisan's actual source.

## What happens on a PR

`.github/workflows/ci.yml` runs backend tests and a frontend build on every
PR into `main`; both need to pass before merge. See the README's
"Releasing" section for how tagged releases get cut afterward — that part
isn't something a PR needs to worry about.

## Reporting bugs / proposing features

Open a GitHub issue. For a bug, include: what you did, what you expected,
what happened instead, and which of the five roast-data-source modes
(simulator / alog_playback / artisan_live / modbus_live / ms6514_live) you
were using, since a lot of behavior is mode-specific.
