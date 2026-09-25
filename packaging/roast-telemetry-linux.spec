# PyInstaller spec for Roast Telemetry's Linux standalone build --
# command-line only, no tray icon (see [[feedback_no_linux_desktop_tray]]
# for why a Linux tray was tried and deliberately removed: no single
# standard tray protocol the way Windows/macOS each have exactly one).
# Bundles scripts/server_app.py, the FastAPI backend, and the built
# frontend into a single executable -- no separate Python/npm/venv
# install required to run it, same idea as roast-telemetry.spec but for
# a genuinely simpler target (one process, no tray/subprocess dance, no
# pystray/Pillow to bundle at all).
#
# A deliberately separate spec file from roast-telemetry.spec rather than
# a third branch bolted onto it -- that file's icon handling, BUNDLE step,
# and hidden-imports list are all Windows/macOS-tray-specific; forcing a
# third, structurally different target through the same file would cost
# more in added conditionals than a second, smaller file that mostly just
# doesn't need most of what that one does.
#
# Must be run *on* Linux -- no supported cross-compile path (same
# constraint as roast-telemetry.spec). See packaging/build-linux.sh,
# which just wraps:
#
#     pyinstaller packaging/roast-telemetry-linux.spec --distpath dist --workpath build
#
# Every path below is built from SPECPATH (packaging/), not the caller's
# own cwd -- see roast-telemetry.spec's own comment for why that matters
# (confirmed the hard way there already).
#
# PACKAGE_MODE env var picks the output shape, same as roast-telemetry.spec:
#   onefile (default) -- a genuinely single executable.
#   onedir -- a folder (Roast Telemetry + an _internal/ folder next to it).

import os

ONEFILE = os.environ.get("PACKAGE_MODE", "onefile") == "onefile"

block_cipher = None
REPO_ROOT = os.path.dirname(SPECPATH)


def repo_path(*parts):
    return os.path.join(REPO_ROOT, *parts)


# Same reasoning as roast-telemetry.spec's own hidden_imports: PyInstaller's
# static analysis generally follows these already, but a silently-missing
# hidden import surfaces as a runtime ImportError inside the packaged app
# with no useful traceback, not a build-time error here. No pystray/PIL --
# this build never has a tray to begin with.
hidden_imports = [
    "serial.urlhandler",
    "serial.urlhandler.protocol_socket",
    "serial.urlhandler.protocol_rfc2217",
    "serial.urlhandler.protocol_loop",
    "serial.urlhandler.protocol_hwgrep",
    "serial.urlhandler.protocol_alt",
    "serial.urlhandler.protocol_spy",
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
    "backend.app.api.files",
    "hardware_fakes",
    "hardware_fakes.sim",
    "hardware_fakes._thermal",
    "hardware_fakes.tcp_serial",
    "hardware_fakes.modbus_fz94",
    "hardware_fakes.modbus_fz94_evo",
    "hardware_fakes.ms6514_device",
    "hardware_fakes.tc4",
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
    "roast_heuristics.ror",
    "simulator",
    "alog_playback",
    "mock_device",
]

# Same destination-matches-source-layout reasoning as roast-telemetry.spec's
# own datas comment -- backend/app/main.py's FRONTEND_DIST resolves against
# this exact relative path, unmodified whether run from source or frozen.
datas = [
    (repo_path("frontend", "dist"), "frontend/dist"),
    (repo_path("backend", "data", "sample_roasts"), "backend/data/sample_roasts"),
]

a = Analysis(
    [repo_path("scripts", "server_app.py")],
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
    a.binaries + a.zipfiles + a.datas if ONEFILE else [],
    exclude_binaries=not ONEFILE,
    name="roast-telemetry",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # a CLI tool -- prints its own "starting on http://..." line, Ctrl+C stops it
    runtime_tmpdir=None,  # onefile only -- default per-launch temp dir
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
        name="roast-telemetry",
    )
