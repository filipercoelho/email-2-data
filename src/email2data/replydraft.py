"""Phase C (static slice) — draft a SHORT clarifying reply, grounded in the job spec.

Composes an acknowledge-and-ask email for a LEAD/PO: thanks the client, confirms ONLY the facts we
have, and asks ONLY the missing must-haves (from the readiness gate). It NEVER invents a price,
deadline, or spec — for unknowns it asks. **Human-in-the-loop: this is a DRAFT to copy/paste; the
system never sends.** Style lives in ``config/reply_playbook.md`` (per-account style is deferred to the
workspace). Tiered to job emails by the caller; provider plumbing lives in ``llm.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import llm

# Fields that are internal flags, not facts we'd confirm back to the client.
_HIDE = {"client_identity", "design_ready", "process"}


def load_playbook(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def build_reply_message(spec: dict[str, Any], readiness: dict[str, Any]) -> str:
    fields = spec.get("fields", {})
    known = [(k, f["value"]) for k, f in fields.items() if f.get("value") and k not in _HIDE]
    known_lines = "\n".join(f"  - {k}: {v}" for k, v in known) or "  (nada confirmado ainda)"
    q_lines = "\n".join(f"  - {q}" for q in readiness.get("questions", [])) or "  (nenhuma)"
    return (
        f"Assunto original: {spec.get('subject', '')}\n"
        f"Anexo recebido: {'sim' if spec.get('has_attachment') else 'não'}\n"
        f"O que percebemos (factos confirmados — NÃO inventar outros):\n{known_lines}\n"
        f"Detalhes em falta (perguntar APENAS estes):\n{q_lines}\n"
    )


def draft_reply(spec: dict[str, Any], readiness: dict[str, Any], playbook: str,
                client: Any, settings: dict[str, Any]) -> str:
    """Draft a clarifying reply for one job email. Returns the email text (a DRAFT — never sent)."""
    msg = build_reply_message(spec, readiness)
    return llm.call(client, settings["llm"], playbook, msg, text=True, temperature=0.3)
