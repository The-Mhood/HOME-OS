import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

SubscriberFn = Callable[[dict[str, Any]], Awaitable[None]]


class EventBus:
    """In-process async pub/sub event bus powering real-time WebSocket updates and automation triggers."""

    def __init__(self):
        self._subscribers: set[SubscriberFn] = set()
        self._queues: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe_callback(self, fn: SubscriberFn) -> None:
        self._subscribers.add(fn)

    def unsubscribe_callback(self, fn: SubscriberFn) -> None:
        self._subscribers.discard(fn)

    def create_queue(self, maxsize: int = 256) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=maxsize)
        self._queues.add(q)
        return q

    def remove_queue(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._queues.discard(q)

    async def publish(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        envelope = {
            "event_type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        for q in list(self._queues):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(envelope)
            except asyncio.QueueFull:
                pass

        for fn in list(self._subscribers):
            try:
                await fn(envelope)
            except Exception:
                # Individual subscriber errors must not break event delivery
                continue
        return envelope
