"""Canonical contracts.

The verdict model (migrated Phase 1) has three axes, learned from real mail:
  * counterparty — WHO, from Lindo's point of view (CLIENT buys from us; "we are the client of X" => X
    is a SUPPLIER). Decided by the BODY, never the domain.
  * purpose      — WHAT the message is doing.
  * direction    — who SENT it (a header fact, set deterministically by signals.py, not the model).
Priority is DERIVED from those + urgency + bulk (see ``derive_priority``); it is partly dynamic
(awaited outbound starts LOW — full escalation-over-time is Phase 4).

``TRIAGE_TOOL`` / ``GEMINI_TRIAGE_SCHEMA`` are the structured-output contracts the model must satisfy.
The model emits counterparty/purpose/urgency/confidence/reason/entities; direction + priority are set
in code. The ``enum`` lists below are the single source of truth.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

# Bump whenever the playbook OR this schema changes verdicts, so re-runs are comparable and the
# verdict cache (Phase 4) invalidates correctly.
EXTRACTOR_VERSION = "counterparty.2026-05-29.v3"

# Counterparty is ALWAYS from Lindo's point of view.
#   CLIENT = buys from us (revenue).  LEAD = prospective client, not yet buying.
#   SUPPLIER = we buy from them (incl. service/tool vendors; "we are the client of X" => X is SUPPLIER).
#   INTERNAL = colleague @lindoservico.pt.  BULK = newsletter/marketing.  OTHER = none of these.
COUNTERPARTY = ["CLIENT", "LEAD", "SUPPLIER", "INTERNAL", "BULK", "OTHER"]
PURPOSE = [
    "PO_FROM_CLIENT",
    "ESTIMATE_REQUEST_FROM_CLIENT",
    "OUTBOUND_INVOICE",  # invoice WE issue to a client (counterparty stays CLIENT)
    "OUR_ORDER_TO_SUPPLIER",
    "SUPPLIER_REPLY_OR_CONFIRMATION",
    "INVOICE_OR_ACCOUNTING",
    "FOLLOW_UP",
    "PUBLICITY",
    "INTERNAL_OPS",
    "OTHER",
]
DIRECTION = ["inbound", "internal"]  # who SENT this message (header fact, set by signals.py)
PRIORITIES = ["HIGH", "MEDIUM", "LOW", "IGNORE", "NEEDS_REVIEW"]

# Only these counterparties may carry IGNORE. Anything else marked IGNORE is incoherent -> NEEDS_REVIEW.
IGNORABLE_COUNTERPARTIES = {"BULK", "OTHER"}
# A possible client/lead is the high-value, never-bin case.
HIGH_VALUE_COUNTERPARTIES = {"CLIENT", "LEAD"}
HIGH_VALUE_PURPOSES = {"PO_FROM_CLIENT", "ESTIMATE_REQUEST_FROM_CLIENT"}
# Awaited-outbound purposes start LOW and escalate with days-without-reply (dynamic part = Phase 4).
AWAITED_OUTBOUND_PURPOSES = {"FOLLOW_UP", "OUR_ORDER_TO_SUPPLIER"}


def derive_priority(counterparty: str, purpose: str, urgency: int, is_bulk: bool) -> str:
    """Priority is a deterministic function of the axes (not a model output).

    Bulk → IGNORE. A client/lead or a client PO/estimate → HIGH. Awaited-outbound → LOW (initial; the
    Phase-4 timer raises it over time). Otherwise HIGH if time-pressured, else MEDIUM.
    """
    if is_bulk or counterparty == "BULK":
        return "IGNORE"
    if counterparty in HIGH_VALUE_COUNTERPARTIES or purpose in HIGH_VALUE_PURPOSES:
        return "HIGH"
    if purpose in AWAITED_OUTBOUND_PURPOSES:
        return "LOW"
    return "HIGH" if urgency >= 70 else "MEDIUM"


@dataclass
class Entities:
    client_name: Optional[str] = None
    client_email: Optional[str] = None
    deadline: Optional[str] = None  # ISO YYYY-MM-DD
    money: Optional[str] = None
    product_or_service: Optional[str] = None
    action_requested: Optional[str] = None
    # Filled deterministically by extract.py (not the model): format-locked, checksum-validated.
    nif: Optional[str] = None       # PT taxpayer id (9 digits, mod-11 valid)
    iban: Optional[str] = None      # PT IBAN


@dataclass
class TriageResult:
    message_id: str
    counterparty: str          # one of COUNTERPARTY
    purpose: str               # one of PURPOSE
    direction: str             # one of DIRECTION (set from signals, not the model)
    priority: str              # one of PRIORITIES (derived)
    urgency: int               # 0-100
    confidence: float          # 0.0-1.0
    reason: str
    entities: Entities = field(default_factory=Entities)
    extractor_version: str = EXTRACTOR_VERSION
    # provenance
    subject: str = ""
    from_addr: str = ""
    decided_by: str = ""       # "tier0:bulk", "tier1:gemini-2.5-flash", ...

    def to_dict(self) -> dict:
        return asdict(self)


# --- Structured-output contracts (model emits these fields; code adds direction/priority) ---

_ENTITY_PROPS_NULLABLE = {
    "client_name": {"type": ["string", "null"]},
    "client_email": {"type": ["string", "null"]},
    "deadline": {"type": ["string", "null"]},
    "money": {"type": ["string", "null"]},
    "product_or_service": {"type": ["string", "null"]},
    "action_requested": {"type": ["string", "null"]},
}

# Anthropic tool (forced) — kept for provider parity.
TRIAGE_TOOL = {
    "name": "record_triage",
    "description": "Record the triage verdict for one email, following the playbook.",
    "input_schema": {
        "type": "object",
        "properties": {
            "counterparty": {"type": "string", "enum": COUNTERPARTY},
            "purpose": {"type": "string", "enum": PURPOSE},
            "urgency": {"type": "integer", "minimum": 0, "maximum": 100},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string"},
            "entities": {"type": "object", "properties": _ENTITY_PROPS_NULLABLE, "required": []},
        },
        "required": ["counterparty", "purpose", "urgency", "confidence", "reason"],
    },
}

# Gemini (Vertex) controlled generation — OpenAPI subset (nullable via "nullable", no min/max).
GEMINI_TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "counterparty": {"type": "string", "enum": COUNTERPARTY},
        "purpose": {"type": "string", "enum": PURPOSE},
        "urgency": {"type": "integer"},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
        "entities": {
            "type": "object",
            "properties": {k: {"type": "string", "nullable": True} for k in _ENTITY_PROPS_NULLABLE},
        },
    },
    "required": ["counterparty", "purpose", "urgency", "confidence", "reason"],
}


# --- Phase B: job-spec draft (second pass, tiered to LEAD/PO) -----------------------------------
# The LLM drafts only the SEMANTIC spec fields it can read in the body; everything is nullable and
# the model is told to return null (not guess) — the spec is often in an attachment it cannot read.
# A single email may list SEVERAL distinct pieces, each with its own material/dimensions/etc, so the
# per-piece fields are drafted as a LIST of line items; ``material_supplied_by``/``delivery`` are
# job-level (one per email). ``process`` is internal (set by us), so the LLM never drafts it.
SPEC_ITEM_KEYS = ["item", "material", "dimensions", "thickness", "quantity", "colour_finish"]
SPEC_JOB_KEYS = ["material_supplied_by", "delivery"]
SPEC_LLM_KEYS = SPEC_ITEM_KEYS + SPEC_JOB_KEYS  # kept for callers that want the full flat key list
SPEC_SUPPLIED = ["client", "us", "unclear"]  # material_supplied_by is coerced to one of these or None

SPEC_TOOL = {
    "name": "record_job_spec",
    "description": "Extract the fabrication job spec explicitly stated in the email body. List EACH "
                   "distinct piece as its own line item (different material/dimensions/quantity = "
                   "different item). Return null for anything not stated — do NOT guess; the spec is "
                   "often in an attachment you cannot read.",
    "input_schema": {
        "type": "object",
        "properties": {
            "line_items": {
                "type": "array",
                "items": {"type": "object",
                          "properties": {k: {"type": ["string", "null"]} for k in SPEC_ITEM_KEYS}},
            },
            **{k: {"type": ["string", "null"]} for k in SPEC_JOB_KEYS},
        },
        "required": [],
    },
}
GEMINI_SPEC_SCHEMA = {
    "type": "object",
    "properties": {
        "line_items": {
            "type": "array",
            "items": {"type": "object",
                      "properties": {k: {"type": "string", "nullable": True} for k in SPEC_ITEM_KEYS}},
        },
        **{k: {"type": "string", "nullable": True} for k in SPEC_JOB_KEYS},
    },
}
