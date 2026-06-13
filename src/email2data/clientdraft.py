"""Deterministic client-email composer for the Projetos page.

Assembles the **email para o cliente** from the clarifying questions the user ticked and an
editable skeleton in ``config/client_email_template.md``. Pure and deterministic — no LLM,
nothing invented: the questions come from the Gate-1 gap analysis (``jobspec.askables``) and
this module only splices them into the template. A human reviews/edits and sends; the system
never sends.

(A later, optional "melhorar tom" button could wrap :func:`build_draft`'s output through the
Gemini reply playbook for tone — by design that polish layer sits *on top* of this deterministic
draft, it does not replace it. Not built yet.)
"""

from __future__ import annotations

from pathlib import Path

PLACEHOLDER = "{perguntas}"

# Fallback when the config file is missing/empty/malformed — keeps the composer working out of
# the box and mirrors the historical hard-coded JS template it replaces.
DEFAULT_TEMPLATE = (
    "Bom dia,\n\n"
    "Para conseguirmos avançar com o orçamento, precisávamos de confirmar:\n\n"
    f"{PLACEHOLDER}\n\n"
    "Obrigado."
)


def load_template(path: str | Path) -> str:
    """Read the body skeleton from a markdown file: everything after the first ``---`` fence
    line (the text above it is an editor note). Falls back to :data:`DEFAULT_TEMPLATE` if the
    file is missing, unreadable, or has lost the ``{perguntas}`` token (a guard so a botched
    edit degrades gracefully instead of shipping a token-less email)."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return DEFAULT_TEMPLATE
    _note, sep, body = raw.partition("\n---\n")
    tmpl = (body if sep else raw).strip()
    return tmpl if PLACEHOLDER in tmpl else DEFAULT_TEMPLATE


def build_draft(questions: list[str], template: str | None = None) -> str:
    """Render the email body: the ``questions`` as a 1-based numbered list spliced into the
    template at :data:`PLACEHOLDER`. Order is the caller's (the endpoint sorts by registry
    order so the list matches the on-screen checklist). With no questions the list collapses to
    empty — the caller decides whether to offer the email at all."""
    tmpl = template or DEFAULT_TEMPLATE
    numbered = "\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1))
    return tmpl.replace(PLACEHOLDER, numbered)
