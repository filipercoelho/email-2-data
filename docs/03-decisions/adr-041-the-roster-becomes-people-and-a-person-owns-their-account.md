# ADR-041 — The roster becomes people, and a person owns their own account

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-07-26 |
| Phase | C (completion) of the multi-user work. Extends [ADR-039](adr-039-people-auth-and-the-default-deny-gate.md) and [ADR-040](adr-040-the-first-authorization-check-and-the-honest-refusal.md) |

## Context

[ADR-040](adr-040-the-first-authorization-check-and-the-honest-refusal.md) closed the one gap that
mattered most — anyone with a login could repoint `imap.accounts` — and said plainly what it left
open: *"`is_admin` is a boolean… no per-object permission"*, one surface gated, nothing else. Phase C
was a first cut. Four holes remained, and none of them is theoretical.

**1. The gate refused; the shell kept inviting.** `/admin` answered **403** and the gear menu went on
offering **«Administração»** to everybody. A member's only feedback was a page they had to walk into,
once per lens, forever. That is ADR-040's own complaint — *a refusal the app knows about and does not
say* — reappearing on the other side of the same door.

**2. The shell never said whose session it was.** On a shared workshop machine the answer to *"am I
signed in as me?"* was `/api/me` in a browser console. With one role and one shared laptop that was
survivable; with a roster it is how someone marks a thread handled under a colleague's name.

**3. Nothing after `create_person` had a home.** Promote, demote, mark a leaver inactive, fix a typo'd
name, grant an inbox, re-issue a password — all of it was hand-written SQL against **`workspace.db`,
the precious store, the one with no rebuild path**. `must_change` had been a column since ADR-039 and
**nothing read it**, so an admin handing out a "temporary" password was making a promise the app never
kept. And a person who suspected their own password was compromised had no move they could make: the
only path was an admin at a terminal minting a fresh *onboarding* invite, which is friction that gets
resolved by never changing the password.

**4. Two rosters, and permissions read the third.** The owner picker read `settings.json team` ∪ the
in-app `roster` table. Permissions read `people`. So "Rita" could be an owner and not be a person —
you could assign her work and could not grant her anything — and "who is on this team" had no single
answer, which is precisely the defect ADR-039 created `people` to remove and then did not finish.

## Decision

**1. The link agrees with the gate.** `cockpit_ui.page()` takes the signed-in `person`; the
**«Administração»** entry renders only for `person.is_admin`. `person=None` is **default-deny** — no
admin entry, no account menu — so a builder that forgets to forward it costs an *admin* their link
(visible, self-reporting) rather than offering a member a door that is locked (silent). All seven
lens builders forward it, pinned twice: a static scan over every lens module, and behavioural route
tests asserting an admin sees the entry on every lens and a member never does.

**2. The shell says who you are.** An account control between the sync pill (status) and the gear
(config), because identity is neither: avatar, name, and a menu carrying the **role in words**
(«Administrador» / «Membro»), **«A minha conta»**, and **«Terminar sessão»**. Naming the role is what
makes a missing button self-explaining instead of looking like breakage. Sign-out is a **form POST** —
`/logout` revokes the session *row* (ADR-039), so an anchor would 405 and leave the person signed in
while appearing to have worked. The name is escaped: it is person-controlled text rendered into the
header of every page, and unescaped it is stored XSS with the widest blast radius in the app.

**3. «A minha conta» is the one surface a person owns outright.** Name, role, assigned inboxes, a
change-password form, and the live session list. Every route acts on the **signed-in** person and
takes no id, so *"can I edit someone else's account?"* is not a question this surface can be asked.
Changing a password **requires the current one** — otherwise a session left open on an unlocked
workshop machine is a permanent account takeover rather than a walk-up. The form carries a **readonly
`autocomplete="username"` account row** and a reveal toggle; see the third Consequence for why that
row is load-bearing and not decoration. `set_password` revokes every
session in the same transaction, **including the one doing the changing**, so the route mints a fresh
one: without it the success case logs you out, which reads as failure and invites a retry with the old
password. «Terminar as outras sessões» does the same minus the password change — the answer to a
laptop left signed in at a client's office.

