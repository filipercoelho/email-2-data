# Reference — data stores & outputs

| Field | Value |
| --- | --- |
| Type | Reference |
| Status | Active |
| Last reviewed | 2026-07-26 |

Where the pipeline persists state. The recoverability tier of each store is an invariant —
see [ADR-010](../03-decisions/adr-010-workspace-db-precious-vs-regenerable.md).

## Files under `out/`

| Path | What | Tier | Rebuilt? | Versioned by |
| --- | --- | --- | --- | --- |
| `out/results.jsonl` | append-only `TriageResult` per message | derived | re-run `triage --full` | `EXTRACTOR_VERSION` |
| `out/crm.db` | interactions (event log) + contacts (person rollup) + `asset_spread` — how many **distinct threads** each inline image we send rides into, the [ADR-048](../03-decisions/adr-048-recurring-branding-art-is-omitted-from-the-attachment-funnel.md) branding register (v6) | **regenerable** | `email2data crm` drops & rebuilds each run | `crm.SCHEMA_VERSION` |
| `out/sync.db` | per-mailbox IMAP **UID watermark** (cursor) + `message_scope` — which of our inboxes each message reached ([ADR-038](../03-decisions/adr-038-mail-account-attribution.md)) | cursor | deletable — next `fetch` re-bootstraps by date; **attribution degrades**, see below | `sync.SCHEMA` (additive) |
| `out/workspace.db` | **human decisions** + Projects + edit history + the intake capture queue/allowlist (v5; capture `transcript` v6; `extracted_fields_json`+`confidence` v7; `para_ti_dismissals`+`counterparty_names` v8; `thread_snooze` v9; `people`+`person_scopes` v10 ([ADR-039](../03-decisions/adr-039-people-auth-and-the-default-deny-gate.md)); `people.email` v11 ([ADR-042](../03-decisions/adr-042-the-app-sends-exactly-one-kind-of-mail.md)); `people.signature`+`job_title`+`phone` v12 ([ADR-047](../03-decisions/adr-047-the-signature-belongs-to-the-person-not-the-playbook.md)) — see [ADR-028](../03-decisions/adr-028-decisions-persist-and-stay-reviewable.md)) | **precious** | **never auto-rebuilt** | `workspace.SCHEMA_VERSION` (`user_version`) |
| `out/auth.db` | **credentials + sessions + invites + password resets** ([ADR-039](../03-decisions/adr-039-people-auth-and-the-default-deny-gate.md), [ADR-042](../03-decisions/adr-042-the-app-sends-exactly-one-kind-of-mail.md)): scrypt password hashes, SHA-256 session tokens, single-use invites, single-use reset tokens (30 min). Deliberately **not** in `workspace.db` — the precious store must be restorable without stale password material. Joined to `people` by `person_id`, with **no cross-file FK** SQLite could enforce. Every person-keyed table is named once in `auth._PERSON_TABLES`, which both `known_person_ids` and `purge_person` walk — a new table missing from it leaves a deleted person's secrets behind while the drift check reports clean | **precious** | **never auto-rebuilt** — losing it re-opens `/setup` (see below) | `auth.SCHEMA` (`CREATE TABLE IF NOT EXISTS`, additive) |
| `out/knowledge.db` | the hand-curated **gazetteer** — `key → counterparty` priors ([ADR-005](../03-decisions/adr-005-gazetteer-is-prior-not-verdict.md)); a prior attached to the LLM input, never a verdict, and the **veto that stops an offline IGNORE** on a known client (`cascade.py:94`) | **regenerable from `config/gazetteer.csv`** — and the CSV is regenerable from the table (`email2data gazetteer export`), see below | `cascade.build_store` calls `store.seed_or_warn` on every run, which **`DELETE`s and replaces** the table from `config/gazetteer.csv` — and **warns loudly** if that CSV is missing while the table is non-empty | *(unversioned — `CREATE TABLE IF NOT EXISTS`)* |
| `out/jobspecs.jsonl` | one JobSpec + Gate-1 readiness per job-relevant email (LEAD / PO / estimate), LLM-drafted | derived | `email2data jobspec`, and incrementally after every triaging sync | *(unversioned — no version field on a row)* · ⚠ **not covered by `bin/backup-workspace.sh`**, see below |
| `out/evidence.jsonl` | **the located sentence** per message ([ADR-054](../03-decisions/adr-054-llm-derived-body-fragments-live-in-out-sidecars.md) Phase 4): `{message_id, quotes{key→sentence}, rejected{key→reason}}`. The quote is a **body fragment**, stored as the email's own text at the matched span — never the string the model typed | derived (LLM) | `email2data locate`, and incrementally after every triaging sync. Gate = **presence of a row**, so a rejection is stored too | *(unversioned)* · ⚠ not backed up — see below |
| `out/narratives.jsonl` | **«Evolução da conversa»** per thread ([ADR-054](../03-decisions/adr-054-llm-derived-body-fragments-live-in-out-sidecars.md) Phase 5): `{thread_root, watermark, steps[{message_id, date, text}], state}`. Threads with ≥ 2 messages only | derived (LLM) | `email2data narrate`, and incrementally after every triaging sync | `watermark` — a sha256 over each message's `(id, date, direction, purpose, speech_act, counterparty, entities, subject)` · ⚠ not backed up |
| `corpus/*.eml` | raw fetched messages (read-only source mirror) | cache | re-fetch | — |
| `captures/<chat>/…` | intake media ([ADR-020](../03-decisions/adr-020-capture-egress-and-data-handling.md)) — sole copy once scrubbed from Telegram | **precious** | never | — · ⚠ **not covered by `bin/backup-workspace.sh`**, see below |

