#!/usr/bin/env bash
# Runs the backend -- wraps the PYTHONPATH=. + uvicorn invocation from
# docs/getting-started.html so there's nothing to remember, just run
# this.
#
# Rebuilds the frontend first by default. The backend serves whatever's
# already sitting in frontend/dist/ and never rebuilds it for you (see
# main.py's static-file fallback) -- a stale build silently keeps serving
# old code, including old auth/login behavior, with no error of any
# kind (confirmed live: an old pre-auth build let anyone straight into
# the app with no login screen, even though the backend itself was
# correctly rejecting every API call). Pass --skip-build once you know
# your build is current and want faster iteration.
#
# Usage:
#   scripts/run-server.sh                # rebuild, then port 8000, localhost only
#   scripts/run-server.sh 7890           # a different port
#   scripts/run-server.sh --reload       # auto-restart on backend code changes
#   scripts/run-server.sh --skip-build 7890
#   scripts/run-server.sh --lan          # reachable from other devices on your LAN
#   scripts/run-server.sh --host 0.0.0.0 # same as --lan, spelled out
set -euo pipefail
cd "$(dirname "$0")/.."

RELOAD=()
SKIP_BUILD=0
PORT=8000
HOST_ADDR=127.0.0.1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --reload) RELOAD=(--reload) ;;
    --skip-build) SKIP_BUILD=1 ;;
    --lan) HOST_ADDR=0.0.0.0 ;;
    --host)
      HOST_ADDR="${2:-}"
      shift
      ;;
    *) PORT="$1" ;;
  esac
  shift
done

if [[ "$SKIP_BUILD" -eq 0 ]]; then
  echo "Building frontend (pass --skip-build to skip this)..."
  (cd frontend && npm run build)
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
