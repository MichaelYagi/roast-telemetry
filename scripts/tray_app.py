"""A system tray / menu-bar icon that starts and stops Roast Telemetry --
the GUI equivalent of run-server.sh/run-server.ps1, for someone who'd
rather click an icon than remember a terminal command. Works on Windows,
macOS, and a real Linux desktop (pystray needs an actual tray
implementation -- GTK/AppIndicator/Ayatana on Linux -- which a plain
WSL2 shell doesn't have without WSLg; WSL2's own role in this project
stays the terminal-based install.sh/fake-hardware.sh workflow, unchanged
by this file). See scripts/tray.sh / scripts/tray.ps1 for how this gets
launched with the right interpreter either way.

Requires the extra deps in scripts/tray_requirements.txt (pystray,
Pillow) installed into the same .venv scripts/install.sh already set up
-- not part of the app's own backend/requirements.txt, see that file's
own comment for why. Port/Host dialogs and the save-logs dialog use
tkinter (stdlib) -- on Linux specifically this sometimes needs a
separate `python3-tk` package (Debian/Ubuntu split it out); see
install.sh.
"""
from __future__ import annotations

import json
import os
import platform
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
# Next to roasts.db/roasts/ -- same backend/data/ directory storage.py
# already creates, so no separate mkdir concern here.
LOG_PATH = REPO_ROOT / "backend" / "data" / "server.log"
CONFIG_PATH = REPO_ROOT / "backend" / "data" / "tray_config.json"
LOCK_PATH = REPO_ROOT / "backend" / "data" / "tray.lock"

DEFAULT_CONFIG = {
    "port": 7890,
    # 127.0.0.1, not 0.0.0.0 -- deliberately local-only by default (see
    # this repo's own docs on the safety tradeoffs of LAN-exposing
    # control of a real heat-producing machine). Type a real IP or
    # 0.0.0.0 via "Host: ..." to opt in.
    "host": "127.0.0.1",
    "rebuild_on_start": True,
    "launch_at_login": False,
}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text())
            if isinstance(data, dict):
                return {**DEFAULT_CONFIG, **data}
        except (json.JSONDecodeError, OSError):
            pass  # corrupt/unreadable -- fall through to defaults rather than crash the tray over a settings file
    return dict(DEFAULT_CONFIG)


def save_config(config: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2))


# -- Single-instance lock ------------------------------------------------
# Nothing previously stopped double-clicking the launcher twice from
# running two independent tray icons at once, each with its own idea of
# whether the server is running -- confirmed live (a real "why are there
# two of these" report). A PID lock file, checked before the pystray
# Icon is even constructed, closes that: the second launch bails out
# with a message instead of spawning a second icon.
def _is_process_alive(pid: int) -> bool:
    if platform.system() == "Windows":
        # os.kill(pid, 0) doesn't work as a liveness check on Windows
        # (0 isn't a valid signal there) -- tasklist is a native,
        # always-present tool that does the job with no new dependency.
        try:
            result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True, timeout=5)
            return str(pid) in result.stdout
        except (OSError, subprocess.TimeoutExpired):
            return False
    try:
        os.kill(pid, 0)  # sends no actual signal on POSIX -- just checks whether the PID exists
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just owned by someone else -- still alive
    except OSError:
        return False


def _acquire_single_instance_lock() -> bool:
    """True if this process now holds the lock (the only instance);
    False if another instance is already running. A leftover lock file
    from a previous crash (process no longer alive) is treated as stale
    and safely taken over, not as "someone else is running"."""
    if LOCK_PATH.exists():
        try:
            existing_pid = int(LOCK_PATH.read_text().strip())
        except (ValueError, OSError):
            existing_pid = None
        if existing_pid is not None and _is_process_alive(existing_pid):
            return False
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.write_text(str(os.getpid()))
    return True


def _release_single_instance_lock() -> None:
    try:
        if LOCK_PATH.exists() and int(LOCK_PATH.read_text().strip()) == os.getpid():
            LOCK_PATH.unlink()
    except (ValueError, OSError):
        pass  # already gone, or never ours to begin with -- fine either way


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


# -- Dialogs -----------------------------------------------------------
# tkinter is used for the text-entry/save-file dialogs -- a short-lived
# hidden root is created fresh per dialog rather than one kept alive
# for the tray's whole lifetime; simpledialog/filedialog's own calls run
# their own small internal loop and return synchronously, so this
# doesn't need to fight pystray's own event loop for the "real" mainloop.
#
# Known, disclosed risk: pystray invokes menu actions (and so these
# dialogs) on its own worker thread, not the thread that called
# icon.run(). tkinter is documented as main-thread-only on macOS (same
# restriction pystray's own Cocoa backend has) -- this is the common
# workaround pattern for small pystray+tkinter tools and works in
# practice on Windows/Linux, but isn't verified here on macOS (no
# macOS in this environment). Worth a real test there.
def _ask_string(prompt: str, initial: str) -> str | None:
    import tkinter as tk
    from tkinter import simpledialog

    root = tk.Tk()
    root.withdraw()
    try:
        return simpledialog.askstring("Roast Telemetry", prompt, initialvalue=initial, parent=root)
    finally:
        root.destroy()


