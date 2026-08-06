# Reference — triage verdict schema

| Field | Value |
| --- | --- |
| Type | Reference |
| Status | Active |
| Source of truth | `src/email2data/schema.py` |
| Last reviewed | 2026-06-10 |

The single source of truth is `schema.py` — this page mirrors it for discoverability. If the
code and this page disagree, **the code wins and this page is stale; fix it** (same commit as
the code change).

## The axes

| Axis | Set by | Meaning |
| --- | --- | --- |
| **counterparty** | LLM (body) | WHO, from Lindo's POV ([ADR-003](../03-decisions/adr-003-counterparty-from-body-not-domain.md)) |
| **purpose** | LLM (body) | WHAT business-object the message is about |
| **speech_act** | LLM (body) | what the message DOES to the reader — orthogonal to the above ([ADR-036](../03-decisions/adr-036-speech-act-obligation-fila.md)) |
| **direction** | `signals.py` (header fact) | who SENT it ([ADR-004](../03-decisions/adr-004-direction-by-recipient-not-sender.md)) |
| **priority** | code (`derive_priority`) | derived from the above + urgency + bulk |
| **obligation** | code (`cockpit.derive_obligation`) | the thread's NEXT MOVE, folded from the last decisive `speech_act × direction × purpose`; NAMES the Fila group ([ADR-036](../03-decisions/adr-036-speech-act-obligation-fila.md)) |

## Vocabularies (enums — the single source of truth)

**`COUNTERPARTY`** = `CLIENT`, `LEAD`, `SUPPLIER`, `INTERNAL`, `BULK`, `OTHER`
- `CLIENT` buys from us (revenue); `LEAD` prospective client not yet buying; `SUPPLIER` we buy
  from them (incl. tool/service vendors — "we are the client of X" ⇒ X is SUPPLIER);
  `INTERNAL` colleague @lindoservico.pt; `BULK` newsletter/marketing; `OTHER` none of these.

