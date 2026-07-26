# Acceptance criteria & evaluation

| Field | Value |
| --- | --- |
| Type | QA |
| Status | Active |
| Last reviewed | 2026-07-25 |

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
- **Given** any route, **when** a signed-out visitor requests it, **then** it is refused unless it is
  on the closed public allowlist — `tests/test_auth_gate.py::test_every_route_is_gated_by_default`
  walks the real route tree ([ADR-039](../03-decisions/adr-039-people-auth-and-the-default-deny-gate.md)).
- **Given** a signed-in **non-admin**, **when** they open any lens, **then** the shell offers no
  «Administração» entry — and `/admin` still answers 403, never a redirect —
  `tests/test_auth_gate.py`, `tests/test_cockpit_ui.py`
  ([ADR-040](../03-decisions/adr-040-the-first-authorization-check-and-the-honest-refusal.md),
  [ADR-041](../03-decisions/adr-041-the-roster-becomes-people-and-a-person-owns-their-account.md) §1).
- **Given** the only active administrator, **when** anything tries to demote, deactivate or remove
  them, **then** the store refuses — `/setup` 404s once a credential exists, so zero-admin is
  unrepairable from the app — `tests/test_people.py`, `tests/test_auth_gate.py`
  ([ADR-041](../03-decisions/adr-041-the-roster-becomes-people-and-a-person-owns-their-account.md) §7).
- **Given** a person who owns a thread, project or capture, **when** removal is attempted, **then** it
  is refused with what and how much (deactivation is the exit) — `tests/test_people.py`
  ([ADR-041](../03-decisions/adr-041-the-roster-becomes-people-and-a-person-owns-their-account.md) §8).
- **Given** an inbox grant that is not one of our mailboxes, **when** it is saved, **then** it is
  refused and **nothing** is written — a stored typo reads as a permission and matches no mail —
  `tests/test_auth_gate.py`
  ([ADR-041](../03-decisions/adr-041-the-roster-becomes-people-and-a-person-owns-their-account.md) §9).

### Baseline pin

**2026-07-26, branch `feat/fila-mesa` (ADR-041): 1020 passed, 0 failed** in 109.2 s (full run, Chrome
present). **988 passed** in 84.5 s with the three opt-in browser e2e modules ignored
(`tests/test_para_ti_live_e2e.py` 19, `tests/test_cockpit_urls_e2e.py` 9,
`tests/test_session_expiry_e2e.py` 4 = 32), which `importorskip` themselves when the `e2e` extra or a
Chrome/Chromium is missing. The 988 was produced with `--ignore`, **not** observed as a skip count —
this machine has system Chrome and the fixtures ask for it by channel.

**+76 on the previous pin of 944**, itemised in [CLAUDE.md](../../CLAUDE.md) §"Baseline pin"
(13 identity-in-the-shell + 15 «A minha conta»/`auth reset` + 33 «Pessoas» + 11 one-roster + 4 for
defects found by looking at the app rather than by a test — two in the render, two in the
change-password form a password manager mis-read).

```bash
.venv/bin/python -m pytest -q
```

Re-confirm against this pin; a change that moves the count must explain why. "Tests pass" must be
backed by shown output.

> **This pin is the one that goes stale.** It read *244 passed, 3 failed (2026-06-10, `feat/cockpit`)*
> until 2026-07-25 — nearly 700 tests behind, and still describing three failures that had long since
> been fixed, which is worse than being merely out of date: it told a reader that red was the expected
> state. [CLAUDE.md](../../CLAUDE.md) §"Baseline pin" is the same number and the two **must be moved in
> the same commit**. Two pins that disagree are two pins nobody trusts.
