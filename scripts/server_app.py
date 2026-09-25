"""Frozen entry point for the Linux standalone build -- the packaged
equivalent of run-server.sh, command-line only. Deliberately a separate,
minimal file from tray_app.py rather than reusing that file's own
--run-server mode: tray_app.py imports pystray/Pillow unconditionally at
module load (see its own module docstring), both pointless to bundle for
a build that has no tray to begin with and can never show one on Linux
(see [[feedback_no_linux_desktop_tray]]) -- this file needs neither.

No subprocess re-exec trick either, unlike tray_app.py's start()/
_run_server_entrypoint() pair: that dance exists so one exe can play two
roles (the tray icon process, and the server it launches as a child).
There's no tray process here for the server to be a child *of* -- this
binary just runs the server directly, in the foreground, like
run-server.sh already does from source.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FROZEN = bool(getattr(sys, "frozen", False))
if not FROZEN and str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Where PyInstaller unpacks/exposes bundled data (onefile: a fresh
# per-launch temp dir; onedir: a stable folder next to the exe) --
# REPO_ROOT when running from source, same convention as tray_app.py's
# own BASE_DIR.
BASE_DIR = Path(getattr(sys, "_MEIPASS", REPO_ROOT))


def _user_data_dir() -> Path:
    """Per-user, writable directory for a packaged build's roasts.db/
    roasts/ -- never next to the executable itself (may not be writable,
    and gets wiped on every reinstall/upgrade). XDG convention. Kept as
    its own copy rather than importing tray_app.py's identical Windows/
    macOS-aware version -- see module docstring for why this file avoids
    that import entirely."""
    base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / "RoastTelemetry"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="roast-telemetry",
        description="Roast Telemetry -- standalone server, no separate Python/Node install needed.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1, localhost only)")
    parser.add_argument("--port", type=int, default=7890, help="Port to listen on (default: 7890)")
    args = parser.parse_args()

    if FROZEN:
        os.chdir(BASE_DIR)
        os.environ["ROAST_TELEMETRY_DATA_DIR"] = str(_user_data_dir())

    import uvicorn

    from backend.app.main import app

    # flush=True: stdout is block-buffered (not line-buffered) whenever
    # it's not a real TTY -- e.g. `./roast-telemetry > server.log 2>&1 &`,
    # a plausible way to run a long-lived server binary. Confirmed live:
    # without this, the line sat in the buffer until the process actually
    # exited, appearing after every one of uvicorn's own (eagerly-flushed)
    # startup log lines instead of before them.
    print(f"Roast Telemetry starting on http://{args.host}:{args.port} (Ctrl+C to stop)", flush=True)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
