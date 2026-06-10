# 03 — Engineering Principles

| Field | Value |
|---|---|
| Type | Standard |
| Audience | All agents, all projects |
| Status | Active |
| Owner | Filipe Coelho |

> **How to think about the code on a Lindo Serviço project.** These are stable
> across every domain. They are distilled from existing project `CLAUDE.md` files and
> the `agentic-requirements` / `agentic-implementation-engineering` skills, so they
> match how the user already works.

## 1. Zero hallucination (the hard rule)

Never invent formulas, dependencies, ports, hardware specs, business rules, or
intent. Classify every non-trivial statement:

- ✅ **FACT** — directly observable in code, config, or a document. Cite where.
- 🔶 **INFERENCE** — reasoned from facts. Label it as inference, state the basis.
- ❓ **UNKNOWN** — not observable. Say so. Do **not** "complete" it from a pattern.

> *We are not reading files. We are reconstructing a system.* Missing logic is
> marked UNKNOWN, never guessed.

## 2. Understand before editing

Read the relevant code before changing it. Treat any requirement or Task Brief as a
**hypothesis to reconcile against the codebase**, not an order to execute blindly.
Surface every mismatch between what was asked and what the code actually does.

## 3. Strike narrowly

Make the smallest change that solves the real problem. Find the strike zone; don't
refactor adjacent code because you're "already there." Touching more than the task
needs is scope creep until logged and agreed.

## 4. No silent scope expansion — the Surprise Protocol

Real work surfaces surprises (hidden coupling, an edge case, code that already does
the thing). When one appears, **classify and decide explicitly**: absorb (trivial,
in-scope), log (note it, keep going), pause (needs a call), or escalate (changes the
goal). Never quietly expand the change to swallow a surprise.

## 5. Validate honestly, plan rollback

Before editing risky code, know how you'll verify it and how you'll undo it. After
editing, actually run the validation. **Report results faithfully** — if tests fail,
say so with the output; if you skipped a step, say so. "Done" means verified, not
hoped.

## 6. Guard the vision, not just the acceptance criteria

A change that passes every Given/When/Then but undermines the larger intent is a
bug. Before declaring done, check the change against *why* it was asked for.

## 7. Preserve traceability

Decisions get a recorded reason (ADR for anything architectural or hard to reverse).
Assumptions go in the ledger, not in your head. Durable facts go on the right `/docs`
shelf via the documentation gatekeeper. A future reader — or the next agent session —
must be able to reconstruct *why*, not just *what*.

## 8. Simplicity one person can maintain

This is a solo-plus-agents shop. Prefer boring, legible solutions over clever ones.
If a design needs a team to keep alive, it's the wrong design here. Fewer moving
parts beats more capability that nobody can debug at 2am.

## 9. Match the surrounding code

Write code that reads like the code already there — its naming, idioms, structure,
comment density, error-handling style. Consistency within a project outranks your
personal preference.

## 10. Respect the physical reality

This software runs on **specific hardware** (`01`) inside a **specific LAN** (`02`).
"Works on my machine" is not done. Check target RAM/CPU, the right OS/arch, the
claimed port, offline behaviour, and PT locale before calling it finished — see
`04-always-verify.md`.

---

### The two one-line rules to remember

> **Requirements:** *Move fast, but never hide uncertainty.*
>
> **Implementation:** *Understand before editing. Reconcile before assuming. Strike
> narrowly. Validate honestly. Preserve traceability.*
