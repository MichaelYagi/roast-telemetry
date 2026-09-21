"""Pub/sub fan-out for live roast data, shared by the WebSocket and SSE endpoints."""
from __future__ import annotations

import asyncio
from typing import Any


class RoastPubSub:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = {}

    def subscribe(self, roast_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.setdefault(roast_id, set()).add(queue)
        return queue

    def subscriber_count(self, roast_id: str) -> int:
        """How many pages currently have this roast open."""
        return len(self._subscribers.get(roast_id, ()))

    def unsubscribe(self, roast_id: str, queue: asyncio.Queue) -> None:
        subs = self._subscribers.get(roast_id)
        if subs and queue in subs:
            subs.discard(queue)
            if not subs:
                self._subscribers.pop(roast_id, None)

    async def publish(self, roast_id: str, message: dict[str, Any]) -> None:
        for queue in list(self._subscribers.get(roast_id, ())):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            await queue.put(message)


pubsub = RoastPubSub()


class SettingsPubSub:
    """Single-topic broadcaster for app-wide settings changes -- unlike
    RoastPubSub there's no per-roast key, just one set of subscribers,
    since there's only ever one settings object."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    async def publish(self, message: str) -> None:
        """`message` is already a JSON string (AppSettings.model_dump_json())
        -- passed straight through as an SSE `data` field, unlike
        RoastPubSub's dict messages which get json.dumps'd per-consumer."""
        for queue in list(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            await queue.put(message)


settings_pubsub = SettingsPubSub()
