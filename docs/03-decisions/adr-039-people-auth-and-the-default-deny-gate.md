# ADR-039 — People, authentication, and the default-deny gate

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-07-25 |
| Phase | B of the multi-user work. Builds on [ADR-038](adr-038-mail-account-attribution.md); graduates [ADR-021](adr-021-intake-lan-binding-minimal-auth.md) |

## Context

The owner asked for multiple users, roles, and modular per-user visibility and editability. The
sibling **materials-costing** app was reviewed first as the reference implementation. Its invite flow,
its `require_permission` dependency, and above all its route-tree coverage test are worth copying. Its
session design is not:

* It documents a `sessions` table in `auth_db.py`'s module docstring and **never creates it**;
  `token_jti` appears nowhere. Logout clears a cookie, so a copied 24h JWT stays valid with no kill
  switch short of rotating `JWT_SECRET` and logging everyone out.
* The JWT carries `role` + `permissions` claims that **nothing reads** — every check re-queries the DB
  (correct for revocation, but the claims look authoritative to the next reader).
* The httpOnly cookie is defeated by also storing the token in `localStorage`, and the cookie has no
  `secure` flag while Caddy serves port 80.
* `ROLE_DEFAULTS` is snapshotted per user at creation, so adding a permission key silently skips
  existing users (this really happened with `projects:override_steps`).
* `PATCH /users/{id}` guards only self-`readonly`; an admin can `PATCH` themselves to `active: false`
  or `role: viewer`, and no last-admin invariant exists.

This app also had **no authentication at all** — its security model *was* the loopback bind — and its
roster was free text in two places (`settings.json.team` ∪ the in-app `roster` table), so "who is
Rita" had no single answer and could not carry a permission.

Owner decisions taken during the review: LAN-only bind (graduating ADR-021), users become the roster,
**self-signed TLS on 8042**, password + admin-created invites, and a **required** accountable user for
anyone without platform access.

## Decision

**1. `people` (workspace v10) is the one assignable-identity namespace; login is an attribute.**
Some people are assigned work but never sign in and have no mailbox — they are accountable *through*
a platform user. So `can_login` is a column, not a separate table. `responsible_id` is **required**
when `can_login = 0`, enforced by a `CHECK` and not by convention: work assigned to someone who never
signs in must appear in some signed-in human's view, or it sits in a queue nobody opens. That is the
"never silently bin a client" non-negotiable reaching ownership.

**2. `name` stays the join key.** `thread_owners.owner`, `project_owners.owner`,
`captures.asserted_by` and `capture_users.roster_owner` all store a name and all stayed `TEXT`. At
v10 those tables held 0, 0, 3 and 1 rows, so an id migration would have bought churn across six
modules and nothing else. The price is that a rename must cascade — `rename_person` does, in one
transaction. Uniqueness runs on a **NFKC + casefolded `name_key`**, not `COLLATE NOCASE`: SQLite's
NOCASE folds ASCII only, so `"Luís"` and `"LUÍS"` would have been two people, each owning half a
queue. The team has accented names; a test pins this.

**3. Credentials live in `out/auth.db`, never in `workspace.db`.** The precious store must stay
restorable without also restoring stale password material, and an auth schema change must never risk
the DB that is "never auto-rebuilt". Joined by `person_id`; SQLite cannot enforce a cross-file FK, so
`purge_person` keeps the two from drifting.

**4. A session is a row, not a signed blob — and there is no server secret.** The cookie holds an
opaque 256-bit random token; only its SHA-256 is stored. Revocation is therefore a column write, so
logout, log-out-everywhere, and "a password change kills live sessions" all work on day one. Nothing
is signed, so there is no key to place in `.env`, leak, or rotate. **No new dependencies**:
`hashlib.scrypt` (~28 ms/verify), `secrets`, `hmac.compare_digest`. Adding bcrypt + PyJWT to a
two-dependency project in order to reimplement a weaker design would have been a poor trade.

**5. The gate is default-deny middleware with a closed allowlist**, not a per-route decorator. The
sibling's own U5a notes record 67 hand-copied inline checks and the bugs that came from forgetting
one. `tests/test_auth_gate.py` walks the real FastAPI route tree and fails when any non-public path
is reachable signed-out — the generalisation of the one idea that app got clearly right.
`/api/*` returns **401**; HTML routes **303** to `/login` with a fully percent-encoded `next`, guarded
against open redirect (`//evil.example` is protocol-relative and must not pass a `startswith("/")`
check). Identity is re-read from `workspace.db` on **every** request, so deactivating someone takes
effect on their next request.

**6. Server-rendered means the gate is real.** The login page imports nothing from the cockpit
renderers, so a signed-out visitor receives no triage data — not hidden data, *absent* data. This is
the structural advantage over the sibling SPA, where `hasPermission` runs client-side on data already
shipped.

