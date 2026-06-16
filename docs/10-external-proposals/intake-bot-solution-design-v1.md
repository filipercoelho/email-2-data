# Conversational Intake — Solution Design v1

| Field | Value |
| --- | --- |
| Status | **Draft proposal — decisions R1–R9 + the R1/R6 confirmations incorporated (2026-06-16); LAN access = minimal single-user auth.** Three ADRs gate the build (§2, §11). |
| Date | 2026-06-16 |
| Responds to | [intake-bot-brief-v1.pdf](intake-bot-brief-v1.pdf) (external brief) |
| Builds on | [ADR-015 — knowledge-capture claim ledger](../03-decisions/adr-015-knowledge-capture-claim-ledger.md) |
| Precedent | `materials-costing` Telegram invoice-intake (same shop, same owner, same GCP project `materials-492723`) |
| Nature | Internal solution design. §11 records the owner decisions and the **residual** risks those decisions create — it does not pretend the risks are gone. |

## How to read this document

This is the engineering answer to the external brief, written **with** the repo access the brief
lacked, and now updated with the owner's decisions on every open risk. Two facts still frame it:

1. **Most of the brief's "ingestion subsystem" already exists** as the ADR-015 claim ledger. The bot
   is a **capture front-end on that ledger**, not a new store.
2. **A Telegram bot that does not break the loopback posture is already solved in production** by the
   `materials-costing` invoice bot. This design lifts that pattern.

The owner's decisions (§11) **expanded** the privacy envelope (Telegram transit and cloud processing
are signed off) and **tightened** the data-handling rule (everything is scrubbed from Telegram once
stored locally, and preserved in full on our side). Both are now first-class in the design (§5–§8).
Where a decision narrows a written invariant, that is flagged — not absorbed silently.

## 1. Purpose & success measure

Let workshop staff feed information captured away from the desk — a client phone call narrated into a
voice memo, a supplier quote photographed on the floor, a measurement, a decision from a meeting —
into the right project **at the moment they have it**, instead of memorising it or re-typing it later.

Built around the governing principle ([VISION.md](../../VISION.md)): **spend compute in proportion to
`uncertainty × business impact`**. Deterministic matching does the cheap, certain work; the model is
invoked only where a human cannot be matched without it; and **the user is the sole gatekeeper of what
reaches a project** (decision R9).

**Success measure (unchanged):** faster and lower-friction than the current path (open laptop → find
project → type), at data quality **at or above** manual entry. If staff find it slower or less
trustworthy than typing, it has failed regardless of how good the inference is.

## 2. Non-negotiables, and the two the owner is narrowing

Inherited invariants this design honours:

| # | Invariant | Source | Status under the owner decisions |
| --- | --- | --- | --- |
| N1 | Read-only IMAP, always | CLAUDE.md | **Held** — capture is additive, never touches fetch. |
| N2 | A capture is never silently dropped | non-negotiable #2 (analogue) | **Held** — unmatched captures are held in the queue (R7), never lost. |
| N3 | `workspace.db` is precious — never auto-rebuilt | CLAUDE.md | **Held, and reinforced** — after the scrub policy, our copy is the *only* copy (§7). |
| N4 | Secrets via `.env`/ADC; raw bodies/audio never logged | CLAUDE.md | **Held** — token in `.env`; transcripts/audio never logged. |
| N7 | Always-confirm; no machine invents a field address | ADR-015 §Decision-4 | **Held, and reinforced** — no auto-apply (R9). |

Two written invariants are **narrowed by an explicit owner decision**. These must be recorded as ADRs
that supersede the relevant clause — the project's traceability rule does not allow a silent contradiction:

| # | Written invariant | Owner decision | What must be recorded |
| --- | --- | --- | --- |
| **N5** | "Verbatim call/opinion content must not auto-ship to Vertex" (ADR-015 §Decision-4) | **R1 + R3 + R5**: Telegram transit approved; cloud transcription/extraction permitted | An ADR narrowing N5 **for the capture path**, scoped to the staffer's own dictation/artifacts, bound to the safety practices R5 names. |
| **N6** | "Port 8042, `127.0.0.1` loopback only" | **R6 (decided)**: webapp **LAN-bound** | An ADR relaxing *loopback-only* → **LAN-only, never public, behind a minimal single-user auth gate** (R11 decided 2026-06-16). "Never public" stays firm. |

