# ADR-015 — Off-email knowledge capture: one provenance-rich claim ledger, deterministic, desktop

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-06-14 |

## Context

A project's facts are decided across many channels — phone calls, meetings, WhatsApp, SMS — not
just email. Today only email-derived facts reach a project's canonical spec; everything else lives
in someone's head. We want to capture that off-email knowledge into the **same** per-project shape,
with provenance (who said it · on what channel · when it was acquired) and an auditable timeline,
including surfacing genuine source disagreement.

This ADR records the decisions taken **after an adversarial red-team of the original plan** (45
agents; see the review that preceded this commit). The red-team broke two of the original
assumptions and pointed at three pre-existing defects; the decisions below reflect the corrected
design.

## Decision

1. **One claim ledger, not a parallel store.** Off-email knowledge reuses the existing precious
   tables. Field-claims (the 14 `jobspec.FIELDS` + custom) keep flowing through `project_fields`
   (current value) + `project_field_history` (append-only audit). Non-field knowledge
   (`NOTE` / `DECISION` / `OPINION` / `TODO`) is appended to **`project_field_history`** under a
   reserved field namespace (`__note__`, `__decision__`, …) with `op='event'` — exactly the
   precedent of the reserved `__n_items__` decision key (`workspace.ITEM_COUNT_FIELD`). The
   **timeline is therefore one indexed SELECT over a single table** (no second table, no UNION).
   *Rejected:* a separate `project_events` table — it doubled the migration surface on the one DB
   that is never rebuilt, for no query we actually need.

2. **Provenance is real columns, not an overloaded `source_mid`.** `project_fields` and
   `project_field_history` gain `channel`, `asserted_by`, `acquired_at`. `acquired_at` (when the
   knowledge was acquired in the real world) is distinct from `ts` (when it was recorded). Legacy
   rows backfill to `channel='email'` (a real `source_mid`) or `'manual'` (the `''`/`'user'`
   sentinels).

3. **Conflict = contradiction, not supersession.** `merge_job_fields` now flags a conflict ONLY
   when ≥2 distinct values **tie at the winning precedence rank** (equal-authority sources
   disagree). A value cleanly superseded by a higher-rank source — a user override of a stale
   `offline` value, or any normal refinement across messages — is **supersession**, shown in the
   timeline, never flagged. This fixes a live over-firing bug (the old code ignored `best_rank`,
   contradicting its own docstring, and lit up `report.py` / `cli.py` on every curated project).
   The conflict payload is now `{key: [{value, source, ref}, …]}` so the UI can show who said what.

4. **Capture is deterministic and desktop; NO LLM in the v1 path.** This app is single-user,
   loopback, desktop. Door-1 (the structured spec, made editable-in-place) plus a freeform typed
   `NOTE/DECISION/OPINION/TODO` meets the goal with zero LLM surface — honoring the
   zero-hallucination rule (no machine ever invents a field address) and the deterministic-composer
   precedent of [ADR-013](adr-013-client-email-composer-deterministic.md). *Deferred:* an LLM
   "dump → bullets" chunker (Door-2). If ever built, deterministic chunking is the DEFAULT, the LLM
   step is opt-in per note, and `DECISION`/`OPINION` (private content) stay local-only — verbatim
   call/opinion notes must not auto-ship to Vertex.

5. **Custom fields render through a dedicated channel, never the registry overlay.** A custom field
   is stored in `project_fields` like any field, but the canonical read path (`canonical_spec` →
   `to_dict` → API → page) must carry it on a separate `custom_fields` map, because `js.confirm`
   silently no-ops non-registry addresses and `readiness`/`askables` iterate the static registry.
   Custom fields are **tier=context**: they appear in the workbench and timeline but **never touch
   the estimable gate**. (Promote-on-recurrence into `jobspec.FIELDS` is the path to making one
   count toward estimability.)

6. **Route/UX compliance.** Capture is project-scoped, not a new top-level resource
   ([ADR-014](adr-014-restful-deep-linkable-cockpit-urls.md)): the spec/timeline/capture live on
   `/projetos/{pid}` behind a tab strip; capture opens as `?registar=nota` **query view-state**
   (`replaceState`, preserved across the existing path-based `popstate`/legacy-`?p=`
   canonicalizer). The global "＋ Registar" navigates to `/projetos/{pid}?registar=nota`
   (`pushState`). Capture UI is a styled non-blocking surface (reuse the ⌘K palette), never
   `window.prompt()` (single-line, blocking, untestable).

7. **LLM endpoints run off the event loop.** Any LLM call is dispatched via `anyio.to_thread`
   (mirror `/api/sync`), NOT run inline like the old `/api/reply`. A blocking Gemini call on the
   single worker would freeze every other request.

## Consequences

- **Performance:** the projects list reads denormalized `coverage`/`estimable` (recomputed on
  write/sync) instead of `build_canonical` per row; `_fila_rows`/`_clusters` are memoized per
  request; the stale comment at `webapp.py` claiming the list avoids per-row recompute is now true.
- **Migration:** `workspace.SCHEMA_VERSION` 2 → 3 with a real, guarded `ALTER TABLE ADD COLUMN`
  block in `_migrate` (CREATE TABLE IF NOT EXISTS cannot add columns to an existing table). Pinned
  by a regression test that upgrades a v2 DB **with rows** and asserts both column presence and a
  provenance-bearing round-trip — see [ADR-010](adr-010-workspace-db-precious-vs-regenerable.md),
  whose "additive via CREATE TABLE IF NOT EXISTS" guidance is corrected to *new tables only;
  new columns require `_migrate`*.
- **Trace:** `src/email2data/project.py` (`merge_job_fields` conflict gate, `canonical_spec`
  custom-field channel, `coverage_for`), `workspace.py` (`_migrate` v3, provenance columns,
  `set_field`/`add_event`/`field_provenance`/`timeline`), `webapp.py` (`/api/projects/{pid}/event`,
  `/api/projects/{pid}/custom-field`, `/api/projects/{pid}/timeline`, off-thread reply, denormalized
  list, memoized nav), `projetos_page.py` (tab strip, capture overlay, provenance/conflict chips,
  timeline render, `?registar=` view-state). Tests: `tests/test_project.py`,
  `tests/test_workspace_migration.py`, `tests/test_webapp.py`.
