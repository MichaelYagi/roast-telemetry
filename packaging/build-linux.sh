#!/usr/bin/env bash
# Builds a standalone, unsigned Linux distributable of Roast Telemetry --
# no Python/Node installed at all required to *run* the result. Needs a
# *real* Linux desktop to actually run the result (pystray needs
# GTK/AppIndicator/Ayatana for a real tray icon -- a plain WSL2 shell
# doesn't have one without WSLg; see scripts/tray_app.py's own module
# docstring), but the build itself works fine from WSL2/any Linux,
# since PyInstaller doesn't need a display to run.
#
# Prerequisite: scripts/install.sh already run once (sets up .venv with
# every backend + tray dependency).
#
# Usage:
#   scripts/build-linux.sh          # single-file executable (default)
#   scripts/build-linux.sh onedir   # a folder instead -- faster startup, but
#                                    # 'Roast Telemetry' needs the rest of the
#                                    # folder alongside it, can't move just the binary
set -euo pipefail
cd "$(dirname "$0")/.."
MODE="${1:-onefile}"
if [[ "$MODE" != "onefile" && "$MODE" != "onedir" ]]; then
  echo "Usage: $0 [onefile|onedir]" >&2
  exit 1
fi

if [[ ! -e .venv/bin/python ]]; then
  echo "No .venv found -- run scripts/install.sh first." >&2
  exit 1
fi

echo "== Building frontend =="
(cd frontend && npm run build)

echo "== Installing pyinstaller into .venv =="
.venv/bin/pip install pyinstaller

echo "== Running PyInstaller ($MODE) =="
rm -rf build dist
PACKAGE_MODE="$MODE" .venv/bin/pyinstaller packaging/roast-telemetry.spec --distpath dist --workpath build

echo
if [[ "$MODE" == "onefile" ]]; then
  echo "Built: dist/Roast Telemetry"
  echo "A genuinely single file -- copy/tar just that, nothing else needed alongside it"
  echo "(chmod +x it if the executable bit doesn't survive however you transferred it)."
else
  echo "Built: dist/Roast Telemetry/"
  echo "Tar/zip the whole folder to distribute it -- 'Roast Telemetry' inside needs the rest"
  echo "of the folder alongside it, can't be moved alone"
  echo "(chmod +x it if the executable bit doesn't survive however you transferred the archive)."
fi
echo "No Gatekeeper/SmartScreen-equivalent warning on Linux -- runs straight away."
echo "Needs a real desktop tray (GTK/AppIndicator/Ayatana) to show the icon -- a bare WSL2"
echo "shell without WSLg won't display one even though the binary itself runs fine."