## 3. What is reused vs net-new

| Capability | Status today | Where |
| --- | --- | --- |
| Off-email capture with `channel · asserted_by · acquired_at` provenance | **Exists** | [workspace.py](../../src/email2data/workspace.py) |
| Write a note / field to a project + per-project timeline | **Exists** — `/event`, `/field`, `/timeline` | [webapp.py](../../src/email2data/webapp.py) |
| Project model + lifecycle stage + owners roster | **Exists** | [project.py](../../src/email2data/project.py), `/api/roster` |
| pt-PT extraction vocabulary (field questions, purpose labels, gazetteer, human-corrected pairs) | **Exists** — the R2 seed | [jobspec.py](../../src/email2data/jobspec.py), [labels.py](../../src/email2data/labels.py), [triage_playbook.md](../../config/triage_playbook.md), `/api/reclassifications` |
| Long-poll worker · default-deny allowlist · idempotency on `(message_id, chat_id)` | **Pattern proven** in `materials-costing` | `_poller.py`, `_allowlist.py`, `telegram_intake.py` |
| **Content-class router + pluggable transcription/LLM engine** | **Net-new** | §5 |
| **Capture review queue (webapp "Caixa de Capturas") + no-link gatekeeping** | **Net-new** | §6, §8 |
| **Minimal single-user auth gate on the (now LAN-bound) webapp** | **Net-new** (R11) | §6 |
| **Scrub-from-Telegram-after-store + preserve-and-append-to-project (incl. media)** | **Net-new** | §7 |
| **Media/audio attached to a project event** | **Net-new** (was "Missing" in the turn-1 audit) | §7, §8 |

## 4. Architecture overview

A separate, co-deployed **worker process** shares the email2data package and writes through the same
store seam the CLI uses — **never through the HTTP API**, so 8042 needs no inbound exposure for the bot.

```text
  staff phone                 Telegram cloud           workshop box (single host, trusted LAN)
 ┌───────────┐  voice/text/  ┌──────────────┐ long-poll ┌────────────────────────────────────────┐
 │  Telegram │  photo/file   │  api.telegram│ ◀──────── │  email2data intake-bot (worker)          │
 │   client  │ ────────────▶ │     .org     │ getUpdates│   1 allowlist (default-deny)             │
 └───────────┘               └──────────────┘ ────────▶ │   2 download payload (text/audio/media)  │
       ▲   buttons only (no URL)  ▲   delete msgs        │   3 content-class route → transcribe     │
       │   T1/T2/T3 (bot→staff)   │   after store        │   4 DURABLY PERSIST full capture ────────┼─┐
       └──────────────────────────┴──────────────────────│   5 then SCRUB from Telegram (step ↑)    │ │
                                                          │   6 deterministic pre-filter → LLM if amb│ │
                                                          └──────────────────────────────────────────┘ │
                                       in-process store calls (no HTTP) │                                │
                                                                        ▼                                ▼
                              ┌──────────────────────────────────────────────────────┐   media/audio on disk
                              │  workspace.db (precious)  +  captures queue           │   captures/  (sole copy
                              │  +  capture_users allowlist                           │   now → backed up, §7)
                              └───────────────┬──────────────────────────────────────┘
                                              │ user validates in the app (gatekeeper, R9)
                                              ▼
                  email2data serve · LAN-only, never public · /projetos → "Caixa de Capturas"
                  (pending project edits await validation; nothing auto-applies)
```

## 5. The capture pipeline

`Capture → Normalise → Classify content → Infer project → Resolve → (user validates) → Apply`

### 5.1 Content-class router + pluggable engine (net-new)

The router survives, but its job has shifted. With Telegram transit (R1) and cloud processing (R5)
signed off, it is no longer a hard local/cloud privacy gate — it is a **routing + engine-selection +
handling** control:

| Class | Examples | Engine (pluggable — R3) | Notes |
| --- | --- | --- | --- |
| **ARTIFACT** | photographed quote, spec sheet, sketch, measurement card | Vertex (Gemini, `materials-492723`, `europe-west1`) **or** another contender | cloud signed off (R5), bound to the safety practices |
| **CONVERSATION** | staffer's **own** voice memo / dictation / typed note | local Whisper **or** Vertex / hosted Whisper (R3) | scope is the staffer narrating — **not** a covert recording of a client call (R1/Option B) |

