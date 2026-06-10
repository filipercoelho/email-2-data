# ADR-011 — Export sends only a job shell, and never auto-fires

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-06-10 (back-filled; commit 7714993) |

## Context

A finished Project is offloaded to the materials-costing estimator. But materials-costing
line items reference catalog materials and pricing snapshots that email-2-data's free-text
spec does not carry. Pretending to export costed lines would fabricate data the system never
actually had — the cardinal data-extraction sin.

## Decision

Export observes an **honesty boundary**:

- It sends only the **shell** of the job — the brief in `project_name` / `cliente` /
  `descricao` / `notas`. The estimator builds the costed lines there, where the catalog lives.
- Export is **always an explicit human action** — it never auto-fires.
- It **refuses** a non-estimable Project, and refuses a re-export unless `--force`.
- Advancing a Project to `QUOTED` is a side effect of a successful export, nothing else.

## Consequences

- The two systems stay honestly separated: email-2-data owns the brief; materials-costing
  owns the pricing.
- Adapters are pluggable (`--adapter json | materials-costing`); JSON is a dry-run.
- Trace: `src/email2data/export.py`, `project.py`; README §"Export honesty boundary". This is
  also an **internal service dependency** on materials-costing (see CLAUDE.md).
