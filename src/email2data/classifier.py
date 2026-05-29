"""M1 — classify one EmailEnvelope into a TriageResult using Claude + the triage playbook."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import audit
from .config import claude_api_key, paths
from .identity import canonical_id  # noqa: F401  (kept for callers/tests)
from .schema import (
    EXTRACTOR_VERSION,
    GEMINI_TRIAGE_SCHEMA,
    IGNORABLE_TYPES,
    PRIORITIES,
    TRIAGE_TOOL,
    TYPES,
    Entities,
    TriageResult,
)


class ClassifyError(Exception):
    pass


def load_playbook(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def build_user_message(env: dict[str, Any]) -> str:
    """Compact, signal-dense rendering of the email for the model.

    The received date is included so the model can resolve relative PT dates ("até sexta").
    Attachment filenames/types are strong signal for job requests, so they are listed.
    """
    atts = env.get("attachments") or []
    att_lines = "\n".join(
        f"  - {a.get('filename') or '(sem nome)'} [{a.get('content_type')}]" for a in atts
    )
    return (
        f"Received: {env.get('date') or '(desconhecido)'}\n"
        f"From: {env.get('from', {}).get('name')} <{env.get('from', {}).get('email')}>\n"
        f"Subject: {env.get('subject', '')}\n"
        f"Attachments ({len(atts)}):\n{att_lines or '  (nenhum)'}\n"
        f"---\n"
        f"{env.get('body_text', '')}"
    )


def _tool_input(response: Any) -> dict[str, Any]:
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", "") == TRIAGE_TOOL["name"]:
            return dict(block.input)
    raise ClassifyError("model did not return a record_triage tool call")


def _coerce(raw: dict[str, Any], env: dict[str, Any], floor: float) -> TriageResult:
    ent = raw.get("entities") or {}
    type_ = raw.get("type") if raw.get("type") in TYPES else "OTHER"
    priority = raw.get("priority") if raw.get("priority") in PRIORITIES else "NEEDS_REVIEW"
    urgency = max(0, min(100, int(raw.get("urgency", 0))))
    confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.0))))
    reason = str(raw.get("reason", "")).strip()

    # --- Anti-IGNORE backstop, enforced in code, not just the prompt (red-team S6) ---
    # 1. Coherence: only PUBLICITY/OTHER may carry IGNORE. A client/supplier marked IGNORE is a bug
    #    in the verdict, regardless of how confident the model is.
    # 2. Confidence floor: even a coherent IGNORE needs high confidence; otherwise route to review.
    if priority == "IGNORE":
        if type_ not in IGNORABLE_TYPES:
            priority = "NEEDS_REVIEW"
            reason = f"[guardrail: IGNORE on {type_} downgraded] {reason}"
        elif confidence < floor:
            priority = "NEEDS_REVIEW"
            reason = f"[guardrail: low-confidence IGNORE ({confidence:.2f}<{floor}) downgraded] {reason}"

    return TriageResult(
        message_id=env["message_id"],
        type=type_,
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
        ),
        extractor_version=EXTRACTOR_VERSION,
        subject=env.get("subject", ""),
        from_addr=env.get("from", {}).get("email", ""),
    )


def _call_anthropic(env: dict[str, Any], playbook: str, client: Any, cfg: dict[str, Any]) -> dict[str, Any]:
    response = client.messages.create(
        model=cfg["model"],
        max_tokens=int(cfg.get("max_tokens", 1024)),
        temperature=0,
        # The playbook is identical on every call -> cache it (prompt caching) to cut cost/latency.
        system=[{"type": "text", "text": playbook, "cache_control": {"type": "ephemeral"}}],
        tools=[TRIAGE_TOOL],
        tool_choice={"type": "tool", "name": TRIAGE_TOOL["name"]},
        messages=[{"role": "user", "content": build_user_message(env)}],
    )
    return _tool_input(response)


def _call_gemini(env: dict[str, Any], playbook: str, client: Any, cfg: dict[str, Any]) -> dict[str, Any]:
    from google.genai import types

    resp = client.models.generate_content(
        model=cfg["model"],
        contents=build_user_message(env),
        config=types.GenerateContentConfig(
            system_instruction=playbook,
            temperature=0,
            max_output_tokens=int(cfg.get("max_tokens", 1024)),
            response_mime_type="application/json",
            response_schema=GEMINI_TRIAGE_SCHEMA,  # controlled generation -> valid enums/shape
            thinking_config=types.ThinkingConfig(thinking_budget=0),  # don't starve JSON output
        ),
    )
    text = resp.text
    if not text:
        raise ClassifyError("gemini returned empty text (blocked or token-starved)")
    return json.loads(text)


def classify(env: dict[str, Any], playbook: str, client: Any, settings: dict[str, Any]) -> TriageResult:
    """Classify one envelope. ``client`` is injected (real SDK client or a fake) for testability.

    Provider-agnostic: ``llm.provider`` selects the backend. Both paths return the same dict shape,
    so the guardrail/coercion in ``_coerce`` is shared.
    """
    cfg = settings["llm"]
    floor = float(cfg.get("ignore_confidence_floor", 0.85))
    provider = cfg.get("provider", "anthropic")
    raw = _call_gemini(env, playbook, client, cfg) if provider == "vertex_gemini" else _call_anthropic(env, playbook, client, cfg)
    return _coerce(raw, env, floor)


def _make_client(settings: dict[str, Any]) -> Any:
    cfg = settings["llm"]
    provider = cfg.get("provider", "anthropic")
    if provider == "vertex_gemini":
        from google import genai  # lazy import so the anthropic path / tests don't need it

        return genai.Client(
            vertexai=True,
            project=cfg["vertex_project"],
            location=cfg.get("vertex_location", "global"),
        )
    from anthropic import Anthropic  # imported lazily so tests/fetch don't need the SDK

    return Anthropic(
        api_key=claude_api_key(settings),
        max_retries=int(cfg.get("max_retries", 5)),  # 429/5xx backoff (red-team S2)
    )


def classify_corpus(settings: dict[str, Any], client: Any | None = None) -> dict[str, int]:
    """Classify every .eml in the corpus -> out/results.jsonl.

    Returns counts {corpus, classified, failed} so a partial run can't masquerade as a clean one
    (red-team S2). A single email failing is logged and skipped, never aborts the batch.
    """
    from .envelope import parse_eml

    p = paths(settings, settings["__settings_path__"])
    corpus_dir, out_dir, audit_log = p["corpus_dir"], p["out_dir"], p["audit_log"]
    playbook = load_playbook(p["playbook"])
    client = client or _make_client(settings)

    eml_files = sorted(corpus_dir.glob("*.eml"))
    results_path = out_dir / "results.jsonl"
    classified = failed = 0
    audit.log(audit_log, "classify_started", "corpus", {"corpus": len(eml_files)})

    with results_path.open("w", encoding="utf-8") as out:
        for eml in eml_files:
            try:
                env = parse_eml(eml.read_bytes())
                result = classify(env, playbook, client, settings)
                out.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")
                classified += 1
            except Exception as exc:  # noqa: BLE001 — isolate per-email failures
                failed += 1
                audit.log(audit_log, "classify_failed", eml.name, {"error": type(exc).__name__})

    counts = {"corpus": len(eml_files), "classified": classified, "failed": failed}
    audit.log(audit_log, "classify_done", "corpus", counts)
    return counts
