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
# --yes / -y: never prompt -- answers yes to every *required* install
# (Homebrew, Python, Node) so this can run unattended (CI, provisioning a
# Raspberry Pi over SSH). Optional extras (socat) are skipped under
# --yes rather than auto-installed: "do what's needed" isn't "install
# everything nice-to-have". Note that means it will run sudo apt-get /
# brew / Homebrew's own installer without asking -- only pass it where
# that's intended.
#
# Usage: scripts/install.sh [--yes]
set -euo pipefail
cd "$(dirname "$0")/.."

ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    -y|--yes) ASSUME_YES=1 ;;
    *)
      echo "Unrecognized argument: $arg (usage: scripts/install.sh [--yes])" >&2
      exit 1
      ;;
  esac
done

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
  if [[ "$ASSUME_YES" -eq 1 ]]; then
    echo "$1 [y/N] y (--yes)"
    return 0
  fi
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

# (No tkinter/AppIndicator setup here anymore -- those were only for the
# native-Linux tray icon, which is deliberately unsupported. See
# tray_app.py's own docstring. Linux/WSL2 runs the server in a terminal
# via run-server.sh; macOS's tray needs neither.)

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
# Skipped entirely under --yes -- see this file's header: --yes means
# "install what's required", not "install every optional extra too".
if ! have socat && [[ "$ASSUME_YES" -eq 0 ]]; then
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

echo
echo "Done. Next:"
echo "  scripts/run-server.sh          # build (if needed) + start the app at http://localhost:8000"
if [[ "$IS_MAC" -eq 1 ]]; then
  echo "  scripts/tray.sh                # optional -- a menu-bar icon instead of the terminal"
fi
echo "  scripts/fake-hardware.sh fz94  # optional -- test without real hardware (needs socat)"
