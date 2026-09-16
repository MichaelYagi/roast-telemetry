#!/usr/bin/env bash
# Starts a fake roaster/meter for testing modbus_live/ms6514_live without
# real hardware -- wraps the socat-pair + hardware_fakes dance from
# docs/modbus/fz-94-usb.html / hardware_fakes/README.md into one command.
#
# Usage:
#   scripts/fake-hardware.sh fz94       # Coffee-Tech FZ-94 (Direct Modbus)
#   scripts/fake-hardware.sh ms6514     # Mastech MS6514 meter (Direct USB)
#
# Prints the serial port to paste into the app once it's up, and cleans
# up both the socat process and the fake on Ctrl+C. Picks a fresh link
# name every run (suffixed with this shell's own PID) rather than a fixed
# one -- reusing the same name across restarts carries over the fake's
# own internal thermal clock from the previous run, corrupting timing
# (see hardware_fakes/README.md).
set -euo pipefail
cd "$(dirname "$0")/.."

KIND="${1:-}"
if [[ "$KIND" != "fz94" && "$KIND" != "ms6514" ]]; then
  echo "Usage: $0 fz94|ms6514" >&2
  exit 1
fi

if ! command -v socat >/dev/null; then
  echo "socat is required (sudo apt install socat) -- see hardware_fakes/README.md" >&2
  exit 1
fi

LINK="/tmp/ttyFAKE_${KIND}_$$"
APP_LINK="${LINK}_APP"

SOCAT_PID=""
FAKE_PID=""
cleanup() {
  echo
  echo "Stopping fake hardware..."
  [[ -n "$FAKE_PID" ]] && kill "$FAKE_PID" 2>/dev/null || true
  [[ -n "$SOCAT_PID" ]] && kill "$SOCAT_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

socat -d -d "pty,raw,echo=0,link=$LINK" "pty,raw,echo=0,link=$APP_LINK" &
SOCAT_PID=$!

# Give socat a moment to actually create the linked ports before the fake
# tries to open one -- it's created asynchronously right after the
# process starts, not necessarily before this script's next line runs.
for _ in $(seq 1 50); do
  [[ -e "$LINK" ]] && break
  sleep 0.1
done
if [[ ! -e "$LINK" ]]; then
  echo "socat didn't create $LINK in time" >&2
  exit 1
fi

if [[ "$KIND" == "fz94" ]]; then
  PYTHONPATH=. .venv/bin/python -m hardware_fakes.modbus_fz94 --port "$LINK" --quiet &
else
  PYTHONPATH=. .venv/bin/python -m hardware_fakes.ms6514_device --port "$LINK" --quiet &
fi
FAKE_PID=$!

echo
echo "Fake $KIND hardware is running."
echo "In the app's Configure Roast form, use serial port:"
echo
echo "    $APP_LINK"
echo
echo "Press Ctrl+C to stop."
wait "$FAKE_PID"
