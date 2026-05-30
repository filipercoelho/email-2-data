"""Tier-1 LLM classification — counterparty/purpose/direction model (Phase 1 migration).

The model emits counterparty/purpose/urgency/confidence/reason/entities; this module sets direction
from the Tier-0 signals, derives priority deterministically, and enforces the anti-IGNORE guardrail.
Includes retry-on-empty (the transient empty-response failure the SDK's max_retries doesn't cover).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import extract, llm
from .config import claude_api_key
from .schema import (
    EXTRACTOR_VERSION,
    GEMINI_TRIAGE_SCHEMA,
    IGNORABLE_COUNTERPARTIES,
    COUNTERPARTY,
    PURPOSE,
    TRIAGE_TOOL,
    Entities,
    TriageResult,
    derive_priority,
)
from .signals import Signals, facts_block


def load_playbook(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def build_user_message(env: dict[str, Any], signals: Signals, gazetteer_hint: str | None) -> str:
    """Signal-dense rendering: deterministic header FACTS, then deterministically extracted values
    (Idea 2), then the email. The extracted values are priors/candidates only — the body is the
    final authority (see the playbook)."""
    atts = env.get("attachments") or []
    att_lines = "\n".join(
        f"  - {a.get('filename') or '(sem nome)'} [{a.get('content_type')}]" for a in atts
    )
    subject, body = env.get("subject", ""), env.get("body_text", "")
    offline = extract.render_candidates(extract.extract_values(subject, body))
    facts = f"[FACTS] {facts_block(signals, gazetteer_hint)}"
    if offline:
        facts += f"\n[OFFLINE SIGNALS — priors only, the body decides] {offline}"
    return (
        f"{facts}\n"
        f"Received: {env.get('date') or '(desconhecido)'}\n"
        f"From: {env.get('from', {}).get('name')} <{env.get('from', {}).get('email')}>\n"
        f"Subject: {subject}\n"
        f"Attachments ({len(atts)}):\n{att_lines or '  (nenhum)'}\n"
        f"---\n{body}"
    )


def _coerce(raw: dict[str, Any], env: dict[str, Any], signals: Signals, floor: float) -> TriageResult:
    ent = raw.get("entities") or {}
    counterparty = raw.get("counterparty") if raw.get("counterparty") in COUNTERPARTY else "OTHER"
    purpose = raw.get("purpose") if raw.get("purpose") in PURPOSE else "OTHER"
    urgency = max(0, min(100, int(raw.get("urgency", 0))))
    confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.0))))
    reason = str(raw.get("reason", "")).strip()

    priority = derive_priority(counterparty, purpose, urgency, signals.is_bulk)
    # Anti-IGNORE guardrail: never bin on a low-confidence "it's just bulk" call.
    if priority == "IGNORE" and (counterparty not in IGNORABLE_COUNTERPARTIES or confidence < floor):
        priority = "NEEDS_REVIEW"
        reason = f"[guardrail: uncertain IGNORE -> review] {reason}"

    # Deterministic, format-locked values override the model for NIF/IBAN (the model still owns
    # money/deadline, where relevance — not format — is the hard part).
    vals = extract.extract_values(env.get("subject", ""), env.get("body_text", ""))

    return TriageResult(
        message_id=env["message_id"],
        counterparty=counterparty,
        purpose=purpose,
        direction=signals.direction,
        priority=priority,
        urgency=urgency,
        confidence=confidence,
        reason=reason,
        entities=Entities(
            client_name=ent.get("client_name"),
            client_email=ent.get("client_email"),
            deadline=ent.get("deadline"),
            money=ent.get("money"),
            product_or_service=ent.get("product_or_service"),
            action_requested=ent.get("action_requested"),
            nif=vals.get("nif"),
            iban=vals.get("iban"),
        ),
        extractor_version=EXTRACTOR_VERSION,
        subject=env.get("subject", ""),
        from_addr=env.get("from", {}).get("email", ""),
    )


def classify(env, signals, gazetteer_hint, playbook, client, settings) -> TriageResult:
    """Tier-1 LLM classification of one envelope, given Tier-0 signals + a gazetteer hint."""
    cfg = settings["llm"]
    floor = float(cfg.get("ignore_confidence_floor", 0.85))
    user = build_user_message(env, signals, gazetteer_hint)
    raw = llm.call(client, cfg, playbook, user, schema=GEMINI_TRIAGE_SCHEMA, tool=TRIAGE_TOOL)
    return _coerce(raw, env, signals, floor)


def make_client(settings: dict[str, Any]) -> Any:
    cfg = settings["llm"]
    if cfg.get("provider", "anthropic") == "vertex_gemini":
        from google import genai

        return genai.Client(vertexai=True, project=cfg["vertex_project"],
                            location=cfg.get("vertex_location", "global"))
    from anthropic import Anthropic

    return Anthropic(api_key=claude_api_key(settings), max_retries=int(cfg.get("max_retries", 5)))
