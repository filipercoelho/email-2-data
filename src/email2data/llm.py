"""Shared LLM call plumbing — provider dispatch + retry-on-empty, used by every LLM stage.

ONE place for the Gemini (Vertex) and Anthropic call patterns, so a retry fix or a new provider is a
one-file change (was duplicated across classifier/specdraft/replydraft). Callers pass a system prompt
and a user message, plus EITHER structured-output contracts (``schema`` for Gemini controlled
generation, ``tool`` for the Anthropic forced tool) → returns the parsed **dict**, OR ``text=True`` →
returns the **str**. Retry-on-empty covers the transient 200-with-empty-text the SDK retry misses.
"""

from __future__ import annotations

import json
from typing import Any, Optional


class LLMError(Exception):
    pass


def _attempts(cfg: dict[str, Any]) -> int:
    return max(1, int(cfg.get("max_retries", 5)))


def call(client: Any, cfg: dict[str, Any], system: str, user: str, *,
         schema: Optional[dict] = None, tool: Optional[dict] = None,
         text: bool = False, temperature: float = 0.0) -> Any:
    """Run one LLM call with retries. Returns a dict (structured output) or a str (``text=True``)."""
    provider = cfg.get("provider", "anthropic")
    if provider == "vertex_gemini":
        return _gemini(client, cfg, system, user, schema, text, temperature)
    return _anthropic(client, cfg, system, user, tool, text, temperature)


def _gemini(client, cfg, system, user, schema, text, temperature) -> Any:
    from google.genai import types

    last = None
    for _ in range(_attempts(cfg)):  # retry-on-empty: a 200 with empty text is transient
        try:
            kw: dict[str, Any] = dict(
                system_instruction=system, temperature=temperature,
                max_output_tokens=int(cfg.get("max_tokens", 1024)),
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            )
            if not text:
                kw.update(response_mime_type="application/json", response_schema=schema)
            resp = client.models.generate_content(
                model=cfg["model"], contents=user, config=types.GenerateContentConfig(**kw))
            if resp.text:
                return resp.text.strip() if text else json.loads(resp.text)
            last = "empty text"
        except Exception as exc:  # noqa: BLE001 — retry transient, surface after attempts
            last = f"{type(exc).__name__}: {exc}"
    raise LLMError(f"gemini failed after retries ({last})")


def _anthropic(client, cfg, system, user, tool, text, temperature) -> Any:
    last = None
    for _ in range(_attempts(cfg)):
        try:
            kw: dict[str, Any] = dict(
                model=cfg["model"], max_tokens=int(cfg.get("max_tokens", 1024)), temperature=temperature,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
            )
            if not text:
                kw.update(tools=[tool], tool_choice={"type": "tool", "name": tool["name"]})
            resp = client.messages.create(**kw)
            blocks = getattr(resp, "content", []) or []
            if text:
                parts = [b.text for b in blocks if getattr(b, "type", None) == "text"]
                if parts:
                    return "\n".join(parts).strip()
                last = "no text block"
            else:
                for b in blocks:
                    if getattr(b, "type", None) == "tool_use":
                        return dict(b.input)
                last = "no tool_use block"
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
    raise LLMError(f"anthropic failed after retries ({last})")
