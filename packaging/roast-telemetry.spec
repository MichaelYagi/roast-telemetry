# PyInstaller spec for Roast Telemetry's desktop tray app -- bundles the
# tray icon (scripts/tray_app.py), the FastAPI backend it launches as a
# subprocess (see that file's own FROZEN-mode handling), and the built
# frontend into a single standalone executable (or a folder -- see
# PACKAGE_MODE below), no separate Python/npm/venv install required to
# run it.
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
#
# PACKAGE_MODE env var picks the output shape (all three build scripts
# set it -- override by hand if you ever want the other one):
#   onefile (default) -- a genuinely single executable, nothing else to
#     keep alongside it. Costs something real: every launch re-extracts
#     the whole bundle to a fresh temp directory first (slower startup
#     than onedir, and since --run-server re-invokes the same exe as a
#     second process -- see tray_app.py's own comment on that -- it
#     happens *twice* per app launch, once for the tray icon, once for
#     the server it spawns). Also more likely to trip antivirus
#     false-positives on Windows specifically (single self-extracting
#     exe is a common packing pattern for both legitimate installers and
#     malware, and some AV heuristics can't tell them apart).
#   onedir -- a folder (Roast Telemetry.exe + an _internal/ folder next
#     to it that must travel with it) -- what this spec built and was
#     actually verified against in this sandbox before onefile support
#     was added; faster startup, no antivirus-heuristic risk, just not
#     a single file. Set PACKAGE_MODE=onedir to build this instead.
#
# sys._MEIPASS-based path resolution (tray_app.py's own BASE_DIR) works
# identically either way -- PyInstaller sets it to the same kind of
# "where the bundled data actually is" location in both modes (a stable
# _internal/ folder for onedir, a fresh per-launch temp dir for
# onefile), so no application code needed to change for this at all,
# only this spec's own final packaging step below.

import os
import sys

ONEFILE = os.environ.get("PACKAGE_MODE", "onefile") == "onefile"

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
    # pystray picks its actual backend (_win32/_darwin/_xorg/_appindicator/
    # _dummy) via a plain conditional import inside pystray/__init__.py,
    # not something dynamic PyInstaller's analysis could miss on its
    # own -- pyinstaller-hooks-contrib ships a dedicated hook for this
    # already (collect_submodules("pystray")), so this entry is a second,
    # cheap safety net for the case that hook doesn't fire for some
    # reason (an older pyinstaller-hooks-contrib version without it,
    # etc.) -- confirmed live: "ModuleNotFoundError: No module named
    # 'pystray'" at runtime on a first Windows build, root-caused to
    # tray_requirements.txt not actually being installed in that .venv
    # (see this spec's build scripts, which now explicitly ensure that
    # too) -- this alone wouldn't have caught that particular case
    # (nothing to bundle if the package was never installed at all),
    # but is still worth having for a *different* way this exact
    # failure could otherwise recur.
    "pystray",
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
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

# exclude_binaries=True is the onedir shape (binaries deferred to the
# COLLECT step below, next to the exe rather than inside it); onefile
# wants them included directly in the exe itself instead, and skips
# COLLECT entirely.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries + a.zipfiles + a.datas if ONEFILE else [],
    exclude_binaries=not ONEFILE,
    name="Roast Telemetry",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # windowed -- no console flash for the tray icon or the re-exec'd server subprocess
    icon=icon_path,
    runtime_tmpdir=None,  # onefile only -- default per-launch temp dir; see this spec's own PACKAGE_MODE comment
)

if not ONEFILE:
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

# macOS only -- wraps the EXE/COLLECT output into a real double-
# clickable .app bundle (works the same way whether exe above is a
# onefile single binary or the onedir COLLECT folder -- BUNDLE accepts
# either). Windows/Linux just use the exe/folder directly, no BUNDLE
# equivalent there.
if sys.platform == "darwin":
    app = BUNDLE(
        exe if ONEFILE else coll,
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
