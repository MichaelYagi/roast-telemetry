"""Fake Artisan "WebLCDs" WebSocket server standing in for a real Artisan
install, for testing ``artisan_bridge`` without another machine running
actual Artisan connected to actual hardware.

Protocol (see ``artisan_bridge/engine.py``'s docstring for the full
citation against Artisan's own source): a WebSocket server at
``ws://host:port/websocket`` that pushes JSON text frames shaped like
``{"data": {"bt": "<num-str>", "et": "<num-str>", "time": "MM:SS"}}`` --
values are strings, not JSON numbers, matching Artisan's own formatting.
A client sending an empty text frame right after connecting gets an
immediate reply with current state; this fake also just pushes state on
every incoming message and on a steady ~1s interval regardless, which is
harmless (matches the docstring's "harmless either way" note about older
Artisan versions that ignore the empty-frame convention).

BT/ET come from the same thermal model as the app's own Simulator mode
(see ``_thermal.py``). No control channel exists in the real protocol,
so this fake doesn't accept writes either -- matches
``ArtisanBridgeEngine.apply_command`` being a no-op.

Usage::

    python -m hardware_fakes.weblcds_server --port 8080

Then point the app's "Artisan Live Bridge" host/port fields at
127.0.0.1:8080.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time

from websockets.sync.server import serve

from ._thermal import ThermalDriver

PUSH_INTERVAL_S = 1.0


def _format_mmss(time_s: float) -> str:
    total = max(0, round(time_s))
    return f"{total // 60}:{total % 60:02d}"


def _payload(driver: ThermalDriver) -> str:
    snap = driver.snapshot()
    return json.dumps({
        "data": {
            "bt": f"{snap['bt']:.1f}" if snap["bt"] is not None else "--",
            "et": f"{snap['et']:.1f}" if snap["et"] is not None else "--",
            "time": _format_mmss(snap["time_s"]),
        }
    })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    driver = ThermalDriver()
    driver.start()

    def handler(ws) -> None:
        send_lock = threading.Lock()
        stop = threading.Event()

        def push_loop() -> None:
            while not stop.is_set():
                time.sleep(PUSH_INTERVAL_S)
                try:
                    with send_lock:
                        ws.send(_payload(driver))
                except Exception:
                    return

        pusher = threading.Thread(target=push_loop, daemon=True)
        pusher.start()
        if not args.quiet:
            print(f"client connected from {ws.remote_address}", file=sys.stderr)
        try:
            for _message in ws:
                # Real Artisan replies immediately to the initial empty
                # frame; we just reply to anything received, harmlessly.
                with send_lock:
                    ws.send(_payload(driver))
        finally:
            stop.set()
            if not args.quiet:
                print("client disconnected", file=sys.stderr)

    with serve(handler, args.host, args.port) as server:
        print(f"WebLCDs fake listening on ws://{args.host}:{args.port}/websocket", file=sys.stderr)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            driver.stop()


if __name__ == "__main__":
    main()
