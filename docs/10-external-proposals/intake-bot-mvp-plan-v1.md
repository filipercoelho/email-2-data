# Conversational Intake — MVP Build Plan v1

| Field | Value |
| --- | --- |
| Status | **Draft plan — build sequencing for quick end-to-end validation.** |
| Date | 2026-06-16 |
| Builds on | [solution-design-v1](intake-bot-solution-design-v1.md); [ADR-019](../03-decisions/adr-019-conversational-intake-capture-adapter.md) · [ADR-020](../03-decisions/adr-020-capture-egress-and-data-handling.md) · [ADR-021](../03-decisions/adr-021-intake-lan-binding-minimal-auth.md) (Proposed) |
| Intent | Prove the riskiest **cheap-to-test** assumptions end-to-end **before** investing in LLM inference. |

## 1. What the MVP validates — and what it deliberately does not

The expensive, iterative part of this system is **LLM project-inference + field extraction** (the R2
measured loop). The MVP's job is to **de-risk everything around it first**, cheaply, so that the
investment later lands on a loop already proven to be worth it. Three hypotheses — **none of which needs
a single model call:**

- **H1 — Capture-at-source beats the desk path.** A staffer gets a note/photo into the right project
  from their phone in less time than "open laptop → find project → type" (the solution design's success
  measure).
- **H2 — The privacy discipline is workable.** Persist-then-scrub actually clears Telegram once the
  capture is stored, and the preserved copy + audit trail is intact ([ADR-020](../03-decisions/adr-020-capture-egress-and-data-handling.md)).
- **H3 — The confirm loop feels right.** Validating a pending capture in the webapp is low-friction
  enough that captures don't pile up unvalidated (R9).

If H1/H3 don't hold, **no amount of inference quality saves the product** — far better to learn that in
days of build than weeks. The MVP is the cheapest honest test of that.

## 2. MVP scope

### In scope

- Long-poll worker (outbound only) + default-deny allowlist (a chat-id in `.env` to start).
- Capture of **text + photo** (one message → one capture).
- **Explicit project selection** via an inline-button pick-list of active projects — **no matching, no
  LLM**.
- **Persist-then-scrub** ([ADR-020](../03-decisions/adr-020-capture-egress-and-data-handling.md) §2) +
  **preserve and append-on-validate** with the photo attached to the project event (§3).
- A minimal webapp **"Caixa de Capturas"** pending-edits queue; validate **at the desk on loopback**.
- Idempotency (unique `(message_id, chat_id)`); WAL + a single write-lock (R4); a capture is never
  dropped (N2).

### Out of scope (deferred — §8)

- **Audio** — needs the R3 engine pick → Increment 1.
- **LLM project-inference + field extraction** (R2) → Increment 2.
- **Deterministic resolve** (pre-filter to shorten the pick-list) → Increment 1.
- **LAN-bind + minimal auth** ([ADR-021](../03-decisions/adr-021-intake-lan-binding-minimal-auth.md)) →
  Increment, when cross-device validation is actually wanted.
- Lead creation; media groups / edited / forwarded messages (R7/R8).

> The MVP carries **zero open dependencies** — in particular it does **not** wait on the R3 engine
> choice, because it has no transcription step yet.

## 3. The end-to-end slice (the demoable loop)

```text
STAFF → BOT (phone)   text "Sousa: confirmou prazo 30 jun, inox 304"   — or a photo of a quote
BOT → STAFF  T1   "📥 Recebido. A guardar…"
  → download payload → PERSIST capture (text/media on disk + a captures row) → status = stored
  → SCRUB: delete the staffer's message from Telegram                       (ADR-020 §2)
BOT → STAFF  T2   "A que projeto pertence?  [p-0123 Padaria Sousa] [p-0119 Café Central] … [Outro]"
  (inline buttons = active-project pick-list; no matching)  → staffer taps  → status = parsed
USER (at the desk, loopback :8042)  →  "Caixa de Capturas"
  → sees the pending capture (text + photo thumbnail)  → "Aplicar"
  → appended to p-0123: an ADR-015 __note__ event (channel · asserted_by · acquired_at) + photo attached
BOT → STAFF  T3   "✔ Aplicado ao projeto Padaria Sousa."
```

Nothing reaches the project until the user taps **Aplicar** (N7/R9). An untapped capture is **held**,
never dropped (N2). All user-facing strings pt-PT.

