"""Thin async client for a local Ollama server -- connectivity check +
review generation. No SDK dependency, just two plain HTTP calls against
Ollama's own REST API (``/api/tags``, ``/api/generate``).
"""
from __future__ import annotations

from typing import Optional

import asyncio

import httpx

CONNECT_TIMEOUT_S = 3.0
# A first lookup of a dynamic-DNS name can fail (Windows reports it as
# "getaddrinfo failed") and succeed a moment later, so a single failed
# connection doesn't mean the server is down. Only connection-level errors
# are retried; an HTTP error from a server that answered is final.
CONNECT_ATTEMPTS = 3
CONNECT_RETRY_DELAY_S = 1.0
# This runs in a background task (see roasts.py's _run_review), never on the
# request path, so a generous timeout costs nothing but eventually giving up
# on a genuinely wedged/unreachable server. 180s turned out too short for a
# real setup: a large local model (e.g. a 32B one) on modest hardware can
# run at under 2 tokens/sec, so processing a several-thousand-token roast
# prompt plus generating the review comfortably exceeds 3 minutes and was
# failing with "ReadTimeout" partway through an otherwise-working
# generation (confirmed live against Ollama's own server log).
GENERATE_TIMEOUT_S = 1800.0


def _is_prompt_to_text(model: dict) -> bool:
    """Excludes embedding-only (and any other non-completion) models from
    the Settings dropdown -- generateReview/requestInsight both send a plain
    text prompt to /api/generate, which an embedding model can't answer.
    /api/tags reports each model's `capabilities` (e.g. ["completion",
    "vision"]) on any reasonably current Ollama server; an older server that
    doesn't report it at all gets the benefit of the doubt (kept, not
    hidden) rather than guessed at some other way."""
    capabilities = model.get("capabilities")
    return capabilities is None or "completion" in capabilities


async def check_connection(url: str) -> dict:
    """Returns {"connected": bool, "models": [str], "error": str|None}."""
    url = url.rstrip("/")
    last_error: Optional[Exception] = None
    for attempt in range(CONNECT_ATTEMPTS):
        if attempt:
            await asyncio.sleep(CONNECT_RETRY_DELAY_S)
        try:
            async with httpx.AsyncClient(timeout=CONNECT_TIMEOUT_S) as client:
                resp = await client.get(f"{url}/api/tags")
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError as exc:
            last_error = exc
            continue
        except Exception as exc:  # noqa: BLE001 -- deliberately broad, this is a reachability probe
            return {"connected": False, "models": [], "error": str(exc)}
        models = sorted(m["name"] for m in data.get("models", []) if "name" in m and _is_prompt_to_text(m))
        return {"connected": True, "models": models, "error": None}
    return {"connected": False, "models": [], "error": str(last_error)}


async def generate(url: str, model: str, prompt: str, options: Optional[dict] = None) -> str:
    """Blocking (from the caller's perspective) call to Ollama's /api/generate
    with streaming off -- returns the full response text, or raises.
    `options` are Ollama model options, e.g. {"num_ctx": 8192}."""
    url = url.rstrip("/")
    body: dict = {"model": model, "prompt": prompt, "stream": False}
    if options:
        body["options"] = options
    async with httpx.AsyncClient(timeout=GENERATE_TIMEOUT_S) as client:
        resp = await client.post(f"{url}/api/generate", json=body)
        resp.raise_for_status()
        data = resp.json()
    text = data.get("response")
    if not text:
        raise ValueError(f"Ollama returned no response text (raw: {data!r})")
    return text
