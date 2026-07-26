# ADR-038 — Mail-account attribution: the durable fact a visibility layer filters on

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-07-25 |
| Phase | A of the multi-user work (identity + permissions); prerequisite for per-user visibility |

## Context

The owner asked for multiple users with modular, per-user **visibility** and **editability**
permissions. For a mail-triage app the natural visibility axis is *which inbox the mail reached* —
"Rita sees `orcamentos@`, Pedro sees everything". Two facts made that unbuildable as it stood:

1. **`account_id` was never persisted.** It existed only in `sync.db`'s `fetch_cursor` (per-mailbox
   UID watermark) and in `audit.jsonl` lines. `corpus/` is a **flat, hash-keyed** directory of
   `.eml` files (`safe_filename(canonical_id)`), with no per-account partition — by design, because
   a message reaching two of our mailboxes must be cached **once**.
2. **The sibling app has no pattern to copy.** materials-costing's auth layer (reviewed the same
   day) gates purely on `resource:verb` permission strings applied globally; it has **no row-level
   scoping of any kind**. The editability half of the ask ports from it; this half does not.

A third fact decided the shape. Measured on the live corpus (551 cached messages): **449** carry a
delivery header naming one of our inboxes, **102** carry none at all — but every one of those 102
does name an internal `@lindoservico.pt` address in `From`/`To`/`Cc`/`Reply-To`.

## Decision

**1. Persist attribution as `(message_id, address)` rows** in a new `sync.message_scope` table, keyed
by the app's existing canonical `message_id` (`identity.canonical_id`) — which joins directly to
`results.jsonl` and `crm.db.interactions` with no filename hashing. Many-to-many by construction: a
message reaching two inboxes gets two rows.

**2. The scope token is the ADDRESS, not the configured account id.** Mail demonstrably reaches
`margarida.reis@`, `carmen.martins@`, `lindoservico@`, `silva@`, `julio.morais@` and
`recrutamento@` — six real inboxes we do **not** fetch. Keying on the address makes those grantable
without inventing a fetch account for each; the 4 configured accounts are simply the subset we pull.

**3. Three evidence tiers, recorded per row, mapping onto the PROFILE.md discipline:**

| `source` | Evidence | Origin | Messages |
| --- | --- | --- | ---: |
| `fetch` | **FACT** | the account we authenticated as when caching it (`fetch.py`, live) | going forward |
| `header` | **FACT** | the receiving server's `Envelope-to` / `Delivered-To` / `X-Original-To` / `X-Rcpt-To` | 449 |
| `participant` | **INFERENCE** | one of our addresses in `From`/`To`/`Cc`/`Reply-To`, only when no delivery header survived | 102 |
| *(absent)* | **UNKNOWN** | folds to `SCOPE_UNATTRIBUTED` (`"sem-atribuicao"`) | **0** |

`participant` is weaker evidence, and is labelled as such rather than laundered into a FACT. For the
*visibility* question it is nonetheless close to the right semantic: you were a party to the mail.

**4. Attribution may only ever be upgraded, never downgraded.** `set_message_scopes` compares
`SOURCE_RANK` (`participant` < `header` < `fetch`) and refuses a weaker write. This is what makes
the backfill safe to re-run beside a live fetch — a derived guess can never clobber what the IMAP
server told us. It also makes every writer idempotent.

**5. A thread's scope is the UNION of its messages' scopes** (`scopes.thread_scopes`), never the
intersection and never "first account wins". **172 of 373 real threads (46%) reach more than one
inbox** — under any narrowing rule nearly half the queue would vanish from a legitimate reader's
view. The union can therefore only ever *widen* visibility.

**6. Unattributable mail is admin-visible, not everyone-visible.** A thread with no attributable
member carries `SCOPE_UNATTRIBUTED`, which admins always see and which can be **granted** to a named
delegate like any other token. A thread with *one* unattributed member carries the token *in addition
to* its real addresses, so it stays with its reader **and** remains findable by an admin.

**7. Attribution is best-effort at fetch time and can never fail a fetch.** A store error inside
`fetch_account` is audited as `scope_record_failed` and swallowed. Read-only IMAP is the one
unrecoverable-mistake surface (non-negotiable #1); it does not get to break over bookkeeping.

**8. Storage lives in `sync.db` (regenerable), not `workspace.db` (precious).** Losing `sync.db`
costs the tier-1 rows, which the backfill then re-derives at tier 2/3 — strictly *less* precise,
never wrong, and it degrades **toward** the admin-visible bucket rather than toward hiding mail. No
migration touches the precious DB in this phase.

## Consequences

- **This ADR contains no policy.** It answers only "which of our mailboxes did this land in?". Who
  is granted which inbox is Phase D, gated behind Phase B (identity) and C (editability).
  `scopes.visible()` is the seam Phase D will call: admins true, otherwise a set intersection, and an
  **empty** scope set fails **closed** for non-admins (a thread naming a message we never cached must
  not become visible-to-all).
- **Non-negotiable #2 holds ("never silently bin a client").** A visibility filter *is* a silent bin
  if it hides mail nobody is watching. Hence: union not intersection, an explicit admin-visible
  bucket rather than absence, fail-closed on empty, and 0 stranded threads verified on real data.
- **Verified, not asserted** (2026-07-25): 551 messages → 947 scope rows across 10 inboxes, 0
  unattributed; re-run writes 0 rows (idempotent); `fetch_cursor` unchanged at 9 rows; 373 threads,
  0 admin-only, 172 multi-inbox. Suite 786 → **819 passed** (33 new in `tests/test_scopes.py`), ruff
  clean.
- **New surface:** `email2data scopes backfill` / `scopes status`. Read-only over `corpus/`, writes
  only `sync.db`. Re-runnable at any time; run it once after any `--full` fetch.
- **Known limit — Sent-folder direction.** `participant` attribution uses `From` so our own outbound
  mail is attributable to the sending mailbox. That is deliberate, but it means an internal mailbox
  that merely *sent* to a client is a scope holder for that thread. Correct for visibility ("you
  wrote it, you can see it"); noted because it is not the same claim as "it was delivered to you".
- **Extends** ADR-016 (fetch resilience) and the `sync.db` contract in
  [data-stores.md](../05-reference/data-stores.md). **Superseded by** nothing. **Prerequisite for**
  the auth work tracked in [ADR-021](adr-021-intake-lan-binding-minimal-auth.md).
