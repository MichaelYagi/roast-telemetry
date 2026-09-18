from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth, storage
from .api import auth as auth_api
from .api import device_profiles, devices, presets, roasts, serial_ports, settings
from .models import DeviceProfileCreateRequest, RoastCreateRequest, UserStatus
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
    {"id": "default-fz94-usb", "name": "FZ-94, USB", "config": RoastCreateRequest(title="", mode="modbus_live")},
    {
        "id": "default-ms6514-usb",
        "name": "Mastech MS6514, USB",
        "config": RoastCreateRequest(title="", mode="ms6514_live"),
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
    storage.seed_default_presets([
        {
            "id": p["id"],
            "name": p["name"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "config_json": p["config"].model_dump_json(),
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
        "API-first coffee roasting platform: Artisan-style simulator mode, "
        "a mock USB/serial device layer, and an .alog playback engine, "
        "all running without physical hardware."
    ),
    version="1.2.0",
    lifespan=lifespan,
)

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
    "/api/health",
    "/api/auth/status",
    "/api/auth/register",
    "/api/auth/login",
    "/api/auth/logout",
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


app.include_router(auth_api.router, prefix="/api")
app.include_router(roasts.router, prefix="/api")
app.include_router(devices.router, prefix="/api")
app.include_router(presets.router, prefix="/api")
app.include_router(device_profiles.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(serial_ports.router, prefix="/api")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


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
