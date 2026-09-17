"""A system tray / menu-bar icon that starts and stops Roast Telemetry --
the GUI equivalent of run-server.sh/run-server.ps1, for someone who'd
rather click an icon than remember a terminal command. Works on Windows,
macOS, and a real Linux desktop (pystray needs an actual tray
implementation -- GTK/AppIndicator/Ayatana on Linux -- which a plain
WSL2 shell doesn't have without WSLg; WSL2's own role in this project
stays the terminal-based install.sh/fake-hardware.sh workflow, unchanged
by this file. See scripts/tray.sh / scripts/tray.ps1 for how this gets
launched with the right interpreter either way.

Requires the extra deps in scripts/tray_requirements.txt (pystray,
Pillow) installed into the same .venv scripts/install.sh already set up
-- not part of the app's own backend/requirements.txt, see that file's
own comment for why.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent
# A larger source than the tray actually needs (typically 16-32px) --
# the OS downscales it, which looks crisp on a high-DPI/Retina tray;
# starting from an already-tray-sized image looks soft by comparison.
ICON_PATH = REPO_ROOT / "frontend" / "public" / "icon-256x256.png"
PORT = 8000  # matches run-server.sh/ps1's own default; edit here to change it
# Next to roasts.db/roasts/ -- same backend/data/ directory storage.py
# already creates, so no separate mkdir concern here. A plain Popen
# without this would inherit whatever console launched the tray, which
# closing that window (or its scrollback filling up) would just lose --
# there was nowhere else these ever ended up.
LOG_PATH = REPO_ROOT / "backend" / "data" / "server.log"


def _badge(base: Image.Image, color: str) -> Image.Image:
    """A small colored dot over the bottom-right corner of the app icon --
    the at-a-glance running/stopped/building signal, same idea as e.g.
    Slack's tray icon showing a status dot without needing to open a
    menu to check."""
    img = base.convert("RGBA").copy()
    d = ImageDraw.Draw(img)
    w, h = img.size
    r = w // 4
    d.ellipse([w - r * 2, h - r * 2, w, h], fill=color, outline="white", width=2)
    return img


class TrayApp:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.log_file = None
        base = Image.open(ICON_PATH)
        self.icon_idle = base.convert("RGBA")
        self.icon_running = _badge(base, "#16a34a")  # green -- matches the app's own status-pill green
        self.icon_building = _badge(base, "#ca8a04")  # amber -- npm run build in progress
        self.icon_error = _badge(base, "#dc2626")  # red -- process exited on its own (crashed / port in use)

        self.icon = pystray.Icon(
            "roast-telemetry",
            icon=self.icon_idle,
            title="Roast Telemetry (stopped)",
            menu=pystray.Menu(
                pystray.MenuItem("Start server", self.start, enabled=lambda _: not self.is_running()),
                pystray.MenuItem("Stop server", self.stop, enabled=lambda _: self.is_running()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Open in browser", self.open_browser, enabled=lambda _: self.is_running()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", self.quit),
            ),
        )

    # -- process/state -----------------------------------------------
    def is_running(self) -> bool:
        # poll() is None while still alive -- also catches a crash (port
        # already in use, etc.) between clicks, not just an explicit Stop,
        # since nothing else here polls the subprocess continuously.
        return self.proc is not None and self.proc.poll() is None

    def _set_status(self, image: Image.Image, title: str) -> None:
        self.icon.icon = image
        self.icon.title = title
        self.icon.update_menu()

    def _notify(self, message: str) -> None:
        # Best-effort -- pystray raises NotImplementedError on a backend
        # without OS notification support, which would otherwise crash
        # whichever action (start/stop) triggered it over what's just a
        # nice-to-have heads-up, not something start/stop should ever
        # fail because of.
        try:
            self.icon.notify(message, "Roast Telemetry")
        except NotImplementedError:
            pass

    def start(self, _icon=None, _item=None) -> None:
        if self.is_running():
            return
        self._set_status(self.icon_building, "Roast Telemetry (building frontend...)")
        npm = shutil.which("npm")
        if npm is None:
            self._set_status(self.icon_error, "Roast Telemetry (npm not found)")
            self._notify("npm not found on PATH -- run scripts/install.sh (or install.ps1) first.")
            return
        # Blocking on purpose -- starting uvicorn against a stale
        # frontend/dist/ is exactly the bug run-server.sh/ps1 already
        # guard against (see their own header comments: an old build can
        # silently keep serving old auth/login behavior with no error at
        # all), so Start always rebuilds first, same as those scripts'
        # own default.
        build = subprocess.run([npm, "run", "build"], cwd=REPO_ROOT / "frontend")
        if build.returncode != 0:
            self._set_status(self.icon_error, "Roast Telemetry (frontend build failed)")
            self._notify("Frontend build failed -- see the terminal this was launched from for details.")
            return

        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO_ROOT)
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Append, not overwrite -- a restart's worth of history is more
        # useful than only ever seeing the current run, and this isn't
        # high-volume enough (a local single-roaster app, not a real
        # server under load) to need rotation. line-buffered (buffering=1)
        # so the file reflects what's happening promptly, not only once a
        # large internal buffer fills or the process exits.
        self.log_file = open(LOG_PATH, "a", buffering=1)
        self.log_file.write(f"\n=== {datetime.now().isoformat(timespec='seconds')} -- starting on :{PORT} ===\n")
        # sys.executable is already this venv's own python (tray.sh/
        # tray.ps1 launch this script with it) -- `-m uvicorn` sidesteps
        # ever having to guess .venv/bin/uvicorn vs .venv\Scripts\uvicorn.exe.
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--port", str(PORT)],
            cwd=REPO_ROOT,
            env=env,
            stdout=self.log_file,
            stderr=subprocess.STDOUT,
        )
        self._set_status(self.icon_running, f"Roast Telemetry (running on :{PORT})")
        self._notify(f"Logging to {LOG_PATH}")

    def stop(self, _icon=None, _item=None) -> None:
        if not self.is_running():
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()  # didn't shut down cleanly in time -- force it rather than hang the Stop click
        self.proc = None
        if self.log_file is not None:
            self.log_file.close()
            self.log_file = None
        self._set_status(self.icon_idle, "Roast Telemetry (stopped)")

    def open_browser(self, _icon=None, _item=None) -> None:
        if self.is_running():
            webbrowser.open(f"http://localhost:{PORT}")

    def quit(self, _icon=None, _item=None) -> None:
        self.stop()
        self.icon.stop()

    def run(self) -> None:
        self.icon.run()


if __name__ == "__main__":
    TrayApp().run()