**Engine is configuration, not a hardcoded choice (R3).** Candidates: Vertex Gemini (already
integrated, EU-resident, the path of least resistance), OpenAI Whisper (local model or hosted API), or
any strong, competitively-priced contender. The choice is bound to the R5 safety practices (EU
residency where applicable, no raw content in logs, scrub-after-store).

> **Confirmed (2026-06-16).** Per **R1 + Option B + R8**, the bot ingests the staffer's own text, voice
> memos and media artifacts — **not** deliberately-recorded client-call audio.

### 5.2 Project inference — iterative, seeded from the email corpus (R2)

R2 sets the method: **iterative, persona-first, bootstrapped from what we already know about how a
Portuguese sales person communicates** — not a cold-start model.

- **Seed (deterministic first).** A new editable `config/capture_playbook.md` — same pattern as the
  existing [triage_playbook.md](../../config/triage_playbook.md) / spec / reply playbooks (the project's
  "classifier brain is editable config, not code") — seeded from assets that already encode pt-PT
  business phrasing:
  - the pt-PT field questions in [jobspec.py](../../src/email2data/jobspec.py) (`"Para quando precisam?"`
    → `deadline`, `"Que espessura?"` → `thickness`) — these *are* the extraction targets, already in
    the language staff use;
  - the purpose / counterparty vocabulary in [labels.py](../../src/email2data/labels.py);
  - the [gazetteer](../../config/gazetteer.csv) (client/supplier names, the project-match priors);
  - the **human-corrected reclassification pairs** already exported at `/api/reclassifications` — real
    labelled signal from months of email.
- **Loop.** Start with keyword/phrase matching from the playbook; measure on real captures; promote a
  pattern to the LLM path only where deterministic matching is genuinely insufficient. The playbook is
  edited as the vocabulary grows — a behaviour change treated like a code change (test + doc).
- **Resolution branches** (unchanged): High (1 match ≥ 0.75) → associate + go to review; Partial (2–3)
  → inline buttons; Low (0) → reply with project № / pick from active list / **held** (lead-creation
  deferred per R7). A capture is never dropped (N2).

### 5.3 Stakes split (what the user validates, R9)

- **Free-text knowledge** (NOTE/DECISION/OPINION/TODO) → stored **verbatim**, `tier=context`, never
  touches the estimable gate. On a **certain or user-confirmed** project it can be confirmed with a
  single tap.
- **Structured field values** (e.g. `deadline`, `material#0`) → may be **LLM-suggested** but **always**
  queue as a *pending project edit* the user validates field-by-field in the webapp **before** it
  touches `project_fields` (R9). No auto-apply, ever.

## 6. Privacy & security model (from `materials-costing`)

1. **No inbound door — long-poll, not webhook.** Outbound `getUpdates`; persisted offset; skip-backlog
   on boot; exponential backoff. **No port opened.**
2. **Default-deny allowlist as identity.** `capture_users` keyed by numeric `telegram_user_id`
   (`enabled` soft-disable; `added_by/at` audit). Unknown/disabled → rejected, logged, admin pinged.
   Maps each sender to a roster owner (`/api/roster`) for `asserted_by` attribution. This is
   email2data's first identity model.
3. **In-process worker, not an external API client.** The bot writes via the store seam, never via
   8042.
4. **Idempotency on `(telegram_message_id, telegram_chat_id)`** (unique constraint). A Telegram retry
   is a no-op — important because the store is precious.
5. **Secrets + signed egress.** Token in gitignored `.env`, never logged; rotation via BotFather
   `/revoke`. Cloud egress is **signed off (R5)**, conditional on the safety practices named here, and
   should be recorded as an ADR scoped to the capture path (the `materials-costing` ADR-075 §3.2
   pattern: owner accepts in writing).
6. **No HTTP links leave the chat (R6).** The bot never sends a navigable URL. Project selection uses
   Telegram-native inline buttons (a callback, not a link); field validation happens in the webapp the
   user opens themselves. The webapp is **LAN-bound behind a minimal single-user auth gate** (R6/R11) —
   never public; the in-process worker bypasses the gate by design (it writes via the store seam, not HTTP).

