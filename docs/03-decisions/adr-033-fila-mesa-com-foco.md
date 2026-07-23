# ADR-033 — Fila «Mesa com Foco»: split-pane cockpit, counterparty fronts as tabs, risk as the default order

- **Status:** Accepted (owner-approved 2026-07-23; lands in phases — see
  [fila-mesa-phases.md](../04-implementation/fila-mesa-phases.md))
- **Date:** 2026-07-23
- **Extends:** ADR-023 (live queue), ADR-024 (evidence in place), ADR-028 (decisions persist),
  ADR-029 (obligation grouping — *refined*, see §3), ADR-014 (URL state)
- **Design detail:** [fila-mesa-design.md](../05-reference/fila-mesa-design.md) ·
  north-star: [cockpit-design.md](../05-reference/cockpit-design.md)

## 1 · Context

An audited redesign study (2026-07-22/23: line-level UI audit, read-only corpus inventory, market
research over ~20 email/ops products, three adversarially-judged concepts) found the Fila renders the
*data model* faithfully but the *decision loop* not at all:

- The page is ~10 viewports tall (112 uncollapsible rows); reaching «À espera deles» costs ~5 screens
  of scrolling and J/K walks rows linearly with no section jump.
- 5 of 8 filters exist only inside ⌘K; clicking a counterparty/purpose badge — the universal
  "filter by this" gesture — opens the *reclassify* picker instead.
- The default sort is `Mais recentes`, so a 6-hour-old thread outranks a 13-day reply debt on first
  open — violating cockpit-design.md principle 1 ("the next move is never a question").
- «sem dono» renders 112× (0 threads owned) and «Gemini · 95%» ~100× — violating the project's own
  repeated-label rule (cockpit-design.md §9).
- The hero page is a static snapshot (violates §7, which Para ti already implements) and hides its
  data age; «Sincronizar» ends in `location.reload()`, discarding position.
- Computed signals never become UX: 64 extracted deadlines shown 0 times; 20 `can_draft` rows
  invisible until expansion; money-at-stake, product descriptions, curated display names,
  per-counterparty rollups, `crm.related()`, and the 72 h chase threshold are all paid for and unused.

## 2 · Decision

Rebuild the Fila as **«Mesa com Foco»** — a full-width, stationary split-pane cockpit:

1. **Split pane.** Left ~40%: the bounded, group-collapsed queue (single-line rows, own scroll).
   Right ~60%: an **evidence dossier** that auto-mounts the riskiest thread on load and re-renders on
   focus — reading never inserts markup into the list, and the dossier **reuses the existing thread
   expansion renderer** (`msgHTML` kit + per-root cache): one render path, no fork. Below ~1100 px the
   panes stack.
2. **Risk is the default order.** The deterministic risk tuple (`cockpit.sort_key`) replaces
   `Mais recentes` as the default; recency stays one keystroke away. A queue whose top item is not the
   highest-stakes item fails principle 1 on every load.
3. **Counterparty fronts are structure, not a filter** *(hard owner requirement, 2026-07-23)*.
   First-class tabs — **Hoje · Clientes · Fornecedores · Leads** — with live counts. Each front
   reframes the same queue: Clientes = obligation + job context; Fornecedores = chase-first («A
   cobrar» leads); Leads = respond-same-day, propose-project, tab lights up on arrival. This
   **refines ADR-029**: obligation remains the primary partition *within* each front (and within
   «Hoje»), it just stops being the only top-level axis. Two honesty rules keep tabs safe: the body
   decides counterparty (ADR-003 — an internal forward of a client PO stays in Clientes), and a
   low-confidence counterparty is never silently guessed into a tab — it goes to Para ti (ADR-006).
4. **Five fixed vistas, no view builder.** Em risco (default) · € em jogo · Prazos · Cobranças ·
   Tratados, on keys 1–5. Vistas and tabs are *projections of one queue in one order* — no second
   data structure, no user-built views, no drag-to-reorder.
5. **Act-and-advance.** Every disposition (E tratado, H adiar, A dono, R responder) auto-focuses the
   next riskiest row; with the dossier stationary, the loop costs zero scrolling. All dispositions
   stay optimistic + undoable (`Z`, ADR-028).
