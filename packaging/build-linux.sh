#!/usr/bin/env bash
# Builds a standalone, single-file Linux executable of Roast Telemetry --
# command-line only, no tray icon (see roast-telemetry-linux.spec's own
# header for why) -- you can hand to someone with no Python/Node
# installed at all. Must run natively on Linux (PyInstaller has no
# supported cross-compile path); the resulting binary is tied to the
# glibc version of whatever it was built on, same as any compiled Linux
# binary -- built in CI on ubuntu-latest, the common case.
#
# Prerequisite: scripts/install.sh already run once (sets up .venv with
# the backend dependencies). This script only adds pyinstaller on top of
# that -- no tray_requirements.txt install, unlike build-windows.ps1/
# build-macos.sh, since this build never has a tray to begin with.
#
# Usage:
#   packaging/build-linux.sh            # single-file executable (default)
#   packaging/build-linux.sh onedir     # a folder instead -- faster startup,
#                                        # nothing packed into one self-
#                                        # extracting file
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
PACKAGE_MODE="$MODE" .venv/bin/pyinstaller packaging/roast-telemetry-linux.spec --distpath dist --workpath build

# onefile: dist/roast-telemetry is the binary itself. onedir: it's a
# folder, and the binary of the same name sits inside it.
if [[ "$MODE" == "onefile" ]]; then
  chmod +x "dist/roast-telemetry"
  echo
  echo "Built: dist/roast-telemetry"
else
  chmod +x "dist/roast-telemetry/roast-telemetry"
  echo
  echo "Built: dist/roast-telemetry/ (the whole folder travels together)"
fi
echo "Run it with: ./roast-telemetry (add --host/--port to change from the 127.0.0.1:7890 default)"
echo "A download usually loses the executable bit -- chmod +x roast-telemetry first if it won't run."
