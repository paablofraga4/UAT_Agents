import asyncio
import sys
from contextlib import asynccontextmanager

# Windows: Playwright launches Node as a subprocess, which requires the Proactor
# event loop. Force it before uvicorn creates the loop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.api import knowledge as knowledge_api
from app.api import screen as screen_api
from app.api import sessions as sessions_api
from app.api import tasks as tasks_api
from app.api import ws as ws_api
from app.api.deps import require_token
from app.browser.session_broker import broker
from app.config import settings
from app.storage import db


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    yield
    await broker.shutdown()


app = FastAPI(title="UAT Agents", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000", "http://127.0.0.1:3000",
        "http://localhost:3001", "http://127.0.0.1:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sessions_api.router)
app.include_router(tasks_api.router)
app.include_router(ws_api.router)
app.include_router(screen_api.router)
app.include_router(knowledge_api.router)


# Evidence (screenshots, reports) can contain everything visible in the target
# app, so it is served through a guarded route (token + traversal check), not
# an open static mount.
@app.get("/evidence/{path:path}", dependencies=[Depends(require_token)])
async def evidence_file(path: str) -> FileResponse:
    root = settings.evidence_dir.resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(target)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "model": settings.openai_model}
