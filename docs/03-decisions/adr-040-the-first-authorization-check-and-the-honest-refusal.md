# ADR-040 — The first authorization check, and the honest refusal

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-07-25 |
| Phase | C (first cut) of the multi-user work. Extends [ADR-039](adr-039-people-auth-and-the-default-deny-gate.md) |

## Context

[ADR-039](adr-039-people-auth-and-the-default-deny-gate.md) shipped authentication and said so plainly
in its own Consequences: *"This ADR contains no **authorisation**. Everyone who can sign in can still
do everything."* That was an honest statement of scope, but it left two holes that a post-ship audit
surfaced, and neither is theoretical.

**1. `is_admin` was a column nothing read.** `people.is_admin` existed, `/setup` set it on the first
account, `email2data auth list` printed it — and no request path consulted it. So every person who
could sign in could reach `/admin` and `POST /api/admin/accounts`, **the route that rewrites
`imap.accounts` in `settings.json`**. The worst case is not "someone saw the admin page": it is that
anyone with a login could repoint which mailboxes this app authenticates to. ADR-039's design intent
(a workshop LAN, per-person accounts) reads very differently once the roster grows past its two
current admins, and the gap widens with every invite.

**2. A refused request was rendered as a fact.** The lens pages fetched their own data with the
shape `const d = await (await fetch('/api/fila')).json(); rows = (d.rows||[]);`. When a session
expired or was revoked, the server correctly answered **401** with `{"error": …}` — the page read that
body, found no `rows` key, defaulted it to `[]`, and rendered **«✓ Tudo tratado · nada está a cair ·
0 a responder»**. Reproduced in a real browser, on real code, with the nav above it still reading
**Fila 1**: the page contradicting itself while sounding certain.