### `asset_spread` — the branding register that makes the funnel hide things (ADR-048, v6)

`crm.db` gained one table on 2026-07-30, and it is the only store in this project whose contents
cause the UI to **omit** something. One row per distinct content hash of a `cid:`-referenced
`image/*` part sent from `signals.OUR_DOMAIN`:

| Column | Meaning |
| --- | --- |
| `sha` | sha256 of the **decoded** bytes — the same key `attachments.fold_thread` dedups on |
| `n_threads` | distinct `thread_root`s these bytes appeared in. **The decision signal.** |
| `n_messages` | distinct messages. Kept for the audit only — it does *not* decide (the CAD drawing and the animated footer are 5 messages each; they are 1 and 5 threads) |
| `sample_name`, `px`, `size` | so `email2data assets status` reads like something a human can judge |

The funnel omits every row at or above `attachments.BRANDING_MIN_THREADS` (**3**, sitting in a
measured gap: content tops out at 2 threads, branding starts at 5). It holds **no addresses and no
subjects** — the thread_roots are counted, never stored.

**It is a measurement, not a curated list.** `build_crm` rewrites it whole, so art that stops being
sent leaves the register on the next `crm`/`sync`, and a hand-edit is pointless. Two properties keep
the failure direction safe: a missing or pre-v6 `crm.db` yields an empty set, so the funnel shows
*everything* rather than hiding on stale evidence; and `email2data assets status` **exits 1** in that
state instead of printing an empty list that reads like "nothing is hidden, all good".

### The gazetteer is managed again — CSV restored + the silent case made loud (fixed 2026-07-26)

**What was wrong.** Five docs (this shelf's [index](index.md), `README.md`, `CLAUDE.md`, the roadmap,
[ADR-005](../03-decisions/adr-005-gazetteer-is-prior-not-verdict.md)) describe
**`config/gazetteer.csv` as the editable source of truth** for the gazetteer. That file **did not
exist** — on this host or in the container (`config/` is bind-mounted, so they are the same
directory). It is gitignored on purpose (it names real clients), so it is the one store input with
**no second copy anywhere**. Meanwhile `cascade.build_store` guarded the seed with a bare
`if gaz.exists()`, so its absence produced **no warning, no error and no log line**:
`out/knowledge.db` went on serving **15 rows (4 `CLIENT`, 11 `SUPPLIER`)** frozen at its last
successful seed (mtime `2026-07-23 00:21`) — priors that still fired, including the ADR-005 veto that
stops an offline IGNORE, but that nobody could read or edit. The contract was false in both
directions for three days.

