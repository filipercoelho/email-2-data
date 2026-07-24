# Fila «Mesa com Foco» — phased execution plan

Decision: [ADR-033](../03-decisions/adr-033-fila-mesa-com-foco.md) · full anatomy:
[fila-mesa-design.md](../05-reference/fila-mesa-design.md).

Every phase ends per the project DoD: failing-first regression tests in the matching
`tests/test_<module>.py`, docs updated in the same commit, `ruff check src tests`, full suite green
against the current baseline (690 passed as of 2026-07-23 on `feat/conversational-intake`; state the
new count and why it moved), then `docker compose up -d --build` + `./bin/check-image-drift.sh`
clean before anything is called live. Nothing here changes IMAP posture (read-only), sending
(never), or the precious-store rules (ADR-010).

Baseline facts the plan builds on (verified in code 2026-07-23): the Fila is one server-assembled
page (`fila_page.py` → `cockpit_ui.page()`), all 112 rows embedded as `ROWS` and rendered
client-side; filtering/sorting/grouping are client-side; mutations are optimistic POSTs with a
global undo stack; `cockpit.build_fila` stamps both order keys on every row; Para ti already
implements the ADR-023 poll (`para_ti_page.py` — `refresh()`, `_sig`, `paintFreshness`,
`setNavCounts`); `_sync["last_ts"]` is the freshness source; clusters + display-name overrides are
built per request in `webapp.py`; `crm.interactions.entities` holds the extracted entities JSON;
`workspace.db` is at SCHEMA_VERSION 8 with the guarded-ALTER migration pattern.

---

## Phase 0 — Quick relief on the current renderer (S, ~1–2 days)

Pure `fila_page.py` + one embed from `webapp.py`. No layout change; every item independently
shippable and testable. Goal: the worst audit defects stop hurting *this week*.