## 4. Build breakdown — each milestone gated by a real test (DoD)

**M1 · Store + schema** (no Telegram yet)

- `captures` + `capture_users` tables; `SCHEMA_VERSION` bump with a guarded `_migrate`; WAL on; media on
  disk under `captures/`.
- `capture_store` API: idempotent insert, list-pending, mark-applied.
- *Test:* migration on a **populated** DB round-trips; a duplicate `(message_id, chat_id)` insert no-ops.
  → `tests/test_workspace_migration.py`, `tests/test_intake_store.py`

**M2 · Worker + allowlist + persist-then-scrub** (Telegram mocked)

- A thin Telegram client (`getUpdates` / `getFile` / download / `sendMessage` / `editMessageText` /
  `deleteMessage`); the worker loop (offset, backoff, skip-backlog on boot); allowlist default-deny; the
  pipeline text+photo → persist → scrub.
- *Test:* unknown sender rejected + logged; **persist-then-scrub ordering** (scrub fires only after
  durable persist); **scrub-failure → retried, capture preserved**; never-dropped on no project.
  → `tests/test_intake_worker.py`

**M3 · Caixa de Capturas + apply** (the human loop)

- Webapp: `GET /api/captures`, `POST /api/captures/{id}/apply`, `POST /api/captures/{id}/discard`; a
  minimal pending-queue page; append-to-project via `add_event` + **photo attached to the event**
  (net-new); the timeline renders the attachment.
- *Test:* apply appends an ADR-015 event with provenance + a media link to the **right** project, and is
  idempotent; discard holds/removes without touching the project. → `tests/test_intake_apply.py`,
  `tests/test_webapp.py`

**M4 · CLI + the real validation session** (the real proof, not a unit test)

- `email2data intake-bot` subcommand (separate process); `.env` token + allowlist; runs alongside
  `email2data serve` (loopback).
- The real end-to-end session of §7. This is a **run, not a unit test** — proves H1–H3 on real hardware.

## 5. Data model (MVP subset)

The MVP columns of `captures` + `capture_users` — the full shape is in
[solution-design-v1 §8](intake-bot-solution-design-v1.md). MVP needs:
`captures(capture_id, telegram_message_id, telegram_chat_id UNIQUE pair, raw_text, media_paths,
inferred_project_id, channel, asserted_by, acquired_at, status, telegram_scrubbed_at, created_ts)` and
`capture_users(telegram_user_id, display_name, roster_owner, enabled)`. No `transcript`,
`extracted_fields_json`, or `confidence` columns are exercised yet (audio/LLM are deferred) — they can be
added in the same table when Increment 1/2 land.

## 6. New code surface

- `src/email2data/intake/` — `worker.py`, `telegram.py`, `allowlist.py`, `pipeline.py`, `store.py`.
- `cli.py` — the `intake-bot` subcommand.
- `webapp.py` — three endpoints (`/api/captures`, `…/apply`, `…/discard`).
- `captures_page.py` (or a tab on [projetos_page.py](../../src/email2data/projetos_page.py)) — the queue UI.
- `workspace.py` — the migration + WAL + the shared write-lock.
- `.env` — `TELEGRAM_BOT_TOKEN`, `INTAKE_ALLOWLIST` (chat-ids). Never logged (N4).

## 7. The validation session (real, not a proxy)

This is the deliverable that actually answers H1–H3 — the working-style rule is to prove it end-to-end,
not via cached runs or mocks:

1. BotFather → token → `.env` (owner does this once).
2. Run `email2data intake-bot` + `email2data serve` (loopback) on the box.
3. From the phone: send a text note **and** a photo; pick the project from the buttons.
4. Verify: the capture appears in **Caixa de Capturas**; the **Telegram chat is scrubbed**; on
   **Aplicar**, the note + photo land in the project timeline with provenance.
5. **Time it** against the desk path (H1); check nothing piled up (H3); confirm scrub + preserve (H2).
6. Write the findings down → decide whether to fund Increment 1 (audio) and Increment 2 (inference).

## 8. Deferred to fast-follow increments (with why)

