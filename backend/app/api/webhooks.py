"""CRUD for outbound webhooks (Settings > Webhooks) plus a one-off test
send. See backend/app/webhooks.py for the actual dispatch logic and the
event list."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import storage, webhooks

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class WebhookCreateRequest(BaseModel):
    name: str
    url: str
    events: list[str] = Field(default=[], description="Event keys (see GET /webhooks/events) this webhook fires for; empty means every event.")
    enabled: bool = True


class Webhook(WebhookCreateRequest):
    id: str
    created_at: str

    @classmethod
    def from_row(cls, row: dict) -> "Webhook":
        return cls(
            id=row["id"], name=row["name"], url=row["url"], created_at=row["created_at"],
            events=json.loads(row["events_json"]) if row["events_json"] else [],
            enabled=bool(row["enabled"]),
        )


def _validated_events(events: list[str]) -> list[str]:
    unknown = [e for e in events if e not in webhooks.EVENTS]
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown event(s): {', '.join(unknown)}")
    return events


@router.get("/events")
def list_events() -> dict:
    """{key: label} for every event a webhook can subscribe to -- populates
    the Settings page's per-webhook event checkboxes."""
    return webhooks.EVENTS


@router.get("", response_model=list[Webhook])
def list_webhooks() -> list[Webhook]:
    return [Webhook.from_row(r) for r in storage.list_webhook_rows()]


@router.post("", response_model=Webhook, status_code=201)
def create_webhook(request: WebhookCreateRequest) -> Webhook:
    _validated_events(request.events)
    webhook_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    storage.insert_webhook({
        "id": webhook_id, "name": request.name, "url": request.url,
        "events_json": json.dumps(request.events), "enabled": request.enabled, "created_at": created_at,
    })
    return Webhook(id=webhook_id, created_at=created_at, **request.model_dump())


@router.put("/{webhook_id}", response_model=Webhook)
def update_webhook(webhook_id: str, request: WebhookCreateRequest) -> Webhook:
    row = storage.get_webhook_row(webhook_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"webhook {webhook_id!r} not found")
    _validated_events(request.events)
    storage.update_webhook_row(webhook_id, {
        "name": request.name, "url": request.url, "events_json": json.dumps(request.events), "enabled": request.enabled,
    })
    return Webhook(id=webhook_id, created_at=row["created_at"], **request.model_dump())


@router.delete("/{webhook_id}", status_code=204)
def delete_webhook(webhook_id: str) -> None:
    if storage.get_webhook_row(webhook_id) is None:
        raise HTTPException(status_code=404, detail=f"webhook {webhook_id!r} not found")
    storage.delete_webhook_row(webhook_id)


class TestWebhookRequest(BaseModel):
    url: str


@router.post("/test")
async def test_webhook(request: TestWebhookRequest) -> dict:
    """Posts a fixed test payload straight to `url`, independent of any
    saved webhook row -- lets the Settings page's "Send test" button work
    on a URL that hasn't been saved yet."""
    return await webhooks.send_test(request.url)
