import asyncio
import sys
from contextlib import asynccontextmanager

# Windows: Playwright launches Node as a subprocess, which requires the Proactor
# event loop. Force it before uvicorn creates the loop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import sessions as sessions_api
from app.api import tasks as tasks_api
from app.api import ws as ws_api
from app.browser.session_broker import broker
from app.config import settings
from app.storage import db


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    yield
    await broker.shutdown()


app = FastAPI(title="UAT Agents", version="0.1.0", lifespan=lifespan)

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

# Serve generated evidence (screenshots, reports) statically so the UI can preview them.
app.mount("/evidence", StaticFiles(directory=str(settings.evidence_dir)), name="evidence")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "model": settings.openai_model}
