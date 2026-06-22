"""LLM project inference + field extraction for captures (Increment 2 — ADR-019 §4-5, ADR-001).

Invoked ONLY when the deterministic resolver (capture_resolve) is ambiguous — compute ∝ uncertainty.
Both functions ALWAYS just SUGGEST: every result is validated by a human before it can touch a project
field (R9 / never auto-apply), which is the structural mitigation for the highest-stakes path (an
extracted value can feed the estimable gate). Each call degrades on any LLM failure to an empty result
so the capture always survives. Outputs are defensively coerced — never trust the model (mirrors
``classifier._coerce``): unknown field keys dropped, values trimmed, confidences clamped to [0, 1], and
inferred project ids checked against the real active set (a hallucinated id is discarded).
"""

from __future__ import annotations

from typing import Any, Optional

from . import jobspec as _js, llm
from .schema import (CAPTURE_FIELD_KEYS, CAPTURE_FIELDS_TOOL, GEMINI_CAPTURE_FIELDS_SCHEMA,
                     GEMINI_INFER_SCHEMA, INFER_TOOL)

# The brief's resolution branches: High pre-selects, Partial shows a couple, Low → manual.
HIGH_CONFIDENCE = 0.75

_EXTRACT_SYSTEM = (
    "És um assistente de uma oficina de fabrico (Lindo Serviço: corte laser, CNC, gravação, "
    "sinalética). Lê esta nota curta / transcrição de voz de um colaborador e extrai SÓ os valores "
    "dos campos da ficha de trabalho que estão EXPLICITAMENTE indicados. Devolve null para tudo o que "
    "não for dito — NÃO inventes nem infiras (cada valor é confirmado por uma pessoa e pode afetar o "
    "orçamento). Indica a tua confiança 0-1 na extração.")
_INFER_SYSTEM = (
    "És um assistente de uma oficina de fabrico. Dada uma captura curta e a lista de projetos ATIVOS, "
    "indica a que projeto pertence, com uma confiança 0-1 por candidato (mais provável primeiro), ou "
    "none_match=true se nenhum servir. Usa SÓ project_id da lista — nunca inventes um id.")

_JOB = set(_js.JOB_KEYS)
_ITEM = set(_js.ITEM_KEYS)


def field_address(key: str) -> Optional[str]:
    """The project field ADDRESS an extracted capture field maps to: a job-scope key → the base key
    (``deadline``); an item-scope key → ``<key>#0`` (a capture states a discrete fact, so item 0 is the
    validation target). ``None`` for any key NOT in ``CAPTURE_FIELD_KEYS`` (e.g. the internal
    ``process``, or junk) — it is never written."""
    if key not in CAPTURE_FIELD_KEYS:
        return None
    if key in _JOB:
        return key
    if key in _ITEM:
        return f"{key}#0"
    return None


def _clean(v: Any) -> Optional[str]:
    v = v.strip() if isinstance(v, str) else None
    return v or None


def _clamp01(x: Any) -> float:
    try:
        return max(0.0, min(1.0, float(x)))
    except (TypeError, ValueError):
        return 0.0


def _coerce_fields(raw: dict[str, Any]) -> dict[str, str]:
    """Defensive clamp (never trust the model): keep ONLY known capture field keys with a non-empty
    string value, mapped to their project field address. Anything else — unknown keys, nulls, blanks,
    a stray ``confidence`` — is dropped."""
    out: dict[str, str] = {}
    for k in CAPTURE_FIELD_KEYS:
        v = _clean(raw.get(k))
        addr = field_address(k)
        if v and addr:
            out[addr] = v
    return out


def extract_fields(text: str, client: Any, cfg: dict[str, Any]) -> dict[str, Any]:
    """Extract the job-spec field VALUES explicitly stated in a capture's text/transcript.

    Returns ``{"fields": {project_field_addr: value}, "confidence": 0-1}``. The fields are coerced (only
    known keys, trimmed, addressed); they are STORED for the user to validate one-by-one — never applied
    here. Degrades to ``{"fields": {}, "confidence": 0.0}`` on no text / no client / any ``LLMError``."""
    if not (text or "").strip() or client is None:
        return {"fields": {}, "confidence": 0.0}
    try:
        raw = llm.call(client, cfg, _EXTRACT_SYSTEM, text,
                       schema=GEMINI_CAPTURE_FIELDS_SCHEMA, tool=CAPTURE_FIELDS_TOOL)
    except llm.LLMError:
        return {"fields": {}, "confidence": 0.0}
    raw = raw if isinstance(raw, dict) else {}
    fields = _coerce_fields(raw)
    return {"fields": fields, "confidence": _clamp01(raw.get("confidence")) if fields else 0.0}


def infer_project(text: str, active_projects: list[dict[str, Any]], client: Any,
                  cfg: dict[str, Any]) -> dict[str, Any]:
    """Rank which ACTIVE project a capture is about (LLM). Returns
    ``{"candidates": [{"project_id", "confidence"}, …] (desc), "none_match": bool}``. Only ids that are
    actually in ``active_projects`` survive (a hallucinated id is dropped). Degrades to an empty,
    none_match result on no text / no client / no projects / any ``LLMError``."""
    if not (text or "").strip() or client is None or not active_projects:
        return {"candidates": [], "none_match": True}
    ids = {p["project_id"] for p in active_projects}
    listing = "\n".join(f"{p['project_id']}: {p.get('title', '')}" for p in active_projects)
    user = f"Projetos ativos:\n{listing}\n\nCaptura:\n{text}"
    try:
        raw = llm.call(client, cfg, _INFER_SYSTEM, user, schema=GEMINI_INFER_SCHEMA, tool=INFER_TOOL)
    except llm.LLMError:
        return {"candidates": [], "none_match": True}
    raw = raw if isinstance(raw, dict) else {}
    cands: list[dict[str, Any]] = []
    for c in (raw.get("candidates") or []):
        if not isinstance(c, dict):
            continue
        pid = str(c.get("project_id", "")).strip()
        if pid in ids:  # never trust a hallucinated id
            cands.append({"project_id": pid, "confidence": _clamp01(c.get("confidence"))})
    cands.sort(key=lambda c: c["confidence"], reverse=True)
    return {"candidates": cands, "none_match": bool(raw.get("none_match")) or not cands}


def best_inferred(infer_result: dict[str, Any]) -> Optional[str]:
    """The single High-confidence (≥ ``HIGH_CONFIDENCE``) project id, else ``None`` (Partial / Low →
    the human picks). Even a High match is only ever a pre-selection — the user still confirms (R9)."""
    cands = (infer_result or {}).get("candidates") or []
    if cands and cands[0]["confidence"] >= HIGH_CONFIDENCE:
        return cands[0]["project_id"]
    return None
