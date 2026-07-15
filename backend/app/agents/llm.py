"""LLM client wrapper around the OpenAI Chat Completions API.

Uses gpt-4o by default. All agents talk to the model through this thin layer so
that swapping providers later is a one-file change. Every call is bounded by a
hard timeout and retried with backoff — a single slow/failed API call must not
hang the pilot node indefinitely (that looked like a frozen task).

Two capabilities the Pilot depends on:
- Vision: an optional base64 screenshot rides along with the text context, so
  the model sees the page the way a person does (icons, disabled buttons,
  spinners, layout) instead of guessing from flattened text.
- Structured Outputs: when a JSON Schema is provided, the response is
  constrained server-side (strict mode), which eliminates the whole class of
  "Pilot returned an invalid/malformed action" wasted steps.
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


def _user_content(user: str, image_b64: str | None) -> Any:
    if not image_b64:
        return user
    return [
        {"type": "text", "text": user},
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{image_b64}", "detail": "auto"},
        },
    ]


async def _create(
    messages: list[dict],
    *,
    temperature: float,
    response_format: dict[str, Any] | None,
) -> str:
    kwargs: dict[str, Any] = {
        "model": settings.openai_model,
        "temperature": temperature,
        "messages": messages,
    }
    if response_format is not None:
        kwargs["response_format"] = response_format

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


async def chat_json(
    system: str,
    user: str,
    *,
    temperature: float = 0.1,
    image_b64: str | None = None,
    schema: dict[str, Any] | None = None,
    schema_name: str = "response",
) -> dict[str, Any]:
    """Call the model and return the parsed JSON dict.

    With `schema`, uses Structured Outputs (strict json_schema) so the shape is
    guaranteed. Without it, falls back to json_object mode. A malformed body
    yields an empty dict rather than crashing the pilot node."""
    if schema is not None:
        response_format: dict[str, Any] = {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": schema},
        }
    else:
        response_format = {"type": "json_object"}
    raw = await _create(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": _user_content(user, image_b64)},
        ],
        temperature=temperature,
        response_format=response_format,
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
        response_format=None,
    )