**7. `/healthz` is public, and the healthcheck is scheme-agnostic.** The image `HEALTHCHECK` probes
it; a 401 there would mark the container unhealthy, and `intake-bot` declares
`depends_on: email2data healthy`, so a gated healthcheck would stop the Telegram worker too. The
inline `http://` one-liner became `bin/healthcheck.py`, which tries HTTPS then HTTP — otherwise
turning on TLS would have had the same effect. Both properties are pinned by tests.

**8. TLS and the LAN bind are opt-in, off by default.** `serve` keeps `--host 127.0.0.1` and plain
HTTP; `--tls-cert/--tls-key` enable HTTPS, and `bin/make-cert.sh` writes a self-signed certificate
(SANs for loopback + the LAN IP). Opt-in is what keeps the **whole** suite — every test and every
browser e2e check — on plain HTTP loopback, unchanged. (Stated as a count in the first draft; a count
goes stale the next time anyone adds a test, and the property is what is load-bearing here.) A LAN bind **without** TLS prints a warning rather than silently
putting the session cookie in clear text. The `secure` cookie flag is derived from the live request
scheme, so it is correct under TLS and absent on loopback (where it would drop the cookie).

**9. First-run setup, then invites.** `/setup` exists only while no credential exists and **404s**
afterwards, so it can never mint a second admin. Everyone else gets a single-use invite link
(`create_invite` consumes any earlier unused one, and redemption is an atomic `UPDATE … WHERE
used_ts IS NULL` — the sibling's best idea, kept).

## Consequences

- **Verified, not asserted** (2026-07-25): 0 failed, ruff clean; this work added **+126 tests**
  (+33 ADR-038 scopes, +29 people, +39 auth, +25 gate). The v10 migration was tested against a
  `VACUUM INTO` backup copy before touching the live DB: v9 → v10, 2 tables added, **0 pre-existing
  rows changed**. *(The absolute total quoted here first — 912 — was retired on the same day by
  [ADR-040](adr-040-the-first-authorization-check-and-the-honest-refusal.md); the live pin lives in
  [CLAUDE.md](../../CLAUDE.md), and a total copied into an ADR is stale the next time anyone writes
  a test. The delta is the durable fact.)*
- **The 216 pre-existing route assertions now sign in for real.** There is deliberately **no test
  bypass**: an env var that disabled auth would mean those assertions proved nothing about it.
  `tests/conftest.py` provides `sign_in` / `signed_in_client`, and `AuthedBrowser` wraps Playwright so
  the e2e call sites are unchanged.
- **New prerequisite tooling:** `bin/backup-workspace.sh` (`VACUUM INTO` + verified restore). Nothing
  in `bin/` could previously take a safe copy of the precious DB — and a bare `cp` yields an empty
  database, because the live rows sit in the WAL sidecar.
- **Known limit — self-signed TLS proves encryption, not identity.** A hostile device on the LAN
  could still MITM. Accepted for a workshop LAN with no guest devices; revisit if that changes.
- **Known limit — CSRF rests on `SameSite=Strict`.** There is no per-form token. For a single-origin,
  LAN-only, cookie-authenticated app this is adequate; it would not be for a public deployment.
- **This ADR contains no *authorisation*.** Everyone who can sign in can still do everything.
  `person_scopes` (granting ADR-038 inbox tokens) and `/api/me` exist as the seam, and
  `scopes.visible()` is ready — but no route consults them yet. That is Phase C (editability
  permissions) and Phase D (account-scoped visibility), neither of which is built.
  **Amended 2026-07-25 by [ADR-040](adr-040-the-first-authorization-check-and-the-honest-refusal.md):**
  the admin surface (`/admin`, `/api/admin/*`) is now gated on `is_admin` in this same middleware —
  `POST /api/admin/accounts` rewrites `imap.accounts`, so leaving it open to every login was the
  sharpest edge of this gap. The rest stands: Phase D (account-scoped visibility) is still unbuilt and
  `scopes.visible()` is still consulted by nothing.
- **Graduates [ADR-021](adr-021-intake-lan-binding-minimal-auth.md)** — its decisions 1 and 3 (LAN
  bind + the app's first auth gate) ship here, superseding its "single shared secret" sketch with
  per-person accounts. Its decisions 2 and 4 (no inbound port; the intake worker writes through the
  store seam and bypasses the HTTP gate) are unchanged and still hold: the worker needs no session.
- **Amends CLAUDE.md's "127.0.0.1 loopback only — single-user, never public"** to "loopback by
  default; LAN-only opt-in behind the auth gate". **"Never public" is unchanged and still firm.**