def _ask_save_path(initial_name: str) -> str | None:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    try:
        path = filedialog.asksaveasfilename(
            title="Save Roast Telemetry logs", initialfile=initial_name, defaultextension=".log", parent=root
        )
        return path or None
    finally:
        root.destroy()


def _copy_to_clipboard(text: str) -> bool:
    """True on success. Prefers each OS's own dedicated clipboard tool
    (clip/pbcopy -- always present, zero new dependency, a single
    stdin-piping subprocess call, nothing to get wrong) over tkinter's
    own clipboard, which is unreliable on Linux/X11 specifically: X11's
    clipboard model only keeps content available while the owning
    window/process is still alive unless a clipboard manager is
    running, which a briefly-created-then-destroyed Tk root defeats.
    tkinter is only the last-resort fallback, for Linux without
    xclip/xsel/wl-copy installed."""
    system = platform.system()
    try:
        if system == "Windows":
            subprocess.run(["clip"], input=text.encode(), check=True)
            return True
        if system == "Darwin":
            subprocess.run(["pbcopy"], input=text.encode(), check=True)
            return True
        for tool, args in (("wl-copy", []), ("xclip", ["-selection", "clipboard"]), ("xsel", ["--clipboard", "--input"])):
            path = shutil.which(tool)
            if path:
                subprocess.run([path, *args], input=text.encode(), check=True)
                return True
    except (OSError, subprocess.CalledProcessError):
        pass

    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        root.after(200, root.destroy)  # give the clipboard a moment to actually take before tearing the window down
        root.mainloop()
        return True
    except Exception:
        return False


# -- Launch at login -----------------------------------------------------
# Three different OS mechanisms under one dispatch -- none of these are
# testable in this environment (no real Windows/macOS/Linux-desktop
# session here), reviewed carefully instead. Windows/Linux use plain
# text files (a .bat, a .desktop) with no external tool needed; macOS
# needs `launchctl` to actually register the LaunchAgent, not just
# writing its plist.
def _windows_startup_bat_path() -> Path:
    appdata = os.environ.get("APPDATA", "")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Roast Telemetry.bat"


def _set_launch_at_login_windows(enabled: bool) -> None:
    path = _windows_startup_bat_path()
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        # -WindowStyle Hidden here specifically (unlike the repo-root
        # Roast Telemetry.bat, which stays visible on purpose) -- a
        # login autostart is expected to start quietly in the
        # background, not pop a console on every boot.
        content = (
            "@echo off\r\n"
            f'powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{REPO_ROOT}\\scripts\\tray.ps1"\r\n'
        )
        path.write_text(content)
    else:
        path.unlink(missing_ok=True)


def _mac_launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "com.roasttelemetry.tray.plist"


def _set_launch_at_login_mac(enabled: bool) -> None:
    path = _mac_launch_agent_path()
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.roasttelemetry.tray</string>
    <key>ProgramArguments</key>
    <array>
        <string>{REPO_ROOT}/scripts/tray.sh</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
