# ADR-020 — Capture data-handling: owner-signed cloud egress, minimise-at-edge / preserve-at-core

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-06-16 (accepted 2026-06-23, on shipping the scrub worker + sole-copy backup discipline) |

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

- **Status path:** **Accepted** on shipping the persist-then-scrub worker + the sole-copy backup
  discipline. Now immutable.
- **Trace.** Decisions 1, 3, 4 shipped in full; decision 2's *ordering* shipped, its
  *retry-until-confirmed + reconciliation sweep* deferred (see below):
  - **Owner-signed cloud egress, EU posture (1):** transcription (`intake._transcribe`) + extraction
    (`capture_infer.extract_fields`) go through the shared Vertex dispatch (`classifier.make_client`,
    `materials-492723`). Raw audio/content is **never logged** — only `capture_id` in the failure path
    (`intake._transcribe`/`_extract_fields`); pinned by `test_transcription_failure_keeps_the_capture_intact`.
  - **Minimise at the edge — persist-then-scrub, in that order (2):** `intake._handle_message` persists
    via `CaptureStore.add` (a durable commit) **before** `_scrub`; a failed persist raises
    `TransientPersistError`, holds the long-poll offset and **never scrubs**. Tests:
    `test_persist_failure_never_scrubs`, `test_persist_lock_holds_offset_and_retries_until_committed`,
    `test_voice_capture_persists_then_scrubs_then_transcribes`. **Deferred:** the delete is currently a
    one-shot best-effort (a failed `deleteMessage` is non-fatal and logged — `test_scrub_failure_is_
    nonfatal_and_capture_kept`); the **retry-until-confirmed loop + periodic reconciliation sweep** are
    a remaining hardening follow-up. The safety guarantee still holds — a capture is never lost because
    it is persisted-first; only the Telegram-side surface may linger until manually cleared.
  - **Preserve at the core — full fidelity, appended for audit (3):** verbatim text + transcript
    (`captures.transcript`, v6) + original audio/media (`captures/` on disk) + provenance are retained;
    on validation the text/transcript lands as an ADR-015 event and a photo links via
    `source_mid="capture:<cid>"` (rendered in the project timeline). A discarded capture is **kept**
    (`test_discarded_capture_is_retained_for_audit`). Tests: `tests/test_captures_api.py`,
    `tests/test_captures_page.py`.
  - **Our copy is the sole copy → precious → backed up (4):** `captures/` is gitignored (WP0) and
    flagged sole-copy-must-back-up in [data-stores.md](../05-reference/data-stores.md) (WAL sidecars
    note); the [ADR-010](adr-010-workspace-db-precious-vs-regenerable.md) discipline extends to it.
  - Design: [solution-design-v1](../10-external-proposals/intake-bot-solution-design-v1.md) §6–§7;
    [execution-plan-v1](../10-external-proposals/intake-bot-execution-plan-v1.md) WP0/WP3. Precedent:
    materials-costing ADR-075 §3.2 (owner-signed egress).
