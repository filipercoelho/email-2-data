"""Phase A — the JobSpec backbone (deterministic, no LLM).

Turns a triage verdict + envelope into a structured **job spec** and scores **estimation readiness
(Gate 1)**. Empirically (see design/estimation-roadmap), only ~12% of mail is job-relevant and the
spec is usually in an attachment we don't parse — so this layer does NOT try to extract the spec. It
(a) reshapes the signals we already have into the spec, (b) flags that an attachment likely holds the
rest, and (c) reports exactly which must-haves are missing/unconfirmed and the questions to ask. The
semantic fields are drafted by the tiered LLM pass (``specdraft.py``); the user confirms.

**Multi-item.** A single lead/PO routinely lists several distinct pieces ("20 placas acrílico +
5 expositores MDF + 100 stickers"), each with its OWN material/dimensions/thickness/quantity. So the
spec is NOT flat: per-piece fields live in ``items`` (a list, one dict per line item); the rest are
job-level and live in ``job_fields``. Which fields are per-item vs job-level is the ``scope`` column of
the ONE registry (``FIELDS``) — tier, the PT clarifying question, and scope are co-located, so
adding/retiering/rescoping a field is a one-line change.

Field **addresses** (the string keys used on the wire — workspace decisions, the confirm API, report
DOM ids) are ``"<key>"`` for a job-level field and ``"<key>#<i>"`` for the per-item field of item ``i``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# (key, PT label, tier, PT clarifying question, scope).  tier ∈ must | should | context.  scope ∈ item | job
FIELDS: list[tuple[str, str, str, str, str]] = [
    ("item",                 "o que produzir",          "must",    "O que pretendem produzir?",                       "item"),
    ("design_ready",         "ficheiro/desenho",        "must",    "Têm o ficheiro/desenho final? Em que formato?",   "job"),
    ("dimensions",           "dimensões",               "must",    "Quais as dimensões de cada peça?",                "item"),
    ("material",             "material",                "must",    "Em que material?",                                "item"),
    ("thickness",            "espessura",               "must",    "Que espessura?",                                  "item"),
    ("material_supplied_by", "quem fornece o material", "must",    "Fornecem o material ou tratamos da compra?",      "job"),
    ("process",              "processo de fabrico",     "must",    "(interno) Definir o processo de fabrico.",        "item"),
    ("quantity",             "quantidade",              "must",    "Que quantidade?",                                 "item"),
    ("deadline",             "prazo",                   "must",    "Para quando precisam?",                           "job"),
    ("colour_finish",        "cor/acabamento",          "should",  "Que cor ou acabamento?",                          "item"),
    ("quality_acceptance",   "critério de aceitação",   "should",  "Precisam de amostra/prova antes da produção?",    "job"),
    ("delivery",             "entrega/instalação",      "should",  "Entrega, morada e instalação?",                   "job"),
    ("budget",               "orçamento/budget",        "should",  "Têm um budget de referência?",                    "job"),
    ("client_identity",      "cliente",                 "context", "",                                                "job"),
]
_TIER = {k: t for k, _, t, _, _ in FIELDS}
_SCOPE = {k: s for k, _, _, _, s in FIELDS}
_QUESTION = {k: q for k, _, _, q, _ in FIELDS}
MUST = [k for k, _, t, _, _ in FIELDS if t == "must"]
SHOULD = [k for k, _, t, _, _ in FIELDS if t == "should"]
# Per-item vs job-level split (registry order).
ITEM_KEYS = [k for k, _, _, _, s in FIELDS if s == "item"]
JOB_KEYS = [k for k, _, _, _, s in FIELDS if s == "job"]
ITEM_MUST = [k for k in ITEM_KEYS if _TIER[k] == "must"]
JOB_MUST = [k for k in JOB_KEYS if _TIER[k] == "must"]


def address(key: str, item_index: Optional[int]) -> str:
    """Wire address for a field: ``"deadline"`` (job-level) or ``"material#0"`` (per item)."""
    return key if item_index is None else f"{key}#{item_index}"


def parse_address(addr: str) -> tuple[str, Optional[int]]:
    """Inverse of :func:`address`. Returns (base_key, item_index|None). Tolerates a bad index -> None."""
    if "#" in addr:
        base, _, idx = addr.rpartition("#")
        return (base, int(idx)) if idx.isdigit() else (addr, None)
    return addr, None


@dataclass
class SpecField:
    value: Optional[str] = None
    source: str = ""        # "offline" | "llm" | "user"
    confirmed: bool = False

    def to_dict(self) -> dict:
        return {"value": self.value, "source": self.source, "confirmed": self.confirmed}


def _empty_item() -> dict[str, SpecField]:
    return {k: SpecField() for k in ITEM_KEYS}


@dataclass
class JobSpec:
    message_id: str
    subject: str = ""
    counterparty: str = ""
    purpose: str = ""
    has_attachment: bool = False
    attachment_names: list[str] = field(default_factory=list)
    job_fields: dict[str, SpecField] = field(default_factory=dict)
    items: list[dict[str, SpecField]] = field(default_factory=list)

    def field_at(self, addr: str) -> Optional[SpecField]:
        """Resolve a wire address to its SpecField (or None if out of range / unknown)."""
        base, i = parse_address(addr)
        if i is None:
            return self.job_fields.get(base)
        return self.items[i].get(base) if 0 <= i < len(self.items) else None

    def to_dict(self) -> dict:
        return {
            "message_id": self.message_id, "subject": self.subject,
            "counterparty": self.counterparty, "purpose": self.purpose,
            "has_attachment": self.has_attachment, "attachment_names": self.attachment_names,
            "job_fields": {k: f.to_dict() for k, f in self.job_fields.items()},
            "items": [{k: f.to_dict() for k, f in it.items()} for it in self.items],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "JobSpec":
        """Rebuild a JobSpec from ``to_dict`` output. Accepts the legacy flat ``fields`` shape too
        (splits it by scope into one line item + the job-level fields), so old jobspecs.jsonl still loads."""
        if "items" in d or "job_fields" in d:
            job = {k: SpecField(**v) for k, v in (d.get("job_fields") or {}).items()}
            items = [{k: SpecField(**v) for k, v in (it or {}).items()} for it in (d.get("items") or [])]
        else:  # legacy flat {fields: {key: SpecField}} — migrate
            flat = {k: SpecField(**v) for k, v in (d.get("fields") or {}).items()}
            job = {k: flat.get(k, SpecField()) for k in JOB_KEYS}
            items = [{k: flat.get(k, SpecField()) for k in ITEM_KEYS}]
        for k in JOB_KEYS:
            job.setdefault(k, SpecField())
        for it in items:
            for k in ITEM_KEYS:
                it.setdefault(k, SpecField())
        if not items:
            items = [_empty_item()]
        return cls(
            message_id=d.get("message_id", ""), subject=d.get("subject", ""),
            counterparty=d.get("counterparty", ""), purpose=d.get("purpose", ""),
            has_attachment=bool(d.get("has_attachment")),
            attachment_names=list(d.get("attachment_names") or []), job_fields=job, items=items,
        )


def build_jobspec(result: dict[str, Any], env: dict[str, Any],
                  draft: Optional[dict[str, Any]] = None) -> JobSpec:
    """Deterministically assemble a JobSpec from a triage verdict + envelope (+ optional LLM draft).

    Reuses signals we already have — no new extraction:
      * attachments  -> ``design_ready`` (offline)        * counterparty    -> ``client_identity`` (offline)
      * entities.deadline -> ``deadline`` (llm)           * entities.money  -> ``budget`` (llm)
    ``draft`` (from specdraft.py) supplies ``line_items`` (per-piece, source ``llm``) and the job-level
    ``material_supplied_by`` / ``delivery``. With no draft line items we seed ONE item from
    ``entities.product_or_service`` so the spec always has at least one line item to fill.
    """
    draft = draft or {}
    ent = result.get("entities") or {}
    atts = env.get("attachments") or []
    names = [a.get("filename") for a in atts if a.get("filename")]

    job = {k: SpecField() for k in JOB_KEYS}
    if atts:
        job["design_ready"] = SpecField("ficheiro em anexo (rever usabilidade)", "offline", False)
    if result.get("counterparty"):
        job["client_identity"] = SpecField(result["counterparty"], "offline", False)
    if ent.get("deadline"):
        job["deadline"] = SpecField(ent["deadline"], "llm", False)
    if ent.get("money"):
        job["budget"] = SpecField(ent["money"], "llm", False)
    for k in ("material_supplied_by", "delivery"):
        if draft.get(k):
            job[k] = SpecField(str(draft[k]), "llm", False)

    # Per-piece line items come from the LLM draft; fall back to one item seeded from the triage entity.
    items: list[dict[str, SpecField]] = []
    for li in draft.get("line_items") or []:
        it = _empty_item()
        for k in ITEM_KEYS:
            if li.get(k):
                it[k] = SpecField(str(li[k]), "llm", False)
        if any(it[k].value for k in ITEM_KEYS):
            items.append(it)
    if not items:
        it = _empty_item()
        if ent.get("product_or_service"):
            it["item"] = SpecField(ent["product_or_service"], "llm", False)
        items.append(it)

    return JobSpec(
        message_id=result.get("message_id", ""), subject=result.get("subject", ""),
        counterparty=result.get("counterparty", ""), purpose=result.get("purpose", ""),
        has_attachment=bool(atts), attachment_names=names, job_fields=job, items=items,
    )


def confirm(spec: JobSpec, addr: str, value: str) -> JobSpec:
    """Apply a user confirmation/override (the authoritative source) at a field address. Returns spec."""
    base, i = parse_address(addr)
    if i is None:
        if base in spec.job_fields:
            spec.job_fields[base] = SpecField(value, "user", True)
    elif 0 <= i < len(spec.items) and base in spec.items[i]:
        spec.items[i][base] = SpecField(value, "user", True)
    return spec


def readiness(spec: JobSpec) -> dict[str, Any]:
    """Gate-1 scorer over N line items: which must-haves (per item + job-level) are present/confirmed/
    missing, is the whole job estimable, and what to ask. Lists are field **addresses**."""
    must_addrs: list[str] = list(JOB_MUST)
    for i in range(len(spec.items)):
        must_addrs += [address(k, i) for k in ITEM_MUST]

    def f(addr):
        return spec.field_at(addr)

    present = [a for a in must_addrs if f(a) and f(a).value]
    confirmed = [a for a in must_addrs if f(a) and f(a).value and f(a).confirmed]
    missing = [a for a in must_addrs if not (f(a) and f(a).value)]
    unconfirmed = [a for a in must_addrs if f(a) and f(a).value and not f(a).confirmed]
    estimable = bool(spec.items) and len(confirmed) == len(must_addrs)
    # Questions: one per missing must-have base key (deduped — don't ask "material?" once per item).
    seen, questions = set(), []
    for a in missing:
        base, _ = parse_address(a)
        if base not in seen and _QUESTION.get(base):
            seen.add(base)
            questions.append(_QUESTION[base])
    return {
        "estimable": estimable,
        "coverage": round(len(present) / len(must_addrs), 2) if must_addrs else 0.0,
        "present": present, "confirmed": confirmed,
        "missing": missing, "unconfirmed": unconfirmed,
        "n_items": len(spec.items),
        # if the must-haves aren't all nailed and there's a file, it probably holds the rest
        "attachment_to_review": spec.has_attachment and (bool(missing) or bool(unconfirmed)),
        "questions": questions,
    }


def score_drafts(specs: list[dict], labels: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Functional eval (Phase B): per-field draft-vs-label presence agreement over the labeled set.

    ``labels`` maps message_id -> {field: true_value}; the label sheet is flat (one column per base
    field). We score **presence agreement** at the base-field level: did the draft fill this field on
    ANY line item (or job-level) where the human also filled it. Exact-string accuracy is meaningless on
    free text at n≈32; per-item gold labels aren't in the flat sheet (a known limitation). Returns
    per-field and overall agreement.
    """
    by_field: dict[str, list[int]] = {}
    for s in specs:
        lab = labels.get(s["message_id"])
        if lab is None:
            continue
        spec = JobSpec.from_dict(s)
        for k in MUST + SHOULD:
            if _SCOPE[k] == "job":
                fld = spec.job_fields.get(k)
                drafted = bool(fld and fld.value)
            else:
                drafted = any(it.get(k) and it[k].value for it in spec.items)
            labeled = bool((lab.get(k) or "").strip())
            by_field.setdefault(k, []).append(int(drafted == labeled))
    per = {k: round(sum(v) / len(v), 2) for k, v in by_field.items() if v}
    flat = [x for v in by_field.values() for x in v]
    return {"n": len(set(labels) & {s["message_id"] for s in specs}),
            "per_field_agreement": per,
            "overall_agreement": round(sum(flat) / len(flat), 2) if flat else None}
