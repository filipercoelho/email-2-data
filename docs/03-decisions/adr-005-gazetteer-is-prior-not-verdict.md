# ADR-005 — The gazetteer is a prior, not a verdict

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-06-10 (back-filled) |

## Context

We hand-curate a mapping of email-or-domain → counterparty hint (the gazetteer). The
temptation is to treat a gazetteer hit as the final answer. But that would re-introduce
domain-as-verdict ([ADR-003](adr-003-counterparty-from-body-not-domain.md)) through the back
door, and a stale or wrong gazetteer row would silently mislabel mail.

## Decision

The gazetteer (`config/gazetteer.csv`, loaded into SQLite) is a **prior attached to the
classifier input, never a standalone verdict**. Its job in the cascade is two-fold:
(1) supply the LLM with a hint, and (2) **veto an offline IGNORE** — *any* gazetteer knowledge
(client / supplier / internal) about a party stops a header-based bin and escalates the
message to the LLM with the hint attached. Knowledge thus compounds: curated facts shrink
future uncertainty without ever short-circuiting the body-decides rule.

## Consequences

- `cascade.py:70` — `if signals.ignorable_offline and hint is None:` → only mail with **no**
  gazetteer knowledge can be binned offline.
- Adding a gazetteer row makes the system escalate *more* carefully, never bin *more*.
- Trace: `src/email2data/store.py` (gazetteer KnowledgeStore), `cascade.py:62-70`.
  Source: [VISION.md](../../VISION.md) tenet 5 ("knowledge compounds").
