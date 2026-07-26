# ADR-042 — The app sends exactly one kind of mail, and it is a password-reset link

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-07-26 |
| Relationship | Extends [ADR-039](adr-039-people-auth-and-the-default-deny-gate.md) and [ADR-041](adr-041-the-roster-becomes-people-and-a-person-owns-their-account.md). **Scopes, does not overturn, [ADR-002](adr-002-read-only-imap-guarantee.md)** |

## Context

The owner locked himself out of his own install **twice within an hour** on 2026-07-26 — the incident
[ADR-041](adr-041-the-roster-becomes-people-and-a-person-owns-their-account.md) records at the end of
its Consequences. Both times the only way back in was `email2data auth reset` typed at a shell on the
Docker host. He is the **only** administrator; Luís and Pedro hold invites that are still unredeemed.
So the app had, for its sole admin, **no recovery path that ran inside the app at all**.

Before building anything, the obvious cheap answer was checked and remains true: *promote a second
admin*. Two minutes of work, removes most of the risk, and it is still the right first move. It is
not, however, a substitute — a two-person install where both people forget is the same dead end, and
"ask the other admin" is not self-service.

**The blocker everyone assumed, checked and found not to exist.** The standing reading was that
"email me a reset link" is closed by non-negotiable #1 — read-only IMAP, never sends — and that
building it would need an ADR to *overturn* a core invariant. Read literally,
[ADR-002](adr-002-read-only-imap-guarantee.md) says something narrower:

> *"Access is **strictly read-only**, enforced two ways… The client **never** issues
> `STORE / EXPUNGE / DELETE / APPEND / COPY`"*

That binds **the IMAP client and the mailboxes we triage**. It says nothing about the application
never sending mail, because until now the application had no reason to. A dedicated outbound account
that never opens IMAP and never touches a triaged mailbox therefore **sits beside ADR-002 rather than
against it**. This ADR scopes the invariant precisely; it does not repeal it. The two things that
*would* violate it — sending through a triaged account's session, or `APPEND`ing a copy to a Sent
folder we also fetch — are named below and forbidden.

**What was actually missing was not SMTP.** `people` carried
`person_id, name, name_key, can_login, is_admin, responsible_id, active, created_ts, updated_ts` and
**no address**. `identity_links.email` is a *counterparty's* address; `projects.client_email` is a
*client's*; `imap.accounts[].username` are *mailboxes the app reads*. None of them is a staff member's
own address. So the real cost of this feature is a migration of the **precious** database, an admin
surface to fill it in, and only then a transport.

**Deliverability was measured, not assumed.** `lindoservico.pt` publishes
`v=spf1 ip4:185.12.116.228 … -all` and `p=reject`, and `mail.lindoservico.pt` resolves to exactly that
IP. Mail therefore arrives **only** when it leaves through the domain's own server carrying a `From:`
on the domain — and is rejected outright, not spam-filed, otherwise. That is a hard constraint on the
design, not a footnote.

## Decision

**1. One mailbox, write-only, absent from the fetch list.** A dedicated account
(`email-2-data@lindoservico.pt`) sends; nothing reads it. It is deliberately **not** in
`imap.accounts[]`, so it is never fetched, never triaged, and never attributed
([ADR-038](adr-038-mail-account-attribution.md)). `mailer.py` imports no `imaplib` and issues no
`APPEND` — pinned by `test_the_mailer_never_touches_imap`, which reads the module source rather than
trusting future authors, because the failure it prevents is someone adding an APPEND-to-Sent later.

**2. One message, one template, no caller-supplied body.** This is not a mail API. There is no
attachment path, no arbitrary recipient, and no reply handling; recipients are restricted to an
address on a `people` row. Plain text only — an HTML mail with a styled button is what phishing looks
like, and a bare URL is what a person can read and verify before clicking.

**3. `people.email` (workspace v11), never inferred.** The address is set explicitly by an
administrator in «Pessoas» or by `email2data auth email`. It is **not** back-filled from
`person_scopes` or `imap.accounts[]`: a scope grant is an inbox someone *reads* and an account is a
mailbox the app *fetches*, and neither is evidence of whose address it is. A guess here mails a
credential-bearing link to the wrong human, which is the zero-hallucination doctrine
([ADR-001](adr-001-compute-proportional-to-uncertainty-impact.md)) applied to the one field where
being wrong hands over an account. Every existing row migrates to `''`, and `''` is a real state —
"no address on file", after which recovery simply has nowhere to send.

**4. One address belongs to one person.** Enforced in `workspace.set_person_email`, because an
address mapping to two people is a reset link with two possible destinations.

**5. The reset token is an invite with a shorter fuse.** A separate `password_resets` table in
`auth.db`, copying `create_invite`/`redeem_invite` verbatim: opaque 256-bit random, **SHA-256 at
rest**, consumed by an atomic `UPDATE … WHERE used_ts IS NULL` whose `rowcount` is the gate. TTL is
**30 minutes** against the invite's 72 hours — the window is the one an attacker with later access to
that mailbox inherits. Minting a new one consumes any earlier unused one, so a person never has two
live links. A separate table rather than a `purpose` column on `invites`, so no existing invite query
needs a `WHERE` that a future reader could forget.

**6. Redeeming revokes every other session.** `set_password` already does this. It is the right
behaviour here specifically: *"I forgot my password"* and *"someone else is in my account"* are
indistinguishable from the server's side, and the safe reading is the second.

