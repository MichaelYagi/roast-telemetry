#!/usr/bin/env bash
# One entry point: installs (scripts/install.sh) if this is a fresh
# clone, then starts the tray icon (scripts/tray.sh) -- so there's one
# thing to run regardless of whether setup has happened yet, instead of
# needing to know which of install.sh/tray.sh applies this time.
#
# install.sh/tray.sh themselves are untouched and still work standalone
# (e.g. CI just wants install.sh, or someone who knows they're already
# set up and just wants tray.sh directly) -- this only adds a combined
# entry point on top, it doesn't replace either.
#
# Usage: scripts/start.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -d .venv ]] || ! .venv/bin/python -c "import pystray" 2>/dev/null; then
  echo "First-time setup..."
  ./scripts/install.sh
fi

exec ./scripts/tray.sh