## 7. Data-handling lifecycle — minimise at the edge, preserve at the core (new requirement)

The owner's added requirement defines a clear, two-sided rule. It is now a first-class invariant of the
design.

**7.1 Minimise at the edge — scrub Telegram (and any transport) once stored.**

- **Persist-then-scrub, in that order.** On receipt the worker downloads the full payload (text, audio
  bytes, media files), transcribes, and **durably persists** the complete capture locally. **Only after
  durable persistence is confirmed** does it delete the source from Telegram: the user's original
  message(s) **and** any bot message that echoes captured content.
- The scrub happens at **queue-persist time, not at apply time** — so the chat is clean even before the
  user gets to validate. (The data is already safe in our queue.)
- Deletion is **retried until confirmed and logged**; a periodic reconciliation sweep removes any
  straggler. The same persist-then-scrub rule applies to any future transport ("any other data
  transmission source").

**7.2 Preserve at the core — full fidelity, appended to the project for audit.**

- The complete capture is retained on our side: **verbatim text, transcript, the original audio file,
  media files, and all provenance**. Nothing is summarised away.
- Once the user selects and validates the project, the capture is **appended to that project's ledger**:
  the text/transcript as an ADR-015 event in `project_field_history` (with `channel · asserted_by ·
  acquired_at`), and the **audio/media attached to that event** for future reference and audit.
  - ⚠️ Attaching media/audio to a project event is **net-new** — the turn-1 audit found no media-to-
    project capability today. This is now a **required** build item, not optional.

**7.3 The consequence that must not be missed.**
Because we scrub Telegram, **Telegram is no longer a fallback copy** — our local store becomes the
**sole** copy of the audio/media. It is therefore precious (N3) and **must be in the backup set**.
(Note: this is the *opposite* of `materials-costing`, which could exclude intake files from backup as
"re-fetchable from Telegram." We cannot.) Media/audio live on disk under `captures/` with references in
the DB; backups must include that directory.

**7.4 The residual we cannot engineer away (honest disclosure — R10).**
Deleting a message removes it from the Telegram **chat surface** and dereferences the CDN file, but we
**cannot guarantee erasure from Telegram's server-side infrastructure or backups**, and Bot-API
deletion is bounded (private chats; ~48-hour window). If the worker is down > 48 h, older messages may
become undeletable and need manual clean-up. This residual is inherent to using Telegram as transport
and is the price of the R1 sign-off — it is disclosed, not solved.

## 8. Data-model additions

Two new tables; **no change** to ADR-015 provenance columns (reused on apply). Media/audio on disk.

```text
capture_users                                  -- allowlist / identity
  telegram_user_id  INTEGER PRIMARY KEY
  display_name      TEXT     -- greeting + maps to roster owner (asserted_by)
  roster_owner      TEXT
  enabled           INTEGER  -- soft-disable
  added_by, added_at TEXT

captures                                       -- review queue (pending project edits, R9)
  capture_id            TEXT PRIMARY KEY
  telegram_message_id   INTEGER
  telegram_chat_id      INTEGER
  UNIQUE(telegram_message_id, telegram_chat_id)         -- idempotency
  content_class         TEXT  -- artifact | conversation
  raw_text              TEXT  -- verbatim
  transcript            TEXT  -- local/engine transcription
  media_paths           TEXT  -- on-disk refs (sole copy → backed up, §7.3)
  inferred_project_id   TEXT  -- nullable until resolved
  confidence            REAL
  extracted_fields_json TEXT  -- suggestions, NOT applied until validated
  channel               TEXT  -- real-world channel (call|meeting|whatsapp|sms|manual)
  asserted_by, acquired_at TEXT
  status                TEXT  -- received | stored | parsed | needs_clarify | validated | applied | failed | discarded | duplicate
  telegram_scrubbed_at  TEXT  -- when source deleted from Telegram (§7.1)
  created_ts, applied_ts TEXT
```

On apply, the note/field flows into the existing `project_fields` / `project_field_history` via
`add_event` / `set_field`; the `captures` row is a staging record + audit trail, never a second source
of truth (N3).

## 9. Process & communication

### 9.1 Runtime choreography (no links — R6; user is gatekeeper — R9)

All user-facing strings pt-PT (English here is annotation).

```text
STAFF → BOT   voice memo / text / photo / file
  └─ allowlist → unknown: "Não estás autorizado a usar este canal."  (logged; admin pinged)   ▢ N2-safe
                 authorised ↓
BOT → STAFF  T1   "📥 Recebido. A guardar…"                     → download payload
  └─ DURABLY PERSIST full capture (text+transcript+audio+media)  → status = stored
  └─ SCRUB from Telegram: delete the user's message(s)           → telegram_scrubbed_at  (§7.1)
  ─ content-class route → transcribe → deterministic pre-filter → LLM only if ambiguous
BOT → STAFF  T2   (inline buttons, no URL) — three branches:
   HIGH    "✅ Associei a *Estante Inox — Padaria Sousa (p-0123)*. Notei: prazo 30/06, inox 304.
            Para validar, abre a Caixa de Capturas na app."        → pending edit queued
   PARTIAL "A que projeto pertence?  [Padaria Sousa] [Café Central] [Outro]"   (buttons → callback)
   LOW     "Não consegui associar. Responde com o nº do projeto ou escolhe da lista ativa."
            (lead-creation deferred, R7 → otherwise the capture is HELD, never dropped)
USER (in the app, on the LAN)  /projetos → "Caixa de Capturas"  (queue of pending project edits)
   • verbatim note on a certain project → one-tap confirm
   • extracted field values            → validate field-by-field → Apply   → set_field/add_event
   (nothing is written to a project until the user validates — R9)
BOT → STAFF  T3   "✔ Aplicado ao projeto Padaria Sousa."   (content-free receipt; throwaway client)
ADMIN ← BOT  out-of-band: unknown sender; processing failure (opaque id, no content)
```

### 9.2 Build & decision process (how the work proceeds)

1. **Record the two supersessions (§2) as ADRs** before build: the N5 narrowing (signed-off cloud
   capture path) and — if cross-device review is wanted — the N6 relaxation (LAN-only). Plus the R5
   egress ADR.
2. **Pick the engine (R3)** and, for CONVERSATION, confirm hardware headroom if local
   (`standards/01-hardware-baseline.md`).
3. **Phased build (§10)**, each phase clearing Definition of Done (§12) in the same commit.
4. **Iterate the playbook (R2)** on real captures; widen the LLM path only against measured need.
5. **Stop-and-report** on any ambiguity in a classification rule or source of truth (CLAUDE.md).

## 10. Phased rollout

| Phase | Scope | New risk |
| --- | --- | --- |
| **0 · Align** | ADRs for N5/N6/egress; engine choice; hardware check | none (no code) |
| **1 · Safe transport + scrub** | minimal auth gate + LAN-bind the webapp; long-poll + allowlist + T1; staffer **names the project**; capture stored **verbatim** + media; **scrub-after-store**; preserve+append on confirm | the bot + auth gate + scrub/backup discipline (§7.3) |
| **2 · Deterministic resolve** | project pre-filter (playbook + gazetteer); user confirms association | wrong-match (mitigated: confirm) |
| **3 · LLM inference** | inference for ambiguous cases, behind thresholds + "none of these"; playbook loop (R2) | **inference accuracy** — measured before widening |
| **4 · Field extraction** | LLM-suggested fields → pending-edit queue → field-by-field validation (R9) | **gate-affecting writes** (highest impact) |
| ~~5 · Lead creation~~ | **deferred (R7)** | — |

> Phase 1 alone delivers capture-at-source into the ledger with the full data-handling guarantee and
> **zero inference risk** — the proven-safe parts ship first; the novel LLM parts are isolated later.

## 11. Decisions taken, and the residual risks they create

Owner decisions of 2026-06-16, with the risks that remain *after* each decision (impartiality: a
sign-off resolves a question, it does not always remove the risk).

| Ref | Decision | Residual risk to manage |
| --- | --- | --- |
| **New** | Scrub from Telegram (and any transport) once stored; preserve everything and append to the project | **R10**: Telegram backend retention is unguaranteeable; 48 h / private-chat deletion limits (§7.4). **Sole-copy** media → must be backed up (§7.3), else data loss. |
| **R1** | Telegram transit **approved**; **Option B** scope | **Confirmed (2026-06-16)**: staffer's own dictation/artifacts, **not** covert client-call recordings. Narrows **N5** → needs an ADR (§2). |
| **R2** | Iterative, persona-seeded from email vocabulary | Cold-start accuracy is low by design; needs the measured playbook loop and a "good-enough" bar before Phase 3 widens. |
| **R3** | Engine pluggable: Vertex / Whisper / other | Choice still open; if local transcription is chosen, **hardware headroom unverified** (was R3). |
| **R4** | SQLite + WAL + single app-level write lock | Accepted; low risk at single-user volume. Background writer must respect the lock. |
| **R5** | Cloud egress **signed off**, conditional on the safety practices | Must record the egress ADR; the conditions (EU residency, no raw content in logs, scrub-after-store) are now binding, not optional. |
| **R6** | **No links**; webapp **LAN-bound** | **R11 decided (2026-06-16): minimal single-user auth gate** before the webapp (net-new; the materials-costing precedent). Relaxes **N6** → LAN-only, never public (ADR). The in-process worker bypasses the gate by design. |
| **R7** | Lead/project creation **deferred** | Captures with no matching project are **held** in the queue (N2); the user creates the project manually in the app. Make the holding state visible so nothing rots unseen. |
| **R8** | v1 handles text + audio + media; other scenarios ignored | Media groups / edited / forwarded messages are **out** — must fail safe (acknowledge + hold), not mis-handle. |
| **R9** | **No auto-apply**; user is the sole gatekeeper, in chat or the webapp pending-edits inbox | The pending-edits queue must be obvious and low-friction, or captures pile up unvalidated and the success measure (§1) is lost. |

**Both gating confirmations are resolved (2026-06-16):** R1 reading confirmed (staffer's own
dictation/artifacts, not client-call recordings); R6 = webapp **LAN-bound behind a minimal single-user
auth gate**. The only build-time choice still open is the engine (R3 — Vertex / Whisper / other),
deferrable to Phase 0. These three invariant-touching decisions are now recorded as
[ADR-019](../03-decisions/adr-019-conversational-intake-capture-adapter.md) ·
[ADR-020](../03-decisions/adr-020-capture-egress-and-data-handling.md) ·
[ADR-021](../03-decisions/adr-021-intake-lan-binding-minimal-auth.md) (Proposed). The MVP build plan is
[intake-bot-mvp-plan-v1](intake-bot-mvp-plan-v1.md).

## 12. Out of scope for v1

- Lead/project creation from a capture (R7, deferred) — unmatched captures are held, not created.
- Auto-apply of any gate-affecting field (R9) — the user validates everything.
- Media groups, edited/forwarded messages, multi-project single captures (R8) — fail safe and hold.
- Any inbound webhook or **public** exposure of 8042 ("never public" stays firm even under R6).
- Editing/deleting existing project data through the bot — capture is additive only.
- Languages beyond pt-PT and technical English.

## 13. Definition of done (per phase)

Per [CLAUDE.md](../../CLAUDE.md), every L2+ phase ships in the same commit with:

1. **Test** — a fail-before/pass-after regression in the matching `tests/test_*.py`: allowlist gate,
   idempotency no-op, **persist-then-scrub ordering**, **scrub-failure retry**, **auth gate rejects an
   unauthenticated webapp request**, "never dropped" on a no-match, content-class routing. No stubs, no
   `pytest.skip`.
2. **Docs** — the accepted decisions promoted to ADRs (N5 narrowing, R5 egress, optional N6 relaxation);
   reference/contract updates; the new `config/capture_playbook.md` — same commit.
3. **QA self-review** — ruff; edge cases (empty/None/malformed media, oversized/again-sent audio,
   non-ASCII); the non-negotiables (§2); idempotency; **and a backup check that `captures/` is in the
   backup set** (§7.3).

---

*Owner decisions R1–R9, the scrub/preserve requirement, and the R1/R6 confirmations incorporated
2026-06-16 (LAN access = minimal single-user auth). The three invariant-touching decisions are recorded
as ADR-019/020/021 (Proposed); the MVP build plan is intake-bot-mvp-plan-v1.md. This document decides
nothing on its own — it is the
engineering reading of the brief against production, with `materials-costing` and ADR-015 as
load-bearing precedents.*
