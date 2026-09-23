"""Lists folders and roast log files on the machine running the server --
backs the History page's "Browse..." button next to the import path field.

The import endpoint (POST /api/v1/roasts/import) already reads whatever
server-side path a logged-in account gives it, so this doesn't widen what
an account can reach: it only saves typing the path. Every /api route,
this one included, already sits behind the login gate in main.py, and each
allowed account has full access to everything else too (see the README).

It lists folder names and files with a roast-log extension only (.alog,
.json, .csv, .tsv, .xlsx -- see alog_playback/roastlog.py for what the last
three hold) -- never file contents, and no other file types -- and skips
names starting with a dot. If the server is reachable from other devices
(--lan), anyone signed in can see its folder names through this, the same
trust the rest of the app assumes."""
from __future__ import annotations

import os
import string
import sys
from typing import Optional

from fastapi import APIRouter, HTTPException

from ..models import FileEntry, FileListing, FileShortcut

router = APIRouter(prefix="/files", tags=["files"])

_MAX_ENTRIES = 2000
# Kept in sync with IMPORTABLE_EXTENSIONS in frontend/src/views/HistoryDashboard.jsx.
_IMPORTABLE_SUFFIXES = (".alog", ".json", ".csv", ".tsv", ".xlsx")


def _windows_drives() -> list[str]:
    """Drive roots that exist, from the OS's drive bitmask -- instant, unlike
    probing every letter (a disconnected network drive can hang for ~30 s)."""
    try:
        import ctypes

        mask = ctypes.windll.kernel32.GetLogicalDrives()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 -- not Windows, or ctypes unavailable
        return []
    return [f"{letter}:\\" for i, letter in enumerate(string.ascii_uppercase) if mask & (1 << i)]


def _shortcuts() -> list[FileShortcut]:
    home = os.path.expanduser("~")
    out = [FileShortcut(label="Home", path=home)]
    if sys.platform == "win32":
        out.extend(FileShortcut(label=d, path=d) for d in _windows_drives())
    else:
        out.append(FileShortcut(label="/ (root)", path="/"))
    return out


@router.get("", response_model=FileListing)
def list_files(path: Optional[str] = None) -> FileListing:
    """Folders (first) and roast-log files in ``path``; the user's home
    folder when ``path`` is omitted."""
    target = os.path.abspath(os.path.expanduser(path)) if path and path.strip() else os.path.expanduser("~")
    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail=f"Folder not found: {target}")
    if not os.path.isdir(target):
        raise HTTPException(status_code=400, detail=f"Not a folder: {target}")

    dirs: list[FileEntry] = []
    files: list[FileEntry] = []
    truncated = False
    try:
        with os.scandir(target) as it:
            for entry in it:
                if entry.name.startswith("."):
                    continue
                try:
                    if entry.is_dir():
                        dirs.append(FileEntry(name=entry.name, path=entry.path, kind="dir"))
                    elif entry.name.lower().endswith(_IMPORTABLE_SUFFIXES) and entry.is_file():
                        files.append(FileEntry(name=entry.name, path=entry.path, kind="file", size=entry.stat().st_size))
                except OSError:
                    continue  # a broken link or a vanished entry -- just leave it out
                if len(dirs) + len(files) >= _MAX_ENTRIES:
                    truncated = True
                    break
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=f"No permission to read {target}") from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Can't read {target}: {exc.strerror or exc}") from exc

    dirs.sort(key=lambda e: e.name.lower())
    files.sort(key=lambda e: e.name.lower())
    parent = os.path.dirname(target.rstrip("\\/")) if target.rstrip("\\/") else None
    if not parent or parent == target:
        parent = None
    return FileListing(path=target, parent=parent, entries=[*dirs, *files], shortcuts=_shortcuts(), truncated=truncated)
