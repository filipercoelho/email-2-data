# ADR-006 — Two-tier cascade with an asymmetric anti-IGNORE guardrail

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-06-10 (back-filled) |

## Context

Following [ADR-001](adr-001-compute-proportional-to-uncertainty-impact.md), most mail is
obvious bulk and should cost zero tokens, while the rest needs a model. But "decide offline
to save money" is dangerous if it can ever bin a real client.

## Decision

A **two-tier cascade**:

- **Tier 0 (offline, free):** deterministic header signals. Bulk/automated mail **from an
  unknown party** → `IGNORE`, no LLM. This is the *only* place mail is binned without a model,
  and **only header signals may do it**.
- **Tier 1 (cheap LLM):** everything else, classified by Gemini with the Tier-0 facts +
  gazetteer hint attached.

**Guardrail:** the offline IGNORE fires only when `signals.ignorable_offline and hint is None`
— any gazetteer knowledge vetoes it ([ADR-005](adr-005-gazetteer-is-prior-not-verdict.md)),
and genuinely uncertain bins route to `NEEDS_REVIEW` (a human signal), never to silent IGNORE.
The asymmetry is intentional: a false IGNORE loses revenue; a false review costs seconds.

## Consequences

- Tier-0 catches ~95% of obvious bulk at zero token cost; escalation rate trends down as the
  gazetteer grows.
- Trace: `src/email2data/cascade.py` (module docstring + `_offline_ignore` + `triage`),
  `signals.py` (`ignorable_offline`, `bulk_evidence`). Source: [VISION.md](../../VISION.md)
  tenets 2 & 7.
