# 05 — Definition of Done

| Field | Value |
|---|---|
| Type | Standard |
| Audience | All agents, all projects |
| Status | Active |
| Owner | Filipe Coelho |

> The quality bar. `04-always-verify.md` is the *checklist you run*; this is the
> *bar a unit of work must clear* before it counts as finished. Scaled by risk — a
> typo fix does not need an ADR; a destructive migration needs all of it.

## Tiered by risk (mirrors the `agentic-requirements` L0–L4 levels)

| Level | Example | Done means |
|---|---|---|
| **L0** | typo, log line, comment | Change is correct; surrounding code unaffected. No ceremony. |
| **L1** | small local fix | + the relevant test passes; touched file lints/compiles. |
| **L2** | a feature / clear task | + Section A–D of `04` cleared; Task Brief acceptance criteria met; docs updated where knowledge changed. |
| **L3** | architectural / cross-cutting | + ADR written for the decision; NFR scan considered; full `04` checklist; rollback documented. |
| **L4** | destructive / irreversible | + explicit user confirmation; tested dry-run; backup verified; rollback rehearsed. |

> When unsure which level, **pick the higher one.** Over-classifying costs one extra
> check; under-classifying ships a wrong change.

## Universal floor (every level, even L0)

1. **It runs on our reality.** Target hardware (`01`) and LAN/port (`02`) respected —
   never "works on my machine."
2. **No hallucinated facts.** Everything is FACT or marked UNKNOWN (`03 §1`).
3. **Honest status.** Tests shown, skips named, UNKNOWNs surfaced (`04 §F`).
4. **Traceable.** A future reader can find *why*, not just *what* (`03 §7`).

## Test expectations

- Default stack ships with **pytest** (`06`). New behaviour gets a test; bug fixes
  get a regression test that fails before and passes after.
- A smoke test that proves the thing **starts and answers** (e.g. `/health`) counts
  for L1–L2 web work; it does not replace unit tests for logic.
- "Tests pass" is a claim that must be backed by **shown output**.

## Documentation expectations (via the gatekeeper)

- Knowledge changes land on the right `/docs` shelf (00–09) — see
  `06-tech-stack-defaults.md` and the docs skeleton.
- Decisions → `03-decisions/` ADR. Exact values → `05-reference/`. How-to →
  `04-implementation/`. Don't write hybrid files.
- Prefer **UPDATE / LINK** over new files; new files are the exception.

## What "not done" looks like (reject these)

- Passes the acceptance criteria but undermines the actual goal (Vision fail, `03 §6`).
- Green tests on the dev Mac, untested against the real target.
- A decision made with no recorded reason.
- A surprise silently absorbed into a wider change.
- "Done" reported for something not actually run.
