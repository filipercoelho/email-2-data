"""Phase B — tiered LLM spec draft (second pass, LEAD/PO only).

Drafts the SEMANTIC spec fields the model can read in the body (item/material/dimensions/thickness/
quantity/colour_finish/material_supplied_by/delivery). The CALLER decides who gets this pass (it must
be tiered to the ~12% job-relevant mail), so non-job email never pays for it. Provider plumbing lives
in ``llm.py``. The model is told to return null rather than guess — see ``config/spec_playbook.md``.
Results feed ``jobspec.build_jobspec(..., draft=...)`` with source ``llm``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import llm
from .schema import GEMINI_SPEC_SCHEMA, SPEC_LLM_KEYS, SPEC_SUPPLIED, SPEC_TOOL


def load_playbook(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def build_spec_message(env: dict[str, Any]) -> str:
    atts = env.get("attachments") or []
    att_lines = "\n".join(f"  - {a.get('filename') or '(sem nome)'} [{a.get('content_type')}]" for a in atts)
    return (
        f"Subject: {env.get('subject', '')}\n"
        f"Attachments ({len(atts)}):\n{att_lines or '  (nenhum)'}\n"
        f"---\n{env.get('body_text', '')}"
    )


def coerce_spec(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep only known keys; empty/whitespace -> None; clamp material_supplied_by to its enum."""
    out: dict[str, Any] = {}
    for k in SPEC_LLM_KEYS:
        v = raw.get(k)
        v = v.strip() if isinstance(v, str) else None
        out[k] = v or None
    if out["material_supplied_by"] not in SPEC_SUPPLIED:
        out["material_supplied_by"] = None
    return out


def draft(env: dict[str, Any], playbook: str, client: Any, settings: dict[str, Any]) -> dict[str, Any]:
    """Draft the semantic spec for one (job-relevant) email. Returns coerced {key: str|None}."""
    raw = llm.call(client, settings["llm"], playbook, build_spec_message(env),
                   schema=GEMINI_SPEC_SCHEMA, tool=SPEC_TOOL)
    return coerce_spec(raw)
