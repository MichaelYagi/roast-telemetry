#!/usr/bin/env bash
# Sets up everything needed to run Roast Telemetry from a fresh clone --
# Python 3.10+ and Node.js/npm if either is missing (asks before touching
# anything system-wide, via sudo apt-get or brew), then the backend venv
# and frontend npm dependencies. socat is offered too, but it's optional
# -- only scripts/fake-hardware.sh (testing without real hardware) needs
# it, not the app itself.
#
# Covers macOS and Linux/WSL2 (the two setup tracks docs/getting-started.html
# treats as identical). For native Windows PowerShell, see install.ps1
# instead, which also offers to bootstrap this inside WSL2 (if present)
# for fake-hardware.sh's sake.
#
# Usage: scripts/install.sh
set -euo pipefail
cd "$(dirname "$0")/.."

OS="$(uname -s)"
IS_MAC=0
[[ "$OS" == "Darwin" ]] && IS_MAC=1

have() { command -v "$1" >/dev/null 2>&1; }

# y/N prompt -- default no, since these commands touch system state
# (sudo apt-get / brew), not just this repo. Never call this bare; always
# `if ask "..."; then ... fi` -- a bare call would trip `set -e` the
# moment someone answers no (a non-zero exit from a plain statement, not
# an if-condition, aborts the whole script under -e).
ask() {
  local reply
  read -r -p "$1 [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]]
}

echo "== Roast Telemetry setup =="
echo

# --- Homebrew (macOS only) -- bootstraps itself if missing, so it's not
# a prerequisite you have to already have; everything below (Python/
# Node/socat) leans on `have brew` and just quietly skips its own
# offer if this was declined or failed, same as always. ---
if [[ "$IS_MAC" -eq 1 ]] && ! have brew; then
  echo "Homebrew not found -- needed to install Python/Node/socat automatically on macOS."
  if ask "Install Homebrew now via its official installer (https://brew.sh)?"; then
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # A fresh install isn't on PATH yet in this shell -- Homebrew's own
    # installer prints the permanent fix (add this to your shell
    # profile) at the end, but this script still needs `brew` usable
    # for the rest of *this* run. Apple Silicon -> /opt/homebrew, Intel
    # -> /usr/local -- the two locations the installer itself picks
    # based on CPU architecture.
    if [[ -x /opt/homebrew/bin/brew ]]; then
      eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [[ -x /usr/local/bin/brew ]]; then
      eval "$(/usr/local/bin/brew shellenv)"
    fi
  fi
  echo
fi

# --- Python 3.10+ ---
PYTHON=""
for candidate in python3 python; do
  if have "$candidate" && "$candidate" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done

if [[ -z "$PYTHON" ]]; then
  echo "Python 3.10+ not found."
  if [[ "$IS_MAC" -eq 1 ]]; then
    if have brew; then
      if ask "Install it now via 'brew install python3'?"; then
        brew install python3
        have python3 && PYTHON=python3
      fi
    else
      echo "Homebrew still isn't available (declined above, or its install failed) -- install it from https://brew.sh, then re-run this script."
    fi
  else
    if have apt-get; then
      if ask "Install it now via 'sudo apt-get install -y python3 python3-venv python3-pip'?"; then
        sudo apt-get update
        sudo apt-get install -y python3 python3-venv python3-pip
        have python3 && PYTHON=python3
      fi
    else
      echo "No apt-get found -- install Python 3.10+ for your distro, then re-run this script."
    fi
  fi
fi

if [[ -z "$PYTHON" ]]; then
  echo "Python 3.10+ is required -- install it, then re-run this script." >&2
  exit 1
fi
echo "Using $("$PYTHON" --version)"

# --- tkinter -- needed for scripts/tray_app.py's Port/Host/save-logs
# dialogs, not the app itself. Not guaranteed just because Python is
# present, on either platform this section covers -- both split it out
# of the main Python package: Debian/Ubuntu into python3-tk (apt),
# Homebrew's python formula into a separate python-tk@X.Y formula
# matching the installed Python's own minor version. Confirmed live:
# a fresh Homebrew-installed Python on macOS raised "No module named
# '_tkinter'" the first time the tray actually tried to start --
# Homebrew's python formula doesn't bundle Tk support at all, so this
# isn't a Linux-only gap the way it looked at first. ---
if ! "$PYTHON" -c "import tkinter" 2>/dev/null; then
  echo
  echo "tkinter not found -- needed for the tray icon's Port/Host/save-logs dialogs (scripts/tray.sh), not the app itself."
  if [[ "$IS_MAC" -eq 1 ]]; then
    PY_MINOR="$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    if have brew; then
      if ask "Install it now via 'brew install python-tk@$PY_MINOR'?"; then
        brew install "python-tk@$PY_MINOR"
      fi
    else
      echo "Homebrew isn't available -- install python-tk@$PY_MINOR (or whatever matches your Python's own version) yourself if you want the tray icon."
    fi
  elif have apt-get; then
    if ask "Install it now via 'sudo apt-get install -y python3-tk'?"; then
      sudo apt-get install -y python3-tk
    fi
  else
    echo "No apt-get found -- install your distro's tkinter package for Python 3, then re-run this script if you want the tray icon."
  fi