6. **Extracted values render dashed and never outrank the clock.** `entities.money` («€ 4 900?») and
   `entities.deadline` («⚑ 3 d») appear as AI-proposed chips and power the € em jogo / Prazos vistas
   and within-band tiebreaks — but the deterministic clock owns the default order. An LLM value
   reordering the queue above the clock would be a fake number wearing a dashed border.
7. **Repeated labels collapse.** Trust renders as a dot (dashed ring = proposed, solid = committed)
   except on the focused row; «sem dono» disappears as a per-row chip (the rail facet counts it
   once). A label that is identical on every row carries zero bits and camouflages the clock.
8. **The queue says how live it is.** The Fila ports Para ti's ADR-023 poll (30 s, signature-diffed,
   in-place, focus keyed by `thread_root`) and shows a freshness stamp («correio há N min», louder
   when stale). «Sincronizar» stops reloading the page.
9. **Adiar (snooze) wakes on date OR new inbound**, whichever first — a hidden thread can never be
   lost to the counterparty's move (non-negotiable #2). New precious table in `workspace.db` (v9).
10. **A conversation timeline, vertical.** The dossier's «Conversa» carries a slim vertical time rail:
    one direction-coded marker per message (click jumps to it), **silence gaps labeled** («6 dias sem
    resposta») rather than compressed away, terminating at **«agora»** where the current open gap is
    colored by the clock band — the response debt made visible as the growing tail of the thread.
11. **Bulk verbs exclude IGNORE structurally.** X/Shift+X multi-select exposes tratado / adiar / dono
    only. A mass silent bin is the one unrecoverable triage mistake (non-negotiable #2). (Automated
    mail never reaches the Fila — tier-0 bins it upstream — so no separate "bundle" surface is
    needed; collapsed counted group headers already fold the low-stakes tail.)

## 3 · Relation to prior ADRs

- **ADR-029** (obligation is the primary partition): *refined, not reverted*. Obligation stays the
  partition inside every front and inside Hoje; counterparty becomes the top-level segmentation
  because the three relationships demand different verbs, not just different filters.
- **ADR-023/-024/-028**: this ADR finishes what they started on the hero surface — the Fila becomes
  live, evidence-carrying, and decision-persistent like Para ti already is.
- **ADR-013/-027/-031 composers**: Phase 3 gives the queue a contextual **R** that maps
  purpose × clock-state to the right client-email composer (follow-up for chases, ask-with-gaps for
  incomplete specs, payment for invoices) — the composers stay deterministic + checked; the Fila only
  *reaches* them.
- **`cockpit.py` docstring** ("recent is the default — what the user asked for"): superseded by this
  ADR on explicit owner decision (2026-07-23).

## 4 · Alternatives rejected

- **One-thread-at-a-time as THE page** (Superhuman/Zendesk-Play taken literally): starves the
  many-fronts overview; survives only as the «Tratar agora» overlay iterating the same order.
- **Briefing/digest grid as the landing surface**: inserts a click between the operator and the
  riskiest thread; violates principle 1 at rest.
- **€/prazo as primary sort** (from the owner's own design study — adopted as vista + tiebreak
  instead): LLM values must not own the order (no-fake-numbers).
- **User-built views / manual reorder** (Notion Mail, Shortwave patterns): contradicts "the next move
  is never a question"; market evidence (Notion Mail shutdown) concurs.
- **Hey-style permanent sender screener**: one wrong screen-out silently loses a client forever.
- **Inbox-as-chat**: every action becomes a typed negotiation — the inverse of one-keystroke
  throughput.
- **A second Fila surface** (separate list mode / minimap rail): two representations of one queue
  guarantee divergence; overlay modes iterate the same queue instead.

## 5 · Consequences

- `fila_page.py` is rebuilt around the split pane; the thread renderer, undo stack, POST endpoints,
  `cockpit.py` fold/clock/sort, and `workspace.db` persistence all survive unchanged.
- `/api/fila` rows gain joined fields (display name, entities, related count, chase/momentum,
  project readiness, `synced_at`) — each with provenance, absent when unextracted (never «—» filled).
- `workspace.db` migrates to v9 (thread_snooze) in Phase 3 — a precious-DB migration with the usual
  guarded-ALTER discipline (ADR-010).
- The old flat-list rendering is deleted, not kept as a mode (rejected: second surface).
- Phases and their DoD live in [fila-mesa-phases.md](../04-implementation/fila-mesa-phases.md);
  the full anatomy/spec in [fila-mesa-design.md](../05-reference/fila-mesa-design.md).
