# ADR-019 — Conversational intake: a cloud-capable capture adapter on the ADR-015 ledger

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-06-16 (accepted 2026-06-23, on shipping M1–M3 + Increments 1–2) |

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

- **Status path:** **Accepted** on shipping M1–M3 + Increments 1–2 (the build is complete and pinned).
  Now immutable; further changes go in a new ADR.
- **Depends on** the egress sign-off [ADR-020](adr-020-capture-egress-and-data-handling.md) and the
  network posture [ADR-021](adr-021-intake-lan-binding-minimal-auth.md) (the latter still *Proposed* —
  its LAN-bind + auth gate are not yet built; the intake bot needs no inbound port either way).
- **Trace.** All six decisions are shipped and pinned:
  - **Capture adapter, not a new store (1):** `captures.py` (`CaptureStore`) appends through the
    ADR-015 ledger (`webapp.apply_capture` → `ProjectStore.add_event`); `workspace.db` v5
    `captures`/`capture_users` tables. Tests: `tests/test_intake_store.py`, `tests/test_captures_api.py`.
  - **Pluggable cloud inference, narrowing ADR-015 §Decision-4 for this path (2):** transcription +
    extraction reuse the shared Vertex dispatch (`classifier.make_client` → `llm.call`) in `intake.py`
    (`_transcribe`) and `capture_infer.py` (`extract_fields`/`infer_project`). Tests:
    `tests/test_intake_bot.py` (voice), `tests/test_capture_infer.py`.
  - **Scope = the staffer's own assertions + artifacts (3):** the worker ingests text/photo/voice
    (`intake._handle_message`), `content_class` = `conversation`/`artifact`; no client-call recording path.
  - **Deterministic-first, model-by-uncertainty (4):** `capture_resolve.py` (offline rank, seeded from
    `config/capture_playbook.md` + the gazetteer) resolves the certain cases; `capture_infer.py` is
    invoked only when the deterministic resolver is ambiguous. Tests: `tests/test_capture_resolve.py`,
    `tests/test_capture_infer.py`.
  - **No auto-apply — the user is the sole gatekeeper (5):** nothing writes a project field except an
    explicit per-field POST to `/api/projects/{pid}/field`; extraction only *stores* on the capture.
    Pinned by `test_extracted_fields_never_reach_the_estimable_gate_without_explicit_confirm` and the
    apply-idempotency / apply-after-discard tests in `tests/test_captures_api.py`.
  - **A capture is never dropped (6):** an unmatched capture stays pending
    (`test_no_active_projects_holds_capture_in_queue`); a locked DB holds the offset and retries
    (`test_persist_lock_holds_offset_and_retries_until_committed`).
  - Design: [solution-design-v1](../10-external-proposals/intake-bot-solution-design-v1.md) §3–§5;
    [execution-plan-v1](../10-external-proposals/intake-bot-execution-plan-v1.md). Deterministic-composer
    precedent: [ADR-013](adr-013-client-email-composer-deterministic.md).
