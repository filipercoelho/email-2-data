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


def _known(fields: dict[str, Any]) -> list[tuple[str, str]]:
    return [(k, f["value"]) for k, f in (fields or {}).items() if f.get("value") and k not in _HIDE]


def build_reply_message(spec: dict[str, Any], readiness: dict[str, Any]) -> str:
    # Per-piece facts are grouped per line item so the model can acknowledge each distinct piece;
    # job-level facts (deadline, delivery, budget…) are listed once.
    items = spec.get("items") or [spec.get("fields", {})]  # tolerate the legacy flat shape
    item_lines = []
    for n, it in enumerate(items, 1):
        facts = _known(it)
        if facts:
            joined = "; ".join(f"{k}: {v}" for k, v in facts)
            item_lines.append(f"  - item {n}: {joined}")
    job = _known(spec.get("job_fields", {}))
    job_lines = [f"  - {k}: {v}" for k, v in job]
    known_block = "\n".join(item_lines + job_lines) or "  (nada confirmado ainda)"
    q_lines = "\n".join(f"  - {q}" for q in readiness.get("questions", [])) or "  (nenhuma)"
    n_items = len(item_lines)
    return (
        f"Assunto original: {spec.get('subject', '')}\n"
        f"Anexo recebido: {'sim' if spec.get('has_attachment') else 'não'}\n"
        f"Nº de peças/artigos distintos no pedido: {n_items or 1}\n"
        f"O que percebemos (factos confirmados — NÃO inventar outros):\n{known_block}\n"
        f"Detalhes em falta (perguntar APENAS estes):\n{q_lines}\n"
    )


def draft_reply(spec: dict[str, Any], readiness: dict[str, Any], playbook: str,
                client: Any, settings: dict[str, Any]) -> str:
    """Draft a clarifying reply for one job email. Returns the email text (a DRAFT — never sent)."""
    msg = build_reply_message(spec, readiness)
    return llm.call(client, settings["llm"], playbook, msg, text=True, temperature=0.3)


def draft_reply_stream(spec: dict[str, Any], readiness: dict[str, Any], playbook: str,
                       client: Any, settings: dict[str, Any]):
    """Same draft as :func:`draft_reply`, but yields text chunks as the model produces them."""
    msg = build_reply_message(spec, readiness)
    yield from llm.call_stream(client, settings["llm"], playbook, msg, temperature=0.3)
