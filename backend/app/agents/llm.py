"""LLM client wrapper around the OpenAI Chat Completions API.

Uses gpt-4o by default. All agents talk to the model through this thin layer so
that swapping providers later is a one-file change. Every call is bounded by a
hard timeout and retried with backoff — a single slow/failed API call must not
hang the pilot node indefinitely (that looked like a frozen task).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from openai import AsyncOpenAI

from app.config import settings


logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_S = 60.0
MAX_RETRIES = 2  # in addition to the initial attempt

_client: AsyncOpenAI | None = None


def client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            timeout=REQUEST_TIMEOUT_S,
            max_retries=0,  # we do our own bounded retry/backoff below
        )
    return _client


async def _create(messages: list[dict], *, temperature: float, json_mode: bool) -> str:
    kwargs: dict[str, Any] = {
        "model": settings.openai_model,
        "temperature": temperature,
        "messages": messages,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = await asyncio.wait_for(
                client().chat.completions.create(**kwargs),
                timeout=REQUEST_TIMEOUT_S + 5,
            )
            return resp.choices[0].message.content or ""
        except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
            last_err = e
            if attempt < MAX_RETRIES:
                wait = 1.5 * (2 ** attempt)
                logger.warning(
                    "LLM call failed (attempt %d/%d): %s — retrying in %.1fs",
                    attempt + 1, MAX_RETRIES + 1, e, wait,
                )
                await asyncio.sleep(wait)
            else:
                logger.error("LLM call failed after %d attempts: %s", MAX_RETRIES + 1, e)
    raise RuntimeError(f"LLM request failed after {MAX_RETRIES + 1} attempts: {last_err}")


async def chat_json(system: str, user: str, *, temperature: float = 0.1) -> dict[str, Any]:
    """Call the model with response_format=json_object and return the parsed dict.
    A malformed body yields an empty dict rather than crashing the pilot node."""
    raw = await _create(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=temperature,
        json_mode=True,
    )
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        logger.error("LLM returned non-JSON content: %.200s", raw)
        return {}


async def chat_text(system: str, user: str, *, temperature: float = 0.2) -> str:
    return await _create(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=temperature,
        json_mode=False,
    )