"""
        path.write_text(plist)
        subprocess.run(["launchctl", "load", str(path)], check=False)
    else:
        subprocess.run(["launchctl", "unload", str(path)], check=False)
        path.unlink(missing_ok=True)


def _linux_autostart_path() -> Path:
    return Path.home() / ".config" / "autostart" / "roast-telemetry-tray.desktop"


def _set_launch_at_login_linux(enabled: bool) -> None:
    path = _linux_autostart_path()
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Roast Telemetry\n"
            f"Exec={REPO_ROOT}/scripts/tray.sh\n"
            f"Icon={ICON_PATH}\n"
            "Terminal=false\n"
        )
        path.write_text(content)
    else:
        path.unlink(missing_ok=True)


def _set_launch_at_login(enabled: bool) -> None:
    system = platform.system()
    if system == "Windows":
        _set_launch_at_login_windows(enabled)
    elif system == "Darwin":
        _set_launch_at_login_mac(enabled)
    else:
        _set_launch_at_login_linux(enabled)


class TrayApp:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.log_file = None
        self.config = load_config()

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
                pystray.MenuItem("Restart server", self.restart, enabled=lambda _: self.is_running()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Open in browser", self.open_browser, enabled=lambda _: self.is_running()),
                pystray.MenuItem("Copy URL to clipboard", self.copy_url, enabled=lambda _: self.is_running()),
                pystray.MenuItem("Save logs...", self.save_logs),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(lambda _: f"Port: {self.config['port']}", self.change_port),
                pystray.MenuItem(lambda _: f"Host: {self.config['host']}", self.change_host),
                pystray.MenuItem(
                    "Rebuild frontend on start",
                    self.toggle_rebuild,
                    checked=lambda _: self.config["rebuild_on_start"],
                ),
                pystray.MenuItem(
                    "Launch at login",
                    self.toggle_launch_at_login,
                    checked=lambda _: self.config["launch_at_login"],
                ),
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
        port = self.config["port"]
        host = self.config["host"]

        if self.config["rebuild_on_start"]:
            self._set_status(self.icon_building, "Roast Telemetry (building frontend...)")
            npm = shutil.which("npm")
            if npm is None:
                self._set_status(self.icon_error, "Roast Telemetry (npm not found)")
                self._notify("npm not found on PATH -- run scripts/install.sh (or install.ps1) first.")
                return
            # Blocking on purpose -- starting uvicorn against a stale
            # frontend/dist/ is exactly the bug run-server.sh/ps1 already
            # guard against (see their own header comments: an old build
            # can silently keep serving old auth/login behavior with no
            # error at all), so this rebuilds first by default. Turn off
            # via the "Rebuild frontend on start" checkbox once you know
            # your build is current, for faster restarts.
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
        self.log_file.write(f"\n=== {datetime.now().isoformat(timespec='seconds')} -- starting on {host}:{port} ===\n")
        # sys.executable is already this venv's own python (tray.sh/
        # tray.ps1 launch this script with it) -- `-m uvicorn` sidesteps
        # ever having to guess .venv/bin/uvicorn vs .venv\Scripts\uvicorn.exe.
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--host", host, "--port", str(port)],
            cwd=REPO_ROOT,
            env=env,
            stdout=self.log_file,
            stderr=subprocess.STDOUT,
        )
        self._set_status(self.icon_running, f"Roast Telemetry (running on {host}:{port})")
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

    def restart(self, _icon=None, _item=None) -> None:
        if not self.is_running():
            return
        self.stop()
        self.start()

    def open_browser(self, _icon=None, _item=None) -> None:
        if self.is_running():
            webbrowser.open(f"http://{self.config['host']}:{self.config['port']}")

    def copy_url(self, _icon=None, _item=None) -> None:
        if not self.is_running():
            return
        url = f"http://{self.config['host']}:{self.config['port']}"
        if _copy_to_clipboard(url):
            self._notify(f"Copied {url}")
        else:
            self._notify(f"Couldn't copy automatically -- the URL is {url}")

    def save_logs(self, _icon=None, _item=None) -> None:
        if not LOG_PATH.exists():
            self._notify("No logs yet -- start the server at least once first.")
            return
        dest = _ask_save_path("roast-telemetry-server.log")
        if not dest:
            return  # cancelled
        try:
            shutil.copyfile(LOG_PATH, dest)
            self._notify(f"Saved to {dest}")
        except OSError as exc:
            self._notify(f"Couldn't save logs: {exc}")

    # -- settings ------------------------------------------------------
    def _apply_setting_change(self) -> None:
        save_config(self.config)
        self.icon.update_menu()  # refreshes the dynamic "Port: ..."/"Host: ..." labels
        if self.is_running():
            # A running server needs to actually restart to pick up the
            # new value -- a silent save the operator would have to
            # remember to act on later isn't what "change the setting"
            # should mean while it's live.
            self.restart()

    def change_port(self, _icon=None, _item=None) -> None:
        value = _ask_string("Port number:", str(self.config["port"]))
        if value is None:
            return  # cancelled
        try:
            port = int(value)
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            self._notify(f"{value!r} isn't a valid port (1-65535) -- not changed.")
            return
        self.config["port"] = port
        self._apply_setting_change()

    def change_host(self, _icon=None, _item=None) -> None:
        value = _ask_string("Host/IP (127.0.0.1 = this computer only, 0.0.0.0 = your whole LAN):", self.config["host"])
        if not value:
            return  # cancelled or cleared
        self.config["host"] = value.strip()
        self._apply_setting_change()

    def toggle_rebuild(self, _icon=None, _item=None) -> None:
        self.config["rebuild_on_start"] = not self.config["rebuild_on_start"]
        save_config(self.config)
        self.icon.update_menu()

    def toggle_launch_at_login(self, _icon=None, _item=None) -> None:
        new_value = not self.config["launch_at_login"]
        try:
            _set_launch_at_login(new_value)
        except OSError as exc:
            self._notify(f"Couldn't change login-launch setting: {exc}")
            return
        self.config["launch_at_login"] = new_value
        save_config(self.config)
        self.icon.update_menu()

    def quit(self, _icon=None, _item=None) -> None:
        self.stop()
        self.icon.stop()

    def run(self) -> None:
        self.icon.run()


if __name__ == "__main__":
    if not _acquire_single_instance_lock():
        print("Roast Telemetry is already running -- check your system tray (it may be in the hidden/overflow icons area, not pinned).")
        sys.exit(0)
    try:
        TrayApp().run()
    finally:
        _release_single_instance_lock()