The exposure pointed at non-negotiable #2 (*never silently bin a client*): a client added after
2026-07-23 had **no veto row**, so bulk-shaped mail from them could be IGNOREd offline. Nothing was
observed doing so — that was a stated risk, not a measured defect.

**What fixed it.** The earlier note called this a data call for the owner, because recreating the CSV
looked like it meant retyping real client names. It did not: the 15 rows were still in the table, so
they could be **round-tripped back out**.

- **`store.export_gazetteer`** — the inverse of `seed_gazetteer`; writes the live table out in
  seedable form (`#` preamble, then `domain,counterparty,note`). Verified lossless on the real store:
  export → re-seed left the table hash-identical (`8db94255…`), 15 rows in, 15 rows out.
- **`store.seed_or_warn`** replaces the bare `if gaz.exists()` in `build_store`. A missing CSV over a
  **non-empty** table now warns on stderr and names the way out; a missing CSV over an **empty** table
  stays quiet (a fresh install with nothing curated yet — warning on every run would train people to
  ignore the warning that matters).
- **`email2data gazetteer status | export`** is the management surface. `status` prints counts per
  counterparty — **never the keys**, which are real client domains and do not belong in scrollback or
  a log — and **exits 1** while the table is frozen, so the drift is scriptable. `export` refuses to
  overwrite an existing CSV without `--force` (it may hold hand edits not yet seeded) and refuses to
  write an empty file (which would only erase the table on the next run).

```bash
email2data gazetteer status     # exit 1 = the CSV is gone and the priors are frozen
email2data gazetteer export     # write the live table back to config/gazetteer.csv
```

**Recovery path, unchanged in principle:** because the table is `DELETE`d and replaced on seed,
restoring the CSV fully repairs the store on the next run — and because `knowledge.db` is *not* in
the backup set, the CSV is what to restore. The gap that remains is the ordinary one: if
`knowledge.db` and the CSV are lost *together*, the priors are gone. `export` closes the common case
(CSV missing, table alive), not that one.

Pinned by [tests/test_store.py](../../tests/test_store.py) (round-trip, quoting, the warn/quiet
split), [tests/test_cascade.py](../../tests/test_cascade.py) (`build_store` warns instead of silently
serving frozen priors — confirmed failing against the pre-fix guard), and
[tests/test_cli.py](../../tests/test_cli.py) (status exit code, key redaction, the two export
refusals).

### `message_scope` — inbox attribution (ADR-038)

One row per `(message_id, address)`: which of **our** mailboxes a message reached. The scope token is
the **address**, not a configured account id — mail reaches six inboxes we never fetch
(`margarida.reis@`, `carmen.martins@`, `lindoservico@`, `silva@`, `julio.morais@`, `recrutamento@`),
and keying on the address makes those grantable too.

