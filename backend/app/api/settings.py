"""App-wide settings -- currently just the Ollama connection used for
post-roast AI reviews (see roasts.py's /review endpoints)."""
from __future__ import annotations

import re

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from .. import ollama_client, storage
from ..models import BREAKOUT_PANEL_KEYS, CHART_SERIES_KEYS, VERTICAL_CONTROL_KEYS, AppSettings, OllamaStatus
from ..ws_manager import settings_pubsub

router = APIRouter(prefix="/settings", tags=["settings"])

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


@router.get("", response_model=AppSettings)
def get_settings() -> AppSettings:
    return AppSettings(**storage.get_settings())


def _filter_panels(keys: list[str]) -> list[str]:
    return [k for k in keys if k in BREAKOUT_PANEL_KEYS]


def _filter_colors(colors: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in colors.items() if k in BREAKOUT_PANEL_KEYS and _HEX_COLOR_RE.match(v)}


def _filter_vertical_layout(groups: list[list[str]]) -> list[list[str]]:
    # Drops unknown keys and empty groups (a group that loses its only
    # member to filtering shouldn't leave a phantom empty column), and
    # de-dupes across the whole layout (a channel appearing twice would
    # render two drag-independent copies of the same slider, confusing
    # rather than just redundant). Does NOT enforce "drum/fan present" or
    # "at least one of heater_pct/burner_sv_c" here -- the settings editor
    # UI is what prevents constructing an invalid layout in the first
    # place; the *renderer* (VerticalControlPanel.jsx) is what stays safe
    # regardless, by falling back to its own built-in defaults for
    # whichever mandatory channel a still-malformed layout is missing --
    # same belt-and-suspenders split as _filter_colors' shape check above
    # not needing to also guess a "right" color.
    seen: set[str] = set()
    result: list[list[str]] = []
    for group in groups:
        filtered = [k for k in group if k in VERTICAL_CONTROL_KEYS and k not in seen]
        seen.update(filtered)
        if filtered:
            result.append(filtered)
    return result


def _filter_arrows(arrows: dict[str, float]) -> dict[str, float]:
    # Was `bool(v)` -- now that a positive value is a real step size, not
    # just an on/off flag, coercing it away would silently throw out the
    # step the moment it got saved. Non-positive/missing still means off,
    # same convention as before, just without flattening a real number.
    return {k: v for k, v in arrows.items() if k in VERTICAL_CONTROL_KEYS and v and v > 0}


def _filter_series_visible(visible: dict[str, bool]) -> dict[str, bool]:
    return {k: bool(v) for k, v in visible.items() if k in CHART_SERIES_KEYS}


def _clamp_history_page_size(size: int) -> int:
    return max(10, min(500, size))


def _clamp_max_compare(n: int) -> int:
    return max(3, min(100000, n))


@router.get("/stream")
async def stream_settings() -> EventSourceResponse:
    """Pushes the current settings immediately, then again on every save
    from any client -- replaces polling for LiveRoastView's Big/Small
    Readout panel hot-apply (see that view's settings useEffect)."""

    async def event_generator():
        yield {"event": "settings", "data": AppSettings(**storage.get_settings()).model_dump_json()}
        queue = settings_pubsub.subscribe()
        try:
            while True:
                message = await queue.get()
                yield {"event": "settings", "data": message}
        finally:
            settings_pubsub.unsubscribe(queue)

    return EventSourceResponse(event_generator())


@router.put("", response_model=AppSettings)
async def update_settings(settings: AppSettings, http_request: Request) -> AppSettings:
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
    temperature_unit = settings.temperature_unit if settings.temperature_unit in ("c", "f") else "c"
    vertical_layout = _filter_vertical_layout(settings.vertical_control_layout)
    vertical_arrows = _filter_arrows(settings.vertical_control_arrows)
    series_visible = _filter_series_visible(settings.chart_series_visible)
    history_page_size = _clamp_history_page_size(settings.history_page_size)
    max_compare = _clamp_max_compare(settings.max_compare)
    # A client that never sends `control` (an older cached page) must not
    # reset the saved safety limits back to the defaults.
    was_safety_disabled = AppSettings(**storage.get_settings()).control.safety_disabled
    if "control" in settings.model_fields_set:
        control = settings.control
    else:
        control = AppSettings(**storage.get_settings()).control
    storage.set_settings(
        ollama_url=settings.ollama_url,
        ollama_model=settings.ollama_model,
        broken_out_panels=panels,
        breakout_panel_colors=colors,
        small_readout_panels=small_panels,
        temperature_unit=temperature_unit,
        vertical_control_layout=vertical_layout,
        vertical_control_arrows=vertical_arrows,
        chart_series_visible=series_visible,
        history_page_size=history_page_size,
        max_compare=max_compare,
        away_alarm_enabled=settings.away_alarm_enabled,
        control=control.model_dump(),
    )
    # Not what tripped while it was off (see enter_safe_state's own
    # docstring -- nothing about that is recorded anywhere), just the
    # on/off moment itself, so an incident can at least be traced back to
    # "safety was off starting at this time" -- the one thing worth being
    # able to reconstruct after the fact.
    if control.safety_disabled != was_safety_disabled:
        user = getattr(http_request.state, "user", None)
        storage.log_activity(
            "safety",
            "safety_disabled" if control.safety_disabled else "safety_enabled",
            username=user["username"] if user else None,
            message="Roaster safety disabled -- Emergency Stop, fail-safes and command limits are all off"
            if control.safety_disabled
            else "Roaster safety re-enabled",
        )
    result = AppSettings(
        ollama_url=settings.ollama_url,
        ollama_model=settings.ollama_model,
        broken_out_panels=panels,
        breakout_panel_colors=colors,
        small_readout_panels=small_panels,
        temperature_unit=temperature_unit,
        vertical_control_layout=vertical_layout,
        vertical_control_arrows=vertical_arrows,
        chart_series_visible=series_visible,
        history_page_size=history_page_size,
        max_compare=max_compare,
        away_alarm_enabled=settings.away_alarm_enabled,
        control=control,
    )
    await settings_pubsub.publish(result.model_dump_json())
    return result


@router.get("/ollama/status", response_model=OllamaStatus)
async def ollama_status(url: str) -> OllamaStatus:
    """Checks connectivity to `url` directly (not necessarily the saved
    one) -- lets the Settings page probe live as the user edits the field,
    before they've saved anything."""
    result = await ollama_client.check_connection(url)
    return OllamaStatus(**result)
