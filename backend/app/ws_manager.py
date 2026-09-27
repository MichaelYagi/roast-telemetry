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


class SingleTopicPubSub:
    """Single-topic broadcaster: unlike RoastPubSub there's no per-roast
    key, just one set of subscribers getting everything published, for
    events that aren't scoped to any one roast (settings changes, a roast
    starting somewhere). Messages are passed straight through as given --
    either an already-JSON string (settings_pubsub, from
    AppSettings.model_dump_json()) or a plain dict a consumer json.dumps's
    itself (active_roast_pubsub), same convention RoastPubSub's own
    per-roast queues already use."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    async def publish(self, message) -> None:
        for queue in list(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            await queue.put(message)


settings_pubsub = SingleTopicPubSub()

# Fires whenever a roast starts recording anywhere (see
# RoastSessionManager.start()/begin_recording() in roast_session/session.py)
# -- lets an idle Configure Roast tab on one device notice a roast started
# from another device/tab and reconnect to it, instead of only finding out
# on its own next full page load.
active_roast_pubsub = SingleTopicPubSub()
