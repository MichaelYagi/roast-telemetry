#!/usr/bin/env bash
# Launches the macOS menu-bar icon (scripts/tray_app.py) that starts/stops
# the server with a click. macOS only from this script (Windows uses
# scripts/tray.ps1) -- deliberately no native Linux desktop tray, see
# tray_app.py's own header for why. On Linux/WSL2 use
# scripts/run-server.sh instead.
#
# scripts/install.sh already installs the tray deps by default -- the
# check below is only a safety net for a .venv created before that
# (install.sh predates tray_requirements.txt existing at all).
#
# Usage: scripts/tray.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "$(basename "$0"): the tray icon is macOS/Windows only -- there's no native Linux desktop tray." >&2
  echo "Use scripts/run-server.sh instead (it serves the app at http://localhost:8000 from this terminal)." >&2
  exit 1
fi

if [[ ! -e .venv/bin/python ]]; then
  echo "No virtual environment found (.venv/bin/python missing)." >&2
  echo "Run scripts/install.sh first -- it creates it." >&2
  exit 1
fi

if ! .venv/bin/python -c "import pystray" 2>/dev/null; then
  echo "Tray dependencies aren't installed yet. Run this once:" >&2
  echo "    .venv/bin/pip install -r scripts/tray_requirements.txt" >&2
  echo "(or just re-run scripts/install.sh, which installs this by default now)" >&2
  exit 1
fi

# No tkinter check here: tray_app.py deliberately doesn't use tkinter on
# macOS at all -- Tk 9.0 hard-crashes on a real Mac the instant it tries
# to create a window (an upstream Tk/macOS bug, confirmed live), so it
# uses osascript instead, which needs nothing this check would catch.

exec .venv/bin/python scripts/tray_app.py