That second one is the zero-hallucination doctrine (VISION; CLAUDE.md non-negotiable #2) escaping the
classifier and turning up in the UI. Every rule in this project about FACT / INFERENCE / UNKNOWN was
written for extracted values, and none of it was being applied to what the screen asserts. Of all the
false things this app can say, *"there is nothing left for you to do"* is the one a person acts on by
walking away from work that is actually waiting.

The two are one topic, which is why they are one ADR: **what happens when the app refuses you.**
Server-side it must refuse correctly; client-side it must say so.

## Decision

**1. Authorization lives in the same middleware as authentication, as a closed path set.**
`_ADMIN_EXACT = {"/admin"}` and `_ADMIN_PREFIX = ("/api/admin/",)`, checked in `_auth_gate` right
after identity is resolved. Deliberately **not** an `@admin_required` decorator: ADR-039 §5 chose
default-deny middleware precisely because a rule that must be remembered per route is forgotten
exactly once, and the sibling app's 67 hand-copied inline checks are the evidence. The same argument
applies unchanged one layer up. A new `/api/admin/*` route is covered by the prefix the moment it
exists — it is protected by *existing*, not by remembering.

**2. The path set is pinned against the real route tree, in both directions.**
`test_the_admin_path_set_matches_the_route_tree` walks FastAPI's actual routes and asserts the admin
surface is exactly `{"/admin", "/api/admin/accounts"}`. Adding an admin route without listing it there
fails; so does *removing* the gate. The test names the required action in its own failure message
("update `ADMIN_ONLY` **and** `webapp._ADMIN_EXACT/_ADMIN_PREFIX` — deliberately, not reflexively"),
because a red test whose fix is "delete the assertion" is how a coverage test dies.

**3. A signed-in non-admin gets 403 — never a 303 to `/login`.** Bouncing someone to a login page
they would pass instantly is a loop that reads as a broken app, and it misnames the problem: the
question is not *who are you?* when the answer is *not enough*. `/api/*` gets a JSON 403; HTML gets
`cockpit_ui.forbidden_page()` at status 403.

**4. The 403 page says what was refused and why, in the normal shell.** It renders through the same
`page()` shell as every lens, so the nav stays on screen — a wrong turn is a dead end of one click,
not a dead end full stop. It states that the account *is* active and authenticated and simply lacks
the admin flag, and it points at the remedy ("pede a um administrador para te promover"). A refusal
is a fact about this account; a screen that hides its own reason trains people to reload instead of
to ask. It carries **no admin data** — pinned by `test_the_403_page_leaks_no_admin_data`, because a
403 page that renders the thing it is refusing is not a gate.

**5. Non-admins keep every decision lens.** `/fila`, `/para-ti`, `/projetos`, `/contrapartes`,
`/captures` are untouched, pinned by `test_a_non_admin_keeps_full_access_to_every_decision_lens`. This
ADR restricts **one** surface — the settings that decide which mailboxes are read. Per-person
*visibility* (whose mail you may see) is still not built; `scopes.visible()` and `person_scopes`
remain the unread seam ADR-039 left, and Phase D still owes it.

**6. Demotion takes effect on the next request.** Nothing about the role is cached in the cookie —
`_current_person` already re-reads `workspace.db` every request (ADR-039 §5), so `is_admin` inherits
that property for free. Pinned by `test_demoting_an_admin_takes_effect_on_their_next_request`.

**7. Client-side, every fetch goes through one `fetchJSON()` that treats 401/403 as an event, not as
data.** In the shared cockpit shell: a non-2xx response, a network failure, or a body that is not
JSON all raise `HttpError` — they never return a value a renderer could mistake for "the server said
zero". `d.rows||[]` is only reached on a response the server actually agreed to give. This is the
`||[]` idiom's real defect: it cannot distinguish *empty* from *unknown*, and this app's whole
doctrine is that those are different.

**8. On 401/403 the page raises a curtain instead of re-rendering.** `sessionEnded()` shows a modal
overlay — **«Sessão terminada»**, plus the line that matters: *«O que está por trás desta janela é o
estado anterior — pode já não ser verdade.»* The stale data stays visible behind it deliberately,
because blanking the screen would destroy context someone may be mid-thought on; what changes is that
the app stops **claiming** it. The button links to `/login?next=<current path>`, so signing back in
returns you to the page you were on, not to `/`.

**9. The curtain stops the background polls.** `setInterval` handles are registered in `_timers` and
cleared on `sessionEnded()`. A tab left open overnight otherwise keeps firing ADR-023's 30 s poll at
a server that will refuse every one of them, filling the log with 401s that describe a browser, not
an attack.

**10. This is proven in a real browser, because nothing else can see it.** `tests/test_session_expiry_e2e.py`
signs in, revokes the session **server-side**, drives the lens's own refresh path, and asserts the
page does not say «Tudo tratado». A `TestClient` cannot catch this (it never runs the JS) and the
static checks in `test_cockpit_ui.py` cannot either — they prove the seam is *shipped*, not that it
*works*. The headline test settles on **either** outcome (curtain or the empty-queue claim) rather
than waiting only for the curtain, so a regression fails by name — *"the Fila declared an empty queue
off the back of a 401"* — instead of as an opaque 5 s timeout.

## Consequences

- **Verified in both directions, not asserted** (2026-07-25). Authorization: the admin-only tests
  fail before the gate exists and pass after. Session honesty: the symptom was **reproduced verbatim**
  first — with the pre-fix `fila_page.py` restored from `git HEAD`, a revoked session, and a real
  Chromium, the body printed `✓ Tudo tratado / nada está a cair · 0 a responder` with
  `curtain visible: False` and a console `401 (Unauthorized)` — then 3 of the 4 new e2e checks failed
  on that code and all 4 pass on the fix.
- **The suite moves to 944 measured, 0 failed** (912 with the three browser-e2e modules ignored;
  32 browser e2e checks collected, up from 28 with this module). The pin in
  [CLAUDE.md](../../CLAUDE.md) and [acceptance-criteria.md](../06-qa/acceptance-criteria.md) read
  913 / 885 + 28 and was stale by +31 before this change — it was re-measured here, not carried
  forward, which is the whole point of the W0 step that preceded this work.
- **`is_admin` is now load-bearing.** Before this ADR it was decoration and could have been dropped
  without breaking anything. Promoting or demoting someone is now a real privilege change, so
  `email2data auth add --admin` and `auth set` deserve the care given to any destructive command.
- **The `||[]` pattern is now a bug smell across the codebase.** It was the whole mechanism of the
  reported symptom. Every remaining occurrence should be read as "does this turn an error into a
  confident zero?" — the shared `fetchJSON()` closes the lens data paths, and it is the right seam for
  anything added later.
- **Known limit — one role, not a permission model.** `is_admin` is a boolean. There is no
  `viewer`/`editor` gradient and no per-object permission, and this ADR does not pretend otherwise.
  *(Amended 2026-07-26: still true. [ADR-041](adr-041-the-roster-becomes-people-and-a-person-owns-their-account.md)
  makes the boolean **manageable** — a «Pessoas» panel, a zero-admin invariant — without making it
  richer. It also closes this ADR's other unstated gap: the gate refused `/admin` while the gear went
  on offering it, so a member's only feedback was a 403 they had to walk into.)*
  ADR-039 §Context lists the sibling's snapshotted `ROLE_DEFAULTS` as a defect to avoid; a real role
  model, if it comes, must compute permissions live rather than freeze them per user at creation.
- **Known limit — visibility is still not enforced.** Every signed-in person still sees every thread.
  ADR-038 persisted the attribution and `scopes.visible()` is ready, but no query filters on it. This
  ADR narrows *editability of one settings surface* only; Phase D is unbuilt and should not be assumed
  from the existence of `person_scopes`.
- **Extends [ADR-039](adr-039-people-auth-and-the-default-deny-gate.md)**, supersedes nothing. ADR-039's
  Consequences bullet "contains no authorisation" is amended there to point here.
