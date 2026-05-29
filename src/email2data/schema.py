"""Canonical contracts for v1.

Two things live here:

* ``TriageResult`` / ``Entities`` — the typed verdict the classifier produces. A trimmed,
  single-tenant version of ``BusinessEvent`` from the architectural draft.
* ``TRIAGE_TOOL`` — the Anthropic tool schema we force the model to call, so the model returns
  validated structured data instead of prose we have to parse.

Keep this file and ``config/triage_playbook.md`` in sync: the playbook is the human-readable rubric,
this schema is the machine-readable shape. The ``enum`` lists below are the single source of truth
for valid values.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

# Bump whenever the playbook OR this schema changes in a way that affects verdicts, so re-runs over
# the same corpus are comparable. Stamped onto every TriageResult.
EXTRACTOR_VERSION = "playbook.2026-05-29"

TYPES = [
    "CLIENT_JOB_REQUEST",
    "QUOTE_FOLLOWUP",
    "REMINDER_EVENT",
    "SUPPLIER_INVOICE",
    "CLIENT_COMPLAINT",  # defect/rework/reclamação — high urgency, must not sink into SUPPORT
    "SUPPORT_INTERNAL",
    "PUBLICITY",
    "OTHER",
]

# Types for which IGNORE priority is coherent. The classifier forces any other type away from
# IGNORE (see classifier coherence check) — a client request can never be "ignore".
IGNORABLE_TYPES = {"PUBLICITY", "OTHER"}
PRIORITIES = ["HIGH", "MEDIUM", "IGNORE", "NEEDS_REVIEW"]


@dataclass
class Entities:
    client_name: Optional[str] = None
    client_email: Optional[str] = None
    deadline: Optional[str] = None  # ISO YYYY-MM-DD
    money: Optional[str] = None
    product_or_service: Optional[str] = None
    action_requested: Optional[str] = None


@dataclass
class TriageResult:
    message_id: str          # rfc822 Message-ID (or content hash fallback)
    type: str                # one of TYPES
    priority: str            # one of PRIORITIES
    urgency: int             # 0-100
    confidence: float        # 0.0-1.0
    reason: str
    entities: Entities = field(default_factory=Entities)
    extractor_version: str = EXTRACTOR_VERSION
    # provenance (filled by caller, not the model)
    subject: str = ""
    from_addr: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# Anthropic tool definition. We set tool_choice to force this call, so the model cannot reply with
# free text — it must emit an object matching this schema.
TRIAGE_TOOL = {
    "name": "record_triage",
    "description": "Record the triage verdict for one email, following the playbook.",
    "input_schema": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": TYPES},
            "priority": {"type": "string", "enum": PRIORITIES},
            "urgency": {"type": "integer", "minimum": 0, "maximum": 100},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string"},
            "entities": {
                "type": "object",
                "properties": {
                    "client_name": {"type": ["string", "null"]},
                    "client_email": {"type": ["string", "null"]},
                    "deadline": {"type": ["string", "null"]},
                    "money": {"type": ["string", "null"]},
                    "product_or_service": {"type": ["string", "null"]},
                    "action_requested": {"type": ["string", "null"]},
                },
                "required": [],
            },
        },
        "required": ["type", "priority", "urgency", "confidence", "reason"],
    },
}


def _nullable_str() -> dict:
    return {"type": "string", "nullable": True}


# Gemini (Vertex) controlled-generation schema. Same shape as TRIAGE_TOOL's input_schema, but in the
# OpenAPI subset Gemini accepts: nullable via "nullable", no min/max keywords (the classifier clamps
# defensively anyway), enums enforced by the model.
GEMINI_TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": TYPES},
        "priority": {"type": "string", "enum": PRIORITIES},
        "urgency": {"type": "integer"},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
        "entities": {
            "type": "object",
            "properties": {
                "client_name": _nullable_str(),
                "client_email": _nullable_str(),
                "deadline": _nullable_str(),
                "money": _nullable_str(),
                "product_or_service": _nullable_str(),
                "action_requested": _nullable_str(),
            },
        },
    },
    "required": ["type", "priority", "urgency", "confidence", "reason"],
}
