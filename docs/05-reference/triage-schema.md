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

## The four axes

| Axis | Set by | Meaning |
| --- | --- | --- |
| **counterparty** | LLM (body) | WHO, from Lindo's POV ([ADR-003](../03-decisions/adr-003-counterparty-from-body-not-domain.md)) |
| **purpose** | LLM (body) | WHAT the message is doing |
| **direction** | `signals.py` (header fact) | who SENT it ([ADR-004](../03-decisions/adr-004-direction-by-recipient-not-sender.md)) |
| **priority** | code (`derive_priority`) | derived from the above + urgency + bulk |

## Vocabularies (enums — the single source of truth)

**`COUNTERPARTY`** = `CLIENT`, `LEAD`, `SUPPLIER`, `INTERNAL`, `BULK`, `OTHER`
- `CLIENT` buys from us (revenue); `LEAD` prospective client not yet buying; `SUPPLIER` we buy
  from them (incl. tool/service vendors — "we are the client of X" ⇒ X is SUPPLIER);
  `INTERNAL` colleague @lindoservico.pt; `BULK` newsletter/marketing; `OTHER` none of these.

**`PURPOSE`** = `PO_FROM_CLIENT`, `ESTIMATE_REQUEST_FROM_CLIENT`, `OUTBOUND_INVOICE` (an invoice
*we* issue to a client — counterparty stays CLIENT), `OUR_ORDER_TO_SUPPLIER`,
`SUPPLIER_REPLY_OR_CONFIRMATION`, `INVOICE_OR_ACCOUNTING`, `FOLLOW_UP`, `PUBLICITY`,
`INTERNAL_OPS`, `OTHER`.

**`direction`** = `inbound`, `internal` (our domain → our domain), `outbound` (Sent folder) —
all three are in the `DIRECTION` constant (`schema.py:42`), pinned by
`tests/test_signals.py::test_schema_direction_constant_covers_every_emitted_value`.

**`PRIORITIES`** = `HIGH`, `MEDIUM`, `LOW`, `IGNORE`, `NEEDS_REVIEW`.

## Priority derivation (deterministic — not a model output)

`derive_priority(counterparty, purpose, urgency, is_bulk)` (`schema.py:54`):

1. `is_bulk` or `counterparty == BULK` → **`IGNORE`**
2. `counterparty ∈ {CLIENT, LEAD}` or `purpose ∈ {PO_FROM_CLIENT, ESTIMATE_REQUEST_FROM_CLIENT}` → **`HIGH`** (the high-value, never-bin case)
3. `purpose ∈ {FOLLOW_UP, OUR_ORDER_TO_SUPPLIER}` (awaited-outbound) → **`LOW`** (initial; Phase-4 timer escalates over time)
4. else → **`HIGH`** if `urgency ≥ 70`, otherwise **`MEDIUM`**

Coherence sets: `IGNORABLE_COUNTERPARTIES = {BULK, OTHER}` (anything else marked IGNORE is
incoherent → `NEEDS_REVIEW`); `HIGH_VALUE_COUNTERPARTIES = {CLIENT, LEAD}`;
`AWAITED_OUTBOUND_PURPOSES = {FOLLOW_UP, OUR_ORDER_TO_SUPPLIER}`.

## `TriageResult` (one per message, appended to `out/results.jsonl`)

`message_id`, `counterparty`, `purpose`, `direction`, `priority`, `urgency` (0–100),
`confidence` (0.0–1.0), `reason`, `entities` (see below), `extractor_version`, and provenance:
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

`EXTRACTOR_VERSION` (currently `counterparty.2026-05-29.v3`) — **bump whenever the playbook or
this schema changes verdicts**, so re-runs stay comparable and the Phase-4 verdict cache
invalidates correctly.

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
