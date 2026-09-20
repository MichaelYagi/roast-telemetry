#!/usr/bin/env bash
# One entry point: installs (scripts/install.sh) if this is a fresh
# clone, then starts the app -- so there's one thing to run regardless
# of whether setup has happened yet, instead of needing to know which of
# install.sh/tray.sh/run-server.sh applies this time.
#
#   macOS        -> the menu-bar tray icon (scripts/tray.sh)
#   Linux/WSL2   -> the server, in this terminal (scripts/run-server.sh) --
#                   there's deliberately no native Linux tray icon (see
#                   tray_app.py's docstring). Any arguments are passed
#                   straight through, e.g. `scripts/start.sh --lan 7890`.
#
# install.sh/tray.sh/run-server.sh themselves are untouched and still
# work standalone -- this only adds a combined entry point on top, it
# doesn't replace any of them.
#
# Usage: scripts/start.sh [run-server.sh options, Linux/WSL2 only]
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "$(uname -s)" == "Darwin" ]]; then
  if [[ ! -d .venv ]] || ! .venv/bin/python -c "import pystray" 2>/dev/null; then
    echo "First-time setup..."
    ./scripts/install.sh
  fi
  exec ./scripts/tray.sh
fi

# A Windows-created .venv (Scripts/, not bin/) has no .venv/bin/python,
# so it reads as "not set up" here and routes into install.sh -- which
# has the clear "that can't be reused from WSL2/Linux" message for
# exactly that case.
if [[ ! -e .venv/bin/python ]] || ! .venv/bin/python -c "import uvicorn" 2>/dev/null; then
  echo "First-time setup..."
  ./scripts/install.sh
fi

exec ./scripts/run-server.sh "$@"
