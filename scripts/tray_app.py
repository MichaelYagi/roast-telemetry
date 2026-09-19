"""A system tray / menu-bar icon that starts and stops Roast Telemetry --
the GUI equivalent of run-server.sh/run-server.ps1, for someone who'd
rather click an icon than remember a terminal command. Windows and
macOS only -- deliberately not Linux desktop (see
[[feedback_no_linux_desktop_tray]] for why: pystray has no single
standard tray protocol to target there the way Windows/macOS each have
exactly one, and real testing across Raspberry Pi OS's default desktop
and GNOME both surfaced genuine, unresolvable-from-this-app's-own-code
friction -- an invisible/non-interactive icon depending on which
backend pystray happened to pick, with no reliable way to know which
one will actually render on a given machine ahead of time). WSL2's own
role in this project stays the terminal-based
install.sh/fake-hardware.sh workflow, unaffected by any of this. See
scripts/tray.sh / scripts/tray.ps1 for how this gets launched with the
right interpreter either way.

Requires the extra deps in scripts/tray_requirements.txt (pystray,
Pillow) installed into the same .venv scripts/install.sh already set up
-- not part of the app's own backend/requirements.txt, see that file's
own comment for why.
"""
from __future__ import annotations

import json
import os
import platform
import queue
import shutil
import subprocess
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent
# True only inside a PyInstaller-frozen build (see packaging/) -- every
# existing dev/CI invocation of this file runs it as a plain script, so
# this is always False there, and every branch below that checks it is
# a no-op for that case, not a behavior change.
FROZEN = bool(getattr(sys, "frozen", False))
# Bundled read-only resources (icon, the sample .alog files shipped
# with the app) -- sys._MEIPASS is where PyInstaller unpacks/exposes
# them (set for both onefile and onedir builds), REPO_ROOT is the
# equivalent when running from source.
BASE_DIR = Path(getattr(sys, "_MEIPASS", REPO_ROOT))


def _user_data_dir() -> Path:
    """Per-user, per-OS writable directory for a packaged build -- never
    write app data next to the executable itself (may not be writable,
    e.g. Program Files, and gets wiped on every reinstall/upgrade
    unlike a real per-user profile directory). Standard per-platform
    convention, same one most desktop apps use."""
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA") or Path.home())
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / "RoastTelemetry"


# A larger source than the tray actually needs (typically 16-32px) --
# the OS downscales it, which looks crisp on a high-DPI/Retina tray;
# starting from an already-tray-sized image looks soft by comparison.
# Bundled under assets/ in a frozen build (see packaging/roast-telemetry.spec).
ICON_PATH = (BASE_DIR / "assets" / "icon-256x256.png") if FROZEN else (REPO_ROOT / "frontend" / "public" / "icon-256x256.png")
# Log/config/lock all move to the real per-user data directory in a
# frozen build (see _user_data_dir's own docstring for why) -- when
# running from source, unchanged: next to roasts.db/roasts/, same
# backend/data/ directory storage.py already creates.
_DATA_DIR = _user_data_dir() if FROZEN else (REPO_ROOT / "backend" / "data")
LOG_PATH = _DATA_DIR / "server.log"
CONFIG_PATH = _DATA_DIR / "tray_config.json"
LOCK_PATH = _DATA_DIR / "tray.lock"

