"""Phase A — the JobSpec backbone (deterministic, no LLM).

Turns a triage verdict + envelope into a structured **job spec** and scores **estimation readiness
(Gate 1)**. Empirically (see design/estimation-roadmap), only ~12% of mail is job-relevant and the
spec is usually in an attachment we don't parse — so this layer does NOT try to extract the spec. It
(a) reshapes the signals we already have into the 14-field spec, (b) flags that an attachment likely
holds the rest, and (c) reports exactly which must-haves are missing/unconfirmed and the questions to
ask. The semantic fields are drafted by the tiered LLM pass (``specdraft.py``); the user confirms.

Design for maintainability: the 14 variables live in ONE registry (``FIELDS``) — tier and the PT
clarifying question are co-located, so adding/retiering a field is a one-line change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# (key, PT label, tier, PT clarifying question).  tier ∈ must | should | context
FIELDS: list[tuple[str, str, str, str]] = [
    ("item",                 "o que produzir",          "must",    "O que pretendem produzir?"),
    ("design_ready",         "ficheiro/desenho",        "must",    "Têm o ficheiro/desenho final? Em que formato?"),
    ("dimensions",           "dimensões",               "must",    "Quais as dimensões de cada peça?"),
    ("material",             "material",                "must",    "Em que material?"),
    ("thickness",            "espessura",               "must",    "Que espessura?"),
    ("material_supplied_by", "quem fornece o material", "must",    "Fornecem o material ou tratamos da compra?"),
    ("process",              "processo de fabrico",     "must",    "(interno) Definir o processo de fabrico."),
    ("quantity",             "quantidade",              "must",    "Que quantidade?"),
    ("deadline",             "prazo",                   "must",    "Para quando precisam?"),
    ("colour_finish",        "cor/acabamento",          "should",  "Que cor ou acabamento?"),
    ("quality_acceptance",   "critério de aceitação",   "should",  "Precisam de amostra/prova antes da produção?"),
    ("delivery",             "entrega/instalação",      "should",  "Entrega, morada e instalação?"),
    ("budget",               "orçamento/budget",        "should",  "Têm um budget de referência?"),
    ("client_identity",      "cliente",                 "context", ""),
]
_TIER = {k: t for k, _, t, _ in FIELDS}
_QUESTION = {k: q for k, _, _, q in FIELDS}
MUST = [k for k, _, t, _ in FIELDS if t == "must"]
SHOULD = [k for k, _, t, _ in FIELDS if t == "should"]


@dataclass
class SpecField:
    value: Optional[str] = None
    source: str = ""        # "offline" | "llm" | "user"
    confirmed: bool = False

    def to_dict(self) -> dict:
        return {"value": self.value, "source": self.source, "confirmed": self.confirmed}


@dataclass
class JobSpec:
    message_id: str
    subject: str = ""
    counterparty: str = ""
    purpose: str = ""
    has_attachment: bool = False
    attachment_names: list[str] = field(default_factory=list)
    fields: dict[str, SpecField] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "message_id": self.message_id, "subject": self.subject,
            "counterparty": self.counterparty, "purpose": self.purpose,
            "has_attachment": self.has_attachment, "attachment_names": self.attachment_names,
            "fields": {k: f.to_dict() for k, f in self.fields.items()},
        }


def build_jobspec(result: dict[str, Any], env: dict[str, Any],
                  draft: Optional[dict[str, Any]] = None) -> JobSpec:
    """Deterministically assemble a JobSpec from a triage verdict + envelope (+ optional LLM draft).

    Reuses signals we already have — no new extraction:
      * attachments  -> ``design_ready`` (offline)            * counterparty -> ``client_identity`` (offline)
      * entities.product_or_service -> ``item`` (llm)         * entities.deadline -> ``deadline`` (llm)
      * entities.money -> ``budget`` (llm)
    ``draft`` (from specdraft.py) fills the semantic fields with source ``llm``.
    """
    fields = {k: SpecField() for k, _, _, _ in FIELDS}
    ent = result.get("entities") or {}
    atts = env.get("attachments") or []
    names = [a.get("filename") for a in atts if a.get("filename")]

    if atts:
        fields["design_ready"] = SpecField("ficheiro em anexo (rever usabilidade)", "offline", False)
    if result.get("counterparty"):
        fields["client_identity"] = SpecField(result["counterparty"], "offline", False)
    if ent.get("product_or_service"):
        fields["item"] = SpecField(ent["product_or_service"], "llm", False)
    if ent.get("deadline"):
        fields["deadline"] = SpecField(ent["deadline"], "llm", False)
    if ent.get("money"):
        fields["budget"] = SpecField(ent["money"], "llm", False)

    for k in (draft or {}):
        v = draft.get(k)
        if v and k in fields and not fields[k].confirmed:
            fields[k] = SpecField(str(v), "llm", False)

    return JobSpec(
        message_id=result.get("message_id", ""), subject=result.get("subject", ""),
        counterparty=result.get("counterparty", ""), purpose=result.get("purpose", ""),
        has_attachment=bool(atts), attachment_names=names, fields=fields,
    )


def confirm(spec: JobSpec, key: str, value: str) -> JobSpec:
    """Apply a user confirmation/override (the authoritative source). Returns the spec for chaining."""
    if key in spec.fields:
        spec.fields[key] = SpecField(value, "user", True)
    return spec


def readiness(spec: JobSpec) -> dict[str, Any]:
    """Gate-1 scorer: which must-haves are present/confirmed/missing, is it estimable, and what to ask."""
    present = [k for k in MUST if spec.fields[k].value]
    confirmed = [k for k in MUST if spec.fields[k].value and spec.fields[k].confirmed]
    missing = [k for k in MUST if not spec.fields[k].value]
    unconfirmed = [k for k in MUST if spec.fields[k].value and not spec.fields[k].confirmed]
    estimable = len(confirmed) == len(MUST)
    return {
        "estimable": estimable,
        "coverage": round(len(present) / len(MUST), 2),
        "present": present, "confirmed": confirmed,
        "missing": missing, "unconfirmed": unconfirmed,
        # if the must-haves aren't all nailed and there's a file, it probably holds the rest
        "attachment_to_review": spec.has_attachment and (bool(missing) or bool(unconfirmed)),
        "questions": [_QUESTION[k] for k in missing if _QUESTION[k]],
    }


def score_drafts(specs: list[dict], labels: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Functional eval (Phase B): per-field draft-vs-label presence agreement over the labeled set.

    ``labels`` maps message_id -> {field: true_value}. We score *presence agreement* (did the draft
    fill a field the human also filled, and leave blank what the human left blank) — exact-string
    accuracy is meaningless on free text at n≈32. Returns per-field and overall agreement.
    """
    by_field: dict[str, list[int]] = {}
    for s in specs:
        lab = labels.get(s["message_id"])
        if lab is None:
            continue
        for k in MUST + SHOULD:
            drafted = bool(s["fields"].get(k, {}).get("value"))
            labeled = bool((lab.get(k) or "").strip())
            by_field.setdefault(k, []).append(int(drafted == labeled))
    per = {k: round(sum(v) / len(v), 2) for k, v in by_field.items() if v}
    flat = [x for v in by_field.values() for x in v]
    return {"n": len(set(labels) & {s["message_id"] for s in specs}),
            "per_field_agreement": per,
            "overall_agreement": round(sum(flat) / len(flat), 2) if flat else None}
