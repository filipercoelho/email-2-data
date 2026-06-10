# 00 — Company Context

| Field | Value |
|---|---|
| Type | Overview / Standard |
| Audience | All agents, all projects |
| Status | Active |
| Owner | Filipe Coelho |
| Source of truth | `project-scaffolding-roadmap/standards/` (this repo) |
| Stamp | see `VERSION` |

> Read this first on any Lindo Serviço project. It tells you **who you are building
> for, how they work, and what they value** — before you read a single line of code.

## Who

**Lindo Serviço** — a Portuguese production company (CNC, laser cutting, printing,
finishing of materials). Software is built to run the business: internal tools for
the LAN, machine/CNC integration, data extraction from existing systems, and web
apps for company operations. Most software is **internal and on-premises**, not a
public SaaS.

The development team is effectively **one engineer (Filipe) pairing with AI agents.**
That shapes everything below: low ceremony, high traceability, no work that only a
large team could maintain.

## Language & locale

- **Working language with the user:** English is fine; the domain and UI are often
  **Portuguese (pt-PT)**. Default user-facing strings to **PT with EN fallback**
  (`i18n` with `pt` / `en`). Currency **EUR (€)**, dates `DD/MM/YYYY`, decimal comma
  in user-facing PT contexts.
- Code, identifiers, comments, commits: **English.**

## Ways of working (the non-negotiables)

1. **Move fast, but never hide uncertainty.** Prefer a logged assumption to a
   blocking question for low-stakes gaps; escalate genuine ambiguity. (This is the
   `agentic-requirements` rule.)
2. **Understand before editing. Reconcile before assuming. Strike narrowly.
   Validate honestly. Preserve traceability.** (The
   `agentic-implementation-engineering` rule.)
3. **Zero hallucination.** Never invent formulas, dependencies, ports, hardware
   specs, or business rules. If it is not observable, mark it **UNKNOWN** and ask.
4. **Place knowledge, don't write documents.** Every durable fact lands on the right
   `/docs` shelf (00–09) via the documentation gatekeeper — not in a scratch file.
5. **Inherit, don't re-explain.** Company invariants live in `./standards/`. Read
   them; don't re-derive them per project.

## What "good" looks like to this user

The user cares, on **every** project regardless of domain, about:

- **It actually runs on the target hardware and LAN** (see `01`, `02`) — not just
  "works on my machine."
- **Traceability** — every decision, assumption, and business rule has a recorded
  reason a future reader (or agent) can find.
- **Simplicity that one person can maintain** — no architecture that needs a team.
- **No silent scope creep** — surprises are surfaced and classified, not absorbed
  quietly.
- **Honest reporting** — if tests fail, say so; if a step was skipped, say so.

These are expanded into a checklist in `04-always-verify.md` and a bar in
`05-definition-of-done.md`.

## How the standards fit together

| File | Answers |
|---|---|
| `00-company-context.md` | Who you're building for and how they work *(this file)* |
| `01-hardware-baseline.md` | What hardware you deploy to |
| `02-network-lan.md` | The LAN you deploy into |
| `03-engineering-principles.md` | How to think about the code |
| `04-always-verify.md` | The standing checklist before declaring anything done |
| `05-definition-of-done.md` | The quality bar |
| `06-tech-stack-defaults.md` | What to reach for unless told otherwise |
| `07-project-taxonomy.md` | The kinds of apps and their per-type expectations |