**`PURPOSE`** = `PO_FROM_CLIENT`, `ESTIMATE_REQUEST_FROM_CLIENT`, `OUTBOUND_INVOICE` (an invoice
*we* issue to a client — counterparty stays CLIENT), `OUTBOUND_QUOTE` (a quote/proposal *we* send,
awaiting the client's decision — **not** billing; ADR-036 Stage 1), `OUR_ORDER_TO_SUPPLIER`,
`SUPPLIER_REPLY_OR_CONFIRMATION`, `SUPPLIER_INVOICE` (an inbound bill *we* must **pay** — obligation-to-pay,
ADR-036 Stage 1 Bug 2), `INVOICE_OR_ACCOUNTING` (other accounting mail — receipt/statement, **not** a
payable bill), `FOLLOW_UP` (an active chase on something already sent), `OWN_REJECTION`, `CLIENT_REJECTION`,
`PUBLICITY`, `INTERNAL_OPS`, `OTHER`.

**`direction`** = `inbound`, `internal` (our domain → our domain), `outbound` (Sent folder) —
all three are in the `DIRECTION` constant (`schema.py:48`), pinned by
`tests/test_signals.py::test_schema_direction_constant_covers_every_emitted_value`.

**`SPEECH_ACT`** (ADR-036, orthogonal to purpose) = `ASK` (requests an action/answer) · `OBLIGATION`
(imposes a duty: pay/do-by-a-date) · `FYI` (informational, no move) · `ACK` (acknowledges, nothing
pending) · `CLOSE` (explicitly ends the thread) · `UNKNOWN` (unclear — **preferred over a guess**).
The thread `obligation` folds from the last decisive act: inbound `ASK`→`OWE_REPLY`, outbound
`ASK`→`AWAIT_THEM`, inbound `OBLIGATION` on a bill→`OWE_PAYMENT`, outbound `OBLIGATION` on our
invoice→`COLLECT`, `ACK`/`CLOSE`→`RESOLVED`, `FYI`→`INFO`; an **inbound** `FYI`/`UNKNOWN` never
overrides a live move. **[ADR-051](../03-decisions/adr-051-a-reply-we-can-see-discharges-an-owed-reply.md):
any `outbound` message after the decisive one discharges an owed REPLY** (`OWE_REPLY`→`AWAIT_THEM`),
whatever act our own mail was given — the fact that we replied beats an inference about what the reply
meant. `internal` does not count (a forward to a colleague is not an answer to the client), and
`OWE_PAYMENT` is never discharged this way: an email does not pay a bill.
Fila groups: `OWE_REPLY`→«Precisam de resposta», `OWE_PAYMENT`→«A pagar», `COLLECT`→«A cobrar»,
`AWAIT_THEM`→«A aguardar»/«À espera deles» (by chase band), `FYI`→«Informações». Before a user-run
`triage --full` populates `speech_act`, `cockpit._legacy_obligation` reproduces the deterministic routing.

**`PRIORITIES`** = `HIGH`, `MEDIUM`, `LOW`, `IGNORE`, `NEEDS_REVIEW`.

## Priority derivation (deterministic — not a model output)

`derive_priority(counterparty, purpose, urgency, is_bulk)` (`schema.py:54`):

1. `is_bulk` or `counterparty == BULK` → **`IGNORE`**
2. `counterparty ∈ {CLIENT, LEAD}` or `purpose ∈ {PO_FROM_CLIENT, ESTIMATE_REQUEST_FROM_CLIENT}` → **`HIGH`** (the high-value, never-bin case)
3. `purpose ∈ {FOLLOW_UP, OUTBOUND_QUOTE, OUR_ORDER_TO_SUPPLIER}` (awaited-outbound) → **`LOW`** (initial; Phase-4 timer escalates over time)
4. else → **`HIGH`** if `urgency ≥ 70`, otherwise **`MEDIUM`** (a `SUPPLIER_INVOICE` is `MEDIUM`, `HIGH` near its due date)

Coherence sets: `IGNORABLE_COUNTERPARTIES = {BULK, OTHER}` (anything else marked IGNORE is
incoherent → `NEEDS_REVIEW`); `HIGH_VALUE_COUNTERPARTIES = {CLIENT, LEAD}`;
`AWAITED_OUTBOUND_PURPOSES = {FOLLOW_UP, OUTBOUND_QUOTE, OUR_ORDER_TO_SUPPLIER}`;
`CLOSING_PURPOSES = {CLIENT_REJECTION, OWN_REJECTION}`.

An inbound `SUPPLIER_INVOICE` folds to a distinct cockpit state **`TO_PAY`** («A pagar») — we owe a
**payment**, not a reply (ADR-036 Stage 1 Bug 2) — gated on the *new* purpose so pre-re-triage threads
keep their current routing. This is a verdict-schema change: `EXTRACTOR_VERSION` bumped to
`counterparty.2026-07-25.v5`; the new purposes appear only after a user-run `triage --full`.

`CLOSING_PURPOSES` self-close the thread when it is the LAST move, from **either** side — the client's
thank-you/decline (`CLIENT_REJECTION`, inbound) or **our** definitive refusal (`OWN_REJECTION`, outbound).
The Fila auto-resolves both to `HANDLED` (a new inbound with a different purpose reopens it). `OWN_REJECTION`
moved here from `AWAITED_OUTBOUND_PURPOSES` in **ADR-036 Stage 0** (a job we declined was reading as an
outstanding «A cobrar»); `derive_priority` is unchanged (both sets map to `LOW`), so no re-triage is needed.

## `TriageResult` (one per message, appended to `out/results.jsonl`)

`message_id`, `counterparty`, `purpose`, `direction`, `priority`, `urgency` (0–100),
`confidence` (0.0–1.0), `reason`, `speech_act` (one of `SPEECH_ACT`, default `UNKNOWN`),
`entities` (see below), `extractor_version`, and provenance:
`subject`, `from_addr`, `decided_by` ([ADR-008](../03-decisions/adr-008-every-verdict-records-decided-by.md)).

`decided_by` values: `tier0:<signal>` (offline header IGNORE), `tier1:<model>` (LLM classification),
and `tier1:error` — a Tier-1 failure (e.g. LLM/auth down) that **escalated** the message to
`NEEDS_REVIEW` instead of dropping it, so client mail never silently vanishes from the Fila; clear it
with `triage --full` once the LLM is back ([ADR-016](../03-decisions/adr-016-post-audit-resilience-hardening.md)).

**`Entities`**: `client_name`, `client_email`, `deadline` (ISO `YYYY-MM-DD`, or `YYYY-MM-DDTHH:MM`
when a time of day was stated — see [Editor input types](#editor-input-types-jobspecinput_type)), `money`,
`product_or_service`, `action_requested` — drafted by the LLM, nullable; plus `nif` (PT taxpayer
id, 9 digits, mod-11 valid) and `iban` — filled **deterministically** by `extract.py`,
checksum-validated, authoritative ([ADR-007](../03-decisions/adr-007-nif-iban-authoritative-rest-candidates.md)).

## Structured-output contracts

The model emits `counterparty / purpose / urgency / confidence / reason / entities`; code adds
`direction` + `priority`. Two provider shapes are kept for parity:
- `TRIAGE_TOOL` — Anthropic forced tool (`input_schema`, min/max constraints).
- `GEMINI_TRIAGE_SCHEMA` — Vertex controlled-generation OpenAPI subset (nullable via `"nullable"`,
  no min/max).

## Versioning

`EXTRACTOR_VERSION` (currently `counterparty.2026-07-25.v5`) — **bump whenever the playbook or
this schema changes verdicts**, so re-runs stay comparable and the Phase-4 verdict cache
invalidates correctly. `v5` (ADR-036) added the `FOLLOW_UP`→`OUTBOUND_QUOTE` split, `SUPPLIER_INVOICE`,
and the orthogonal `speech_act` axis; populating them needs a user-run `triage --full`.

## Phase B — job-spec draft schema

A second, tiered pass (LEAD / PO only) drafts the fabrication spec **stated in the body**, all
nullable, model told to return `null` not guess (the spec is often in an unreadable attachment).
Per-piece fields are a **list** of line items: `SPEC_ITEM_KEYS` = `item, material, dimensions,
thickness, quantity, colour_finish`; job-level `SPEC_JOB_KEYS` = `material_supplied_by`
(coerced to `client|us|unclear|None`), `delivery`. `process` is internal — the LLM never drafts it.

### Editor input types (`jobspec.INPUT_TYPE`)

Spec values are stored as plain strings whatever the editor looks like. `INPUT_TYPE` maps a field
key to the HTML input the Projetos workbench renders; anything unlisted is free text. Today:
`deadline → datetime-local`, so `prazo` offers a native calendar + clock.

**`deadline` accepts two stored shapes**, both first-class:

| Shape | Written by | Renders as |
| --- | --- | --- |
| `2026-09-02` | `extract.py`, the LLM when no hour was stated, every pre-clock deadline | picker, widened to `T00:00` for display |
| `2026-09-02T14:30` | the LLM when the client named an hour; the user picking a time | picker, verbatim |
| anything else (`meados de agosto`) | LLM/user free text | **text input, value shown verbatim** |

Two invariants the renderer must keep — both are load-bearing, and both have a regression test:

1. **Never hide a stored value the widget can't display.** A picker can only hold a value it can
   parse; given anything else the browser renders an *empty* box, which on a required field reads as
   "no deadline" when one exists. So `inputType()` (`projetos_page.py`) degrades to a text input for
   any unparseable value. Applies to every future `INPUT_TYPE` entry, not just this one.
2. **Display widening must not reach the store.** A `datetime-local` input cannot hold a bare date,
   so `pickerValue()` widens `2026-09-02` → `2026-09-02T00:00` *for rendering only*. That midnight is
   invented; nobody stated it. It is never written back — no `change` event fires unless the user
   actually edits the field, and only a real edit persists a time.

The LLM side of the contract lives in `config/triage_playbook.md` §entities, which instructs the
model to use the longer shape **only** when the message states a time of day — never to invent an
hour to fill it.

## The entities are NOT where evidence lives (ADR-054)

A recurring temptation, rejected twice and now decided: **do not add an evidence/quote field to the
triage schema.** It would land in `_ENTITY_PROPS_NULLABLE`, which feeds **both** provider contracts,
so it changes verdicts and demands an `EXTRACTOR_VERSION` bump — the corpus-split failure the
roadmap already records.

The sentence justifying a value is produced by a **separate call**
([ADR-054](../03-decisions/adr-054-llm-derived-body-fragments-live-in-out-sidecars.md), `locate.py`)
that receives the already-extracted values and returns only quotes. It cannot change a verdict, and
the context cache is keyed on `(model, sha256(system_instruction))`, so its prompt can never collide
with `triage_playbook.md`'s cached prefix.

Where the quote ends up: `out/evidence.jsonl`, keyed by `message_id`, and it reaches the UI riding
**inside** each entry of `/api/thread`'s existing `facts` list — never as a field on `TriageResult`
and never in `results.jsonl`, which is body-free by contract. The stored quote is the email's own
text at the matched span, not the string the model typed.
