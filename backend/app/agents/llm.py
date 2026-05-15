"""LLM client wrapper around the OpenAI Chat Completions API.

Uses gpt-4o by default. All agents talk to the model through this thin layer so
that swapping providers later is a one-file change.
"""
from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

from app.config import settings


_client: AsyncOpenAI | None = None


def client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


async def chat_json(system: str, user: str, *, temperature: float = 0.1) -> dict[str, Any]:
    """Call the model with response_format=json_object and return the parsed dict."""
    resp = await client().chat.completions.create(
        model=settings.openai_model,
        temperature=temperature,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    raw = resp.choices[0].message.content or "{}"
    return json.loads(raw)


async def chat_text(system: str, user: str, *, temperature: float = 0.2) -> str:
    resp = await client().chat.completions.create(
        model=settings.openai_model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content or ""
