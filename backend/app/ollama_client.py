"""Thin async client for a local Ollama server -- connectivity check +
review generation. No SDK dependency, just two plain HTTP calls against
Ollama's own REST API (``/api/tags``, ``/api/generate``).
"""
from __future__ import annotations

from typing import Optional

import httpx

CONNECT_TIMEOUT_S = 3.0
GENERATE_TIMEOUT_S = 180.0  # local models can be slow; this runs in a background task, not on the request path


async def check_connection(url: str) -> dict:
    """Returns {"connected": bool, "models": [str], "error": str|None}."""
    url = url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=CONNECT_TIMEOUT_S) as client:
            resp = await client.get(f"{url}/api/tags")
            resp.raise_for_status()
            data = resp.json()
        models = sorted(m["name"] for m in data.get("models", []) if "name" in m)
        return {"connected": True, "models": models, "error": None}
    except Exception as exc:  # noqa: BLE001 -- deliberately broad, this is a reachability probe
        return {"connected": False, "models": [], "error": str(exc)}


async def generate(url: str, model: str, prompt: str) -> str:
    """Blocking (from the caller's perspective) call to Ollama's /api/generate
    with streaming off -- returns the full response text, or raises."""
    url = url.rstrip("/")
    async with httpx.AsyncClient(timeout=GENERATE_TIMEOUT_S) as client:
        resp = await client.post(f"{url}/api/generate", json={"model": model, "prompt": prompt, "stream": False})
        resp.raise_for_status()
        data = resp.json()
    text = data.get("response")
    if not text:
        raise ValueError(f"Ollama returned no response text (raw: {data!r})")
    return text
