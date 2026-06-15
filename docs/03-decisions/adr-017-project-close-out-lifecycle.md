# ADR-017 — Project close-out: CANCELLED is a first-class stage with a reason

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-06-15 |

## Context

A real fabrication job dies for a reason, and *whose* reason it was matters: a client pulls out, a
supplier can't deliver, or we decline (margin/feasibility). The lifecycle had `WON`/`LOST` (deal
outcomes) and `ARCHIVED` (a silent soft-retire) — but **no way to cancel an in-flight project and
record why**, so that information lived nowhere and the Projetos lens couldn't answer "why did this
stall?". Deleting the project (the only other option) destroys the record entirely.

## Decision

Add **`CANCELLED`** as a first-class terminal stage, and a **close-out** captured as queryable project
columns (not just a timeline note):

- `STAGES` gains `CANCELLED`; it joins `TERMINAL_STAGES`. `CLOSED_STAGES = {CANCELLED, LOST}` are the
  stages that carry a close-out.
- `projects` gains three v4 columns (precious-DB migration, guarded `ALTER`): `close_party`
  (`client | supplier | our` — from Lindo's POV; "our" = our own decision), `close_reason` (free text),
  `closed_at` (UTC). Entering a CLOSED stage stamps them; leaving for any non-closed stage **clears**
  them, so a reopened project is no longer "cancelled". `LOST` may also carry a party/reason.
- `POST /api/projects/{pid}/stage` accepts `close_party` (validated against `CLOSE_PARTIES`) +
  `close_reason`; the Projetos lens routes CANCELLED/LOST through an inline close-out form (party chips
  + reason) and shows a close-out banner on the cancelled project.

Cancellation is distinct from deletion: `DELETE` is still the hard-remove for mistakes/duplicates;
`CANCELLED` **keeps the record + the reason**. It is a human verdict — the auto-suggester never sets it.

## Consequences

- "Why did this die, and on whose side?" is a queryable attribute of the project, not lost context.
- Pinned by `tests/test_project.py` (close-out set/clear-on-reopen, LOST party) +
  `tests/test_webapp.py` (endpoint, bad-party rejection) + the v3→v4 migration test.
- Trace: `project.py` (STAGES/CLOSED_STAGES/`set_stage`), `workspace.py` (v4 columns + `CLOSE_PARTIES`),
  `projetos_page.py` (close-out form/banner). Builds on
  [ADR-010](adr-010-workspace-db-precious-vs-regenerable.md) (precious-DB migration discipline).
