# ADR-014 — Cockpit views are RESTful and deep-linkable: ids in the path, filters in the query

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-06-10 |

## Context

The cockpit lens pages (Fila, Projetos, Contrapartes, Para ti) are single-page views that toggle
list ↔ detail and apply filters in client memory. Only Contrapartes named its open object in the
URL (`/contrapartes/<key>`). Projetos opened a project through a transient `?p=<pid>` deep-link
that `history.replaceState` **wiped** on arrival; the Fila's `?focus=<root>` deep-link was wiped
the same way; and the Fila's counterparty filter and its expanded thread were never reflected in
the URL at all. The Contrapartes "Projetos" card even linked to a dead `/projetos#<pid>` hash.

The effect: a detail view or a filtered list could not be refreshed, shared, or bookmarked, and the
address bar never named what was on screen. The old static report (`/inbox`) already solved this for
itself with a generic facet + URL-state engine (`writeURL`/`applyURLState`), but the cockpit pages
never adopted it.

## Decision

Every cockpit view reflects its state in the URL, under **one rule with two shapes**:

- **Full-page detail resources carry their id in the path** — a REST resource URL. `/projetos/<pid>`
  (new) joins `/contrapartes/<key>` (existing). The server serves the lens HTML for that route and
  **404s on an unknown id** (a stale/shared link fails honestly, like the Contrapartes detail); the
  page JS reads the id from `location.pathname` and opens that detail; opening/closing uses
  `pushState`/`popstate`, so the browser Back button returns to the list.
- **List filters and inline view-state ride in the query string** — kept in sync with `replaceState`
  (the same approach the report uses), so the URL is shareable and survives a refresh without
  spamming the Back history. The Fila writes `?counterparty=<CP>` for its filter and `?thread=<root>`
  for the expanded thread. The legacy `?focus=<root>` link is still honoured, then canonicalized out
  of the address bar.

Why two shapes, not one: a project/contraparte detail **replaces** the list (a navigated-to resource
→ path segment, history-navigable), whereas a Fila thread **expands in place** inside a still-visible,
possibly multi-open list (view-state on a list → query param, sync-only). When several threads are
open the URL tracks the single most-recently-opened one — a deliberate simplification, since the
query string holds one `?thread=`.

## Consequences

- Detail views and filtered lists are now refreshable / shareable / bookmarkable; the address bar
  always names what is on screen. No new filters were invented — only the state the user already
  drives (the Fila counterparty filter, the expanded thread, the open project) is surfaced.
- Cross-links were repointed to the canonical path form: the Fila project chip and the Contrapartes
  "Projetos" card now navigate to `/projetos/<pid>` (the card previously used a dead `#<pid>` hash;
  the chip used the transient `?p=`).
- Para ti stays a pure decision list (no detail resource, no filter), so it gains no URL state.
- Trace: `src/email2data/webapp.py` (`GET /projetos/{pid}`), `projetos_page.py`
  (`_pidFromURL`/`loadDetail`/`closeDetail` + `popstate`), `fila_page.py`
  (`syncURL`/`setFilter`/`applyURLState` + `popstate`), `contrapartes_page.py` (project card link).
  Tests: `tests/test_webapp.py` (`test_projetos_detail_route_serves_page_and_404s`), `tests/test_fila.py`
  (`test_projetos_page_reflects_open_project_in_url`, `test_projetos_detail_route_serves_the_lens`,
  `test_fila_page_reflects_filter_and_open_thread_in_url`).
- The text-presence tests above only prove the URL JS is *shipped* — a `TestClient` never runs it.
  The acceptance criterion that it *works* is `tests/test_cockpit_urls_e2e.py`: it serves the app on a
  loopback port and drives a real Chrome (Playwright, the opt-in `e2e` extra) to assert that clicking
  a project row pushes `/projetos/<pid>`, Back returns to the list, a deep-link load opens the detail,
  and the Fila `?thread=` / `?counterparty=` params drive the view. Skipped when the extra is absent.
