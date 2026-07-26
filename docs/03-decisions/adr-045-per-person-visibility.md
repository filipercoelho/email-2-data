# ADR-045 — Per-person visibility: the scope seam finally gets a caller

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-07-26 |
| Relationship | Phase D of the multi-user work. Consumes [ADR-038](adr-038-mail-account-attribution.md); extends [ADR-039](adr-039-people-auth-and-the-default-deny-gate.md), [ADR-040](adr-040-the-first-authorization-check-and-the-honest-refusal.md), [ADR-041](adr-041-the-roster-becomes-people-and-a-person-owns-their-account.md) |

## Context

[ADR-038](adr-038-mail-account-attribution.md) recorded, durably and per message, which of *our*
inboxes each message reached — and said plainly that it contained **no policy**: `scopes.visible()`
was *"the seam Phase D still owes a caller"*. It had **zero** callers. Every signed-in person saw
every thread, every body, every attachment and every project, while «A minha conta» listed inbox
scopes as an editable thing. ADR-040 and ADR-041 both closed by naming this as the outstanding limit.

Two things had to be true before a filter could be written, and only one of them was.

**The scope vocabulary was too narrow, and it would have silently binned client mail.**
`_known_scopes()` — the closed list a grant is validated against — offered only
`imap.accounts[].username` plus the unattributed bucket. Measured on the real corpus on 2026-07-26:

| | |
| --- | --- |
| Addresses in `sync.message_scope` | **10** |
| Grantable (configured accounts) | **4** |
| Threads no grantable scope could reach | **22 of 374 (5.9%)** |
| Of those, carrying `sem-atribuicao` | **1 thread in the whole corpus** |

ADR-038 chose the **address** as the token precisely so inboxes we never fetch stay grantable — mail
reaches them by Cc, by forward, by delivery to an alias (`margarida.reis@`, `lindoservico@`,
`carmen.martins@`, `silva@`, `julio.morais@`, `recrutamento@`). Validating grants against the
configured four would have made those 22 threads ungrantable: invisible to every non-admin, with no
way to fix it from the UI, and **not** caught by the admin-visible unattributed bucket, which reached
exactly one of them. That is the "never silently bin a client" non-negotiable being violated through
a *permission vocabulary* rather than through a classifier — a quieter route to the same lost revenue.

**The suite could not see this feature at all.** Every test signs in as an admin, and an admin
bypasses `person_scopes` unconditionally, so a visibility filter is invisible to ~1100 green tests.
The obvious repair is worse than the gap: sign in a member, assert `200`, and the test stays green
while every lens renders empty — ADR-040's dishonest refusal reborn one layer down.

## Decision

**1. One choke point, and it filters `ints`, not `rows`.** `_fila_rows` filters the interaction list
*before* `cockpit.build_fila`. Everything downstream is recomputed from `ints` rather than from
`rows` — thread summaries, the outbound-only contact fallback, the entity join, the `novo`/`first_seen`
derivation. Filtering `rows` afterwards would leave «↻ 5 relacionadas» pointing at threads the reader
cannot open: a filter that hides the row and keeps the pointer is a leak with extra steps.

**2. `person` is a required keyword with no default.** Not even a safe `None` default. A default of
any kind lets a new call site omit it and silently inherit someone else's visibility; with none, a
forgotten call site is a `TypeError` the suite raises immediately. Default-deny expressed in the
signature rather than in a convention someone has to remember. `person=None` itself yields the empty
set — the gate assigns `request.state.person` only after authorising, so a render path with no person
is unguarded or mid-gate, and neither earns the queue.

**3. The related-list is filtered separately, because it queries the store directly.**
`_crmdb.related()` does not inherit the `ints` filter. Measured before this second gate existed: a
member scoped to one inbox saw **26** «relacionados» entries pointing at threads they could not open,
each leaking a real client subject and a jump-link that 404s.

**4. Every id-bearing route the row-filter does not cover is closed at the route.** `/api/thread/{root}`
(bodies), `/api/relations/{mid}` (both the entry check *and* each returned bucket), and
`/api/attachment/{mid}/{i}` — the last of which reads the corpus file directly with no crm join, so it
is the one escape that hands over real bytes. Refusals are **404, not 403**: a 403 confirms the thread
exists, which is most of what an unauthorised caller wanted. ADR-040's honest-refusal rule governs a
person's *own* surfaces, not someone else's mail.

**5. Projects are gated in the middleware, and their visibility is derived** (owner decision,
2026-07-26). Projects never touch crm.db and have no scope column, so visibility had to be invented:
**you may see a project if you may see any of its threads** — the same union rule
`scopes.thread_scopes` already uses, and the same safe direction. There are **23** id-bearing project
routes, all shaped `/api/projects/{pid}[/...]` or `/projetos/{pid}`, so one path rule in the auth
middleware covers the whole surface *by construction* — ADR-040 §1's argument applied to data. A
project with **no threads yet is admin-only**: nothing is attached, so there is no evidence anyone
should see it, and visible-until-first-thread would make every new project briefly public.
*Rejected:* `project_owners`, whose `owner` column is a free-text **name** rather than a `person_id`,
so it cannot be validated without a name join and existing rows carry owners matching no person —
projects would have vanished for everyone.