**4. `must_change` is finally load-bearing, and it funnels in the middleware.** `email2data auth
reset` sets a temporary password (`invite` was minting an *onboarding* token for someone already
onboarded); the gate then holds every other path until it is replaced — 303 to `/a-minha-conta` for
HTML, **403 with a readable reason** for `/api/*`. In the same middleware as authentication and
authorization, for ADR-039 §5's reason a third time: a funnel each route must remember is a funnel
with holes. `/logout` stays public, so this refuses without trapping.

**5. The roster is `people`, and the fold loses nobody.** `_roster()` is now every **active** person.
`settings.team` and the legacy `roster` table are a **seed**: `backfill_people_from_roster()` folds
them in, idempotently, as **assignable-only** (they were free text a moment ago; nobody decided they
could sign in) with the first active admin as their responsible user. It needs an admin and does
nothing without one — on a real first boot there is none, which is what `/setup` is for, so it runs
again the moment `/setup` mints one rather than leaving the picker empty for a whole session. It never
touches an existing person, **including a deactivated one**: someone who left is still in
`settings.team` (config nobody edits), and re-creating them each boot would make deactivation
impossible to keep. Matching is `_name_key` — the same fold every other lookup uses, no looser: a
backfill matching more loosely than `person()` would be the second vocabulary this change removes.