| # | Item | Detail | Test (failing first) |
| --- | --- | --- | --- |
| 0.1 | **Risk is the default order** | `order = ORDER_RISK` default in the lens; `?order=` and the sticky localStorage preference still win; the `<select>` reflects it. `cockpit.py` docstring updated (ADR-033 supersedes the "recent default" note). | built HTML asserts default `order = ORDER_RISK`; existing default-order pins updated with the ADR reference |
| 0.2 | **Honest headline** | `#_risk` counts `WE_OWE ∧ band∈{red,amber}` only, labeled «N a responder»; a second chip «N a cobrar» counts `AWAITING ∧ age≥72 h` and click-filters to the chase subset. | JS-fragment assertions: the count predicate + both labels |
| 0.3 | **Collapsible obligation sections** | Group headers get a chevron + click-to-collapse; collapsed state persisted per group in localStorage; **«À espera deles» starts collapsed** on first visit; header always shows its count (it already does). | fragment assertions: collapse handler, default-collapsed WAIT, persistence key |
| 0.4 | **Repeated-chip suppression** | Trust chip renders as a dot except on the focused row; the owner button renders only when owned OR focused (A still works anywhere). | fragment assertions on the conditional renders |
| 0.5 | **Freshness stamp** | `webapp.fila()` embeds `synced_at` (`_sync["last_ts"]`); the bar paints «correio há N min» (reusing Para ti's `_agoLabel` logic, copied — the shared-kit move happens in P1), amber past 45 min. | webapp test: embed present; fragment: paint + stale class |
| 0.6 | **`/` focuses search** | Shell gains an optional lens hook `onSlash()` (default: palette, unchanged for other lenses); the Fila lens overrides it to focus `#_search`. | cockpit_ui test: hook plumbing; fila test: override present |
| 0.7 | **`Shift+J/K` section jump** | Jump focus to the first row of the next/previous group. | fragment assertion on the handler |
| 0.8 | **Row-background click opens the thread** | The whole row (minus buttons/controls) toggles expansion, not just `.rmain`. | fragment assertion |

Out of scope: any layout/pane change, any new server join, any new store.

## Phase 1 — The Mesa (L, ~4–6 days) — the D3 hero rebuild

The split-pane layout + counterparty tabs + row anatomy + dossier. The biggest phase; lands as one
coherent change (the old flat rendering is deleted, not kept as a mode).

- **1.1 Layout**: full-width lens (per-lens CSS override of `.wrap`), command strip, vistas rail
  (rail shows Em risco/Tratados live; € em jogo · Prazos · Cobranças appear P2), list pane + dossier
  pane; stacked below 1100 px. Sticky group headers inside the list pane's own scroll.
- **1.2 Counterparty tabs**: Hoje (landing) · Clientes · Fornecedores · Leads with live counts;
  per-tab grouping per the design §3 (Fornecedores = chase-first uses the P0 chase predicate);
  `T`/`Shift+T` cycle; tab in URL (`?tab=`), ADR-014 style.
- **1.3 Row anatomy**: single-line rows per design §5. Server join (P1's only webapp change):
  `display_name` stamped per row from the per-request clusters (override → derived → contact-name →
  raw). Chips limited to what P1 data has: ✍ (`can_draft`), 📎 (has_attachment — typed 📎PDF lands
  P2), n_messages, «↻» postponed to P2 (needs `related_count`). Scan line = `trust.reason` →
  `subject` until `entities` lands in P2 (fallback chain shrinks, order preserved).
- **1.4 Dossier**: mount point + identity strip + subject + verb bar (E/A/P/C live; R and H render
  disabled-with-title until P3 — no dead-looking buttons: they explain themselves) + Análise IA
  card (reason/confidence/Porquê + reclassify pickers move here) + counterparty history card
  (conversas + € em aberto need P2 rollup join; P1 shows conversas from `n_messages`-per-cluster? —
  no: P1 shows what exists per-request already: cluster `msg_count`, `we_owe_count`,
  `response_risk`, `open_projects` via the same cluster map as 1.3) + staged-draft slot (existing
  `/api/reply` flow relocated) + **Conversa with the vertical timeline** (§7 — client-side over the
  fetched messages + clock) reusing `msgHTML` and `_threadCache` verbatim.
- **1.5 Focus by `thread_root`**: `focus` becomes an id, not an index (ADR-023 prerequisite);
  selection/expansion/scroll all re-keyed; act-and-advance on E/A picks the next id in view order.
- **1.6 Keyboard**: full map minus R/H/X/F/1–5 (later phases); footer legend updated.
- **Tests**: `test_fila.py` restructured with the page — assertions move from flat-list fragments to
  the new anatomy (tabs present with counts, dossier mount, single render path = `msgHTML` called
  from dossier only, focus-by-id, act-and-advance, timeline render including the «agora» band and
  gap labels, display_name precedence). `test_webapp.py`: display_name join. `test_cockpit_ui.py`:
  onSlash hook, kit additions.

## Phase 2 — Live + data joins + vistas (M, ~2–3 days)

- **2.1 Live poll**: `/api/fila` response gains `synced_at`/`syncing`/`nav_counts` (mirror
  `/api/para-ti`); the lens ports Para ti's `refresh()` (30 s, hidden-tab pause, signature diff over
  `thread_root|clock.state|clock.band|handled|owners`, focus preserved by id, quiet first run,
  «N novos» toast). `setNavCounts`/`_agoLabel`/`paintFreshness` move to the shared shell
  (`cockpit_ui`) so Para ti and Fila share one implementation.
- **2.2 Row joins** (in `_fila_rows`, all absent-when-unknown): `entities` (money/deadline/
  product_or_service/action_requested from the dominant interaction's `entities` JSON),
  `related_count` (`crm.related`), `novo` (contacts.first_seen ≤ 14 d), `chase`, `momentum`
  (deterministic, computed in `cockpit.py` beside the clock — unit-tested there), project
  `coverage`/`estimable` on the project chip. *Shipped scope notes (2026-07-23):* the typed
  attachment glyph (📎PDF/IMG) and the related-threads jump-links were deferred here for lack of the
  underlying data, then **both closed 2026-07-24 (P5e, gap-closing):**
  - **Typed 📎 (`crm.db` v4).** `crm.interactions` gained an `attach_kinds TEXT` column
    (`SCHEMA_VERSION` 3→4), derived deterministically at `record()` time from each attachment's
    filename extension → a compact category (`cad>vetor>pdf>folha>img>doc>zip`, ranked by what a
    quote needs). `crm.db` is regenerable, so it populates on the next `build_crm` (the boot sync did
    it — 281/542 interactions typed). `cockpit.fold_threads` unions the categories across a thread
    onto the row; `fila_page._attChip` renders «📎CAD +N» (falls back to a bare 📎 on a pre-v4 db, so
    it degrades, never fakes). Still no invented type — an unknown extension contributes nothing.
  - **Related-threads jump-links.** `_fila_rows` now carries a labelled `related` list (up to 8
    `{thread_root, subject}`, deduped by root) instead of only a count; the dossier renders a `.drel`
    block of `[data-relroot]` links that focus that thread in place when it is in the current queue,
    else navigate to `?thread=<root>` — assembling context before replying, never double-answering.
  - **Timeline/​header time agreement.** The open-debt chip now uses `_humanizeAge` (a JS mirror of
    `cockpit._humanize_age`, which **floors** days) instead of `_fmtGap` (which rounds), so the
    dossier clock and the «agora» debt read the same day-count («11 dias», never 11-beside-12).
- **2.3 Vistas**: € em jogo (money desc, dashed, "valores estimados (IA)" banner) · Prazos
  (days-left asc, red past-due) · Cobranças (chase desc) wired on keys 2/3/4; facet counts under the
  rail (tipo top-4, sem dono, ✍, 📎). NEEDS_REVIEW «rever N» chip in the strip (count from
  interactions priority).
- **2.4 Bulk select**: `X`/`Shift+X`, selection bar «N selecionadas», bulk verbs **tratado / dono
  only** (adiar joins in P3); IGNORE structurally absent; bulk actions loop the existing endpoints
  optimistically with one undo entry for the whole batch.
- **Tests**: cockpit momentum unit tests (band edges); webapp join tests (entities/related/novo/
  chase per row, absent-when-unknown honored); poll fragments (signature fields, focus-by-id
  preserved); vistas predicates; bulk-verb allowlist (a test that asserts IGNORE is *not* reachable
  from selection).

## Phase 3 — Actions with reach (M–L, ~3–5 days)

- **3.1 Adiar**: `workspace.db` **v9** — `thread_snooze(thread_root PK, until_ts, created_ts)`;
  guarded migration per ADR-010 (new table via SCHEMA, version bump, no ALTER needed); wake rule in
  `build_fila`: a snoozed thread is excluded from the active queue **unless** `until_ts` has passed
  OR `last_inbound_date > created_ts` (their move always wakes it — never silently bin). Snoozed
  rows visible in the Tratados-style ledger («Adiadas» section) with the wake time. Endpoints:
  `POST /api/thread/snooze` `{thread_root, until}` / `{thread_root, until: null}` to clear; H menu
  (amanhã 09:00 · 2ª feira 09:00 · +7 d · data livre); `Z` undoes.
- **3.2 Contextual R**: `POST /api/thread/reply-draft` `{thread_root}` — server picks the composer
  per the design §10 mapping (existing `/api/reply` for `can_draft`; else the ADR-031 purpose
  templates via `clientdraft`), returns `{kind, draft}` into the dossier staged slot. Never sends;
  the ADR-027/-031 checks stay server-side where they already live.
- **3.3 Cobrar nudge**: in Cobranças and the Fornecedores tab, R routes to `follow-up` with the
  thread context — the chase list gets its action.
- **3.4 «Tratar agora» overlay (F)**: iterates the current view()'s order one thread at a time with
  progress «N de M»; E/H/A/R advance, `→` skips free, Esc exits; no separate queue or order — it
  *is* view(). «Responder em série» = F over the (2) € em jogo or WE_OWE∩✍ subset — no extra mode.
- **Tests**: workspace v9 migration (fresh + upgrade paths, wake-on-inbound rule in cockpit tests —
  the never-lose-a-client property gets its own regression test); reply-draft route mapping per
  condition incl. the no-JobSpec fallback; overlay fragments; snooze undo.

## Sequencing & risk

P0 → P1 → P2 → P3, each deployed (rebuild + drift check) before the next starts. Highest-risk items
and their mitigations: **one render path** (dossier literally calls the existing `msgHTML`/cache —
verified by a test that the old inline-expansion markup generator is gone), **focus keying** (P1.5
lands before the poll so a refresh can never re-point focus at a different thread), **precious-DB
migration** (v9 is table-only; rehearsed on a copy per the sandbox-verify procedure before deploy),
**payload cost** (joins measured on the real corpus; `related()` memoized per request).
