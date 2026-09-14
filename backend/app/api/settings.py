"""App-wide settings -- currently just the Ollama connection used for
post-roast AI reviews (see roasts.py's /review endpoints)."""
from __future__ import annotations

import re

from fastapi import APIRouter

from .. import ollama_client, storage
from ..models import BREAKOUT_PANEL_KEYS, AppSettings, OllamaStatus

router = APIRouter(prefix="/settings", tags=["settings"])

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


@router.get("", response_model=AppSettings)
def get_settings() -> AppSettings:
    return AppSettings(**storage.get_settings())


def _filter_panels(keys: list[str]) -> list[str]:
    return [k for k in keys if k in BREAKOUT_PANEL_KEYS]


def _filter_colors(colors: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in colors.items() if k in BREAKOUT_PANEL_KEYS and _HEX_COLOR_RE.match(v)}


@router.put("", response_model=AppSettings)
def update_settings(settings: AppSettings) -> AppSettings:
    # Silently drop unknown keys/malformed values rather than error --
    # keeps this forward compatible if a stale frontend build sends a key
    # an older/newer backend doesn't know about, instead of failing the
    # whole save. Colors are also value-shape-checked (not just key-
    # checked): these get interpolated straight into an inline `style`
    # attribute on the frontend (BreakoutPanel.jsx), and while a native
    # `<input type="color">` always produces a clean #rrggbb string, this
    # is the API boundary -- don't trust that assumption holds for every
    # caller. small_readout_panels is a second, independent enabled-list
    # over the same BREAKOUT_PANEL_KEYS -- same validation, own storage
    # key -- but shares breakout_panel_colors with broken_out_panels
    # rather than having its own: colors are a property of the item
    # (e.g. "bt" is the same blue everywhere), not something that should
    # drift between two panels showing the same value.
    panels = _filter_panels(settings.broken_out_panels)
    colors = _filter_colors(settings.breakout_panel_colors)
    small_panels = _filter_panels(settings.small_readout_panels)
    storage.set_settings(
        ollama_url=settings.ollama_url,
        ollama_model=settings.ollama_model,
        broken_out_panels=panels,
        breakout_panel_colors=colors,
        small_readout_panels=small_panels,
    )
    return AppSettings(
        ollama_url=settings.ollama_url,
        ollama_model=settings.ollama_model,
        broken_out_panels=panels,
        breakout_panel_colors=colors,
        small_readout_panels=small_panels,
    )


@router.get("/ollama/status", response_model=OllamaStatus)
async def ollama_status(url: str) -> OllamaStatus:
    """Checks connectivity to `url` directly (not necessarily the saved
    one) -- lets the Settings page probe live as the user edits the field,
    before they've saved anything."""
    result = await ollama_client.check_connection(url)
    return OllamaStatus(**result)
