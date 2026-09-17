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

# pystray AND tkinter -- two separate dependencies (tkinter doesn't
# come from pystray/Pillow at all), checked independently. Confirmed
# live: a .venv with pystray already installed successfully, but
# tkinter missing (Homebrew splits Tk support into its own python-tk@
# formula -- see install.sh), made this check alone say "ready" and
# skip straight to tray.sh, which then crashed on `import tkinter`
# instead of routing through install.sh, which actually knows how to
# fix that.
if [[ ! -d .venv ]] || ! .venv/bin/python -c "import pystray" 2>/dev/null || ! .venv/bin/python -c "import tkinter" 2>/dev/null; then
  echo "First-time setup..."
  ./scripts/install.sh
fi

exec ./scripts/tray.sh
