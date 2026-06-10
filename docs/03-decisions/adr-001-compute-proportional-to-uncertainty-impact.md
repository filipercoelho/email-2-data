# ADR-001 — Spend compute in proportion to uncertainty × business impact

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-06-10 (back-filled; principle since Phase 0) |

## Context

Email is Lindo's de-facto coordination layer with no notion of ownership, priority, or
state. A client's estimate request and a Festool promo arrive in the same stream. LLM calls
cost money and latency; running every message through the strongest model is wasteful, and
running none misses revenue. We need one rule that governs cost, quality, and the learning
loop simultaneously.

## Decision

**Spend compute (tokens, model power) in proportion to `uncertainty × business impact`.**
Certain and low-stakes mail is decided offline for free; uncertain and high-stakes mail (a
possible client we might wrongly ignore) is escalated to a stronger model. Rules before
models; cheap models before expensive; models only where needed.

## Consequences

- Drives the two-tier cascade ([ADR-006](adr-006-two-tier-cascade-anti-ignore-guardrail.md))
  and the asymmetric anti-IGNORE guardrail ([ADR-003](adr-003-counterparty-from-body-not-domain.md)
  is downstream of it).
- Success is measured as *tokens-per-email trending down at constant-or-better accuracy*
  (see [06-qa](../06-qa/acceptance-criteria.md)).
- Knowledge must compound — confirmed facts feed back so the expensive path is needed less
  over time ([ADR-005](adr-005-gazetteer-is-prior-not-verdict.md)).
- Source: [VISION.md](../../VISION.md) "The governing principle" + tenet 7.
