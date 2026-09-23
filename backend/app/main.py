from __future__ import annotations

import platform
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth, storage
from .api import auth as auth_api
from .api import activity, analysis, beans, device_profiles, devices, files, presets, roasts, serial_ports, settings, views
from .models import DeviceProfileCreateRequest, RoastCreateRequest, UserStatus
from .roast_session import RoastSessionError, session_manager
from .version import VERSION
from modbus_bridge.device_profiles import BUILT_IN_PROFILES

FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"

# Ships as ready-to-use "Load saved config" entries instead of an empty
# dropdown -- port is deliberately left blank on each (no sensible
# universal default, same reasoning as presets.py's own docstring),
# everything else is exactly that engine's own confirmed defaults (see
# modbus_bridge/engine.py and ms6514_bridge/engine.py). Only the two
# direct-hardware bridges get one -- simulator/alog_playback need no
# connection fields to begin with, so a preset for either would just be
# an empty form.
_DEFAULT_PRESETS = [
    {
        "id": "default-fz94-usb",
        "name": "FZ-94, USB",
        "config": RoastCreateRequest(title="", mode="modbus_live"),
        "manufacturer": "Coffee-Tech",
    },
    {
        "id": "default-fz94-evo",
        "name": "FZ-94 Evo, Ethernet",
        # Factory default IP/port -- see modbus_bridge/device_profiles.py
        # for the full sourcing on this profile's register map.
        "config": RoastCreateRequest(
            title="", mode="modbus_live", modbus_transport="tcp",
            modbus_host="192.168.1.2", modbus_tcp_port=502,
            modbus_device_profile_id="coffeetech-fz94-evo",
        ),
        "manufacturer": "Coffee-Tech",
    },
    {
        "id": "default-ms6514-usb",
        "name": "Mastech MS6514, USB",
        "config": RoastCreateRequest(title="", mode="ms6514_live"),
    },
    {
        "id": "default-aillio-bullet-r1",
        "name": "Bullet R1, USB",
        # No port/host at all -- a raw USB device found by its own
        # vendor/product id (see aillio_bridge/transport.py), genuinely
        # nothing else to configure, unlike the serial-port presets above.
        "config": RoastCreateRequest(title="", mode="aillio_live", aillio_model="r1"),
        "manufacturer": "Aillio",
    },
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage.init_db()
    # Crash/restart recovery: any roast still marked roasting/cooling from
    # a previous process is orphaned -- its in-memory session is gone and
    # can never come back, so leaving it stuck as "roasting" forever would
    # make it permanently unreachable and unstoppable. See
    # storage.abort_stale_roasts for the full rationale.
    storage.abort_stale_roasts()
    session_manager.backfill_durations()
    session_manager.backfill_dates()
    session_manager.backfill_beans()
    session_manager.clean_bean_records()
    storage.seed_default_presets([
        {
            "id": p["id"],
            "name": p["name"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "config_json": p["config"].model_dump_json(),
            "manufacturer": p.get("manufacturer"),
        }
        for p in _DEFAULT_PRESETS
    ])
    storage.seed_default_device_profiles([
        {
            "id": p.id,
            "name": p.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "config_json": DeviceProfileCreateRequest(**p.model_dump()).model_dump_json(),
        }
        for p in BUILT_IN_PROFILES
    ])
    yield


app = FastAPI(
    title="Roast Telemetry",
    description=(
        "API-first coffee roasting platform: a simulator mode, "
        "a mock USB/serial device layer, and an .alog playback engine, "
        "all running without physical hardware."
    ),
    version=VERSION,
    lifespan=lifespan,
)

@app.exception_handler(RoastSessionError)
async def roast_session_error_handler(request: Request, exc: RoastSessionError) -> JSONResponse:
    """Backstop for a roast problem no endpoint handled itself (a missing
    recording file, say): a readable message instead of a bare 500."""
    return JSONResponse(status_code=404, content={"detail": str(exc)})


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Every other /api/* route requires a logged-in, ALLOWED account -- see
# this middleware below. Everything past login is shared: there's no
# per-user data ownership anywhere in this app (roasts, presets, settings
# are all global), so "logged in" is the only gate that exists; the sole
# extra restriction is auth_api's own /auth/users* endpoints, which check
# admin role for themselves via auth.require_admin.
_PUBLIC_API_PATHS = {
    "/api/v1/health",
    "/api/v1/auth/status",
    "/api/v1/auth/register",
    "/api/v1/auth/login",
    "/api/v1/auth/logout",
}


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/") or path in _PUBLIC_API_PATHS:
        return await call_next(request)
    # X-API-Key first -- a script/curl/Home Assistant etc. hitting the API
    # directly (see auth.generate_api_key/Account -> API key) sends this,
    # not a cookie. Falls back to the browser's session cookie when it's
    # absent, so the two credentials are simply alternatives, not layered.
    user = None
    api_key = request.headers.get("X-API-Key")
    if api_key:
        user = storage.get_user_by_api_key_hash(auth.hash_api_key(api_key))
    if user is None:
        user = auth.get_user_for_token(request.cookies.get(auth.SESSION_COOKIE))
    if user is None or user["status"] != UserStatus.ALLOWED.value:
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    request.state.user = user
    return await call_next(request)


app.include_router(auth_api.router, prefix="/api/v1")
app.include_router(roasts.router, prefix="/api/v1")
app.include_router(devices.router, prefix="/api/v1")
app.include_router(presets.router, prefix="/api/v1")
app.include_router(device_profiles.router, prefix="/api/v1")
app.include_router(settings.router, prefix="/api/v1")
app.include_router(serial_ports.router, prefix="/api/v1")
app.include_router(files.router, prefix="/api/v1")
app.include_router(analysis.router, prefix="/api/v1")
app.include_router(beans.router, prefix="/api/v1")
app.include_router(views.router, prefix="/api/v1")
app.include_router(activity.router, prefix="/api/v1")


def _server_platform() -> str:
    """A short, human label for the OS the *server* is actually running on --
    distinct from the client browser's OS, and from "Windows" vs "WSL2"
    (same .venv-eligible Linux python.exe can't tell you which one it's
    under without checking, since uname() alone says "Linux" either way --
    see scripts/run-server.sh's own WSL-vs-native-Windows checks for why
    that distinction matters for this app in particular)."""
    system = platform.system()
    if system == "Darwin":
        return "macOS"
    if system == "Linux":
        try:
            if "microsoft" in Path("/proc/version").read_text().lower():
                return "Linux (WSL2)"
        except OSError:
            pass
        return "Linux"
    return system  # "Windows", or whatever else platform.system() reports


@app.get("/api/v1/health")
def health() -> dict:
    return {"status": "ok", "platform": _server_platform()}


# Serve the built frontend (frontend/dist, from `npm run build`) from the
# same process/port as the API, so the whole app can run as one server.
# In dev mode this directory won't exist -- run the Vite dev server
# separately instead (see README) and this block is simply skipped.
if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="frontend-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str) -> FileResponse:
        # Files Vite copies verbatim from frontend/public/ (favicon.ico, the
        # header icon, etc.) live at dist's root next to index.html, not
        # under /assets -- without this check they fell through to the
        # index.html fallback below like any SPA route, so the browser got
        # back HTML instead of the actual image (looked like a blank icon).
        candidate = (FRONTEND_DIST / full_path).resolve()
        if full_path and candidate.is_relative_to(FRONTEND_DIST.resolve()) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
