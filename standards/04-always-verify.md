# 04 — Always Verify

| Field | Value |
|---|---|
| Type | Checklist / Standard |
| Audience | All agents, all projects |
| Status | Active |
| Owner | Filipe Coelho |

> The standing checklist. Before declaring **any** change done — on any project,
> regardless of domain — walk this list. Items that don't apply are marked N/A *with
> a reason*, not skipped silently. This is what the user means by "things they should
> always verify."

## A. Correctness & intent

- [ ] The change does what was **actually asked** (Vision Guard), not just what a
      literal reading says.
- [ ] No invented facts: every formula / rule / dependency is FACT or marked UNKNOWN
      (`03 §1`).
- [ ] Reconciled against the existing code — known mismatches surfaced, not silently
      papered over.
- [ ] Scope held: any surprise was classified via the Surprise Protocol (`03 §4`),
      not absorbed quietly.

## B. Runs on our reality (hardware `01`, LAN `02`)

- [ ] Fits the **target host's** RAM/CPU/OS/arch — not just the dev Mac.
- [ ] Uses a **port claimed in the `02` port table** — no collision, no guess.
- [ ] Correct **bind address** for the deploy target (LAN-reachable vs localhost).
- [ ] **Offline behaviour** is acceptable if the target can lose internet (esp.
      machine-side).
- [ ] Talks to NAS / edge devices over the **confirmed** interface, not an assumed
      one.

## C. Locale & domain (`00`)

- [ ] User-facing strings respect **pt-PT with en fallback** (i18n), EUR, PT date /
      number format where shown to users.
- [ ] Domain terms (CNC, laser, materials, pricing/margins) used as the business
      uses them.

## D. Quality & safety

- [ ] Tests run and **pass** — output shown, not asserted. Failures reported plainly.
- [ ] Lint / type / compile check clean for the touched files.
- [ ] **Rollback** is known for risky changes; destructive ops confirmed before
      running.
- [ ] **Secrets** not hard-coded; pulled from the project's configured location
      (`02 §D`).

## E. Traceability & docs (`03 §7`)

- [ ] Architectural / hard-to-reverse decisions captured as an **ADR**
      (`docs/03-decisions/`).
- [ ] Open assumptions recorded in the **assumptions ledger**, not left implicit.
- [ ] Durable facts placed on the correct **`/docs` 00–09 shelf** via the
      documentation gatekeeper.
- [ ] `CLAUDE.md` still accurate after the change.

## F. Honest close-out

- [ ] Stated what was done, what was **skipped or left UNKNOWN**, and what the user
      still needs to decide.
- [ ] No "done" claimed for anything not actually verified.

---

> Projects may **append** domain-specific checks (e.g. a CNC project adds "G-code
> validated against the machine's dialect"). They may not **remove** items from this
> base list — extend the profile in `07-project-taxonomy.md` instead.
