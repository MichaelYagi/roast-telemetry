from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import storage
from .api import devices, machines, roasts

FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage.init_db()
    yield


app = FastAPI(
    title="Artisan Web API",
    description=(
        "API-first coffee roasting platform: Artisan-style simulator mode, "
        "a mock USB/serial device layer, and an .alog playback engine, "
        "all running without physical hardware."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(roasts.router, prefix="/api")
app.include_router(machines.router, prefix="/api")
app.include_router(devices.router, prefix="/api")


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
        return FileResponse(FRONTEND_DIST / "index.html")
