# Reference — data stores & outputs

| Field | Value |
| --- | --- |
| Type | Reference |
| Status | Active |
| Last reviewed | 2026-06-16 |

Where the pipeline persists state. The recoverability tier of each store is an invariant —
see [ADR-010](../03-decisions/adr-010-workspace-db-precious-vs-regenerable.md).

## Files under `out/`

| Path | What | Tier | Rebuilt? | Versioned by |
| --- | --- | --- | --- | --- |
| `out/results.jsonl` | append-only `TriageResult` per message | derived | re-run `triage --full` | `EXTRACTOR_VERSION` |
| `out/crm.db` | interactions (event log) + contacts (person rollup) | **regenerable** | `email2data crm` drops & rebuilds each run | `crm.SCHEMA_VERSION` |
| `out/sync.db` | per-mailbox IMAP **UID watermark** (cursor) | cursor | deletable — next `fetch` re-bootstraps by date | `sync.SCHEMA` (additive) |
| `out/workspace.db` | **human decisions** + Projects + edit history + the intake capture queue/allowlist (v5; capture `transcript` v6; `extracted_fields_json`+`confidence` v7) | **precious** | **never auto-rebuilt** | `workspace.SCHEMA_VERSION` (`user_version`) |
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

## WAL mode & backups (v5)

`Workspace.connect` opens `workspace.db` in **WAL** journal mode with a 5 s `busy_timeout` so the
conversational-intake worker — a separate process ([ADR-021](../03-decisions/adr-021-intake-lan-binding-minimal-auth.md))
— can write the `captures` queue alongside the webapp instead of mutually blocking under the default
rollback journal. Consequence for the **precious** store: WAL keeps committed-but-not-checkpointed data
in the **`workspace.db-wal`** sidecar (alongside `workspace.db-shm`). **A backup MUST capture all three
files together, or use the SQLite Online Backup API / `VACUUM INTO`** — a naive `cp workspace.db` alone
can lose the latest committed decisions. A clean connection close checkpoints the WAL back into the main
file. This matters doubly once intake media becomes the sole copy
([ADR-020](../03-decisions/adr-020-capture-egress-and-data-handling.md) preserve-at-core).

## Dangling references

`project_threads.thread_root` points into the regenerable CRM. A CRM rebuild can orphan a
reference; `project show` and the web UI flag these as **dangling** so a Project never silently
loses messages.

## Project lifecycle

`LEAD → GATHERING → ESTIMABLE → QUOTED → WON | LOST`, plus `CANCELLED` (called off in flight) and
`ARCHIVED` (soft-retire, hidden by default). A successful export advances a Project to `QUOTED`
([ADR-011](../03-decisions/adr-011-export-honesty-boundary.md)). `CANCELLED`/`LOST` carry a **close-out**
— `close_party` (client/supplier/our) + `close_reason` + `closed_at`, cleared on reopen
([ADR-017](../03-decisions/adr-017-project-close-out-lifecycle.md)).

## Ownership & roster (v4)

Owners are a **set**, not a single field: `thread_owners(thread_root, owner)` and
`project_owners(project_id, owner)` join tables (the pre-v4 single `thread_state.owner` is backfilled
into `thread_owners` and then vestigial). The owner **roster** is `settings.team` (config, ordered)
plus an in-app `roster(name)` table — the effective roster is their union, computed per request so a
newly-added owner needs no restart ([ADR-018](../03-decisions/adr-018-multi-owner-and-in-app-roster.md)).
The per-project **participants** view (`GET /api/projects/{pid}/participants`) is a read-only rollup of
the ADR-015 ledger's `asserted_by` — who fed knowledge into the project.

> **Migration note:** v4 added the close-out columns + the three tables above. v3 added the ADR-015
> provenance columns. Each is a guarded `ALTER`/`CREATE IF NOT EXISTS` in `_migrate`, pinned by
> `tests/test_workspace_migration.py` (a prior-version DB *with rows*).
