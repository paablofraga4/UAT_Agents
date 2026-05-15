"""Launcher that guarantees the Proactor event loop policy on Windows BEFORE
uvicorn (or anything else) creates an event loop. Use this instead of calling
`uvicorn` directly:

    python run.py          # production-like
    python run.py --reload # dev with auto-reload
"""
from __future__ import annotations

import asyncio
import os
import sys


if sys.platform == "win32":
    # Must be set before uvicorn, anyio, asyncio.run, etc. touch a loop.
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


def main() -> None:
    import uvicorn

    reload = "--reload" in sys.argv
    port = int(os.environ.get("BACKEND_PORT", "8765"))
    host = os.environ.get("BACKEND_HOST", "127.0.0.1")
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload,
        loop="asyncio",  # force stdlib asyncio so our policy applies
    )


if __name__ == "__main__":
    main()
