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

# Apple Silicon quirk: if this shell is itself running under Rosetta 2
# (x86_64 translation) but Homebrew is the native-ARM install (the
# default, /opt/homebrew), brew refuses to install anything at all --
# confirmed live: "Cannot install under Rosetta 2 in ARM default
# prefix (/opt/homebrew)!", with the fix being to prefix every brew
# invocation with `arch -arm64`. Wrapping brew itself here (resolved to
# its real path first, so the wrapper doesn't just call itself) means
# every brew install call below (Python, node, socat, python-tk) gets
# this fix for free, not just whichever one happened to be hit first.
if [[ "$IS_MAC" -eq 1 ]] && have brew && [[ "$(sysctl -n sysctl.proc_translated 2>/dev/null)" == "1" ]]; then
  BREW_BIN="$(command -v brew)"
  brew() { arch -arm64 "$BREW_BIN" "$@"; }
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

# --- tkinter (Linux only) -- needed for scripts/tray_app.py's
# Port/Host/save-logs dialogs on Windows/Linux, not the app itself, and
# not guaranteed just because Python is present -- Debian/Ubuntu split
# it out of the main python3 package into python3-tk (apt). NOT checked
# on macOS: tray_app.py deliberately doesn't use tkinter there at all
# -- Tk 9.0 (via Homebrew's python-tk@X.Y, which this used to offer to
# install) hard-crashes the whole process on a real Mac the instant it
# tries to create a window (an upstream Tk/macOS bug, confirmed live),
# so macOS uses osascript (AppleScript) instead, which needs nothing
# from Homebrew at all. ---
if [[ "$IS_MAC" -eq 0 ]] && ! "$PYTHON" -c "import tkinter" 2>/dev/null; then
  echo
  echo "tkinter not found -- needed for the tray icon's Port/Host/save-logs dialogs (scripts/tray.sh), not the app itself."
  if have apt-get; then
    if ask "Install it now via 'sudo apt-get install -y python3-tk'?"; then
      sudo apt-get install -y python3-tk
    fi
  else
    echo "No apt-get found -- install your distro's tkinter package for Python 3, then re-run this script if you want the tray icon."
  fi
fi

# --- AppIndicator/GTK bindings (Linux only) -- needed for the tray
# MENU to actually work, not just the icon itself. Without python3-gi
# + the AppIndicator GI typelib, pystray silently falls back to its own
# plain Xorg backend, which -- confirmed live, not just documented --
# shows the icon fine but implements no menu functionality at all (not
# a click-detection bug; that backend just doesn't have one). Debian/
# Ubuntu (Raspberry Pi OS included) package this as
# gir1.2-appindicator3-0.1, though newer releases have moved to the
# Ayatana fork under gir1.2-ayatanaappindicator3-0.1 -- tried in that
# order, falling back to the second name if the first isn't in this
# distro's repos.
if [[ "$IS_MAC" -eq 0 ]] && have apt-get; then
  if ! dpkg -s python3-gi gir1.2-gtk-3.0 >/dev/null 2>&1 || \
     { ! dpkg -s gir1.2-appindicator3-0.1 >/dev/null 2>&1 && ! dpkg -s gir1.2-ayatanaappindicator3-0.1 >/dev/null 2>&1; }; then
    echo
    echo "AppIndicator GTK bindings not found -- without them, the tray icon shows up but its menu won't open at all."
    if ask "Install them now via apt (python3-gi gir1.2-gtk-3.0 gir1.2-appindicator3-0.1)?"; then
      sudo apt-get update
      if ! sudo apt-get install -y python3-gi gir1.2-gtk-3.0 gir1.2-appindicator3-0.1; then
        echo "gir1.2-appindicator3-0.1 not found -- trying the newer Ayatana package name instead..."
        sudo apt-get install -y python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1
      fi
    fi
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
# A .venv created by native Windows Python (install.ps1, or a manual
# `python -m venv` from PowerShell) has .venv/Scripts/python.exe, not
# .venv/bin/python -- confirmed live: reusing one of these from WSL2/
# Linux (this script) says "Reusing existing .venv" here, then fails on
# the very next line (.venv/bin/pip: No such file or directory) with no
# explanation of why. Catch it before that, not after.
if [[ -d .venv ]] && [[ ! -e .venv/bin/python ]] && [[ -e .venv/Scripts/python.exe ]]; then
  echo ".venv exists but was created by native Windows Python (.venv/Scripts/python.exe, no .venv/bin/python) -- that can't be reused from WSL2/Linux." >&2
  echo "Rename or delete .venv, then re-run this script to create a proper Linux one (your Windows one, if you still need it, can be recreated later via install.ps1)." >&2
  exit 1
fi
if [[ -d .venv ]]; then
  echo "Reusing existing .venv"
  # Retrofit: an existing venv predating the --system-site-packages
  # flag below won't have it -- flip pyvenv.cfg's own setting in place
  # instead of requiring a full recreate. Needed on Linux specifically
  # so the venv can actually see the AppIndicator GTK bindings just
  # installed above -- python3-gi is a system package, tied to the
  # system Python + GTK libraries, not something pip can install into
  # an isolated venv the normal way.
  if [[ "$IS_MAC" -eq 0 ]] && [[ -f .venv/pyvenv.cfg ]] && grep -q "^include-system-site-packages = false" .venv/pyvenv.cfg; then
    sed -i "s|^include-system-site-packages = false|include-system-site-packages = true|" .venv/pyvenv.cfg
  fi
else
  echo "Creating .venv..."
  if [[ "$IS_MAC" -eq 0 ]]; then
    "$PYTHON" -m venv --system-site-packages .venv
  else
    "$PYTHON" -m venv .venv
  fi
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
