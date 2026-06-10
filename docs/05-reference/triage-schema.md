# Reference — triage verdict schema

| Field | Value |
|---|---|
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

**`direction`** = `inbound`, `internal` (our domain → our domain), `outbound` (Sent folder).
> ⚠️ **Known discrepancy (2026-06-10):** `signals.py` emits all three values
> (`signals.py:72` sets `"outbound"`), but the `DIRECTION` constant at `schema.py:42` lists only
> `["inbound", "internal"]`. The emitted set is authoritative; the constant is stale. Tracked for
> a code fix — not corrected here (docs change).

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

**`Entities`**: `client_name`, `client_email`, `deadline` (ISO `YYYY-MM-DD`), `money`,
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
