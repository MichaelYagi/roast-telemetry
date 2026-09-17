#!/usr/bin/env bash
# Double-click in Finder to run scripts/install.sh without opening
# Terminal yourself. Finder runs a .command file with your home
# directory as cwd, not this file's own folder -- the cd below is what
# actually anchors it back to the repo.
cd "$(dirname "$0")"
./scripts/install.sh
echo
read -p "Press Enter to close this window..." _
