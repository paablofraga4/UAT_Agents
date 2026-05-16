import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.agents.events import bus


router = APIRouter()


@router.websocket("/ws/tasks/{task_id}")
async def task_events(ws: WebSocket, task_id: str) -> None:
    await ws.accept()
    queue = bus.subscribe(task_id)
    try:
        while True:
            event = await queue.get()
            await ws.send_json(event)
            # `completed` is always the final frame (the error path emits
            # `error` then `completed`). Drain anything already buffered, then
            # close so the socket and its subscription don't leak.
            if event.get("type") == "completed":
                while not queue.empty():
                    await ws.send_json(queue.get_nowait())
                break
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        bus.unsubscribe(task_id, queue)
        with contextlib.suppress(Exception):
            await ws.close()