**7. The public endpoint is not an account oracle.** `POST /recuperar` returns a byte-identical
response and status whether the name matched a person, matched nobody, matched someone with no
address, was throttled, or matched someone whose mail then failed to send. The roster is people's real
names; a distinguishable "no such person" makes it enumerable by anyone who can reach the port.
Pinned by `test_the_reset_request_is_not_an_account_oracle`.

**8. The link is built from configuration, never from the request.** `mail.base_url` is authoritative.
A reset URL built from an attacker-controlled `Host:` header is the classic reset-poisoning bug — the
victim receives a *genuine* token pointing at the attacker's server. Pinned by
`test_the_mailed_link_uses_the_configured_base_url_not_the_request_host`.

**9. The throttle bounds mail, not logins.** At most `mail.reset_max_per_hour` (default 5) messages
per person per hour. It never refuses authentication and never locks an account — otherwise a
mail-abuse guard would become a denial-of-service anyone could aim at anyone.

**10. Unconfigured is a first-class state, stated honestly.** No `mail` block means `/recuperar`
answers **503** with "recuperação por email indisponível — pede a um administrador", not "check your
email". This is [ADR-040](adr-040-the-first-authorization-check-and-the-honest-refusal.md)'s honest
refusal applied to recovery: a promise the app knows it is not keeping is the failure that ADR forbids.
A `mail` block that is present but *broken* logs an error and disables recovery rather than making the
app unbootable — and `auth mail-test` exists so the state is checkable.

**11. This does not repair a zero-admin install, and says so.** It needs a `people` row with an
address; a bricked install has no way to create one and `/setup` is already closed
([ADR-041](adr-041-the-roster-becomes-people-and-a-person-owns-their-account.md) §10). Pinned as a
test so it is not later mistaken for that.

## Consequences

- **Verified against the real server, not asserted** (2026-07-26): SMTP authenticated on **465
  (implicit TLS)** and 587 (STARTTLS), TLS 1.3, certificate `CN=lindoservico.pt`,
  `AUTH PLAIN LOGIN` — probe sent **no message**. DNS confirmed
  `mail.lindoservico.pt → 185.12.116.228`, matching the SPF `ip4:`; DMARC read as `p=reject`.
  `email2data auth mail-test` resolves the credential through `.env` → `config.mail_password` →
  `Mailer` and logs in end to end. The full flow was driven in-process: request → mailed link →
  set password → signed in, with the old password dead and a pre-existing session revoked.
- **`workspace.db` went v10 → v11.** The migration was rehearsed **on a copy of the real database**
  before the live one was touched, and `./bin/backup-workspace.sh` ran first (non-negotiable #9):
  4 people preserved, `email=''` on every row, re-connect is a no-op.
- **A public POST now exists.** It is unauthenticated by necessity — the visitor cannot sign in, that
  is the problem — and earns it through decisions 7, 8 and 9. `_PUBLIC_EXACT`/`_PUBLIC_PREFIX` grew
  by one entry each; `tests/test_auth_gate.py::PUBLIC` grew to match, and the route-tree walk caught
  all four new routes before that list was updated, which is the guard working.
- **Telegram was not made an authentication factor.** It was the other available egress
  ([ADR-019](adr-019-conversational-intake-capture-adapter.md)/[ADR-021](adr-021-intake-lan-binding-minimal-auth.md)),
  and ADR-021's decision R6 deliberately sends **no navigable link** through it. Self-hosted mail
  keeps the reset inside infrastructure the company already owns, so R6 stands unamended.
- **The link points at the LAN over self-signed TLS** (`https://192.168.1.253:8042`), which is an
  owner decision taken with its cost stated: every device shows a one-time certificate warning, on
  the one page where teaching people to click through such a warning is least welcome. ADR-039's
  recorded limit — *self-signed TLS proves encryption, not identity* — is unchanged. **"Never public"
  is untouched**: no port-forward, no inbound webhook, no public hostname.
- **The base URL can silently go stale.** This host is a **DHCP Wi-Fi client**, so `192.168.1.253`
  can change, and the symptom would be a reset link that opens nothing on the day someone needs it.
  `auth mail-test` compares the configured host against the current address and warns. That is a
  mitigation, not a fix; a fixed IP or a LAN DNS name would be the real one.
- **A reset lands in a mailbox, so whoever can read that mailbox can take that account.** For
  `@lindoservico.pt` recipients this stays inside self-hosted infrastructure
  (`standards/02-network-lan.md`: *"Self-hosted SMTP/IMAP only, no third-party SaaS"*), so it is not
  third-party egress. It does mean the mailbox is now an authentication factor for its owner —
  accepted, and worth remembering before anyone points `people.email` at a personal address.
- **There is no CAPTCHA and no IP-based rate limit.** The per-person cap is the whole mitigation, on
  a LAN-only service that is never public. Stated as an accepted risk rather than left to be
  discovered.
- **Known limits, unchanged by this ADR:** `is_admin` is still one boolean, not a role model, and
  per-person **visibility is still unenforced** — that is Phase D
  ([ADR-044](adr-044-per-person-visibility.md)).

**Extends ADR-039 and ADR-041. Scopes ADR-002 without weakening it: the read-only guarantee binds the
IMAP client and the mailboxes we triage, and nothing in this decision opens IMAP or writes to a
mailbox.**