**6. The «Pessoas» panel exists, and every rule it enforces lives in the store.** `/api/admin/people`
(covered by ADR-040's `/api/admin/` prefix the moment each route existed) is add · promote/demote ·
activate/deactivate · re-grant scopes · mint an invite · remove. `workspace.py` gained
`set_person_admin` / `set_person_active` / `person_history` / `delete_person`; the routes only turn a
`ValueError` into a 400 a person can read. Rules in the store, not the route, so the CLI and every
later caller inherit them.

**7. The install can never reach zero active administrators.** Enforced in the store, on every path
that could break it. `/setup` 404s as soon as any credential exists (ADR-039), so an install with no
admin **cannot be repaired from the app at all** — the recovery is deleting `auth.db` by hand and
re-onboarding everybody. Separately, the API refuses **self**-demotion and **self**-deactivation even
when another admin exists: that one is not unrecoverable, it is just a misclick with no undo from
inside the app, because you would be locked out of the screen you would need to fix it.

**8. Deactivate is the normal exit; remove is only for names that never did anything.** `name` is the
join key in `thread_owners` / `project_owners` / `captures.asserted_by` / `capture_users`, so a DELETE
cannot cascade the way a real FK would — the rows would simply point at somebody who no longer exists
and the thread would lose its owner with nobody told. `delete_person` refuses anyone with history or
anyone another person is accountable to, naming what and how much; deactivation keeps the record of
who decided what. Removal also purges the credential side: `workspace.db` and `auth.db` are joined by
`person_id` with **no foreign key** — SQLite cannot enforce one across files — so deleting one side
alone is exactly the orphan drift `auth list` reports as a warning.

**9. A grant is validated against the inboxes that exist.** `set_person_scopes` stores any string, so
a mistyped address would round-trip through the UI and *read as a permission* while matching no mail
at all. The panel validates against our configured mailbox addresses + `sem-atribuicao` and names the
known set in the refusal. A permission that looks granted and is not is worse than no permission.

**10. The invite link is minted in the browser.** `auth invite` printed a single-use credential to a
terminal, where it stayed — in shell history, in a scrollback buffer, and then in whatever chat it was
pasted into. It is now generated and copied from the panel; the terminal never sees it.

**11. A refusal carries its reason — still without ever returning a body.** ADR-040's `fetchJSON()`
threw away non-2xx bodies, which was right for *data* and wrong for a considered **400**: «Rita já
existe», «essa caixa não é desta instalação» all reached the user as «falhou». The message now rides
on the thrown `HttpError.detail`, where only a `catch` can read it and no renderer can mistake it for
a result. Rule 1 is unchanged: a non-2xx still throws. `del()` joins `getJSON`/`post` so DELETE goes
through the same single door.

**12. The panel offers nobody a button that always fails.** My own row carries no
«Despromover» / «Desativar» / «Remover» — the server refuses all three by design (§7), and offering
them anyway is ADR-040's defect one screen over. **Found by looking at the rendered page**, not by a
test; the test came after.

## Consequences

- **Verified in a real browser, driving the flows** (2026-07-26): promote → the row's badge and the DB
  agree; a typo'd scope → the server's own sentence on screen and **zero** scopes written; the correct
  scope → granted; an invite minted in-page → `invite_person(token)` resolves to that person; an
  assignable person with no responsible → refused, not created; **password changed → still signed in on
  `/fila`, new password verified, banner «Palavra-passe alterada.»**; a wrong current password →
  «A palavra-passe atual não está correta.». Screens checked in **both themes**.
- **Two defects were found by looking rather than by testing**, and both are now pinned: the
  always-failing buttons on one's own row (§12), and the account page's primary button pinning its
  label to `#fff` — readable on the light theme's dark `--ac`, washed out on the dark theme's pale
  one. The button now follows the theme.
- **A third was found by the owner locking himself out — twice — within the hour** (2026-07-26), and
  it is the sharpest lesson here. The change-password form shipped with three password inputs and
  **no username field**. That is an *ambiguous* form: the username is the anchor a password manager
  uses to decide "this **changes** the password of account X" rather than "this **creates** one".
  NordPass resolved the ambiguity by reading «Palavra-passe atual» as a new-password field — offering
  to generate one — and never recognised the real new-password boxes. The result is the worst shape a
  bug can take: **every layer reported success.** `check_password(current)` passed, the credential was
  written, the session was re-minted, the banner said «Palavra-passe alterada.» — and the stored
  password was a generated string the owner had never seen. Only `auth reset` could undo it, and it
  needed the CLI, because he was the only admin.
  The form now renders a **readonly `autocomplete="username"` account row** (rendered, not `hidden` —
  a manager is entitled to skip what is not displayed) plus `id`/`for` on every box, and a
  **«Mostrar as palavras-passe»** toggle so whatever was filled in can be *read* before it is
  committed. `autocomplete` alone never carried this and never will.
  Two tests were written from the failure, not from the design. The wider lesson: **this project's
  browser verification drove the app and checked its own outputs, and a password manager is neither.**
  Every automated check filled the fields directly, which is precisely the path a real person does not
  take — so an entire class of third-party-autofill defects sat outside what any of them could see.
- **The suite moves to 1020 measured, 0 failed** (988 with the three browser-e2e modules ignored; 32
  browser e2e). The delta from ADR-040's 944 is **+76**, all of it this change: **+13**
  identity-in-the-shell · **+15** «A minha conta» + `auth reset` · **+33** «Pessoas» (store lifecycle,
  API, panel, network seam) · **+11** one-roster · **+2** for the two defects the render exposed · **+2** for the password-manager defect the owner
  found the hard way.
- **`settings.json team` is now a seed, not a source.** It is read once per boot by the backfill and
  never again. Editing it adds names on the next start; it can no longer *remove* one (deactivate in
  Administração does that), and the legacy `roster` table is read only by the same backfill.
- **`/api/roster` still belongs to every member, deliberately** — naming a new owner is a decision made
  mid-flow from a picker, and a member could always do it. What changed is what it creates: a real
  person, assignable-only, accountable to whoever added them. `/api/roster/remove` refuses anyone who
  can sign in, because that endpoint is open to members and would otherwise have become a way to switch
  off a colleague's — or an admin's — access.
- **Known limit — still one role.** ADR-040's boolean is unchanged. There is no viewer/editor gradient
  and no per-object permission; this ADR makes the boolean *manageable*, not richer.
- **Known limit — visibility is still not enforced.** Every signed-in person still sees every thread.
  Scopes are now editable and validated, and the account page says so in as many words: *«As caixas
  atribuídas ainda não filtram o que vês.»* Stating a restriction the app does not apply would be the
  UI asserting something untrue. **Phase D still owes this.**
- **Extends [ADR-039](adr-039-people-auth-and-the-default-deny-gate.md) and
  [ADR-040](adr-040-the-first-authorization-check-and-the-honest-refusal.md)**; completes the
  in-app half of [ADR-018](adr-018-multi-owner-and-in-app-roster.md)'s roster, whose
  `settings.team ∪ roster` rule is superseded by §5. Supersedes nothing else.
