# ADR-008 — Every verdict records who decided it

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-06-10 (back-filled) |

## Context

A triage result with no record of *how* it was reached is undebuggable and unauditable. When
a classification looks wrong, we must be able to tell whether a header rule, the gazetteer, or
the LLM produced it — and which version.

## Decision

**Every `TriageResult` stamps a `decided_by` provenance string** naming the tier and
engine/version that produced it — e.g. `tier0:bulk`, `tier0:<signal>`, `tier1:gemini-2.5-flash`.
Combined with `reason` and `confidence`, every verdict explains itself and is replayable.

## Consequences

- Results are debuggable, auditable, and replayable; regressions can be traced to the
  deciding layer.
- Enables the eval/learning loop — corrections can target the responsible tier.
- Trace: `src/email2data/schema.py` (`TriageResult.decided_by`), `cascade.py:49`
  (`decided_by=f"tier0:{...}"`). Source: [VISION.md](../../VISION.md) tenet 8.
