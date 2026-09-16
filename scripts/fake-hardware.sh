#!/usr/bin/env bash
# Starts a fake roaster/meter for testing modbus_live/ms6514_live without
# real hardware -- wraps the socat-pair + hardware_fakes dance from
# docs/modbus/fz-94-usb.html / hardware_fakes/README.md into one command.
#
# Usage:
#   scripts/fake-hardware.sh fz94              # WSL-internal link, for an app also running in WSL
#   scripts/fake-hardware.sh ms6514
#   scripts/fake-hardware.sh fz94 --tcp        # TCP bridge on port 5020, for an app running natively on Windows
#   scripts/fake-hardware.sh fz94 --tcp 5030   # TCP bridge on a specific port
#
# --tcp matters because a WSL-internal /tmp/... path only exists inside
# WSL's own filesystem -- a native-Windows process (e.g. the app run via
# scripts/run-server.ps1, the recommended way to reach a *real* roaster's
# COM port) can't open it at all, no matter how the path is written. With
# --tcp, socat's second end is a TCP listener instead of a second linked
# port, and pyserial treats a socket://host:port URL as a live serial
# connection -- paste socket://127.0.0.1:<port> into the app's Serial
# port field instead of a /tmp/... path. Without a real roaster in the
# picture, this is more setup than you need -- just run the app in WSL
# too and skip --tcp entirely.
#
# Prints the value to paste into the app once it's up, and cleans up
# both the socat process and the fake on Ctrl+C. Picks a fresh link name
# every run (suffixed with this shell's own PID) rather than a fixed one
# -- reusing the same name across restarts carries over the fake's own
# internal thermal clock from the previous run, corrupting timing (see
# hardware_fakes/README.md).
set -euo pipefail
cd "$(dirname "$0")/.."

KIND="${1:-}"
if [[ "$KIND" != "fz94" && "$KIND" != "ms6514" ]]; then
  echo "Usage: $0 fz94|ms6514 [--tcp [port]]" >&2
  exit 1
fi
shift || true

TCP_MODE=0
TCP_PORT=5020
while [[ $# -gt 0 ]]; do
  case "$1" in
    --tcp)
      TCP_MODE=1
      if [[ "${2:-}" =~ ^[0-9]+$ ]]; then
        TCP_PORT="$2"
        shift
      fi
      ;;
    *)
      echo "Unrecognized argument: $1" >&2
      exit 1
      ;;
  esac
  shift
done

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

if [[ "$TCP_MODE" -eq 1 ]]; then
  # fork -- accepts more than one connection attempt over the run (e.g.
  # the app reconnecting) instead of exiting after the first client
  # disconnects.
  socat -d -d "pty,raw,echo=0,link=$LINK" "TCP-LISTEN:$TCP_PORT,reuseaddr,fork" &
else
  socat -d -d "pty,raw,echo=0,link=$LINK" "pty,raw,echo=0,link=$APP_LINK" &
fi
SOCAT_PID=$!

# Give socat a moment to actually create the linked port(s) before the
# fake tries to open one -- it's created asynchronously right after the
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
if [[ "$TCP_MODE" -eq 1 ]]; then
  echo "In the app's Configure Roast form (running natively on Windows), use serial port:"
  echo
  echo "    socket://127.0.0.1:$TCP_PORT"
  echo
  echo "(WSL2 forwards localhost ports to Windows automatically -- no extra setup needed.)"
else
  echo "In the app's Configure Roast form, use serial port:"
  echo
  echo "    $APP_LINK"
fi
echo
echo "Press Ctrl+C to stop."
wait "$FAKE_PID"
