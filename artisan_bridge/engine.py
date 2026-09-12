"""Mirrors a real, running Artisan instance's live feed for remote viewing.

Unlike ``simulator`` and ``alog_playback``, this doesn't fabricate or
replay data -- it connects to an *actual Artisan install, connected to
actual hardware, roasting for real*, and streams what it's seeing out to
this platform's own web UI so other people can watch along in a browser.

Protocol, verified against Artisan's own source (not guessed) across
versions v2.4.2 (2020) through v4.2.0 (current) -- both use the same
wire protocol, just a different internal server implementation:

- Artisan can run a "WebLCDs" WebSocket server (Config -> Curves -> UI
  tab; default port 8080), implemented in ``artisanlib/weblcds.py`` and
  fed from ``artisanlib/canvas.py``'s ``updateWebLCDs()``.
- Clients connect to ``ws://<host>:<port>/websocket``. Sending an empty
  text frame (``""``) immediately after connecting makes current
  versions reply with their last known state right away; v2.4.2's
  server ignores this (added later) and just waits for the next natural
  update -- harmless either way, just a possible brief initial wait on
  old versions.
- The server pushes partial JSON text frames shaped like
  ``{"data": {"bt": "<num-str>", "et": "<num-str>", "time": "MM:SS"}}``
  -- fields are only present when they changed, values are plain
  formatted numbers (no unit) or a placeholder like ``"--"`` before a
  reading exists. It also independently pushes
  ``{"alert": {"text": ..., "title": ..., "timeout": ...}}`` for
  Artisan's own alarm popups, which this engine ignores.

What WebLCDs does **not** carry: RoR, roast milestone events (Charge/
Turning Point/Dry End/FC/Drop), or any control channel. Nor does any
other Artisan-exposed mechanism -- checked Autosave too (``canvas.py``'s
``OffRecorder``): it only writes the ``.alog`` once, *after* Drop, so
there's no continuously-updating file to poll during the roast either.
Checked the WebSocket-JSON device protocol and MQTT support too: both
are Artisan pulling sensor readings *in* from an external device, never
accepting commands from one. This isn't an integration gap on our end:
even Artisan's own operator marks these milestones by hand (listening
for cracks, watching color) -- there's no live feed of them to relay,
and no write-back channel to relay control through either.

So this engine detects CHARGE/TURNING_POINT/DRY_END/FC_START itself,
independently of Artisan, and computes RoR itself -- both via
``roast_heuristics.LiveRoastDetector``, the same logic ``modbus_bridge``
uses. There's still no control channel here -- this is a read-only
mirror; for real control of this specific setup, see ``modbus_bridge``
(which talks to the roaster directly, bypassing Artisan -- mutually
exclusive with Artisan also holding that port).
"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Optional

import websockets.sync.client as ws_sync
from roast_heuristics import LiveRoastDetector

_MMSS_RE = re.compile(r"^(-?\d+)[:h](\d{2})$")


def _parse_number(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_mmss(value: str) -> Optional[float]:
    """Parses Artisan's ``stringfromseconds`` output ("MM:SS", or "Hh MM"
    for roasts over an hour) back into elapsed seconds."""
    if not isinstance(value, str):
        return None
    match = _MMSS_RE.match(value.strip())
    if not match:
        return None
    major, minor = int(match.group(1)), int(match.group(2))
    sign = -1 if major < 0 else 1
    unit = 3600.0 if "h" in value else 60.0
    return sign * (abs(major) * unit + minor * (60.0 if "h" in value else 1.0))


class ArtisanBridgeEngine:
    def __init__(
        self,
        host: str,
        port: int = 8080,
        path: str = "websocket",
        dry_end_c: Optional[float] = 160.0,
        fc_start_c: Optional[float] = 196.0,
    ):
        if not host:
            raise ValueError("host is required to connect to a running Artisan's WebLCDs server")
        self.uri = f"ws://{host}:{port}/{path.lstrip('/')}"
        self._detector = LiveRoastDetector(dry_end_c=dry_end_c, fc_start_c=fc_start_c)

        self._lock = threading.Lock()
        self._latest_bt: Optional[float] = None
        self._latest_et: Optional[float] = None
        self._latest_time_s: Optional[float] = None
        self._connected = False
        self._last_error: Optional[str] = None
        self._last_time_s: float = 0.0

        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # -- background connection loop ----------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                with ws_sync.connect(self.uri, open_timeout=5) as ws:
                    with self._lock:
                        self._connected = True
                        self._last_error = None
                    ws.send("")  # request the last known state immediately (no-op on old servers)
                    while not self._stop.is_set():
                        try:
                            raw = ws.recv(timeout=1)
                        except TimeoutError:
                            continue
                        self._handle_message(raw)
            except Exception as exc:  # pragma: no cover - network/env dependent
                with self._lock:
                    self._connected = False
                    self._last_error = str(exc)
                if not self._stop.is_set():
                    time.sleep(2)  # backoff before reconnect attempt

    def _handle_message(self, raw) -> None:
        try:
            message = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return
        data = message.get("data")
        if not isinstance(data, dict):
            return
        with self._lock:
            if "bt" in data:
                self._latest_bt = _parse_number(data["bt"])
            if "et" in data:
                self._latest_et = _parse_number(data["et"])
            if "time" in data:
                parsed = _parse_mmss(data["time"])
                if parsed is not None:
                    self._latest_time_s = parsed

    # -- engine contract (matches simulator.SimulatorEngine / AlogPlayer) --------
    def tick(self, dt: float) -> dict:
        with self._lock:
            bt, et, time_s = self._latest_bt, self._latest_et, self._latest_time_s

        if time_s is None:
            time_s = self._last_time_s + dt
        self._last_time_s = time_s

        sample = self._detector.observe(time_s, bt, et)
        sample["heater_pct"] = None
        sample["fan_pct"] = None
        sample["drum_speed_pct"] = None
        return sample

    def get_new_events(self) -> list:
        return self._detector.get_new_events()

    def apply_command(self, cmd: dict) -> None:
        pass  # read-only mirror -- WebLCDs exposes no control channel

    def is_finished(self) -> bool:
        return False  # no end-of-roast signal from WebLCDs; stop manually from the UI

    def status(self) -> dict:
        with self._lock:
            return {
                "mode": "artisan_live",
                "uri": self.uri,
                "connected": self._connected,
                "last_error": self._last_error,
                "latest_bt": self._latest_bt,
                "latest_et": self._latest_et,
            }

    def close(self) -> None:
        self._stop.set()
