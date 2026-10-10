"""backend/app/webhooks.py's dispatch logic (against a mocked httpx client,
same technique as test_ollama_client.py), the CRUD API, and -- the part that
actually matters -- that RoastSession/RoastControl fire the right event at
the right moment with the right payload. The last group monkeypatches
webhooks.fire_background itself (imported into session.py/control.py) to a
capturing stub, so it tests *what* fires and *when* without needing a real
HTTP round trip through the session's own background event loop.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from backend.app import storage, webhooks
from backend.app.models import RoastCreateRequest, RoastMode
from backend.app.roast_session.session import RoastSession


class _RealAsyncClient(httpx.AsyncClient):
    pass


def _mock_client(handler):
    def factory(*args, **kwargs):
        return _RealAsyncClient(transport=httpx.MockTransport(handler), **{k: v for k, v in kwargs.items() if k != "timeout"})

    return factory


async def _stop_background_task(session) -> None:
    if session._task is not None:
        session._task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await session._task


# -- dispatch (webhooks.fire / send_test) -----------------------------------


def test_fire_sends_only_to_webhooks_subscribed_to_the_event(isolated_db, monkeypatch):
    calls = []

    def handler(request):
        calls.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200)

    monkeypatch.setattr(webhooks.httpx, "AsyncClient", _mock_client(handler))

    storage.insert_webhook({
        "id": "w1", "name": "Everything", "url": "http://a.test/hook", "events_json": "[]",
        "enabled": True, "created_at": "2026-01-01T00:00:00+00:00",
    })
    storage.insert_webhook({
        "id": "w2", "name": "Only Drop", "url": "http://b.test/hook", "events_json": json.dumps(["DROP"]),
        "enabled": True, "created_at": "2026-01-01T00:00:00+00:00",
    })
    storage.insert_webhook({
        "id": "w3", "name": "Disabled", "url": "http://c.test/hook", "events_json": "[]",
        "enabled": False, "created_at": "2026-01-01T00:00:00+00:00",
    })

    asyncio.run(webhooks.fire("CHARGE", {"roast_id": "r1", "roast_title": "Test"}))

    urls = {u for u, _ in calls}
    assert urls == {"http://a.test/hook"}  # w2 isn't subscribed to CHARGE, w3 is disabled
    assert calls[0][1]["event"] == "CHARGE" and calls[0][1]["roast_id"] == "r1"


def test_fire_is_a_noop_with_no_matching_webhooks(isolated_db, monkeypatch):
    called = []
    monkeypatch.setattr(webhooks.httpx, "AsyncClient", _mock_client(lambda r: called.append(r) or httpx.Response(200)))
    asyncio.run(webhooks.fire("DROP", {"roast_id": "r1"}))
    assert called == []


def test_fire_logs_but_does_not_raise_when_a_target_is_unreachable(isolated_db, monkeypatch):
    def handler(request):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(webhooks.httpx, "AsyncClient", _mock_client(handler))
    storage.insert_webhook({
        "id": "w1", "name": "Dead", "url": "http://dead.test/hook", "events_json": "[]",
        "enabled": True, "created_at": "2026-01-01T00:00:00+00:00",
    })

    asyncio.run(webhooks.fire("DROP", {"roast_id": "r1", "roast_title": "Test"}))  # must not raise

    log = storage.list_activity(limit=10)
    assert any(r["category"] == "webhook" and r["action"] == "failed" for r in log)


def test_send_test_reports_success_and_failure(monkeypatch):
    monkeypatch.setattr(webhooks.httpx, "AsyncClient", _mock_client(lambda r: httpx.Response(200)))
    result = asyncio.run(webhooks.send_test("http://ok.test/hook"))
    assert result == {"ok": True, "status": 200, "error": None}

    monkeypatch.setattr(webhooks.httpx, "AsyncClient", _mock_client(lambda r: httpx.Response(500, text="boom")))
    result = asyncio.run(webhooks.send_test("http://bad.test/hook"))
    assert result["ok"] is False and result["status"] == 500


# -- CRUD API -----------------------------------------------------------


def test_webhooks_crud_round_trip(client):
    resp = client.get("/api/v1/webhooks/events")
    assert resp.status_code == 200 and "DROP" in resp.json()

    created = client.post("/api/v1/webhooks", json={"name": "My Phone", "url": "http://x.test/hook", "events": ["DROP", "e_stop"]})
    assert created.status_code == 201
    webhook_id = created.json()["id"]

    listed = client.get("/api/v1/webhooks").json()
    assert any(w["id"] == webhook_id for w in listed)

    updated = client.put(f"/api/v1/webhooks/{webhook_id}", json={"name": "My Phone", "url": "http://x.test/hook", "events": [], "enabled": False})
    assert updated.status_code == 200 and updated.json()["enabled"] is False

    assert client.delete(f"/api/v1/webhooks/{webhook_id}").status_code == 204
    assert all(w["id"] != webhook_id for w in client.get("/api/v1/webhooks").json())


def test_webhooks_api_rejects_an_unknown_event(client):
    resp = client.post("/api/v1/webhooks", json={"name": "Bad", "url": "http://x.test/hook", "events": ["NOT_A_REAL_EVENT"]})
    assert resp.status_code == 422


# -- session/control wiring: the right event, at the right moment -------


@pytest.fixture
def captured_webhooks(monkeypatch):
    calls = []
    monkeypatch.setattr("backend.app.roast_session.session.webhooks.fire_background", lambda event, payload: calls.append((event, payload)))
    monkeypatch.setattr("backend.app.roast_session.control.webhooks.fire_background", lambda event, payload: calls.append((event, payload)))
    return calls


def test_manual_milestone_fires_its_named_webhook_event(isolated_db, captured_webhooks):
    from backend.app.models import EventCreateRequest, RoastEventType

    async def body():
        request = RoastCreateRequest(title="Webhook Test", mode=RoastMode.SIMULATOR)
        session = RoastSession("webhook-milestone-test", request)
        await session.start()
        try:
            session.add_event(EventCreateRequest(type=RoastEventType.DRY_END, label="Dry End"))
        finally:
            await _stop_background_task(session)

    asyncio.run(body())

    events = [e for e, _ in captured_webhooks]
    assert "DRY_END" in events
    payload = next(p for e, p in captured_webhooks if e == "DRY_END")
    assert payload["roast_id"] == "webhook-milestone-test" and payload["roast_title"] == "Webhook Test"


def test_a_custom_note_does_not_fire_a_milestone_webhook(isolated_db, captured_webhooks):
    from backend.app.models import EventCreateRequest, RoastEventType

    async def body():
        request = RoastCreateRequest(title="Webhook Test", mode=RoastMode.SIMULATOR)
        session = RoastSession("webhook-custom-test", request)
        await session.start()
        try:
            session.add_event(EventCreateRequest(type=RoastEventType.CUSTOM, label="just a note"))
        finally:
            await _stop_background_task(session)

    asyncio.run(body())

    assert captured_webhooks == []


def test_finishing_a_roast_fires_roast_finished(isolated_db, captured_webhooks):
    async def body():
        request = RoastCreateRequest(title="Webhook Test", mode=RoastMode.SIMULATOR)
        session = RoastSession("webhook-finish-test", request)
        await session.start()
        await session.abort()  # operator OFF -- finishes as STOPPED; already awaits/cleans up its own background task

    asyncio.run(body())

    finished = [p for e, p in captured_webhooks if e == "roast_finished"]
    assert len(finished) == 1 and finished[0]["status"] == "stopped"


def test_emergency_stop_fires_e_stop_with_the_reason(isolated_db, captured_webhooks):
    async def body():
        request = RoastCreateRequest(title="Webhook Test", mode=RoastMode.SIMULATOR)
        session = RoastSession("webhook-estop-test", request)
        await session.start()
        try:
            await session.control.emergency_stop(username="tester", platform="test")
        finally:
            await _stop_background_task(session)

    asyncio.run(body())

    estops = [p for e, p in captured_webhooks if e == "e_stop"]
    assert len(estops) == 1 and estops[0]["reason"] == "emergency stop"
