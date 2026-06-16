# ADR-019 — Conversational intake: a cloud-capable capture adapter on the ADR-015 ledger

| Field | Value |
| --- | --- |
| Status | Proposed |
| Date | 2026-06-16 |

## Context

An external brief proposed a Telegram intake bot to feed off-desk information into projects. Measured
against production, ~70% of it is already the [ADR-015](adr-015-knowledge-capture-claim-ledger.md)
claim ledger; the genuinely new surface is transport + inference. The full engineering reading is the
[solution design](../10-external-proposals/intake-bot-solution-design-v1.md).

[ADR-015](adr-015-knowledge-capture-claim-ledger.md) §Decision-4 deliberately kept capture
**deterministic, desktop, NO-LLM**, with private call/opinion content local-only ("verbatim call/opinion
notes must not auto-ship to Vertex"). That is the right call for the **desktop capture UI**. The owner
has now signed off (2026-06-16) on a **separate** Telegram intake path with cloud transcription/
extraction (decisions R1/R3/R5), which changes the constraint for that path only.

## Decision

1. **Capture adapter, not a new store.** The bot is a front-end on the ADR-015 ledger; captures land
   through the existing event/field provenance path (`project_field_history`). No parallel fact store
   (honours [ADR-010](adr-010-workspace-db-precious-vs-regenerable.md) / ADR-015).
2. **Pluggable inference engine; cloud permitted — this narrows ADR-015 §Decision-4 *for this path*.**
   Transcription/extraction runs through a pluggable engine (Vertex Gemini · Whisper local-or-hosted ·
   other competitive contender). This narrows ADR-015 §Decision-4's no-LLM / local-only rule for the
   **conversational-intake path only**, under the explicit egress sign-off in
   [ADR-020](adr-020-capture-egress-and-data-handling.md). ADR-015 §Decision-4 **remains in force for
   the desktop capture UI** (ADR-015 is immutable; it is narrowed, not edited).
3. **Scope = the staffer's own assertions + artifacts (Option B, confirmed 2026-06-16).** The bot
   ingests the staffer's own text, voice memos/dictation and media artifacts — **not** deliberately-
   recorded client-call audio.
4. **Deterministic-first, model-by-uncertainty ([ADR-001](adr-001-compute-proportional-to-uncertainty-impact.md)).**
   A deterministic pre-filter (active projects + the [gazetteer](../../config/gazetteer.csv) + a new
   editable `config/capture_playbook.md`) resolves the certain cases; the model is invoked only on
   ambiguity. The playbook is seeded from existing pt-PT email assets (the `jobspec` field questions,
   `labels.py`, the gazetteer, the `/api/reclassifications` human-corrected pairs) and evolved
   iteratively — a behaviour change treated like a code change.
5. **No auto-apply — the user is the sole gatekeeper (extends ADR-015 §Decision-4 / -5).** Nothing
   reaches a project field without explicit human validation: project association is confirmed in chat;
   gate-affecting field values queue in the webapp "Caixa de Capturas" pending-edits inbox. Free-text
   notes on a confirmed project stay `tier=context` and never gate estimability.
6. **A capture is never dropped (mirrors [ADR-003](adr-003-counterparty-from-body-not-domain.md)'s
   never-silently-bin).** An unmatched capture is held in the queue and surfaced; lead/project creation
   from a capture is deferred (out of v1).

## Consequences

- **Status path:** Proposed now; becomes **Accepted when Phase 1 ships**, with the Trace below filled in.
  Because it is not yet Accepted it stays mutable while the phased build refines it.
- **Depends on** the egress sign-off [ADR-020](adr-020-capture-egress-and-data-handling.md) and the
  network posture [ADR-021](adr-021-intake-lan-binding-minimal-auth.md).
- **Trace (pending implementation):** the intake worker (long-poll, allowlist), `config/capture_playbook.md`,
  the `captures` queue + `capture_users` allowlist; tests for the allowlist gate, deterministic resolve,
  and the no-auto-apply guarantee. Design: [solution-design-v1](../10-external-proposals/intake-bot-solution-design-v1.md)
  §3–§5. Deterministic-composer precedent: [ADR-013](adr-013-client-email-composer-deterministic.md).
