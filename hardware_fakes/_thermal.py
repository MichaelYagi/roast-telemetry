"""Shared roast physics for both hardware fakes.

Rather than each fake (Modbus, MS6514) inventing its own ad hoc
BT/ET behavior, they both drive the same ``simulator.SimulatorEngine`` --
the same thermal model the app's own simulator mode uses. This
means a fake roast looks and behaves consistently regardless of which
hardware path you're exercising through it, and control writes (where
applicable -- only the Modbus fake has any) actually move BT/ET the way
a real roaster would react to a burner/fan/drum change.
"""
from __future__ import annotations

import threading
import time

from simulator import SimulatorEngine


class ThermalDriver:
    """Ticks a SimulatorEngine forward in a background thread at real
    wall-clock speed, exposing thread-safe snapshots + control writes."""

    def __init__(self, tick_interval_s: float = 0.5):
        self.tick_interval_s = tick_interval_s
        self._engine = SimulatorEngine()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._lock:
            self._engine.start()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        # Deliberately doesn't auto-restart a new charge once the roast
        # finishes (bt/et just hold at their final cooled-down value --
        # SimulatorEngine.tick() is already a no-op once `_running` is
        # False). An earlier version looped forever "to stay usable as a
        # standing fixture", but that silently resets BT/ET back to a
        # fresh charge mid-recording if you don't stop the app-side roast
        # at exactly the right second -- corrupting the tail of the
        # `.alog` with an unmarked second Charge. Restart this process for
        # a fresh test roast instead; see hardware_fakes/README.md.
        while not self._stop.is_set():
            time.sleep(self.tick_interval_s)
            with self._lock:
                self._engine.tick(self.tick_interval_s)

    def snapshot(self) -> dict:
        """Current bt/et/heater_pct/fan_pct/drum_speed_pct/time_s."""
        with self._lock:
            return {
                "time_s": self._engine.time_s,
                "bt": self._engine.bt,
                "et": self._engine.et,
                "heater_pct": self._engine.heater_pct,
                "fan_pct": self._engine.fan_pct,
                "drum_speed_pct": self._engine.drum_speed_pct,
            }

    def apply_command(self, cmd: dict) -> None:
        with self._lock:
            self._engine.apply_command(cmd)
