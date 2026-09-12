"""App-wide settings -- currently just the Ollama connection used for
post-roast AI reviews (see roasts.py's /review endpoints)."""
from __future__ import annotations

from fastapi import APIRouter

from .. import ollama_client, storage
from ..models import BREAKOUT_PANEL_KEYS, AppSettings, OllamaStatus

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=AppSettings)
def get_settings() -> AppSettings:
    return AppSettings(**storage.get_settings())


@router.put("", response_model=AppSettings)
def update_settings(settings: AppSettings) -> AppSettings:
    # Silently drop unknown keys rather than error -- keeps this forward
    # compatible if a stale frontend build sends a key an older/newer
    # backend doesn't know about, instead of failing the whole save.
    panels = [p for p in settings.broken_out_panels if p in BREAKOUT_PANEL_KEYS]
    storage.set_settings(ollama_url=settings.ollama_url, ollama_model=settings.ollama_model, broken_out_panels=panels)
    return AppSettings(ollama_url=settings.ollama_url, ollama_model=settings.ollama_model, broken_out_panels=panels)


@router.get("/ollama/status", response_model=OllamaStatus)
async def ollama_status(url: str) -> OllamaStatus:
    """Checks connectivity to `url` directly (not necessarily the saved
    one) -- lets the Settings page probe live as the user edits the field,
    before they've saved anything."""
    result = await ollama_client.check_connection(url)
    return OllamaStatus(**result)
