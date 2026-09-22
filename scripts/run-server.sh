#!/usr/bin/env bash
# Runs the backend -- wraps the PYTHONPATH=. + uvicorn invocation from
# docs/getting-started.html so there's nothing to remember, just run
# this.
#
# Rebuilds the frontend first *when it's out of date*. The backend serves
# whatever's already sitting in frontend/dist/ and never rebuilds it for
# you (see main.py's static-file fallback) -- a stale build silently
# keeps serving old code, including old auth/login behavior, with no
# error of any kind (confirmed live: an old pre-auth build let anyone
# straight into the app with no login screen, even though the backend
# itself was correctly rejecting every API call). "Out of date" = dist/
# is missing, or anything under frontend/src, public, index.html,
# vite.config.js or package.json is newer than dist/index.html -- same
# check the tray icon does (see tray_app.py's _frontend_needs_rebuild),
# so a restart with nothing changed doesn't pay for a rebuild. Use
# --force-build to rebuild regardless (e.g. after `npm install` changed
# dependencies without touching package.json), --skip-build to never.
#
# Also runs `npm install` first, on its own equivalent staleness check
# (package.json/package-lock.json newer than node_modules' own record of
# the last install), whenever a build is about to happen -- otherwise a
# `git pull` that added a new frontend dependency (without a matching
# `npm install`) fails the build with a bare Rollup "failed to resolve
# import" error that gives no hint what to actually do about it
# (confirmed live: exactly this, jszip added to package.json for the PDF
# export feature, an existing checkout that had pulled the change but
# never run npm install).
#
# Usage:
#   scripts/run-server.sh                # rebuild if stale, then port 8000, localhost only
#   scripts/run-server.sh 7890           # a different port
#   scripts/run-server.sh --reload       # auto-restart on backend code changes
#   scripts/run-server.sh --skip-build 7890
#   scripts/run-server.sh --force-build  # rebuild even if it looks current
#   scripts/run-server.sh --lan          # reachable from other devices on your LAN
#   scripts/run-server.sh --host 0.0.0.0 # same as --lan, spelled out
#   scripts/run-server.sh --help         # print this, with every option
set -euo pipefail
cd "$(dirname "$0")/.."

usage() {
  cat <<'EOF'
Usage: scripts/run-server.sh [options] [port]

Options:
  <port>              Port to listen on (default: 8000)
  --host <address>    Bind to a specific address (default: 127.0.0.1, localhost only)
  --lan               Same as --host 0.0.0.0 -- reachable from other devices on your LAN
  --reload            Auto-restart the backend on code changes
  --skip-build        Never rebuild the frontend, even if it looks stale
  --force-build       Rebuild the frontend even if it looks current
  -h, --help          Show this help and exit

Examples:
  scripts/run-server.sh                # rebuild if stale, then port 8000, localhost only
  scripts/run-server.sh 7890           # a different port
  scripts/run-server.sh --reload       # auto-restart on backend code changes
  scripts/run-server.sh --skip-build 7890
  scripts/run-server.sh --force-build  # rebuild even if it looks current
  scripts/run-server.sh --lan          # reachable from other devices on your LAN
  scripts/run-server.sh --host 0.0.0.0 # same as --lan, spelled out
  scripts/run-server.sh --lan 7890     # combine freely: LAN-reachable, on port 7890
EOF
}

# Checked before the .venv sanity check below (and before actually doing
# anything) so --help always works, even on a machine with a broken/missing
# venv -- the whole point of a help flag is to not require a working setup
# first.
for arg in "$@"; do
  if [[ "$arg" == "-h" || "$arg" == "--help" ]]; then
    usage
    exit 0
  fi
done

# A .venv created by native Windows Python (install.ps1, or a manual
# `python -m venv` from PowerShell) has .venv/Scripts/python.exe, not
# .venv/bin/python -- WSL2/Linux bash can't execute those .exe binaries
# at all, so every .venv/bin/... call below would otherwise fail with a
# bare "No such file or directory" and no explanation (confirmed live:
# exactly this, on a repo folder shared between native Windows and
# WSL2). Catch it here with a clear message instead.
if [[ -d .venv ]] && [[ ! -e .venv/bin/python ]] && [[ -e .venv/Scripts/python.exe ]]; then
  echo "$(basename "$0"): .venv was created by native Windows Python (.venv/Scripts/python.exe exists, .venv/bin/python doesn't) -- that can't run from WSL2/Linux." >&2
  echo "Rename or delete .venv, then run scripts/install.sh from this WSL2 shell to create a proper Linux one." >&2
  exit 1
fi

# No venv at all (a fresh clone, install.sh never run) -- without this,
# the uvicorn invocation at the bottom fails with a bare "No such file or
# directory" and no hint what to actually do about it.
if [[ ! -e .venv/bin/uvicorn ]]; then
  echo "$(basename "$0"): no backend virtual environment found (.venv/bin/uvicorn missing)." >&2
  echo "Run scripts/install.sh first -- it creates .venv and installs everything needed." >&2
  exit 1
fi

