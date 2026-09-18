# PyInstaller spec for Roast Telemetry's desktop tray app -- bundles the
# tray icon (scripts/tray_app.py), the FastAPI backend it launches as a
# subprocess (see that file's own FROZEN-mode handling), and the built
# frontend into one distributable folder, no separate Python/npm/venv
# install required to run it.
#
# Must be run *on* the target platform -- Windows produces a Windows
# build, macOS produces a macOS build; there is no supported
# cross-compile path. See packaging/build-windows.ps1 /
# packaging/build-macos.sh, which both just wrap:
#
#     pyinstaller packaging/roast-telemetry.spec --distpath dist --workpath build
#
# Every path below is built from SPECPATH -- a variable PyInstaller
# injects automatically when it execs this file, set to the directory
# *this spec file* lives in (packaging/), regardless of the caller's own
# cwd or how the pyinstaller/PyInstaller command was invoked. Confirmed
# necessary, not just defensive: an early version of this spec assumed
# paths were resolved relative to the repo root (the caller's cwd) and
# a real test build in this sandbox failed immediately with "script
# '.../packaging/scripts/tray_app.py' not found" -- PyInstaller resolves
# relative Analysis/datas paths against the spec file's own directory,
# not the invoking shell's cwd.
#
# Verified with a real build in this sandbox (Linux/WSL2) after fixing
# that -- see packaging/build-linux.sh. Windows/macOS still need their
# own first real build to confirm (no such environment available here),
# but this exact path-resolution issue is now already caught and fixed
# rather than something each platform would've hit independently.

import os
import sys

block_cipher = None
REPO_ROOT = os.path.dirname(SPECPATH)


def repo_path(*parts):
    return os.path.join(REPO_ROOT, *parts)

# One exe/app plays two roles -- the tray icon, and (re-invoked with
# --run-server) the backend server subprocess -- see tray_app.py's own
# _run_server_entrypoint()/start() comments for why there's no separate
# second binary. PyInstaller's static analysis generally follows the
# `from backend.app.main import app` inside that function already, but
# these are listed explicitly rather than trusted to be auto-detected --
# a silently-missing hidden import surfaces as a runtime ImportError
# inside the packaged app with no traceback the user can usefully act
# on, not a build-time error here.
hidden_imports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "backend.app.main",
    "backend.app.api.auth",
    "backend.app.api.device_profiles",
    "backend.app.api.devices",
    "backend.app.api.presets",
    "backend.app.api.roasts",
    "backend.app.api.serial_ports",
    "backend.app.api.settings",
    "modbus_bridge",
    "modbus_bridge.device_profiles",
    "ms6514_bridge",
    "aillio_bridge",
    "aillio_bridge.r1",
    "roast_heuristics",
    "simulator",
    "alog_playback",
    "mock_device",
]

# (source, destination-inside-the-bundle) -- destination paths are
# deliberately chosen to match the exact relative layout the *source*
# tree already has, so the app's own existing __file__-relative path
# logic (backend/app/main.py's FRONTEND_DIST, LiveRoastView.jsx's
# hardcoded sample .alog path resolved against the server's own cwd)
# keeps working completely unmodified -- see tray_app.py's own
# BASE_DIR/ICON_PATH comments. Do not "clean up" these paths without
# also updating whichever source file's relative-path assumption
# depends on them landing exactly here.
datas = [
    (repo_path("frontend", "dist"), "frontend/dist"),
    (repo_path("backend", "data", "sample_roasts"), "backend/data/sample_roasts"),
    (repo_path("frontend", "public", "icon-256x256.png"), "assets"),
]

# Windows wants a .ico (frontend/public/favicon.ico already exists and
# works as-is); macOS wants a .icns, which isn't checked into the repo
# -- generate one from the existing PNGs with `iconutil` on an actual
# Mac (see packaging/build-macos.sh's own comment for the exact
# command) and point ICON_ICNS at it, or leave it unset for a first
# build (PyInstaller/py2app fall back to a generic icon, cosmetic only).
ICON_WIN = repo_path("frontend", "public", "favicon.ico")
ICON_MAC = repo_path("packaging", "icon.icns")  # generate this first -- see build-macos.sh

icon_path = None
if sys.platform == "win32" and os.path.exists(ICON_WIN):
    icon_path = ICON_WIN
elif sys.platform == "darwin" and os.path.exists(ICON_MAC):
    icon_path = ICON_MAC

a = Analysis(
    [repo_path("scripts", "tray_app.py")],
    pathex=[REPO_ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Roast Telemetry",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # windowed -- no console flash for the tray icon or the re-exec'd server subprocess
    icon=icon_path,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Roast Telemetry",
)

# macOS only -- wraps the onedir COLLECT output into a real double-
# clickable .app bundle. Windows/Linux just use the COLLECT folder
# directly (dist/Roast Telemetry/), no BUNDLE step needed.
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Roast Telemetry.app",
        icon=icon_path,
        bundle_identifier="com.roasttelemetry.app",
        info_plist={
            "CFBundleShortVersionString": "1.2.0",
            "NSHighResolutionCapable": True,
            # A tray/menu-bar-only app -- no Dock icon or app-switcher
            # entry, matching how the tray icon already behaves on
            # Windows/Linux (background utility, not a normal windowed app).
            "LSUIElement": True,
        },
    )