`source` records the evidence class: `fetch` (FACT — the account we authenticated as) ·
`header` (FACT — the server's `Envelope-to`/`Delivered-To`/`X-Original-To`/`X-Rcpt-To`) ·
`participant` (INFERENCE — one of our addresses in `From`/`To`/`Cc`/`Reply-To`, only when no delivery
header survived). No row at all = UNKNOWN, which folds to `scopes.SCOPE_UNATTRIBUTED` and is
**admin-visible** — never everyone-visible, never hidden.

Writes are **upgrade-only** (`participant` < `header` < `fetch`), so `email2data scopes backfill` is
idempotent and safe to re-run beside a live fetch. Run it once after any `fetch --full`.

**Deleting `sync.db` is safe but lossy in one direction**: the tier-1 `fetch` rows are gone and the
backfill re-derives at tier 2/3 — less precise, never wrong, and it degrades *toward* the
admin-visible bucket rather than toward hiding mail. Live state (2026-07-25): 551 messages → 947 rows
across 10 inboxes, 0 unattributed.

## Provenance / corpus

- `corpus/*.eml` are fetched with `BODY.PEEK[]` only and never mutated
  ([ADR-002](../03-decisions/adr-002-read-only-imap-guarantee.md)). Non-INBOX folders get an
  `X-Email2Data-Source` header prepended on fetch.
- `out/results.jsonl` is the triage ledger; `triage` appends only messages not already present
  ([ADR-009](../03-decisions/adr-009-incremental-idempotent-by-default.md)).

### The filename IS the index — load-bearing perf invariant (ADR-053)

`fetch.py` writes each `.eml` under `safe_filename(canonical_id_from_raw(raw))`
([`identity.py:43-49`](../../src/email2data/identity.py)) which is
`sha256(canonical_id)[:32] + ".eml"` — a **pure function** of the message id — and
`envelope.parse_eml(...)["message_id"]` returns the *same* canonical id
([`envelope.py:717`](../../src/email2data/envelope.py)). So `corpus_dir / safe_filename(mid)` is the
answer, not a candidate.

This is what makes `message_id → .eml` O(1) at three call sites without a persisted index:
[`webapp._file_for`](../../src/email2data/webapp.py), [`report.prepare`](../../src/email2data/report.py),
[`specbuild.rebuild_jobspecs`](../../src/email2data/specbuild.py). Before ADR-053 each of them
globbed and parsed **every** `.eml` on cold call (9 s on a 1094-file corpus) and `_rebuild_state`
threw the resulting index away after every sync — a warm click rearmed as a 9-second click every 15
minutes. See [ADR-053](../03-decisions/adr-053-the-corpus-filename-is-the-index.md) for the diagnosis
and the fix.

**A change to either `safe_filename` or `canonical_id` will silently break every compute-first
lookup.** Files still exist on disk, but nobody finds them until the lazy fallback scan runs (the
one still-slow path). Any such change must ship a migration that renames existing files to the new
scheme, and update ADR-053. Verified over the whole corpus: **1116/1117 files land on the computed
name** — the single miss is a `sha256:`-fallback id from an older derivation and is what the
fallback exists for.

## workspace.db migration discipline

`Workspace.connect` runs `_migrate`, which stamps `user_version` and is where migrations go.
A **new table** is delivered additively by `CREATE TABLE IF NOT EXISTS`; a **new column on an
existing table is not** (that statement no-ops on an existing table), so it requires a guarded
`ALTER TABLE … ADD COLUMN` inside an `if version < N:` block in `_migrate`, gated by the version
check so re-runs are safe. Because this DB is never rebuilt, a missing ALTER silently ships a
column-less DB that crashes on first write — pin the upgrade with a test on a prior-version DB that
contains rows (see `tests/test_workspace_migration.py`). Never drop-and-recreate `workspace.db`.
Hand edits live in `project_fields` (always win) and every edit — plus off-email `__kind__` events
([ADR-015](../03-decisions/adr-015-knowledge-capture-claim-ledger.md)) — is recorded append-only in
`project_field_history`.

## WAL mode & backups (v5)

`Workspace.connect` opens `workspace.db` in **WAL** journal mode with a 5 s `busy_timeout` so the
conversational-intake worker — a separate process ([ADR-021](../03-decisions/adr-021-intake-lan-binding-minimal-auth.md))
— can write the `captures` queue alongside the webapp instead of mutually blocking under the default
rollback journal. Consequence for the **precious** store: WAL keeps committed-but-not-checkpointed data
in the **`workspace.db-wal`** sidecar (alongside `workspace.db-shm`). **A backup MUST capture all three
files together, or use the SQLite Online Backup API / `VACUUM INTO`** — a naive `cp workspace.db` alone
can lose the latest committed decisions. A clean connection close checkpoints the WAL back into the main
file. This matters doubly once intake media becomes the sole copy
([ADR-020](../03-decisions/adr-020-capture-egress-and-data-handling.md) preserve-at-core).

`bin/backup-workspace.sh` is the implementation: `VACUUM INTO` per store, then it **re-opens each
snapshot and counts rows** before reporting success — a backup nobody has read back is a claim, not a
copy.

### `auth.db` is in the backup set for a reason that is not data loss

`STORES=("workspace.db" "auth.db")`. Losing `auth.db` does not merely lose passwords: with no
credential row, `AuthStore.has_any_credentials()` returns `False`, `/setup` un-404s, and the install
is back at **first run** — the next person to reach it becomes admin. On a LAN bind that is the whole
gate gone ([ADR-039](../03-decisions/adr-039-people-auth-and-the-default-deny-gate.md) §9).

**Restore both stores from the *same* snapshot.** They are joined by `person_id` with no cross-file
FK, so a mixed restore yields sessions and credentials pointing at people who do not exist, or a
roster whose admins cannot sign in. A partial restore is also exactly the state
`email2data auth setup`'s brick guard exists to survive: roster back, `auth.db` missing, and the
person recovering typing a name out of `auth list` — pick someone who cannot log in and setting their
password closes `/setup` for good.

### Known gap — `captures/` is precious and is **not** backed up

`config.paths()` documents `captures_dir` as PRECIOUS and says it "MUST be in the backup set (see
data-stores.md)". It is not: `bin/backup-workspace.sh` snapshots SQLite stores via `VACUUM INTO` and
has no file-tree path at all. So the media that ADR-020 calls the sole copy once Telegram is scrubbed
currently has **one** copy. The DB rows describing each capture *are* backed up, which is the trap —
a restore would come back with a queue whose attachments are gone and no indication they ever
existed. Recorded here rather than fixed silently; a fix needs its own change (a tree copy plus a
verified read-back, since a media backup nobody has opened is the same claim as an unverified DB one).

