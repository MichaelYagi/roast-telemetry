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
#   scripts/run-server.sh                # rebuild, then port 8000
#   scripts/run-server.sh 7890           # a different port
#   scripts/run-server.sh --reload       # auto-restart on backend code changes
#   scripts/run-server.sh --skip-build 7890
set -euo pipefail
cd "$(dirname "$0")/.."

RELOAD=()
SKIP_BUILD=0
PORT=8000
for arg in "$@"; do
  case "$arg" in
    --reload) RELOAD=(--reload) ;;
    --skip-build) SKIP_BUILD=1 ;;
    *) PORT="$arg" ;;
  esac
done

if [[ "$SKIP_BUILD" -eq 0 ]]; then
  echo "Building frontend (pass --skip-build to skip this)..."
  (cd frontend && npm run build)
fi

echo "Starting Roast Telemetry on http://localhost:$PORT (Ctrl+C to stop)"
PYTHONPATH=. .venv/bin/uvicorn backend.app.main:app --port "$PORT" "${RELOAD[@]}"
