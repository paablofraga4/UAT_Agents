"""Shared-secret auth for the API.

Local-first by default: with API_TOKEN unset, everything is open (same laptop,
same user). The moment the backend is exposed beyond localhost, set API_TOKEN
and every REST call, WebSocket and evidence file request must present it:

- `Authorization: Bearer <token>` header, or
- `X-API-Key: <token>` header, or
- `?token=<token>` query parameter (WebSockets and <img> URLs can't set
  headers from the browser).
"""
from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, WebSocket

from app.config import settings


def _presented_token(headers, query_params) -> str:
    auth = headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return headers.get("x-api-key") or query_params.get("token") or ""


def _token_ok(presented: str) -> bool:
    if not settings.api_token:
        return True
    return bool(presented) and hmac.compare_digest(presented, settings.api_token)


async def require_token(request: Request) -> None:
    if not _token_ok(_presented_token(request.headers, request.query_params)):
        raise HTTPException(status_code=401, detail="Invalid or missing API token")


def ws_token_ok(ws: WebSocket) -> bool:
    """WebSocket variant — call before accept(); close with 1008 when False."""
    return _token_ok(_presented_token(ws.headers, ws.query_params))
