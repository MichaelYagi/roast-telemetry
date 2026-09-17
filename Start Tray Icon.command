#!/usr/bin/env bash
# Double-click in Finder to launch the tray icon (scripts/tray.sh)
# without opening Terminal yourself. This window stays open for as long
# as the tray icon is running -- closing it closes the tray icon and
# stops the server too.
cd "$(dirname "$0")"
./scripts/tray.sh