> **R3 decided (2026-06-16): reuse the app's existing Google Vertex/Gemini dispatch**
> ([llm.py](../../src/email2data/llm.py) / [ADR-012](../03-decisions/adr-012-shared-llm-provider-dispatch.md),
> project `materials-492723`, EU region) for both increments — no new engine, no new credentials. The
> cloud egress is the owner-signed path (ADR-020 / R5); the increments degrade gracefully when the LLM
> is unavailable (the capture still persists; inference is best-effort).

- **Increment 1 — audio + deterministic resolve.** Transcribe voice notes via the existing Vertex/Gemini
  multimodal path; add a project pre-filter (gazetteer + `capture_playbook.md`) to **shorten/pre-select**
  the pick-list. Audio is the highest-value modality (speaking > thumb-typing).
- **Increment 2 — LLM inference + field extraction.** Project inference + job-spec field extraction via
  the same Vertex/Gemini dispatch — the R2 measured loop. Extracted fields queue for **field-by-field
  validation** (no auto-apply, R9).
- **Increment (on demand) — LAN-bind + minimal auth** ([ADR-021](../03-decisions/adr-021-intake-lan-binding-minimal-auth.md)).
  Needed only when validation must happen on a **different** workstation than the box. Until then,
  loopback-at-the-desk keeps the MVP free of the app's first auth layer.

## 9. MVP-specific risks

- **Pick-list doesn't scale** past a handful of active projects — fine for validation (the small-active-
  set assumption holds); Increment 1's deterministic resolve fixes it.
- **`captures/` is sole-copy from day one** ([ADR-020](../03-decisions/adr-020-capture-egress-and-data-handling.md) §4):
  once Telegram is scrubbed, a lost local file is lost for good. Smallest MVP mitigation: a manual copy of
  `captures/` during the validation window until the backup wiring lands.
- **SQLite concurrency** (worker + webapp both write `workspace.db`): WAL + one write-lock in M1; low
  volume at a single user.
- **Real-Telegram deletion limits** (private chat, ~48 h) won't bite in a short session, but the
  retry/sweep still ships in M2 so the discipline is real, not demo-only.

## 10. Sequencing & effort

M1 → M2 → M3 are roughly independently testable; M4 is the validation. **Most of M2 is a port of the
proven `materials-costing` long-poll + allowlist + idempotency pattern**; the genuinely net-new code is
the `captures` queue and **media-attached-to-an-event**. This is assembly of a proven pattern on an
existing ledger, so the MVP is small — but the gate is **green tests + a successful §7 session**, not a
day count. No phase is "done" with the persist-then-scrub ordering or the never-dropped guarantee
hand-waved.

## M1 status & the M2 prerequisites the review surfaced

**M1 is built and verified** (340 tests pass, ruff clean): the `captures` + `capture_users` schema
(workspace.db v5), the `CaptureStore`, and — per this plan's own deliverable — **WAL + a 5 s
`busy_timeout`** on every `workspace.db` connection, so the M2 worker process can write alongside the
webapp. A 4-lens adversarial review confirmed the migration is crash-safe and the idempotency sound,
and surfaced fixes now applied (a label-preserving allowlist upsert; terminal-state guards). It also
pinned three **data-safety prerequisites M2 must honour** when the worker lands:

1. **Persist-then-scrub ordering (ADR-020 §2):** the worker must `add()` → re-verify the row is
   committed → **only then** delete from Telegram. Treat ANY `sqlite3.OperationalError` from a store
   write as *persist failed → keep Telegram → retry*. WAL + busy_timeout reduce locking but do not
   eliminate it, so the ordering — not WAL — is what prevents sole-copy loss.
2. **Single-migrator gate:** the worker should assert `PRAGMA user_version >= SCHEMA_VERSION` and
   **refuse to self-migrate** (only the webapp/CLI runs upgrades), so a concurrent first-boot against a
   pre-v5 DB can't race the migration.
3. **Backups include the WAL sidecars:** `workspace.db-wal` / `-shm` must be in the backup set (or use
   the Online Backup API / `VACUUM INTO`); a naive `cp` of the main file alone can lose committed
   decisions (see [data-stores.md](../05-reference/data-stores.md)).

---

*Draft build plan derived from [solution-design-v1](intake-bot-solution-design-v1.md) and ADR-019/020/021.
Scoped to validate H1–H3 with zero open dependencies; audio (R3) and inference (R2) are explicit
fast-follow increments, not part of the MVP.*
