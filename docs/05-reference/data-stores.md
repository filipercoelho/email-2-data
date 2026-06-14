# Reference — data stores & outputs

| Field | Value |
| --- | --- |
| Type | Reference |
| Status | Active |
| Last reviewed | 2026-06-10 |

Where the pipeline persists state. The recoverability tier of each store is an invariant —
see [ADR-010](../03-decisions/adr-010-workspace-db-precious-vs-regenerable.md).

## Files under `out/`

| Path | What | Tier | Rebuilt? | Versioned by |
| --- | --- | --- | --- | --- |
| `out/results.jsonl` | append-only `TriageResult` per message | derived | re-run `triage --full` | `EXTRACTOR_VERSION` |
| `out/crm.db` | interactions (event log) + contacts (person rollup) | **regenerable** | `email2data crm` drops & rebuilds each run | `crm.SCHEMA_VERSION` |
| `out/sync.db` | per-mailbox IMAP **UID watermark** (cursor) | cursor | deletable — next `fetch` re-bootstraps by date | `sync.SCHEMA` (additive) |
| `out/workspace.db` | **human decisions** + Projects + edit history | **precious** | **never auto-rebuilt** | `workspace.SCHEMA_VERSION` (`user_version`) |
| `corpus/*.eml` | raw fetched messages (read-only source mirror) | cache | re-fetch | — |

## Provenance / corpus

- `corpus/*.eml` are fetched with `BODY.PEEK[]` only and never mutated
  ([ADR-002](../03-decisions/adr-002-read-only-imap-guarantee.md)). Non-INBOX folders get an
  `X-Email2Data-Source` header prepended on fetch.
- `out/results.jsonl` is the triage ledger; `triage` appends only messages not already present
  ([ADR-009](../03-decisions/adr-009-incremental-idempotent-by-default.md)).

## workspace.db migration discipline

`Workspace.connect` runs `_migrate`, which stamps `user_version` and is where migrations go.
A **new table** is delivered additively by `CREATE TABLE IF NOT EXISTS`; a **new column on an
existing table is not** (that statement no-ops on an existing table), so it requires a guarded
`ALTER TABLE … ADD COLUMN` inside an `if version < N:` block in `_migrate`, gated by the version
check so re-runs are safe. Because this DB is never rebuilt, a missing ALTER silently ships a
column-less DB that crashes on first write — pin the upgrade with a test on a prior-version DB that
contains rows (see `tests/test_workspace_migration.py`). Never drop-and-recreate `workspace.db`.
Hand edits live in `project_fields` (always win) and every edit — plus off-email `__kind__` events
([ADR-015](../03-decisions/adr-015-knowledge-capture-claim-ledger.md)) — is recorded append-only in
`project_field_history`.

## Dangling references

`project_threads.thread_root` points into the regenerable CRM. A CRM rebuild can orphan a
reference; `project show` and the web UI flag these as **dangling** so a Project never silently
loses messages.

## Project lifecycle

`LEAD → GATHERING → ESTIMABLE → QUOTED → WON | LOST`, plus `ARCHIVED` (soft-retire, hidden by
default). A successful export advances a Project to `QUOTED`
([ADR-011](../03-decisions/adr-011-export-honesty-boundary.md)).
