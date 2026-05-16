"""In-process pub/sub for streaming task events to WebSocket clients.

Events are produced by `run_task` the moment the task is spawned, but the
frontend opens its WebSocket a beat later. Without buffering, every event
emitted in that window (first screenshot, plan, early steps) is dropped and the
UI looks frozen at the start. So each task keeps a small replay buffer; a late
subscriber receives the backlog before the live stream.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict, defaultdict, deque
from typing import Any, Deque


# Per-task replay buffer size and how many task buffers we retain (LRU). These
# are bounded so a long-lived server doesn't accumulate event history forever.
MAX_BUFFER_PER_TASK = 250
MAX_TASKS_BUFFERED = 200


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._buffers: "OrderedDict[str, Deque[dict[str, Any]]]" = OrderedDict()

    def _buffer_for(self, task_id: str) -> Deque[dict[str, Any]]:
        buf = self._buffers.get(task_id)
        if buf is None:
            buf = deque(maxlen=MAX_BUFFER_PER_TASK)
            self._buffers[task_id] = buf
            while len(self._buffers) > MAX_TASKS_BUFFERED:
                self._buffers.popitem(last=False)
        self._buffers.move_to_end(task_id)
        return buf

    def subscribe(self, task_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        # Replay anything emitted before this client connected.
        for event in list(self._buffers.get(task_id, ())):
            q.put_nowait(event)
        self._subscribers[task_id].add(q)
        return q

    def unsubscribe(self, task_id: str, q: asyncio.Queue) -> None:
        subs = self._subscribers.get(task_id)
        if not subs:
            return
        subs.discard(q)
        if not subs:
            self._subscribers.pop(task_id, None)

    async def publish(self, task_id: str, event: dict[str, Any]) -> None:
        self._buffer_for(task_id).append(event)
        for q in list(self._subscribers.get(task_id, [])):
            await q.put(event)


bus = EventBus()
