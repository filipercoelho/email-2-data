# Acceptance criteria & evaluation

| Field | Value |
|---|---|
| Type | QA |
| Status | Active |
| Last reviewed | 2026-06-10 |

How we prove email-2-data works. These criteria, plus `standards/05-definition-of-done.md` and
the [CLAUDE.md](../../CLAUDE.md) contract, define "done".

## The success bar (from VISION)

- **~100% recall on client job requests / POs**, and **≈0 real-clients-binned** — the
  asymmetric, never-bin-a-client invariant
  ([ADR-006](../03-decisions/adr-006-two-tier-cascade-anti-ignore-guardrail.md)).
- Most mail resolved with **zero or one cheap LLM call**; escalation rate **trends down** as the
  gazetteer grows.
- **Tokens per email decreasing** over time at constant-or-better accuracy
  ([ADR-001](../03-decisions/adr-001-compute-proportional-to-uncertainty-impact.md)).

## The eval gate

`email2data eval` scores `out/results.jsonl` (counterparty + priority) against hand labels in
`labels/worksheet.csv`. Key rules (see `labels/README.md` for the column spec):

- One row per email; `message_id` must match `out/results.jsonl` exactly.
- `priority` labels are `HIGH | MEDIUM | IGNORE` only — **never label `NEEDS_REVIEW`**, which is a
  model-only routing state, not ground truth.
- `eval` reports rows in labels with no matching result (and vice-versa) **loudly** — it never
  silently drops them; unknown `type` rows are skipped with a warning. (This is the
  reconciliation/coverage report the data-extraction profile requires.)

## Test gate (Given/When/Then style)

The pytest suite is the fast primary gate (`tests/test_<module>.py` per module). Non-negotiable
examples it pins:

- **Given** a fetch run, **when** it touches the mailbox, **then** only `EXAMINE` + `BODY.PEEK[]`
  are issued and no forbidden verb appears — `tests/test_fetch_safety.py`
  ([ADR-002](../03-decisions/adr-002-read-only-imap-guarantee.md)).
- **Given** bulk mail with a gazetteer hint, **when** triaged, **then** it is **not** binned
  offline but escalated — `tests/test_cascade.py`
  ([ADR-005](../03-decisions/adr-005-gazetteer-is-prior-not-verdict.md),
  [ADR-006](../03-decisions/adr-006-two-tier-cascade-anti-ignore-guardrail.md)).
- **Given** a CLIENT/LEAD or client PO, **when** priority is derived, **then** it is `HIGH` —
  `tests/test_classifier.py` / `derive_priority` ([reference](../05-reference/triage-schema.md)).

### Baseline pin

**2026-06-10, branch `feat/cockpit`: 244 passed, 3 failed.** The 3 failures are in
`tests/test_webapp.py` (in-progress cockpit UI work), not a regression. Re-confirm against this
pin; a change that moves the count must explain why. "Tests pass" must be backed by shown output.

```bash
.venv/bin/python -m pytest -q
```
