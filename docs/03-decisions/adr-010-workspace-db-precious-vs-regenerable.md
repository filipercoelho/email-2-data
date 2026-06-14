# ADR-010 — workspace.db is precious; crm.db and sync.db are regenerable

| Field | Value |
| --- | --- |
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
| --- | --- | --- |
| `out/crm.db` | regenerable | `email2data crm` drops & rebuilds each run |
| `out/sync.db` | cursor | deletable — next `fetch` re-bootstraps by date |
| `out/workspace.db` | **precious** | **never auto-rebuilt**; evolves in place via `_migrate` (`user_version`) |

Because `project_threads.thread_root` references the regenerable CRM, a rebuild can orphan a
reference — these are surfaced as **dangling** (in `project show` and the web UI), so a
Project never silently loses messages.

## Consequences

- Migrations to `workspace.db` go through `_migrate` (keyed on `user_version`). **New tables**
  are safe via `CREATE TABLE IF NOT EXISTS`; **new columns on an existing table are NOT** — that
  statement is a no-op on a table that already exists, so an added column needs an explicit
  guarded `ALTER TABLE … ADD COLUMN` inside a versioned `if version < N:` block in `_migrate`
  (the precious DB is never rebuilt, so a forgotten ALTER silently ships a column-less DB that
  crashes on first write). Pin the upgrade path with a test on a prior-version DB **with rows**,
  not just a fresh in-memory one. Never drop-and-recreate it. See
  [ADR-015](adr-015-knowledge-capture-claim-ledger.md) for the v2→v3 example.
- Trace: `src/email2data/workspace.py`, `project.py`, `crm.py`; README §"Stores & schema".
