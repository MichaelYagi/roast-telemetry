"""backend/app/ollama_client.py -- no pytest-asyncio in this suite, so these
drive the async functions directly via asyncio.run(), and swap in an
httpx.MockTransport instead of hitting a real server."""
from __future__ import annotations

import asyncio

import httpx

from backend.app import ollama_client


_RealAsyncClient = httpx.AsyncClient  # captured before any monkeypatching below --
# patching ollama_client.httpx.AsyncClient patches the one shared httpx module
# object, so a fake that itself called httpx.AsyncClient(...) would recurse
# into its own replacement instead of building a real client.


def _mock_client(payload):
    def handler(request):
        return httpx.Response(200, json=payload)

    def fake_async_client(*args, **kwargs):
        return _RealAsyncClient(transport=httpx.MockTransport(handler))

    return fake_async_client


def test_check_connection_keeps_completion_models_and_ones_with_no_capabilities(monkeypatch):
    # nomic-embed-text (embedding-only) must be filtered out -- sending it a
    # plain text prompt via /api/generate (what generateReview/requestInsight
    # do) doesn't work. A model reporting no `capabilities` at all (an older
    # Ollama server that predates the field) is kept rather than guessed at.
    payload = {
        "models": [
            {"name": "qwen2.5:32b", "capabilities": ["completion"]},
            {"name": "nomic-embed-text", "capabilities": ["embedding"]},
            {"name": "llava", "capabilities": ["completion", "vision"]},
            {"name": "old-server-model"},
        ]
    }
    monkeypatch.setattr(ollama_client.httpx, "AsyncClient", _mock_client(payload))

    result = asyncio.run(ollama_client.check_connection("http://localhost:11434"))

    assert result == {
        "connected": True,
        "models": ["llava", "old-server-model", "qwen2.5:32b"],
        "error": None,
    }


def test_check_connection_reports_unreachable_server(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("connection refused")

    def fake_async_client(*args, **kwargs):
        return _RealAsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(ollama_client.httpx, "AsyncClient", fake_async_client)

    result = asyncio.run(ollama_client.check_connection("http://localhost:11434"))

    assert result["connected"] is False and result["models"] == []


def test_check_connection_retries_a_lookup_that_fails_once(monkeypatch):
    # A dynamic-DNS name can fail its first lookup and resolve on the next try.
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("[Errno 11001] getaddrinfo failed")
        return httpx.Response(200, json={"models": [{"name": "llama3.1"}]})

    def fake_async_client(*args, **kwargs):
        return _RealAsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(ollama_client.httpx, "AsyncClient", fake_async_client)
    monkeypatch.setattr(ollama_client, "CONNECT_RETRY_DELAY_S", 0)

    result = asyncio.run(ollama_client.check_connection("http://example.test:11434"))

    assert result["connected"] is True and result["models"] == ["llama3.1"]
    assert calls["n"] == 3


def test_check_connection_gives_up_after_all_attempts(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ConnectError("[Errno 11001] getaddrinfo failed")

    def fake_async_client(*args, **kwargs):
        return _RealAsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(ollama_client.httpx, "AsyncClient", fake_async_client)
    monkeypatch.setattr(ollama_client, "CONNECT_RETRY_DELAY_S", 0)

    result = asyncio.run(ollama_client.check_connection("http://example.test:11434"))

    assert result["connected"] is False and "getaddrinfo" in result["error"]
    assert calls["n"] == ollama_client.CONNECT_ATTEMPTS
