#!/usr/bin/env bash
# Launches the system tray icon (scripts/tray_app.py) that starts/stops
# the server with a click -- for macOS or a real Linux desktop (not
# WSL2, which has no tray without WSLg; see tray_app.py's own header).
#
# scripts/install.sh already installs the tray deps by default -- the
# check below is only a safety net for a .venv created before that
# (install.sh predates tray_requirements.txt existing at all).
#
# Usage: scripts/tray.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if ! .venv/bin/python -c "import pystray" 2>/dev/null; then
  echo "Tray dependencies aren't installed yet. Run this once:" >&2
  echo "    .venv/bin/pip install -r scripts/tray_requirements.txt" >&2
  echo "(or just re-run scripts/install.sh, which installs this by default now)" >&2
  exit 1
fi

# A separate check from the above (Linux only) -- tkinter doesn't come
# from tray_requirements.txt (pystray/Pillow) at all, it's a system
# package (python3-tk on Debian/Ubuntu), so pip can't fix a missing one
# and the message has to say something different. Not checked on
# macOS: tray_app.py deliberately doesn't use tkinter there at all --
# Tk 9.0 hard-crashes on a real Mac the instant it tries to create a
# window (an upstream Tk/macOS bug, confirmed live), so macOS uses
# osascript instead, which needs nothing this check would catch.
if [[ "$(uname -s)" != "Darwin" ]] && ! .venv/bin/python -c "import tkinter" 2>/dev/null; then
  echo "tkinter isn't installed. Re-run scripts/install.sh, which knows how to fix this per-OS (apt/brew), then try again." >&2
  exit 1
fi

exec .venv/bin/python scripts/tray_app.py