DEFAULT_CONFIG = {
    "port": 7890,
    # 127.0.0.1, not 0.0.0.0 -- deliberately local-only by default (see
    # this repo's own docs on the safety tradeoffs of LAN-exposing
    # control of a real heat-producing machine). Type a real IP or
    # 0.0.0.0 via "Host: ..." to opt in.
    "host": "127.0.0.1",
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


# -- Frontend staleness check ---------------------------------------------
# Used to be a manual "Rebuild frontend on start" checkbox (on by default,
# turned off once you knew your build was current) -- replaced with this:
# compare frontend source mtimes against the existing dist/index.html's
# own mtime, so start() only rebuilds when something under frontend/ has
# actually changed since the last build. Same guarantee the checkbox
# existed for (never serve a stale build -- the bug run-server.sh/ps1
# already guard against, an old build can silently keep serving outdated
# behavior with no error at all) without needing anyone to remember a
# setting exists, let alone what it does.
def _frontend_needs_rebuild() -> bool:
    dist_index = REPO_ROOT / "frontend" / "dist" / "index.html"
    if not dist_index.exists():
        return True  # first run, or a fresh clone -- nothing built yet
    dist_mtime = dist_index.stat().st_mtime

    watched_files = [
        REPO_ROOT / "frontend" / "index.html",
        REPO_ROOT / "frontend" / "vite.config.js",
        REPO_ROOT / "frontend" / "package.json",
    ]
    if any(f.exists() and f.stat().st_mtime > dist_mtime for f in watched_files):
        return True

    for root_name in ("src", "public"):
        root = REPO_ROOT / "frontend" / root_name
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.stat().st_mtime > dist_mtime:
                return True
    return False


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
# Windows: tkinter, marshaled onto one consistent thread -- see
# TrayApp.run()/_run_on_main_thread. Confirmed live, not just a
# theoretical risk: creating a throwaway tk.Tk() root inside a pystray
# menu callback (pystray runs each callback on its own fresh worker
# thread, never the thread that called icon.run()) produced a dialog
# that rendered but never responded to clicks or even its own close
# button -- fixed by always driving tkinter from one persistent root on
# the real main thread instead.
#
# macOS: NOT tkinter at all, deliberately -- confirmed live, a real Mac
# hard-crashed the whole process (NSInvalidArgumentException inside
# Tk's own Cocoa color-handling code, `libtcl9tk9.0.dylib`) the instant
# tk.Tk() tried to create a window. This is Tk 9.0 (via Homebrew's
# python-tk@X.Y) itself, an upstream Tk/macOS compatibility bug, not
# something fixable from here. Uses `osascript` (AppleScript) instead --
# a separate process per call, so it has no shared-GUI-toolkit state
# and no cross-thread concern either, unlike tkinter.
def _ask_string(root, prompt: str, initial: str) -> str | None:
    if platform.system() == "Darwin":
        return _osascript_ask_string(prompt, initial)
    from tkinter import simpledialog

    return simpledialog.askstring("Roast Telemetry", prompt, initialvalue=initial, parent=root)


def _ask_save_path(root, initial_name: str) -> str | None:
    if platform.system() == "Darwin":
        return _osascript_ask_save_path(initial_name)
    from tkinter import filedialog

    path = filedialog.asksaveasfilename(
        title="Save Roast Telemetry logs", initialfile=initial_name, defaultextension=".log", parent=root
    )
    return path or None


def _applescript_escape(s: str) -> str:
    # AppleScript string literals: backslash and double-quote both need
    # escaping, backslash first so it doesn't double-escape the quotes'
    # own backslashes.
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _osascript_ask_string(prompt: str, initial: str) -> str | None:
    script = (
        f'display dialog "{_applescript_escape(prompt)}" '
        f'default answer "{_applescript_escape(initial)}" with title "Roast Telemetry"'
    )
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        return None  # Cancel (or anything else non-zero) -- same "cancelled" meaning callers already expect
    # stdout looks like "text returned:VALUE, button returned:OK"
    out = result.stdout.strip()
    marker = "text returned:"
    if marker not in out:
        return None
    return out.split(marker, 1)[1].rsplit(", button returned:", 1)[0]


def _osascript_ask_save_path(initial_name: str) -> str | None:
    script = (
        f'POSIX path of (choose file name with prompt "Save Roast Telemetry logs" '
        f'default name "{_applescript_escape(initial_name)}")'
    )
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        return None  # Cancel
    path = result.stdout.strip()
    return path or None


def _copy_to_clipboard(text: str, root) -> bool:
    """True on success. Prefers each OS's own dedicated clipboard tool
    (clip/pbcopy -- always present, zero new dependency, a single
    stdin-piping subprocess call, nothing to get wrong) over tkinter's
    own clipboard, kept only as a last-resort fallback should clip/pbcopy
    themselves ever fail. Takes the shared persistent root (see the
    dialogs comment above) rather than creating its own -- same
    cross-thread issue every other dialog here had."""
    system = platform.system()
    try:
        if system == "Windows":
            subprocess.run(["clip"], input=text.encode(), check=True)
            return True
        if system == "Darwin":
            subprocess.run(["pbcopy"], input=text.encode(), check=True)
            return True
    except (OSError, subprocess.CalledProcessError):
        pass

    try:
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        return True
    except Exception:
        return False


# -- Launch at login -----------------------------------------------------
# Two different OS mechanisms under one dispatch -- neither is testable
# in this environment (no real Windows/macOS session here), reviewed
# carefully instead. Windows uses a plain .bat text file in the Startup
# folder, no external tool needed; macOS needs `launchctl` to actually
# register the LaunchAgent, not just writing its plist.
def _windows_startup_bat_path() -> Path:
    appdata = os.environ.get("APPDATA", "")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Roast Telemetry.bat"


def _set_launch_at_login_windows(enabled: bool) -> None:
    path = _windows_startup_bat_path()
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        # -WindowStyle Hidden here specifically -- a login autostart is
        # expected to start quietly in the background, not pop a
        # console on every boot (unlike scripts\tray.ps1 run by hand,
        # which stays visible on purpose).
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


def _set_launch_at_login(enabled: bool) -> None:
    system = platform.system()
    if system == "Windows":
        _set_launch_at_login_windows(enabled)
    elif system == "Darwin":
        _set_launch_at_login_mac(enabled)


class TrayApp:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.log_file = None
        self.config = load_config()
        # Set in run(), on the real main thread -- see this module's
        # dialogs comment for why every tkinter call has to go through
        # this one root via _run_on_main_thread rather than being
        # touched from wherever pystray happened to invoke a callback.
        self._tk_root = None

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
                    "Launch at startup",
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

    def _run_on_main_thread(self, func):
        """Runs func() on the thread that owns self._tk_root (see run()
        below) and blocks the caller -- always a pystray callback thread
        -- until it finishes, returning its result (or re-raising its
        exception). This is what actually fixes the unresponsive-dialog
        bug: tk.after(0, ...) is tkinter's own thread-safe way to
        schedule work onto the thread running its mainloop, and the
        queue is just how the result gets back to the calling thread
        synchronously, since after() itself doesn't return one.

        On macOS, self._tk_root is never set (see run()) -- dialogs
        there are osascript subprocess calls instead of tkinter, with
        no shared GUI-toolkit state and so no thread to marshal onto in
        the first place, so this just calls func() directly."""
        if self._tk_root is None:
            return func()

        result: queue.Queue = queue.Queue(maxsize=1)

        def wrapper():
            try:
                result.put((True, func()))
            except Exception as exc:  # noqa: BLE001 -- re-raised on the caller's own thread below, not swallowed
                result.put((False, exc))

        self._tk_root.after(0, wrapper)
        ok, value = result.get()
        if not ok:
            raise value
        return value

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

        if not FROZEN and _frontend_needs_rebuild():
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
            # error at all). Only runs when _frontend_needs_rebuild()
            # above actually found something newer than the existing
            # build -- no setting to remember, correct and fast by
            # default instead of a manual on/off toggle.
            build = subprocess.run([npm, "run", "build"], cwd=REPO_ROOT / "frontend")
            if build.returncode != 0:
                self._set_status(self.icon_error, "Roast Telemetry (frontend build failed)")
                self._notify("Frontend build failed -- see the terminal this was launched from for details.")
                return

        env = os.environ.copy()
        if FROZEN:
            # The bundled backend package is already importable inside
            # the frozen executable itself -- no source tree to point
            # PYTHONPATH at. Points storage.py's own DATA_DIR override at
            # the same per-user directory this file's own log/config/lock
            # already use, so the server subprocess and this tray process
            # agree on where roasts.db/roasts/ live.
            env["ROAST_TELEMETRY_DATA_DIR"] = str(_DATA_DIR)
        else:
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
        if FROZEN:
            # sys.executable is this frozen exe itself (there's no
            # separate python.exe/uvicorn.exe bundled) -- re-invoke it
            # with a hidden flag this same file's own __main__ block
            # recognizes as "act as the server, not the tray icon" (a
            # standard PyInstaller one-exe/multiple-roles pattern). Keeps
            # the server as a genuinely separate process either way, same
            # crash-isolation and stdout/stderr-into-server.log behavior
            # as the source-tree `-m uvicorn` path below.
            cmd = [sys.executable, "--run-server", "--host", host, "--port", str(port)]
        else:
            # sys.executable is already this venv's own python (tray.sh/
            # tray.ps1 launch this script with it) -- `-m uvicorn` sidesteps
            # ever having to guess .venv/bin/uvicorn vs .venv\Scripts\uvicorn.exe.
            cmd = [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--host", host, "--port", str(port)]
        self.proc = subprocess.Popen(
            cmd,
            cwd=BASE_DIR if FROZEN else REPO_ROOT,
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
        if self._run_on_main_thread(lambda: _copy_to_clipboard(url, self._tk_root)):
            self._notify(f"Copied {url}")
        else:
            self._notify(f"Couldn't copy automatically -- the URL is {url}")

    def save_logs(self, _icon=None, _item=None) -> None:
        if not LOG_PATH.exists():
            self._notify("No logs yet -- start the server at least once first.")
            return
        dest = self._run_on_main_thread(lambda: _ask_save_path(self._tk_root, "roast-telemetry-server.log"))
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
        value = self._run_on_main_thread(lambda: _ask_string(self._tk_root, "Port number:", str(self.config["port"])))
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
        value = self._run_on_main_thread(
            lambda: _ask_string(self._tk_root, "Host/IP (127.0.0.1 = this computer only, 0.0.0.0 = your whole LAN):", self.config["host"])
        )
        if not value:
            return  # cancelled or cleared
        self.config["host"] = value.strip()
        self._apply_setting_change()

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
        if self._tk_root is not None:
            # root.quit() has to run on the thread that owns the root
            # too -- it's what makes root.mainloop() in run() below
            # actually return, which is what lets the process exit.
            # Marshaled the same way as every dialog call, but
            # fire-and-forget (no result needed). Not applicable on
            # macOS -- no root there at all (see run()), and
            # icon.stop() alone is enough to end the plain icon.run()
            # call below.
            self._tk_root.after(0, self._tk_root.quit)

    def run(self) -> None:
        if platform.system() == "Darwin":
            # No tkinter on macOS at all -- see the module-level
            # dialogs comment for why (a real, confirmed hard crash in
            # Tk 9.0's own Cocoa integration, not fixable from here).
            # Dialogs use osascript instead, which needs no Tk root and
            # no thread-marshaling, so this can just run pystray
            # directly -- no detached-thread/mainloop combo needed.
            self.icon.run()
            return

        # icon.run_detached() runs pystray's own event loop (and so
        # every menu callback) on a background thread instead of this
        # one, freeing this thread -- the real main thread -- to run
        # tkinter's mainloop() instead. See the module-level dialogs
        # comment: tkinter needs to be driven from one consistent
        # thread, confirmed live by a dialog that rendered but never
        # responded to input when created from wherever pystray
        # happened to invoke a callback.
        import tkinter as tk

        self._tk_root = tk.Tk()
        self._tk_root.withdraw()
        self.icon.run_detached()
        self._tk_root.mainloop()


def _run_server_entrypoint() -> None:
    """Only reachable via `--run-server` (see TrayApp.start()'s own
    comment for why) -- a frozen build has no separate python.exe/
    uvicorn.exe to shell out to, so the same one exe re-invokes itself
    with this flag to act as the server instead of the tray icon,
    a standard PyInstaller one-exe/multiple-roles pattern. Direct
    Python import + uvicorn.run() rather than the source-tree path's
    `-m uvicorn app:module` string form -- more robust inside a frozen
    bundle than relying on uvicorn's own dynamic app-string import
    machinery to resolve correctly there. Never used when running from
    source (see TrayApp.start(), which only ever builds this argv shape
    when FROZEN).

    Sets its own cwd to BASE_DIR before doing anything else -- required
    for onefile builds specifically (PACKAGE_MODE=onefile, see
    packaging/roast-telemetry.spec), caught by a real end-to-end test in
    this sandbox: TrayApp.start()'s own `cwd=BASE_DIR` on the
    subprocess.Popen call only reflects *this parent tray process's*
    own extraction directory, but a onefile build re-extracts fresh to
    a brand new temp directory on every single launch, including this
    subprocess's own -- so the child needs to establish its own correct
    cwd itself once it's actually running as that new process, not
    inherit a stale directory from whoever launched it. Harmless
    no-op for onedir (BASE_DIR is the same stable _internal/ folder
    either way there) and for running from source (BASE_DIR is just
    REPO_ROOT, already the expected cwd)."""
    if FROZEN:
        os.chdir(BASE_DIR)
    host = "127.0.0.1"
    port = 7890
    args = sys.argv[2:]
    if "--host" in args:
        host = args[args.index("--host") + 1]
    if "--port" in args:
        port = int(args[args.index("--port") + 1])
    import uvicorn

    from backend.app.main import app

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--run-server":
        _run_server_entrypoint()
        sys.exit(0)
    if not _acquire_single_instance_lock():
        print("Roast Telemetry is already running -- check your system tray (it may be in the hidden/overflow icons area, not pinned).")
        sys.exit(0)
    try:
        TrayApp().run()
    finally:
        _release_single_instance_lock()
