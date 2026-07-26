# ADR-044 — Início: the landing page is a decision, not a cockpit

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-07-26 |
| Relationship | Extends [ADR-033](adr-033-fila-mesa-com-foco.md) and [ADR-034](adr-034-fila-chrome-fronts-and-scoped-rail.md). Supersedes nothing. |

## Context

`/` served the Fila. Owner feedback, verbatim: *"the main page has too much information on first
impact."*

That is a fair reading of what the Mesa actually puts on screen the moment it loads. Measured on the
live install (2026-07-26, 114 active threads), arriving at `/` presented **simultaneously**:

* a four-card counterparty hero strip (Hoje 114 · Clientes 74 · Fornecedores 40 · Leads 0), each with
  its own two-number demand line;
* a 172 px vistas rail — 5 vistas, 4 «tipo de pedido» facets, 3 «estado» facets, each with a count;
* a 56-row queue under three obligation headings;
* a **fully mounted dossier** for whichever conversation happened to sort first: AI analysis, thread
  ledger, product/pedido/nome fields, a contact card, a related-conversation list, and a timeline;
* a search box, two select filters, a sync pill, an account chip and a settings gear.

Roughly 300 discrete pieces of information, none of them wrong. The Mesa is the right screen to
**work** in and the wrong screen to **arrive** at: the first thing it asks of you is to read it, and
the one question arriving actually poses — *what do I do first?* — is answered nowhere in particular,
because every filter that might refine that answer later is competing for the same first glance.

This is a presentation problem, not a data problem. Every number Início shows already existed; ADR-034
had already established the vocabulary (fronts, scoped demand, calm-at-zero). What was missing was a
screen that shows **only** that.

## Decision

**1. `/` is Início. The Fila moves to `/fila` and is otherwise untouched.**

`/fila` already existed as an alias, so this is a removal of `@app.get("/")` from the Fila route
rather than a move. Every deep link (`?tab=`, `?thread=`, `?counterparty=`, `?vista=`, …) works
unchanged, because `syncURL()` rebuilds the address from `location.pathname` and never hard-codes the
prefix. `/` does **not** redirect to `/fila` — a redirect would be this same regression wearing a 303.

**2. Início is organised by counterparty, and shows four numbers per card and no rows.**

Three counterparty fronts (Clientes · Fornecedores · Leads) plus Para ti, as four large buttons.
Chosen over a by-destination menu (which is a nav bar with bigger fonts, and states nothing) and over
a by-decision layout («Responder agora» / «Em risco»), because the counterparty **is** how this shop
divides its day, and the fronts already carry a validated visual language from ADR-034.

Each card links into the Fila front it describes (`/fila?tab=CLIENT`), so the landing page hands the
cockpit a filter rather than dropping you at its front door.

**3. Two highlights earn a place, and no others.**

* **the demand split** — «N a responder · N a aguardar», the "is the ball in our court" number;
* **the longest-stalled thread we owe** — «a mais antiga está parada há N dias».

`€ em jogo` and `Prazos` were considered and **deliberately left in the Fila's vistas rail**. Both are
real, both are useful, and putting them here would have started the page down exactly the road that
produced the complaint: every individually-defensible addition is how the Mesa got to 300 items.

**4. «A mais antiga» is scoped to what we owe, never to the oldest row.**

`oldest_owed_hours` filters on `owes_reply` first. The oldest row overall is typically an ancient FYI
nobody is waiting on; reporting that would put a large, frightening and **permanently un-actionable**
number on the landing page — one that no amount of work would ever reduce. A highlight has to be a
promise that acting on it makes the number go away.

**5. Demand is defined once, in Python, in `cockpit.py`.**

It had been written three times independently: `webapp._nav_counts` (the nav badge),
`respondCount(list)` and `chaseCount(list)` (the Fila's JS). Início would have been a fourth. Three
surfaces show that number **in the same viewport** — the nav badge, the headline, and the front card —
so a drift between them is a screen that visibly contradicts itself.

`cockpit.owes_reply` / `awaits_chase` / `respond_demand` / `chase_demand` / `oldest_owed_hours` /
`home_summary` are now the definition; `_nav_counts` calls `respond_demand`. The JS keeps its copy
(the Fila re-derives client-side after a poll, by design), and
`test_the_python_and_js_definitions_of_demand_are_the_same_rule` reads the JS source and fails if
either side changes alone. A comment saying "keep these in sync" is not a mechanism.

**6. Every number is scoped, and the page says so.**

`home_summary` returns one block per front plus `"all"`, and each block counts only its own
counterparty regardless of what is being viewed — ADR-034's rule, carried onto the landing page. The
headline is labelled **HOJE**. A count that can be misread as a global total is the 58-vs-32 confusion
ADR-034 already fixed once in the Fila.

**7. Calm at zero.** Colour appears only when something demands you. A morning with nothing owed
renders quiet — not green, not a wall of ✓. A signal that is always on carries no information.

**8. The page renders its answer from the first byte, and never fetches on load.**

Everything is embedded server-side; the only network call is `/api/inicio`, used to repaint in place
after a sync (ADR-023 §7). A landing page whose first impression is a spinner has failed at the one
job it has. `test_the_page_renders_its_answer_without_a_fetch` asserts that `render()` contains no
network call at all.

**9. Início is not a lens, so no nav item claims it — the logo does.**

`active="inicio"` leaves every `nlink` inactive, and the logo becomes an `<a href="/">` carrying the
active state. The nav strip stays as it was (the owner chose to keep it): a header with nothing marked
reads as *"you are nowhere"*, which is the exact complaint this page exists to answer.

## Consequences

**What got better.** The first glance is one sentence and four buttons. The numbers on it are the
same numbers the Fila shows, now provably so. And the app gained a place to *stand* — before this,
every visit began mid-task.

**What this costs.** One more page to keep honest, and a second definition of "the queue's shape" to
keep aligned with the Fila's JS — mitigated by the source-reading test, not by discipline.

**What must not happen.** Início must not accumulate. `test_the_page_is_the_four_buttons_and_carries_
no_queue` fails if `.mesa`, the vistas rail, the dossier, the filter bar or a row list appear on it.
That test is the ADR: each addition will look reasonable on its own, and the sum of reasonable
additions is the screen that prompted this.

**Known limits.**

* The Fila's JS still owns its own `respondCount`/`chaseCount`. Unifying them means shipping the
  Python definition to the client, which is a bigger change than this one earns; the pin covers the
  drift risk in the meantime.
* `€ em jogo` and `Prazos` are absent by choice, not by oversight. If the owner wants either, it is a
  one-line addition to `home_summary` plus a card — but it should cost an explicit decision.
* Início shows the whole install's demand. Per-person visibility is still unenforced (ADR-040 Phase
  D), so this page inherits that limit exactly like every other lens.

## Verification

* `tests/test_home_page.py` — 14 checks: the demand definitions (including the JS-source pin), the
  scoping, the oldest-owed rule, empty and malformed queues, the restraint, no-fetch-on-render,
  `person` forwarding, calm-at-zero, and reachability of the secondary destinations.
* `tests/test_fila.py::test_fila_lives_at_its_own_path_and_no_longer_owns_the_root` and
  `::test_inicio_and_its_api_are_built_from_the_same_queue`.
* `tests/test_cockpit_ui.py::test_the_logo_is_the_way_back_to_inicio`.
* Looked at, not only tested: rendered against the real 114-thread install (light **and** dark),
  clicked through to `/fila?tab=CLIENT`, and confirmed the Fila's own rail then read
  «26 a responder · 37 a aguardar» — the same pair the card had promised.
