# ADR-029 — The Fila groups by obligation: ours vs theirs

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-07-20 |
| Extends | [ADR-028](adr-028-decisions-persist-and-stay-reviewable.md) (queue legibility), the D1 response clock in [cockpit-design](../05-reference/cockpit-design.md) |
| Supersedes | nothing |

## Context

The owner, looking at a 79-thread Fila: *"the items that require our intervention are being rendered
and grouped with the ones where we're waiting for the client."*

Checking the framing before building — the complaint names *grouping*, and grouping was indeed
absent, but the deeper fault is that **the queue had no visual channel for obligation at all**:

- `cockpit._band()` maps a thread to `red` / `amber` / `green` from **age**, and does it for both
  states. A fresh `WE_OWE` is green; a fresh `AWAITING` is green. Colour therefore encodes
  **urgency** and nothing else.
- Both states render into the same slot, with the same weight, in the same component
  (`.clock`). The *only* thing separating "devemos resposta há 27 min" from "à espera há 31 min" is
  the Portuguese prose — so the eye cannot answer "is this mine?" without stopping to **read**.
- The default order, `Mais recentes`, sorts purely by recency and thus **interleaves** the two.
  `sort_key()` already ranks we-owe first, but that ordering only applies under `Risco de resposta`;
  picking the default sort silently discarded the one signal that existed.

The consequence is not cosmetic. The Fila's headline count said **79 threads** and the chip said
**65 em risco**, but a large share of those are threads where we have already replied and the ball is
with the client. The queue was overstating the work: it read as 79 things to do.

`state` (`WE_OWE` / `AWAITING` / `HANDLED` / `INTERNAL`) was already computed server-side and already
present on every row. Nothing needed to be derived — only rendered.

## Decision

**Obligation is the Fila's primary partition. Sort is secondary.**

1. **Sectioned list.** The active queue renders under sticky headers carrying a label, a count, and a
   one-line hint: **Precisam de resposta** (`WE_OWE`), **À espera deles** (`AWAITING`), **Internos**
   (everything else). Sticky, because a header scrolled out of view is a header that stopped working.
2. **Sort applies within a group.** `view()` partitions with a **stable** sort on `groupOf(r)` after
   filtering. Because `rows` is already ordered by `sortRows()`, stability alone preserves the user's
   chosen order inside each section — no second sort key, and `Mais recentes` stays fully useful.
3. **The colour collision is broken at row level too.** `AWAITING` rows get a **hollow** clock dot
   (`.clock.wait .d`). Colour keeps meaning urgency; **fill** now means obligation. A row read
   outside its section — in a screenshot, or as the last row before a scroll boundary — still says
   whose move it is.
4. **"À espera deles" is deliberately muted** (grey, not red). That section is a *status report*, not
   a to-do list. Giving it equal visual weight is what made the queue feel like 79 tasks.
5. **The Tratados ledger is not grouped.** Every row there is `HANDLED`; a section boundary with
   nothing on the far side of it is noise.

### Why not the alternatives

- **Row-level marker only** (accent bar / arrow, no sections) — preserves a single flat scan, but the
  boundary is a per-row judgement the eye must make 79 times. The owner asked for a *group*
  distinction; a partition is the thing that cannot be misread.
- **Segmented tabs** (Todos / Precisam de ti / À espera) — sharpest focus, but it hides half the
  queue behind a click and makes "what is the true state of the world" a two-step question. It also
  fights the existing `em risco` chip, which is already a filter.

## Consequences

- The headline "79 threads" is unchanged, but the queue now *shows* that it is (e.g.) 34 ours + 45
  theirs. Expect the perceived workload to drop without a single thread being hidden.
- Section membership is derived at render time from `clock.state`. **If the API ever stops emitting
  `state`, every row falls into `Internos`** and the queue looks calm while work waits — so the data
  contract is pinned by a test (`test_api_fila_distinguishes_we_owe_from_awaiting`) independent of
  the UI assertions.
- `data-i` still indexes `view()`, so headers cost nothing in the click / focus / `j`-`k` / menu-anchor
  paths — `.ghead` is not `.row`, so `closest('.row')` skips it and keyboard nav walks rows only.
- Filters compose with grouping unchanged: filtering to `em risco` simply empties or shrinks a
  section rather than removing the partition.
- A thread moving `WE_OWE → AWAITING` (we reply) now visibly *jumps sections* on the next render.
  That is the intended feedback, and it is why the reply path is worth keeping in the queue.

## Verification

- `test_fila_page_groups_queue_by_obligation` — partition function, PT-PT labels, group-as-primary-key,
  ledger exemption, sticky headers with counts, hollow-dot rule. Confirmed **failing** before the
  change and passing after.
- `test_api_fila_distinguishes_we_owe_from_awaiting` — a real inbound-only thread reports `WE_OWE`, a
  thread we replied to reports `AWAITING`, and no row arrives without a groupable state. This one
  guards an **existing** contract the new UI now depends on, so it passes before and after by design.
- Full suite **628 passed, 0 failed**; `ruff check src tests` clean.