**6. `/inbox` becomes admin-only** (owner decision). The legacy report renders every body from a
startup-bound closure, joins no crm data, and `report.build_html` takes no `person` at all. Admin-only
is honest and total; a half-filtered report would look filtered and not be, which is the exact failure
this phase exists to prevent. The static `out/report.html` is unaffected — it was always a full-corpus
artefact.

**7. Counts are filtered too, or they contradict the page they sit on.** `_nav_counts`,
`_needs_review_count`, the Contrapartes `stats` rollup and timeline, and the per-cluster
«open projects» tally all take the reader. A badge is a claim about work waiting for *you*; counted
unfiltered it says «7 a responder» over a queue showing three, and the nav is the number people trust,
so the disagreement resolves in favour of the lie. **The trap worth naming:** `_nav_counts` is called
from *inside* `_auth_gate` to render the 403 page, where `request.state.person` is not yet assigned —
so the person is passed explicitly there, or the refusal page leaks aggregate demand to exactly the
person being refused.

**8. The scope vocabulary is what mail actually reached, not what is configured.** `_known_scopes()`
now unions `sync.message_scope`'s distinct addresses with `imap.accounts[].username`. Typos are still
refused — widening the vocabulary must not turn validation off.

**9. An empty queue says *why* it is empty.** A non-admin with zero grants gets «Sem caixas
atribuídas — não tens nenhuma caixa de correio atribuída… pede a um administrador que te atribua uma»,
never «✓ Tudo tratado». The two states are indistinguishable from the rows alone and only one is good
news; asserting the good one on no evidence is the failure a person acts on by walking away from
waiting work. The copy deliberately does **not** name «Administração → Pessoas» — a member cannot open
it, and ADR-041's rule is that a locked door is not offered.

**10. The scope map is cached by store mtime, not per request.** `scopes.thread_scopes` scans all of
`message_scope` plus all of `interactions`, and `_fila_rows` runs up to four times on one
`/contrapartes/{key}` render. Both stores are rebuilt only by a sync, so mtime is a sufficient key —
and a stale map can only ever be *narrower* than the truth for a moment, never wider, because a new
message starts unattributed and unattributed fails closed.

## Consequences

- **Verified on the real corpus, and in a real browser** (2026-07-26). Against a copy of the live
  `crm.db` + `sync.db` (374 threads): admin **374**, a member scoped to `margarida.reis@` **67**,
  a member with no grants **0**; every member's set a strict subset of the admin's. Probing a thread
  outside a member's grants: `/api/thread` **404** (27 bytes), `/api/relations` **404**, `/inbox`
  **403** — all **200** for the admin. `/api/relations` on an *allowed* thread returned **0**
  cross-thread leaks. «relacionados» pointing at genuinely invisible threads: **0** (the 14/12/10 that
  remain point at *handled-but-visible* threads, which the ADR-037 badge exists to show).
- **The first leak measurement was wrong, and the correction matters.** The initial count of 26
  «relacionados» leaks was measured against the *active* queue, which flags a legitimate pointer to a
  HANDLED-but-visible thread as a leak. Re-measured against what the reader may actually open, the
  post-fix figure is 0. A visibility metric that cannot tell "hidden" from "not currently listed"
  will report both a false alarm and, eventually, a false all-clear.
- **Rendered in Chrome on the real data**, because `TestClient` cannot run the JS that draws the empty
  state: admin 114 rows, member-with-grants 57, member-with-no-grants 0 rows with the honest banner
  and **no console errors**. That render caught a real defect no Python test could have: the empty-state
  branch was first written against a `D.no_scopes` object that does not exist in this codebase —
  embeds are emitted as `const NO_SCOPES`. It would have shipped as a `ReferenceError`.
- **`sign_in_member` now takes `scopes`**, and the suite's own docstring says why: a member with no
  grants sees nothing, so any test that signs one in and asserts only a status code proves the
  opposite of what it claims.
- **Admins are unaffected**, deliberately and by the same code path — `_visible_roots` returns `None`
  (no restriction) for an admin, so the unattributed bucket stays *watched* rather than merely
  not-hidden.
- **Confidentiality gain is real but bounded, and worth stating honestly:** all four current staff
  share the IMAP passwords in one `.env`, so anyone determined can still read the mailboxes directly.
  This scopes the *app*, not the mail server.
- **Known limits, unchanged:** `is_admin` is still one boolean, not a role gradient — ADR-040 and
  ADR-041 record that, and it is still true. Captures (`cstore.list_pending()`) have no scope concept
  at all and remain unfiltered; the Caixa de Capturas badge therefore still counts every pending
  capture for everyone.

**Consumes ADR-038's seam. Extends ADR-039/-040/-041. Supersedes nothing.**