### The three LLM-derived JSONL sidecars are deliberately NOT in the backup set (ADR-054)

`out/jobspecs.jsonl`, `out/evidence.jsonl` and `out/narratives.jsonl` all cost real LLM tokens to
produce, so "just rebuild it" is not free — but none of them is in `bin/backup-workspace.sh`'s
`STORES`, and that is now a **decision** rather than the accident it was for `jobspecs.jsonl`.

The reason is mechanical and was measured, not assumed: `snapshot_and_verify` is sqlite3-only. It
opens the source as a SQLite URI, queries `sqlite_master`, and copies with `VACUUM INTO`. Handed a
`.jsonl`, `sqlite3` raises `DatabaseError: file is not a database`, and there is no `except` around
it — so adding any one of the three to `STORES` makes the **whole** pre-migration backup exit 1 and
print *"BACKUP FAILED — do not migrate"*, even though `workspace.db` and `auth.db` snapshotted fine.
A backup script that fails on the run before a migration is worse than a gap you know about.

What that costs, priced: a full rebuild of both ADR-054 sidecars from `corpus/` + `results.jsonl` is
**≈ $0.68** at `gemini-2.5-flash` list price (736 locate calls + 157 narrate calls, derived from the
real body sizes). Losing them is a bill, not a data loss. Backing up a JSONL properly needs the same
change `captures/` needs — a file-tree copy plus a verified read-back — and that is its own change.

## Dangling references

`project_threads.thread_root` points into the regenerable CRM. A CRM rebuild can orphan a
reference; `project show` and the web UI flag these as **dangling** so a Project never silently
loses messages.

## Project lifecycle

