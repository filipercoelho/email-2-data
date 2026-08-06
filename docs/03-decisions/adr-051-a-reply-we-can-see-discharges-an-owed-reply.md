# ADR-051 — A reply we can see discharges an owed reply

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-08-03 |
| Amends | [ADR-036](adr-036-speech-act-obligation-fila.md) §Decision 2 — "`FYI`/`UNKNOWN` never override a live move" is now scoped to **inbound** mail |
| Scope | `cockpit.py` (`derive_obligation`, `_we_replied_after`, `thread_clock`), `fila_page.py` (the dossier timeline's debt chip) |
| Serves | Non-negotiable #2 — *never silently bin a client*. Nothing leaves the queue here: 185 active rows before, 185 after. |

## Context

The owner, looking at a rendered dossier: the header read **«devemos resposta há 2 dias»** while our
own reply — sent that same afternoon, 14:49 — sat visible in the timeline directly below a chip
reading **«sem resposta há 2 dias»**.

Both numbers were correct for what they measured. That is the problem.

The thread is `mid:509ab3fb…@example.pt`, and its acts (from the live `crm.db`) are:

```
2026-07-20 08:21  inbound   ASK   FOLLOW_UP
2026-07-20 12:39  outbound  FYI   OTHER
2026-07-20 12:41  outbound  FYI   OTHER
2026-07-20 12:42  inbound   ASK   FOLLOW_UP
2026-07-22 11:35  outbound  FYI   FOLLOW_UP
2026-07-22 12:13  inbound   ACK   FOLLOW_UP
2026-07-31 21:56  inbound   ASK   FOLLOW_UP   ← last DECISIVE act
2026-08-03 14:49  outbound  FYI   FOLLOW_UP   ← the visible reply; not decisive
```

`derive_obligation` folds from the last message whose act is in `{ASK, OBLIGATION, ACK, CLOSE}`.
Our reply is an **update** — "we ran several tests, we are not yet satisfied, new date 08-07" — which
Gemini reads, correctly, as `FYI`. `FYI` is not decisive, so the 07-31 `ASK` stayed live, and
`_obligation_since(OWE_REPLY)` anchors to `last_inbound_date`. The clock therefore counted from an
ask we had already answered, and **no number of replies could ever move it**. Measured: obligation
`OWE_REPLY`, anchor `2026-07-31 16:56-04:00`, `age_hours` 65.58 → `floor(65.58/24)` = «2 dias», while
`last_outbound_date` was 0.7 h old.

**ADR-036 §2 wrote that rule for inbound mail** — to stop a newsletter or a delivery notice landing
after a client's question and wiping it. Applied symmetrically to our *own* outbound, it turned every
update-shaped answer into a no-op.

**The sharpest evidence that this is a regression, not a design choice:** the pre-re-triage
`_legacy_obligation` fold gets this thread **right** and the act-driven fold gets it **wrong**.

```
act-driven (ADR-036): OWE_REPLY  anchored at 2026-07-31 16:56
legacy fold         : AWAIT_THEM anchored at 2026-08-03 14:49
```

`_legacy_obligation` still carries `if last_outbound_date >= last_inbound_date → AWAIT_THEM`.
ADR-036 Stage 2 dropped that guard when it moved to acts and never replaced it — so spending Tier-1
tokens on `triage --full` made this thread's queue placement *worse* than not spending them.

A second, smaller defect fell out of the same look. The dossier timeline paints its debt chip in the
segment between «agora» and the **newest** message, and its comment claims exactly that span — but it
rendered `clock.age_hours`, the *obligation* age. Those coincide only when the clock is anchored at
the newest message. When it is not, the chip prints a multi-day debt directly above a mail sent an
hour ago. That is what made the screen read as absurd rather than merely debatable.

## Decision

**1. An obligation to REPLY is discharged by the observable fact that we replied.** If any
`outbound` message follows the decisive message, the obligation becomes `AWAIT_THEM` — whatever act
the classifier gave our own mail. This is a *fact* about the thread (we sent something), not an
inference about what our mail meant, which is the cheaper and more reliable signal of the two.

**2. Only `outbound` discharges.** An `internal` forward of a client's question to a colleague is
not an answer to the client — ADR-036's "an internal forward is still *about* a client" fold survives
intact.

**3. An owed PAYMENT is deliberately NOT dischargeable this way.** An email never pays a supplier.
«A pagar» must survive us replying "recebido, pagamos dia 10", so `OWE_PAYMENT` is reached before the
discharge and keeps counting from the bill.

**4. `FYI`/`UNKNOWN` still never override a live *inbound* move.** The rule ADR-036 wrote is kept in
full for the case it was written for; the strike is narrowed to our own outbound only.

**5. The clock says whether the debt covers the segment the timeline draws it across.**
`thread_clock` now also emits `gap_hours` (now → the newest message, any direction) and
`anchored_at_last`. The chip speaks as the response debt only when anchored there; otherwise it
renders the plain elapsed gap like any other inter-message chip. Still server-computed — there is no
client-side `Date.now()` recompute, which is the drift ADR-034 P5c-fix removed.

### Why not the alternatives

- **Teach the playbook to emit `ACK` for a reply that answers an ask.** Puts a per-message LLM
  judgement in the path of a fact we can already observe deterministically, costs Tier-1 tokens on
  every re-triage, and fails silently and invisibly whenever the model reads an update as an update.
  It also contradicts the governing principle: spend compute in proportion to *uncertainty*, and
  there is no uncertainty about whether we sent a message.
- **Anchor `OWE_REPLY` to `max(last_inbound, last_outbound)`.** Fixes the *number* and leaves the
  *label* lying — the thread would read «devemos resposta há 1 h» when we owe nothing.
- **Let any later outbound clear every obligation.** Would let a "recebido, pagamos dia 10" clear an
  unpaid bill. Rejected — see decision 3.

## Consequences

- **Measured against the live corpus** (675 threads, 1087 interactions): **6 threads move**, all
  `OWE_REPLY` → `AWAIT_THEM`, all `CLIENT` — 4 into «A aguardar» (stale, a follow-up candidate) and 2
  into «À espera deles». «Precisam de resposta» goes **36 → 30**. The active Fila is **185 rows before
  and 185 after**: nothing is dropped, nothing is silently binned.
- Threads we have already answered stop inflating the one number the app leads with. The queue's
  claim on the owner's attention gets more honest, not smaller for its own sake.
- **A reply that answers nothing now moves the ball anyway** — an out-of-office, or a "I'll come back
  to you next week", reads to this rule as a reply. That is the accepted cost of preferring an
  observable fact to an inferred one, and it is bounded: the thread stays in the active queue under
  «À espera deles», and turns amber into «A aguardar» after 72 h, which is exactly the follow-up
  prompt that case wants.
- The act-driven and legacy folds now agree on this shape, and a test pins them together so they
  cannot diverge again.

## Verification

- `tests/test_cockpit.py` — `test_our_reply_discharges_an_inbound_ask_even_when_it_reads_as_fyi`
  (the live thread's shape), `test_the_act_driven_fold_agrees_with_the_legacy_fold_that_a_reply_moves_the_ball`,
  `test_a_reply_never_discharges_an_inbound_bill`, `test_an_internal_forward_is_not_an_answer_to_the_client`,
  `test_a_new_ask_after_our_reply_owes_again`, `test_an_inbound_fyi_still_never_overrides_their_live_ask`,
  `test_the_clock_says_whether_the_debt_covers_the_segment_the_timeline_draws_it_across`.
- `tests/test_fila.py::test_the_debt_chip_only_speaks_as_debt_over_the_segment_it_actually_spans`.
- **Fail-before/pass-after was run, not assumed** — the two source edits were reverted alone into a
  copied tree and the new tests run against it: **4 of the 8 fail there**. The other 4
  (`..._never_discharges_an_inbound_bill`, `..._internal_forward_is_not_an_answer`,
  `..._new_ask_after_our_reply_owes_again`, `..._inbound_fyi_still_never_overrides`) pass on both **by
  design** — they are the guards that pin how *narrow* the strike is, and a guard that only starts
  passing after the change would not be guarding anything.
