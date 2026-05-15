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
            if event.get("type") in ("completed", "error"):
                # Keep socket open briefly so the client receives the final frame.
                continue
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(task_id, queue)