`LEAD → GATHERING → ESTIMABLE → QUOTED → WON | LOST`, plus `CANCELLED` (called off in flight) and
`ARCHIVED` (soft-retire, hidden by default). A successful export advances a Project to `QUOTED`
([ADR-011](../03-decisions/adr-011-export-honesty-boundary.md)). `CANCELLED`/`LOST` carry a **close-out**
— `close_party` (client/supplier/our) + `close_reason` + `closed_at`, cleared on reopen
([ADR-017](../03-decisions/adr-017-project-close-out-lifecycle.md)).

## Ownership & roster (v4, folded into `people` by ADR-041)

Owners are a **set**, not a single field: `thread_owners(thread_root, owner)` and
`project_owners(project_id, owner)` join tables (the pre-v4 single `thread_state.owner` is backfilled
into `thread_owners` and then vestigial).
The per-project **participants** view (`GET /api/projects/{pid}/participants`) is a read-only rollup of
the ADR-015 ledger's `asserted_by` — who fed knowledge into the project.

**The owner roster is `people`** — every **active** person, read per request so a change in
Administração needs no restart
([ADR-041](../03-decisions/adr-041-the-roster-becomes-people-and-a-person-owns-their-account.md) §5).
It was `settings.team` ∪ an in-app `roster(name)` table
([ADR-018](../03-decisions/adr-018-multi-owner-and-in-app-roster.md)) while *permissions* read
`people` — two vocabularies for one question, so a name could be an owner and not be a person. Both
legacy sources are now a **seed**: `Workspace.backfill_people_from_roster(team)` folds them in,
idempotently, as **assignable-only** with the first active admin as their responsible user. It needs
an admin and does nothing without one, so it runs at app construction *and* right after `/setup` — on
a real first boot there is nobody to be accountable yet. It never touches an existing person,
including a **deactivated** one: someone who left is still in `settings.team`, and re-creating them
each boot would make deactivation impossible to keep.

### The people lifecycle, and the one invariant behind it

`create_person` was the whole API until ADR-041; everything after it was hand-written SQL against the
precious store. The store now owns the lifecycle — the routes only turn a `ValueError` into a 400 —
so the CLI and any later caller inherit the rules:

| Call | Refuses when |
| --- | --- |
| `set_person_admin(pid, bool)` | promoting someone who cannot sign in; demoting the **last active admin** |
| `set_person_active(pid, bool)` | deactivating the **last active admin** |
| `set_person_email(pid, addr)` | a malformed address; an address **another person already holds** (a reset link with two destinations). `''` is allowed and means "no address on file" |
| `person_by_email(addr)` | *(a read)* — matches only **active, login-capable** people, and never a blank probe (most rows are `''` by default, so a blank match would return an arbitrary person and mail them a reset link) |
| `person_history(name)` | *(a read)* — where the name still appears as a foreign key; `{}` = safe to remove |
| `delete_person(pid)` | any history, anyone accountable to them, or the **last active admin** |

**The install can never reach zero active administrators.** `/setup` 404s as soon as any credential
exists, so an install with no admin cannot be repaired from the app at all — the recovery is deleting
`auth.db` by hand and re-onboarding everybody. The API additionally refuses **self**-demotion and
**self**-deactivation even when another admin exists: recoverable, but only from a screen you would
have just locked yourself out of.

**Deactivate is the exit; delete is only for names that never did anything.** `name` is the join key
in `thread_owners` / `project_owners` / `captures.asserted_by` / `capture_users.roster_owner`, so a
DELETE cannot cascade the way a real FK would — the rows would point at nobody and a thread would
lose its owner silently. Deleting also purges `auth.db` (`AuthStore.purge_person`): the two files are
joined by `person_id` with no cross-file FK, so removing one side alone *is* the orphan drift
`auth list` warns about.

> **Migration note:** v4 added the close-out columns + the three tables above. v3 added the ADR-015
> provenance columns. Each is a guarded `ALTER`/`CREATE IF NOT EXISTS` in `_migrate`, pinned by
> `tests/test_workspace_migration.py` (a prior-version DB *with rows*).
