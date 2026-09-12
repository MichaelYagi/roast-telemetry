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
