# ADR-036 — The Fila groups by a folded obligation, not by a response clock

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-07-25 |
| Extends | [ADR-007](adr-007-nif-iban-authoritative-rest-candidates.md) (FACT/INFERENCE/UNKNOWN) |
| Refines | [ADR-029](adr-029-fila-groups-by-obligation.md) (Fila groups by obligation — now literally true), [ADR-033](adr-033-fila-mesa-com-foco.md) (Mesa) |
| Supersedes | the «N a cobrar» hero-card copy in [ADR-034](adr-034-fila-chrome-fronts-and-scoped-rail.md) |

## Context

The owner, looking at the Fila: an inbound message about a **cost-proposal for a service we were
asked to execute** was rendered under the group label **«A cobrar»** (to collect money owed). Nobody
owed us anything — we were awaiting the client's decision on our quote. The label lied.

Checking the framing before building — the complaint is one mislabel, but the audit found the deeper
fault: **the Fila names its groups from a response *clock*, never from what the message asks.**

- `fila_page.semGroup()` chose the group from `clock.state × clock.band` only — never from the
  message's meaning. `clock.state` (`WE_OWE`/`AWAITING`) is a proxy for **direction**; `clock.band`
  is **age**. So «A cobrar» was emitted the instant *any* thread had been `AWAITING` for ≥72 h —
  nothing about money, an invoice, or a debt was ever consulted (`cockpit.py:_AWAITING_CHASE_H`).
- The playbook funnelled **every** outbound proposal/quote to a client into `purpose=FOLLOW_UP`, and
  `FOLLOW_UP ∈ AWAITED_OUTBOUND_PURPOSES`. So sales proposals sat `AWAITING` and each crossed into
  «A cobrar» on its third day. The live queue told the shop to "go collect payment" on ~28 threads
  where no money was owed.
- **Bug 1:** a job we **declined** (`OWN_REJECTION`, outbound) matched "we replied last" → `AWAITING`
  → amber → «A cobrar». A refused job showed as an outstanding collection.
- **Bug 2:** an inbound supplier bill ("segue fatura, vencimento 30/07") arrived inbound-last →
  `WE_OWE` → «Precisam de resposta». The queue said we owed a *reply* when we owed a *payment*.
- `entities.action_requested` — which already held the real ask — was extracted, displayed as
  «Pedem:», and then **discarded for every routing decision**. Direction ≠ obligation.

## Decision

**A message's speech-act, folded to a thread obligation, names the Fila group. The clock only colours
urgency and sorts within a group.** Landed in three independently-green stages.

1. **A new orthogonal axis, `speech_act`** — what the message *does* to the reader, independent of
   counterparty/purpose/direction: `ASK` / `OBLIGATION` / `FYI` / `ACK` / `CLOSE` / `UNKNOWN`. It is a
   forced field on `TRIAGE_TOOL` + `GEMINI_TRIAGE_SCHEMA`; `UNKNOWN` is first-class and **preferred
   over a confident-wrong act** (ADR-007). `action_requested` stays as the free-text *evidence* behind
   an `ASK`/`OBLIGATION`.
2. **`derive_obligation()`** folds a thread from its last **decisive** message's
   `speech_act × direction × business-object`: an inbound `ASK` → `OWE_REPLY`; an outbound `ASK`/quote
   → `AWAIT_THEM`; an inbound `OBLIGATION` on a bill → `OWE_PAYMENT`; an outbound `OBLIGATION` on our
   invoice → `COLLECT`; `ACK`/`CLOSE` (either side) → `RESOLVED`; `FYI` → a quiet `INFO` pile.
   `FYI`/`UNKNOWN` never override a live move.
3. **The clock is demoted.** `thread_clock` derives the obligation, maps it to a response state, and
   the band/age only colour + sort **within** a group — it never names one. Grouping is computed in
   Python (`cockpit.fila_group`, one source of truth); the JS renders `row.group`.
4. **Genuine «A cobrar».** The word is reserved for `COLLECT` — our unpaid `OUTBOUND_INVOICE`. A
   proposal awaiting a decision is **«A aguardar»** (`G_CHASE`); an inbound bill is **«A pagar»**
   (`G_PAY`, `OWE_PAYMENT`); a notification is **«Informações»** (`G_INFO`). `OWN_REJECTION` and
   `CLIENT_REJECTION` both self-close.
5. **Graceful degradation.** `speech_act` is populated only by a user-run `triage --full` (a Tier-1
   token cost). Until then, `_legacy_obligation()` reproduces the deterministic Stage 0/1 routing, so
   a pre-re-triage `crm.db` still groups correctly. `EXTRACTOR_VERSION` → `counterparty.2026-07-25.v5`;
   `crm` `SCHEMA_VERSION` → 5 (regenerable; the column is `NULL` on the old DB → legacy fold).

### Why not the alternatives

- **Relabel only** (rename «A cobrar» → «A aguardar»): fixes the one word for all rows today (Stage 0
  ships exactly this), but leaves the group clock-named — it does not make the pile act-driven, so an
  inbound bill still reads as "owe a reply".
- **Split `FOLLOW_UP` only** (Stage 1): distinguishes "quote pending" from "invoice unpaid" and gives
  bills their own «A pagar», but obligation is still inferred from purpose+direction, not the ask.
- The full axis is the only option that partitions the queue by *what kind of action is demanded of
  me* — the classification originally requested.

## Consequences

- «A cobrar» can now only ever mean money we invoiced and are owed. Bug 1 (declined job) and Bug 2
  (inbound bill) are fixed structurally, as is "obrigado, recebido" staying open forever (`ACK` →
  resolved). A notification no longer inflates «Precisam de resposta».
- **The re-triage is a user step.** The new fields are absent until `triage --full` runs; the Fila is
  correct-but-not-act-refined until then (legacy fold). This honours the governing principle — spend
  Tier-1 tokens deliberately, not as a side effect of a deploy.
- `speech_act` is durable — it survives into any later refinement, and `action_requested` finally
  earns its keep as the ask's evidence.
- The obligation is derived server-side; if the API stopped emitting it, `fila_group` falls to
  `G_OTHER` — pinned by the fold tests below, independent of the UI assertions.

## Verification

- `tests/test_cockpit.py` — the obligation fold (`test_inbound_ask_owes_reply`,
  `..._obligation_invoice_is_payment`, `..._outbound_invoice_obligation_is_collect`,
  `..._close_auto_resolves_from_either_side`, `..._ack_auto_resolves`, `..._fyi_is_quiet_info_pile...`,
  `..._last_decisive_act_wins...`) and `test_legacy_fallback_when_no_speech_act` (pre-re-triage routing).
  Stage 0/1: `test_own_rejection_outbound_auto_closes` (Bug 1), `test_inbound_supplier_invoice_is_to_pay`
  (Bug 2), `test_overdue_outbound_invoice_is_billing_group`, `test_stale_followup_is_chase_not_billing`.
- `tests/test_classifier.py::test_coerce_maps_speech_act_and_defaults_unknown` + `..._contracts_carry_speech_act`.
- `tests/test_crm.py::test_record_persists_speech_act_for_the_obligation_fold`.
- `tests/test_fila.py` — the six groups + «Informações» ship and route (`..._ships_obligation_groups_and_info_pile`,
  `..._fyi_verdict_folds_to_info_label`, `..._supplier_invoice_routes_to_a_pagar_group`).
- Full suite **778 passed, 0 failed** (was 764 pre-Stage-2); `ruff check src tests` clean. The new
  `speech_act` verdicts populate only after the owner runs `triage --full`.
