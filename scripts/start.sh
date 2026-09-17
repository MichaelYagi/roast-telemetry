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

# pystray AND tkinter are checked independently (tkinter doesn't come
# from pystray/Pillow at all) -- but only on non-macOS. tray_app.py
# deliberately doesn't use tkinter on macOS at all (Tk 9.0 hard-crashes
# there -- see install.sh), so requiring it here would make this check
# permanently fail on a Mac even once everything's genuinely ready,
# looping back into install.sh forever for a dependency that's never
# actually needed on that platform. Elsewhere, this still catches the
# real case that motivated adding it: a .venv with pystray already
# installed successfully, but tkinter missing, used to read as "ready"
# and skip straight to tray.sh, which then crashed instead of routing
# through install.sh, which actually knows how to fix that.
NEEDS_TKINTER=1
[[ "$(uname -s)" == "Darwin" ]] && NEEDS_TKINTER=0

if [[ ! -d .venv ]] || ! .venv/bin/python -c "import pystray" 2>/dev/null; then
  echo "First-time setup..."
  ./scripts/install.sh
elif [[ "$NEEDS_TKINTER" -eq 1 ]] && ! .venv/bin/python -c "import tkinter" 2>/dev/null; then
  echo "First-time setup..."
  ./scripts/install.sh
fi

exec ./scripts/tray.sh
