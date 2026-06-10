# ADR-010 — workspace.db is precious; crm.db and sync.db are regenerable

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-06-10 (back-filled) |

## Context

The system has three SQLite stores with very different value. Two are derived caches that can
be rebuilt from source; one holds **human decisions** (confirmed job-spec fields, Projects,
edit history) that exist nowhere else. Treating them uniformly risks either over-protecting a
cache or, far worse, destroying irreplaceable human input on a rebuild.

## Decision

Stores are tiered by recoverability, and the tiers are enforced:

| Store | Tier | Rebuild policy |
|---|---|---|
| `out/crm.db` | regenerable | `email2data crm` drops & rebuilds each run |
| `out/sync.db` | cursor | deletable — next `fetch` re-bootstraps by date |
| `out/workspace.db` | **precious** | **never auto-rebuilt**; evolves in place via `_migrate` (`user_version`) |

Because `project_threads.thread_root` references the regenerable CRM, a rebuild can orphan a
reference — these are surfaced as **dangling** (in `project show` and the web UI), so a
Project never silently loses messages.

## Consequences

- Breaking migrations to `workspace.db` go through `_migrate`; additive changes via
  `CREATE TABLE IF NOT EXISTS`. Never drop-and-recreate it.
- Trace: `src/email2data/workspace.py`, `project.py`, `crm.py`; README §"Stores & schema".
