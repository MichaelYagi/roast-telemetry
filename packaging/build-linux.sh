#!/usr/bin/env bash
# Builds a standalone, unsigned Linux distributable of Roast Telemetry --
# a folder you can tar/zip up and hand to someone with no Python/Node
# installed at all. Needs a *real* Linux desktop to actually run the
# result (pystray needs GTK/AppIndicator/Ayatana for a real tray icon --
# a plain WSL2 shell doesn't have one without WSLg; see
# scripts/tray_app.py's own module docstring), but the build itself
# works fine from WSL2/any Linux, since PyInstaller doesn't need a
# display to run.
#
# Prerequisite: scripts/install.sh already run once (sets up .venv with
# every backend + tray dependency).
#
# Usage: scripts/build-linux.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -e .venv/bin/python ]]; then
  echo "No .venv found -- run scripts/install.sh first." >&2
  exit 1
fi

echo "== Building frontend =="
(cd frontend && npm run build)

echo "== Installing pyinstaller into .venv =="
.venv/bin/pip install pyinstaller

echo "== Running PyInstaller =="
rm -rf build dist
.venv/bin/pyinstaller packaging/roast-telemetry.spec --distpath dist --workpath build

echo
echo "Built: dist/Roast Telemetry/"
echo "Tar/zip the whole folder to distribute it -- 'Roast Telemetry' inside is the launcher"
echo "(chmod +x it if the executable bit doesn't survive however you transferred the archive)."
echo "No Gatekeeper/SmartScreen-equivalent warning on Linux -- runs straight away."
echo "Needs a real desktop tray (GTK/AppIndicator/Ayatana) to show the icon -- a bare WSL2"
echo "shell without WSLg won't display one even though the binary itself runs fine."
