"""EventBus replay semantics — late WebSocket subscribers must not lose the
events emitted before they connected."""
from app.agents.events import MAX_BUFFER_PER_TASK, EventBus


async def test_late_subscriber_gets_backlog_then_live():
    bus = EventBus()
    await bus.publish("t", {"type": "log", "n": 1})
    await bus.publish("t", {"type": "log", "n": 2})

    q = bus.subscribe("t")
    assert q.get_nowait()["n"] == 1
    assert q.get_nowait()["n"] == 2

    await bus.publish("t", {"type": "log", "n": 3})
    assert q.get_nowait()["n"] == 3
    bus.unsubscribe("t", q)


async def test_buffer_is_bounded():
    bus = EventBus()
    for i in range(MAX_BUFFER_PER_TASK + 50):
        await bus.publish("t", {"type": "log", "n": i})
    q = bus.subscribe("t")
    first = q.get_nowait()
    assert first["n"] == 50  # oldest 50 evicted
    bus.unsubscribe("t", q)
