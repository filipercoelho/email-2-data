"""On-demand translation of a received email body to English — a reading aid (ADR-032).

A cockpit reader clicks "traduzir (EN)" on a message and this turns its body into English through the
shared LLM layer (:mod:`llm`, ADR-012). It is a DISPLAY aid only: it never writes to any store, never
changes a triage/spec verdict, and runs on an explicit click (never on page load). Egress is the same
posture the triage and spec passes already use — the raw body already goes to Vertex there — and the
same rule applies: never *log* the body or the translation (ids/counts only).

Deterministic where it can be: the system prompt is an editable playbook and the call is temperature 0
for a faithful translation; names, numbers, prices, dates, URLs and emails are to be preserved exactly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import llm

# Fallback when config/translation_playbook.md is missing/empty. Carries the same hard rules as the
# file so a missing config never becomes a permissive, paraphrasing prompt.
DEFAULT_TRANSLATION_PLAYBOOK = (
    "You are a professional translator for Lindo Serviço (a Portuguese laser-cutting, CNC, engraving "
    "and signage workshop). Translate the user's message into clear, natural English. RULES: translate "
    "faithfully — do not summarise, add, or omit anything; keep every name, number, price, currency "
    "symbol, date, measurement, URL and email address EXACTLY as written (do not localise or reformat "
    "them); preserve the line/paragraph structure; if a passage is already in English, leave it as is. "
    "Output ONLY the translation — no preamble, no notes, no quotes."
)


def load_playbook(path: str | Path) -> str:
    """Read the translation system prompt (the whole file, like the other playbooks). Falls back to
    :data:`DEFAULT_TRANSLATION_PLAYBOOK` if the file is missing, unreadable, or empty."""
    try:
        raw = Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return DEFAULT_TRANSLATION_PLAYBOOK
    return raw or DEFAULT_TRANSLATION_PLAYBOOK


def translate_to_english(text: str, playbook: str, client: Any, cfg: dict[str, Any]) -> str:
    """Translate ``text`` to English through the LLM. Returns the translation (a display aid — never
    sent, never stored). Raises ``llm.LLMError`` on empty output: the user asked for this and paid for
    the call, so a failure is reported, never degraded into echoing the untranslated text as a success.
    """
    out = llm.call(client, cfg, playbook, text, text=True, temperature=0.0)
    result = (out or "").strip()
    if not result:
        raise llm.LLMError("translation returned empty text")
    return result
