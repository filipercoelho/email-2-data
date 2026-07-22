# ADR-023 — The decision lenses stay live: scheduled ingestion + self-refreshing pages

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-07-19 |
| Extends | [ADR-009](adr-009-incremental-idempotent-by-default.md) (the watermark is what makes a schedule affordable), [ADR-016](adr-016-post-audit-resilience-hardening.md) §startup sync |
| Supersedes | nothing |

## Context

The owner asked that `/para-ti` be "always up to date with the current emails, projects and other
items that require my attention — up to date, not cached."

Investigating the framing first (the request names *caching*, but that turned out not to be the
fault):

- **The render path was already live.** `/para-ti` and `/api/para-ti` rebuild the queue from `crm.db`
  + `workspace.db` on every request — `_fila_rows()`, `_clusters()` and `_para_ti_items()` are
  recomputed per call, with no memoization anywhere. Nothing server-side was serving a stale queue.
- **Ingestion was the actual staleness.** `_run_sync()` had exactly two triggers: the lifespan
  startup thread (ADR-016) and a manual `POST /api/sync` behind the "Sincronizar" button. A server
  left running for six hours therefore served mail as of six hours ago, perfectly freshly rendered.
  Confirmed live before the change: `/api/sync/status` reported `last_ts` frozen at boot time.
- **An open tab never re-fetched.** The lens had no poll, so even after a sync the page in front of
  the user showed the queue as of page load.
- **Nothing forbade HTTP caching** on either route, leaving browser revisit/bfcache free to re-serve
  a stale page.

So "not cached" was a correct description of the *symptom* and a misleading one of the *cause*: the
page was never cached, the data behind it had simply stopped moving. Cache-busting alone would have
changed nothing.

## Decision

1. **A periodic background sync is a first-class ingestion trigger.** `periodic_sync_loop` ticks
   `_run_sync` every `sync.interval_minutes` (**default 15**, owner-chosen 2026-07-19); `<= 0` or an
   unparseable value disables it and leaves startup + the button as the only triggers. It is a daemon
   thread started in the lifespan alongside the startup sync.

2. **The schedule reuses `_run_sync` rather than re-implementing ingestion**, so it inherits both of
   that function's contracts: the *non-blocking* lock (a tick landing on a manual sync is **skipped,
   not queued** — the next tick covers it) and never-raises. The loop guards anyway and keeps
   ticking through a failure: a dead thread would mean silent staleness for the whole session, which
   is precisely what this ADR exists to prevent.

3. **Affordability comes from ADR-009, not from restraint.** The UID watermark means an idle tick
   spends **zero Tier-1 tokens** — only a read-only IMAP check. This is what makes a wall-clock
   schedule compatible with ADR-001: the compute follows genuinely-new mail, not the clock.

4. **The lens refreshes itself in place.** `/para-ti` polls `/api/para-ti` every 30 s (only while the
   tab is visible, plus an immediate catch-up on `visibilitychange`) and swaps the item list,
   preserving scroll, focus and dismissals. **Not** a reload: `location.reload()` mid-decision throws
   away the user's position. The DOM is left untouched when the queue signature is unchanged.

5. **Remembered per-item state is keyed by CONTENT, never by list index.** `itemKey(item)` =
   `kind | thread_root ?? email ?? title`. This is load-bearing, not stylistic: `dismissed` formerly
   held list indices, which were correct only because the list could never change. Once a refresh can
   reorder the queue, an index silently re-points at a *different* decision — resurrecting a
   dismissed card and hiding a live one. In a queue whose whole job is "never silently bin a client"
   (ADR-003), that is a correctness bug, so it is pinned by a real-browser test.

6. **`/para-ti` and `/api/para-ti` send `Cache-Control: no-store`.** The queue is rebuilt per request,
   so the only remaining way to serve a stale one is an HTTP cache in front of us. Opt out
   explicitly rather than relying on the absence of a validator.

7. **Freshness is shown, not assumed.** `/api/para-ti` returns `synced_at` / `syncing` / `served_at`
   alongside the items and nav counts (one round-trip), and the page renders "correio há N min",
   going amber past ~45 min. If ingestion stalls, the user sees it rather than trusting a queue that
   quietly stopped moving — the honesty tenet applied to freshness.

## Consequences

- A long-lived server now converges on its own; the "Sincronizar" button becomes an impatience
  shortcut rather than the only way to see today's mail.
- IMAP is polled ~4×/hour per account. Read-only throughout (ADR-002 unaffected).
- Token cost is unchanged for an idle mailbox and identical-per-message for a busy one — the schedule
  changes *when* new mail is triaged, never *how often* a given message is.
- `data-nav` was added to the shared nav links so any lens can refresh its badges in place.
- The interval is config, not code: set `sync.interval_minutes` in `config/settings.json`.

## Verification

- `periodic_sync_loop` is module-level precisely so the schedule is testable without an app: tests
  drive it with a 10 ms interval and assert it ticks repeatedly, exits promptly on `stop` (a 60 s
  Ctrl-C hang would be a real regression), and survives both a raising and an error-returning sync.
- Real-browser (Playwright) acceptance in `tests/test_para_ti_live_e2e.py`: a gate resolved
  server-side leaves an open tab on the next poll *without navigating*; badges and the zero-state
  follow; and a dismissal survives a refresh **that reorders the queue** — the index-keyed bug was
  deliberately reintroduced and confirmed to fail that test.
- Live check on the running workspace: `auto-sync every 15 min` logged at boot, `no-store` present on
  both routes, and the API payload carrying `nav_counts` + `synced_at`.
