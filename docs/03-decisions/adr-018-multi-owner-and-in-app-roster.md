# ADR-018 — Multi-owner (threads + projects) and an in-app roster

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-06-15 |

## Context

Work is co-owned in reality: two people chase the same lead; a project spans whoever's involved. The
app modelled ownership as **one** `owner` string per Fila thread, **none** on projects, and the roster
was a static `team` list hard-coded in `settings.json` — so you couldn't assign several people, give a
project an owner at all, or add a teammate without editing config.

(Separately, "multiple people reporting information into a project" was already supported by the
ADR-015 capture ledger — every claim/event carries `asserted_by`. The gap there was *surfacing*, not
data; this ADR also adds a read-only **participants** rollup of that ledger.)

## Decision

Ownership becomes a **set**, and the roster becomes **editable in-app** (precious-DB v4):

- Two join tables, `thread_owners(thread_root, owner)` and `project_owners(project_id, owner)` (mirror
  `project_threads`). The legacy single `thread_state.owner` is **backfilled into `thread_owners`** on
  migration and thereafter read/written only via the join (the old column is left vestigial — sqlite
  can't drop it). `set_thread_owner` is kept as a single-owner shim over `set_thread_owners`.
- A `roster(name)` table holds in-app-added owner names. The **effective roster = `settings.team` (in
  its configured order) ∪ the in-app names**, computed per request so a new owner appears without a
  restart. `settings.team` names are not removable in-app (they live in config).
- Endpoints: `POST /api/thread/owner` accepts `{owners: [...]}` (or legacy `{owner}`);
  `POST /api/projects/{pid}/owners`; `GET/POST /api/roster` + `/api/roster/remove`;
  `GET /api/projects/{pid}/participants` (the asserted_by rollup). The Fila owner chip and the Projetos
  owners bar are multi-select pickers with a "+ novo dono" that writes the roster.

## Consequences

- Several people can co-own a Fila item and a project; you can define new owners from the UI.
- Ownership is regenerable-adjacent but lives in the precious DB (it is a human decision); a roster
  rename orphans old owner values (names are the key — acceptable for a single-user shop; stable IDs
  were considered and deferred).
- Pinned by `tests/test_workspace_migration.py` (backfill + roundtrip + roster),
  `tests/test_project.py` (project owners), `tests/test_webapp.py` (endpoints + effective roster +
  participants). Trace: `workspace.py`, `project.py`, `webapp.py`, `fila_page.py`, `projetos_page.py`.
  Surfaces [ADR-015](adr-015-knowledge-capture-claim-ledger.md); uses
  [ADR-010](adr-010-workspace-db-precious-vs-regenerable.md) migration discipline.