RELOAD=()
SKIP_BUILD=0
FORCE_BUILD=0
PORT=8000
HOST_ADDR=127.0.0.1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --reload) RELOAD=(--reload) ;;
    --skip-build) SKIP_BUILD=1 ;;
    --force-build) FORCE_BUILD=1 ;;
    --lan) HOST_ADDR=0.0.0.0 ;;
    --host)
      HOST_ADDR="${2:-}"
      shift
      ;;
    *) PORT="$1" ;;
  esac
  shift
done

# True (exit 0) when frontend/dist/ is missing or any frontend source file
# is newer than dist/index.html. `find ... | head -n 1` can SIGPIPE find
# once head has its line -- harmless, but under `set -o pipefail` it
# would make the assignment fail, hence the `|| true`. Paths that don't
# exist just make find complain on stderr (suppressed) and carry on.
frontend_is_stale() {
  local dist=frontend/dist/index.html newer
  [[ -f "$dist" ]] || return 0
  newer="$(find frontend/src frontend/public frontend/index.html frontend/vite.config.js frontend/package.json \
    -newer "$dist" 2>/dev/null | head -n 1)" || true
  [[ -n "$newer" ]]
}

# Same idea, for whether `npm install` itself needs to run first. npm
# writes node_modules/.package-lock.json on every install -- a snapshot of
# the lockfile it actually installed from -- so comparing that against
# package.json/package-lock.json (not node_modules/ itself, which doesn't
# change just because its contents are stale) is what catches "the
# lockfile moved on since the last install".
node_modules_is_stale() {
  local marker=frontend/node_modules/.package-lock.json newer
  [[ -d frontend/node_modules ]] || return 0
  [[ -f "$marker" ]] || return 0
  newer="$(find frontend/package.json frontend/package-lock.json \
    -newer "$marker" 2>/dev/null | head -n 1)" || true
  [[ -n "$newer" ]]
}

WILL_BUILD=0
if [[ "$SKIP_BUILD" -eq 1 ]]; then
  echo "Skipping frontend build (--skip-build)."
elif [[ "$FORCE_BUILD" -eq 1 ]]; then
  WILL_BUILD=1
elif frontend_is_stale; then
  WILL_BUILD=1
fi

if [[ "$WILL_BUILD" -eq 1 ]]; then
  if node_modules_is_stale; then
    echo "frontend/package.json changed since the last npm install -- installing dependencies..."
    (cd frontend && npm install)
  fi
  if [[ "$FORCE_BUILD" -eq 1 ]]; then
    echo "Building frontend (--force-build)..."
  else
    echo "Frontend changed since the last build (or was never built) -- rebuilding..."
  fi
  (cd frontend && npm run build)
elif [[ "$SKIP_BUILD" -ne 1 ]]; then
  echo "Frontend build is current -- skipping rebuild (--force-build to rebuild anyway)."
fi

echo "Starting Roast Telemetry on http://localhost:$PORT (Ctrl+C to stop)"
if [[ "$HOST_ADDR" == "0.0.0.0" ]]; then
  # Best-effort LAN IP detection so there's something concrete to type on
  # another device, not just "find your own IP" -- falls back silently
  # (empty LAN_IP, message just omits it) if neither command exists/works,
  # covering Linux/WSL2 (hostname -I) and macOS (ipconfig getifaddr,
  # guessing the common Wi-Fi/Ethernet interface names) without hard-
  # failing the whole script over a convenience feature.
  #
  # The trailing "|| true" on each assignment matters under `set -eo
  # pipefail`: macOS's hostname has no -I flag at all, so that pipeline
  # exits non-zero, pipefail propagates that to the assignment itself,
  # and without "|| true" set -e would silently kill the whole script
  # right here -- confirmed live (script printed "Starting Roast
  # Telemetry..." then exited with no error and never started uvicorn).
  LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')" || true
  if [[ -z "$LAN_IP" ]]; then
    LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)" || true
  fi
  if [[ -n "$LAN_IP" ]]; then
    echo "Also reachable from other devices on your LAN at: http://$LAN_IP:$PORT"
  else
    echo "Also reachable from other devices on your LAN -- find this machine's own IP (ip addr / ifconfig) and use http://<that IP>:$PORT"
  fi
fi

# set +u/-u around just the array expansion below -- macOS ships bash 3.2
# by default (no updates since 2007, licensing reasons), where expanding
# an empty array under `set -u` raises "unbound variable" even though the
# array was declared. The tempting fix, "${RELOAD[@]:-}", actually
# introduces a *different* bug instead of avoiding this one: on an empty
# array it expands to one stray empty-string argument (confirmed: $# is
# 1, not 0), which would get passed to uvicorn as a bogus extra CLI
# argument. Toggling nounset off for just this line is what actually
# reproduces plain bash's own correct empty-array-expands-to-zero-args
# behavior on both old and new bash.
set +u
PYTHONPATH=. .venv/bin/uvicorn backend.app.main:app --host "$HOST_ADDR" --port "$PORT" "${RELOAD[@]}"
