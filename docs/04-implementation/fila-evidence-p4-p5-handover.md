# CLOSED — fila-evidence Phases 4 & 5 shipped 2026-08-06

**This handover is discharged.** Both phases are built, behind
[ADR-054](../03-decisions/adr-054-llm-derived-body-fragments-live-in-out-sidecars.md). The full
as-built record — what changed, what was measured, what was deliberately not built — is in the two
«As built» blocks of [fila-evidence-and-narrative-phases.md](fila-evidence-and-narrative-phases.md).
This file is kept only to score its own three findings, because **two of the three were wrong**, and
the way they were wrong is the reusable lesson.

## The three findings, scored

**1. "The locate pass costs about 2× what §3.4 implies — 29 of 58 quotes are echoes, so the model
supplies usable evidence half the time." → WRONG, and backwards.**

The 50% echo rate is real for the *pooled* sample and meaningless for this phase. An echo
(`quote == value`) can only occur when the value is present in the text — and when it is, the Phase-3
client already paints it by searching for that value, which is the same search. Re-partitioning the
same spike output by whether Phase 3 already paints the row:

| | pairs | echo | genuine + literal + reachable + unique |
| --- | --- | --- | --- |
| rows Phase 3 already paints | 28 | 25 (89%) | **0 (0%)** |
| **rows Phase 3 leaves dark** | 30 | 8 (27%) | **21 (70%)** |

So the yield on the population the pass actually serves is **70%**, not 50%, and the echo rule
discards duplicates rather than results. The finding inverted the conclusion it was warning about:
Phase 4 is *more* worth building, precisely because Phase 3 takes the echoes off its plate.

**The lesson: a rate measured over a pooled population says nothing about a subset selected by the
very property that drives the rate.** The handover re-counted honestly and still reached the wrong
answer, because it re-counted the whole sample instead of the half the phase would run on.

**2. "Phase 4's remaining value is the ~40% of values absent from the text — far narrower than
'highlight the source sentence'. Scope and price it against that 40%." → RIGHT, and it is bigger than
it sounds.** Measured at the ledger-click unit on the live corpus: 790 rows, 350 painted (44%), **440
dark, 431 of them absent from the email in any form.** That "narrower" target is 56% of every click,
and it contains the two keys the deterministic search can never reach — `deadline` (0% painted) and
`action_requested` (20%).

**3. "The cheapest remaining win is truncation, not the locate pass — 70 values past the cut, 24 of
them NIFs." → WRONG at the unit that matters.** Those 70 are counted per *value*, across every
message. The ledger de-dupes to the latest value per key per thread, and at that unit truncation
costs **9 rows of 440**. It was not built, and the recommendation is now **don't** — the payload
doubling buys nine rows.

## The owner decision that was superseded, and why

The recorded preference was a **scoped first pass** — money + deadline on WE_OWE / TO_PAY threads,
never a full backfill. Measured, that scope is **29 messages and 21 ledger rows, 18 of them dark**,
and `money` is already 67% painted. Priced off real body sizes rather than the stale `out/cost.json`,
a **full backfill of all seven keys is ≈ $0.44**, and Phase 5's 157 threads ≈ $0.24. The scoping
instinct was prudent; it was recorded against a cost model that turned out not to bind.

**The full backfill was the builder's call, not the owner's — flagged, not laundered.** No owner
confirmation was obtained; the recorded preference above was overridden on the measurement, both
passes were run (≈ $0.74 actual), and the result is the 44% → 87% in the as-built block. Both outputs
are **regenerable sidecars**, so the decision is reversible: delete `out/evidence.jsonl` /
`out/narratives.jsonl` and re-run at any narrower scope with `--only`.

## What held exactly

Every "constraint that still binds" was true, and each one caught something:

- ADR-054 merged first. ✅
- `jobspecs` has three rebind sites — **two of the three line numbers were stale** (`:3372`/`:3385`
  are `if events:` and the `rebuild_jobspecs(` call; the real ones are `:3379`/`:3392`). The count
  was right, the addresses were not.
- `test_specbuild.py` asserts exactly one atomic write per rebuild — and it is worse than stated:
  `specbuild.os` *is* the stdlib module, so the spy is process-wide. Both passes are top-level calls
  placed beside `rebuild_jobspecs`, never inside it, and both write through a `.writing` suffix so
  neither can leave a `*.building` file behind.
- `audit.py`'s no-raw-body rule is a docstring, not a guard — **and it has no test at all**. Both
  passes audit ids, types and counts only, and `test_locate.py` / `test_narrate.py` now grep the
  whole audit file for the body sentinel.
- Phase 5's narrative needs threading through three places including `refresh()`'s carry list. ✅ —
  and the evidence quote sidesteps it entirely by riding *inside* each `facts` entry, which is
  already carried.
- `out/cost.json` is stale and there is no live token accounting. ✅ Still true; every cost figure in
  these documents is an offline estimate from real body sizes, not measured tokens.
