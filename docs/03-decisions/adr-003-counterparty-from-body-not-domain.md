# ADR-003 — The body decides counterparty, not the domain

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-06-10 (back-filled) |

## Context

The instinct is to classify a sender by their email domain. But Vision Box / Amadeus is a
*client* despite its domain, and Spandex is a *supplier*. Domain-based classification is
exactly the kind of plausible-pattern shortcut that silently mislabels real business
relationships — and a mislabelled client is lost revenue.

## Decision

**Counterparty is decided from the message body and context, from Lindo's point of view**
(CLIENT / SUPPLIER / LEAD). The sender's domain is at most a **prior**, never a verdict. The
asymmetry is deliberate: a false IGNORE of a client costs revenue; a false "needs review"
costs seconds — so the system is biased toward review, never toward silent binning.

## Consequences

- Domain knowledge enters only as a gazetteer *hint*
  ([ADR-005](adr-005-gazetteer-is-prior-not-verdict.md)), which the LLM may override.
- "Never silently bin a client" is a hard invariant
  ([ADR-006](adr-006-two-tier-cascade-anti-ignore-guardrail.md)).
- Source: [VISION.md](../../VISION.md) tenets 2 & 3; counterparty vocabulary in
  `src/email2data/schema.py` (`COUNTERPARTY`).
