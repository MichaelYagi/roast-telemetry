#!/usr/bin/env bash
# Double-click this to run Roast Telemetry -- installs first if this is
# a fresh clone, otherwise goes straight to the tray icon. Finder runs a
# .command file with your home directory as cwd, not this file's own
# folder -- the cd below is what actually anchors it back to the repo.
cd "$(dirname "$0")"
./scripts/start.sh
echo
read -p "Press Enter to close this window..." _