fi

# --- Node.js / npm ---
if ! have node || ! have npm; then
  echo
  echo "Node.js/npm not found (needed to build the frontend)."
  if [[ "$IS_MAC" -eq 1 ]]; then
    if have brew; then
      if ask "Install it now via 'brew install node'?"; then
        brew install node
      fi
    else
      echo "Homebrew still isn't available (declined above, or its install failed) -- install it from https://brew.sh, then re-run this script."
    fi
  else
    if have apt-get; then
      if ask "Install it now via 'sudo apt-get install -y nodejs npm'?"; then
        sudo apt-get update
        sudo apt-get install -y nodejs npm
      fi
    else
      echo "No apt-get found -- install Node.js 18+ for your distro, then re-run this script."
    fi
  fi
fi

if ! have node || ! have npm; then
  echo "Node.js/npm is required -- install it, then re-run this script." >&2
  exit 1
fi
echo "Using node $(node --version), npm $(npm --version)"

# --- socat (optional -- only scripts/fake-hardware.sh needs it) ---
if ! have socat; then
  echo
  echo "socat not found -- only needed for scripts/fake-hardware.sh (testing without real hardware), skip this if you don't need that."
  if [[ "$IS_MAC" -eq 1 ]]; then
    if have brew; then
      if ask "Install it now via 'brew install socat'?"; then
        brew install socat
      fi
    fi
  else
    if have apt-get; then
      if ask "Install it now via 'sudo apt-get install -y socat'?"; then
        sudo apt-get install -y socat
      fi
    fi
  fi
fi

# --- Backend venv ---
echo
if [[ -d .venv ]]; then
  echo "Reusing existing .venv"
else
  echo "Creating .venv..."
  "$PYTHON" -m venv .venv
fi
echo "Installing backend dependencies..."
.venv/bin/pip install --upgrade pip >/dev/null
.venv/bin/pip install -r backend/requirements.txt
# Tray icon deps too (pystray/Pillow) -- installs fine even on a
# headless/WSL2 box with no display (pure Python packages, no X server
# needed until scripts/tray.sh actually *runs*), so there's no reason to
# gate this behind a prompt or a separate step; see scripts/tray_app.py.
echo "Installing tray icon dependencies..."
.venv/bin/pip install -r scripts/tray_requirements.txt

# --- Frontend deps ---
echo
echo "Installing frontend dependencies (npm install)..."
(cd frontend && npm install)

# --- Linux desktop launcher paths ---
# "Roast Telemetry.desktop" needs the repo's real absolute path baked
# into both Exec= and Icon=. %k (the Desktop Entry spec's field code
# for "location of this .desktop file") was tried twice -- embedded in
# a quoted argument first, then correctly as a spec-compliant trailing
# unquoted word (`bash -c '...' bash %k`, read back as $1) -- and both
# failed on a real Raspberry Pi OS desktop (confirmed live, twice). The
# second attempt was genuinely spec-compliant, so the conclusion isn't
# "used it wrong" anymore, it's that this file manager's execution path
# for this file (its Execute/Execute in Terminal/Open chooser
# specifically) doesn't perform field-code substitution here at all,
# regardless of how it's written. Not chasing %k further -- baking in
# the real path directly has no such dependency on file-manager-
# specific behavior.
#
# Both keys get the same treatment: Exec='s cd target and Icon= are
# each matched by their current content (whatever that is -- the
# committed placeholder on a first run, or a stale absolute path left
# over from a previous run at a different location) and replaced with
# the real path, so a moved/re-cloned repo gets fixed on the next run
# too, not just handled once. Rewriting this every run is harmless
# (idempotent). Skipped on WSL2/headless -- harmless there too (nothing
# reads this file without a real desktop), just pointless.
if [[ "$IS_MAC" -eq 0 ]] && [[ -f "Roast Telemetry.desktop" ]]; then
  REPO_ROOT_ABS="$(pwd)"
  ICON_ABS="$REPO_ROOT_ABS/frontend/public/icon-256x256.png"
  sed -i "s|cd \"[^\"]*\" && ./scripts/start.sh|cd \"$REPO_ROOT_ABS\" \&\& ./scripts/start.sh|" "Roast Telemetry.desktop"
  if grep -q "^Icon=" "Roast Telemetry.desktop"; then
    sed -i "s|^Icon=.*|Icon=$ICON_ABS|" "Roast Telemetry.desktop"
  else
    sed -i "/^Exec=/a Icon=$ICON_ABS" "Roast Telemetry.desktop"
  fi
fi

echo
echo "Done. Next:"
echo "  scripts/run-server.sh          # build + start the app at http://localhost:8000"
echo "  scripts/tray.sh                # optional -- a tray icon instead of the terminal (needs a real desktop, not WSL2)"
echo "  scripts/fake-hardware.sh fz94  # optional -- test without real hardware"
