#!/usr/bin/env bash
# Launches the system tray icon (scripts/tray_app.py) that starts/stops
# the server with a click -- for macOS or a real Linux desktop (not
# WSL2, which has no tray without WSLg; see tray_app.py's own header).
#
# One-time setup, on top of the regular scripts/install.sh:
#   .venv/bin/pip install -r scripts/tray_requirements.txt
#
# Usage: scripts/tray.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if ! .venv/bin/python -c "import pystray" 2>/dev/null; then
  echo "Tray dependencies aren't installed yet. Run this once:" >&2
  echo "    .venv/bin/pip install -r scripts/tray_requirements.txt" >&2
  exit 1
fi

exec .venv/bin/python scripts/tray_app.py
