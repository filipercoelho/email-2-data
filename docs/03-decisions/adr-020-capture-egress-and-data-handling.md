# ADR-020 — Capture data-handling: owner-signed cloud egress, minimise-at-edge / preserve-at-core

| Field | Value |
| --- | --- |
| Status | Proposed |
| Date | 2026-06-16 |

## Context

The conversational-intake path ([ADR-019](adr-019-conversational-intake-capture-adapter.md)) sends
capture content across Telegram's cloud and, for inference, to a cloud model — a change from the
loopback, no-egress baseline. The sibling **materials-costing** project set the precedent: cloud egress
for invoice images, EU-resident, **accepted by the owner in writing** (its ADR-075 §3.2).

The owner added a hard data-handling requirement: once data is stored on our side it must be **deleted
from Telegram (and any transport)**; and **everything must be preserved in full on our side and appended
to the project** for future reference and audit.

## Decision

1. **Owner sign-off on egress (R5).** The owner accepts, in writing (2026-06-16), that capture content
   transits Telegram and may be processed by a cloud inference engine — **conditional on** the safety
   practices in this ADR. Where the cloud engine is used it reuses the EU posture (Vertex on
   `materials-492723`, `europe-west1`). Scope is bounded by [ADR-019](adr-019-conversational-intake-capture-adapter.md) §3
   (the staffer's own assertions + artifacts, not covert client-call recordings). Raw content/audio is
   **never logged** (CLAUDE.md secrets rule).
2. **Minimise at the edge — persist-then-scrub, in that order.** On receipt: download the full payload,
   transcribe, and **durably persist the complete capture locally**; **only then** delete the source
   from Telegram — the user's message(s) **and** any content-bearing bot message. Deletion is
   **retried until confirmed and logged**, with a periodic reconciliation sweep. The same rule applies
   to any future transport ("any other data transmission source").
3. **Preserve at the core — full fidelity, appended for audit.** Verbatim text + transcript + the
   original audio file + media + provenance are retained. On validation the capture is appended to the
   project ledger: the text/transcript as an ADR-015 event in `project_field_history` (`channel ·
   asserted_by · acquired_at`), and the **audio/media attached to that event** — a net-new capability
   (the turn-1 audit found no media-to-project link today).
4. **Consequence: our copy is the sole copy → precious → backed up.** Because Telegram is scrubbed, it
   is **not** a fallback (the *opposite* of materials-costing, which could treat Telegram as re-fetchable).
   The on-disk `captures/` media is the only copy and **must be in the backup set** — the
   [ADR-010](adr-010-workspace-db-precious-vs-regenerable.md) precious-data discipline extends to it.
5. **Residual disclosed, not solved.** Deleting a message clears Telegram's **chat surface** and
   dereferences the CDN file, but **cannot guarantee erasure from Telegram's server-side
   infrastructure/backups**, and Bot-API deletion is bounded (private chat, ~48 h). This residual is
   inherent to using Telegram as transport and is the accepted price of decision 1.

## Consequences

- **Status path:** Proposed; becomes **Accepted when Phase 1 ships** the scrub worker + backup change.
- **Trace (pending implementation):** the persist-then-scrub worker + retry/reconciliation sweep; the
  backup manifest extended to include `captures/`; tests for **persist-then-scrub ordering** and
  **scrub-failure retry**. Design: [solution-design-v1](../10-external-proposals/intake-bot-solution-design-v1.md)
  §6–§7. Precedent: materials-costing ADR-075 §3.2 (owner-signed egress).
