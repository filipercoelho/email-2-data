# ADR-049 — The folder list is discovered from the server, not configured

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-08-03 |
| Scope | `fetch.py` (`_discover_mailboxes`, `_account_mailboxes`, `fetch_account`), `admin_page.py` (the mailbox panel copy) |
| Upholds | [ADR-002](adr-002-read-only-imap-guarantee.md) — `LIST` opens no mailbox and touches no flag; every added folder is still `EXAMINE` + `BODY.PEEK[]` |
| Serves | Non-negotiable #2 — *never silently bin a client*. A folder we never open is the most complete way to bin one. |

## Context

`config/settings.json` listed the folders to fetch per account, by hand. That list is a **snapshot**,
and people re-organise their mail. The `orcamentos` account pinned **78** folders; the server had
**82**.

This was not theoretical. A client reply in the "Órfãos da Lua" thread —
«Proposta 2600476», Margarida Reis → the client, `Cc: orcamentos@`, **2026-07-29 20:02:05 +0100** —
was **absent from every store**: not in `corpus/`, so not in `results.jsonl`, `crm.db`, or the Fila.
The user opened the thread, saw three messages, and the fourth simply was not there.

It was on the server the whole time, in **`INBOX.orcamentado`** — one of the four folders the
snapshot did not know about. The mechanics, from `out/audit.jsonl`:

- the last `orcamentos/INBOX` poll on 29 Jul ran at **17:27 UTC**, watermark at uid 9797;
- the next ran **30 Jul 10:05 UTC** and found exactly **one** candidate — the 19:53 mail, not this one;
- in the ~17 hours between, the message was filed into `INBOX.orcamentado`, a folder the fetcher
  never opens. The UID watermark on `INBOX` is honest about `INBOX`; it cannot speak for a folder
  that was never in the list.

**The failure is silent by construction**, which is what makes it the dangerous shape: no error, no
audit line, no gap in the cursor. Every downstream store was internally consistent. The only way to
notice is for a human to remember an email and go looking for it.

### Measured scope of the hole (2026-08-03, read-only probe of all four accounts)

| | |
| --- | --- |
| Folders on the server vs configured | 133 vs 81 — **52 never opened** |
| Messages in never-opened folders since 20 Jul | **190** |
| …whose content the app had **never seen** | **63** (the other 127 were captured via another inbox) |

Where the 63 sat: `luis:INBOX.Sent` **41** · `Pedro:INBOX.spam` **9** · `luis:INBOX.Shaper` **4** ·
`orcamentos:INBOX.orcamentado` **3** · `luis:INBOX.Gui`, `filipe:INBOX.Trash`, `filipe:INBOX.Sent`
**2** each.

The 63 is deliberately *not* the 190: Outlook mints a **different `Message-ID` for the Sent copy**
than for the delivered one, so a `Message-ID` join alone reports 78 and overstates the loss by 15.
The honest number comes from joining on `(Date, normalized Subject)` as well.

## Decision

**Ask the server what folders exist, on every fetch. Union that with whatever the account pins.**

1. **Discovery.** `_discover_mailboxes` issues IMAP `LIST` and takes every selectable folder.
   `\Noselect` containers are skipped (no messages, `EXAMINE` fails on them). Names stay in the
   server's own modified-UTF-7 — that is what `EXAMINE` wants back and what `settings.json` already
   held.
2. **Junk, and only junk, is excluded.** By last path segment (`spam`, `junk`, `junk e-mail`,
   `junk email`, `bulk mail`, `lixo`) or by the RFC 6154 `\Junk` attribute, for a server that names
   it something else. Editable via `imap.exclude_mailboxes` without a code change.
3. **Trash is NOT excluded.** Deliberate: deleted mail is still evidence a client wrote to us, and
   `orcamentos/INBOX.Trash` has been fetched since day one. Removing it would be this same defect,
   chosen on purpose.
4. **The pinned list survives as escape hatch and fallback.** A folder named explicitly in
   `mailboxes` is fetched **even if it matches the junk filter**, and a `LIST` that fails or errors
   returns `None` — distinct from `[]` — so the fetch falls back to the pinned list rather than
   silently narrowing to nothing. A server quirk must never shrink the scope below what a human
   configured.
5. **INBOX sorts first**, so the mailbox that matters most is drained before the per-mailbox
   `max_messages` cap can bite.
6. **The selected set is audited** (`mailboxes_selected`, and `mailbox_discovery_failed` when `LIST`
   is unavailable). "Which folders did we even look in?" is the first question when a message is
   missing, and until now the log could not answer it.

### Consequences accepted

- **Sent folders are now fetched for every account**, not just `orcamentos` — the 41-message hole.
  This is a scope increase the owner asked for explicitly. It also makes outbound direction a FACT
  (the `X-Email2Data-Source` Sent-wins rule) rather than an inference from the From domain.
- **A one-time backfill.** A newly discovered folder has no cursor, so it bootstraps at
  `SINCE since_days` (10) capped at `max_messages` (500). Bounded, and it drains in one run.
- **`INBOX.Drafts` is now discovered for every account** (it was already fetched for `orcamentos`).
  A draft is unsent mail, so it can read in the Fila as a message that never left. Not excluded here
  because the owner's instruction was "spam only" — and it is now a one-line `imap.exclude_mailboxes`
  edit, live without a rebuild, if that turns out to be noise.
- **The Admin screen had to change with it.** A panel headed «Caixas de correio (78)» over a 78-item
  list read as the complete scope while the fetcher opened 82 — the same class of defect as offering
  a door the server refuses. It now says the pinned list is *extras + fallback*, names the one
  exclusion, and points at the cursor table as the record of what was actually read.

## Alternatives rejected

- **Just add the four missing folders to `settings.json`.** Fixes today, reopens tomorrow — the next
  folder anyone creates is invisible again, and nothing would say so. It treats the symptom whose
  defining property is that it is silent.
- **Poll more often to shrink the filing window.** Reduces the odds, guarantees nothing, and costs
  IMAP round-trips on every folder. The message was filed once; it only has to win the race once.
- **Exclude Trash and Drafts too, for a "clean" queue.** Rejected: this ADR exists because something
  was excluded without anyone deciding to exclude it. Every omission here is now a named, editable,
  audited choice.

## How this is pinned

[tests/test_fetch_discovery.py](../../tests/test_fetch_discovery.py) — **20 tests, all 20 failing
against `HEAD`**, no network (a fake connection replays real `LIST`/`SEARCH`/`FETCH` response
shapes, including the literal-name tuple form). The load-bearing ones: a folder `settings.json`
never listed is opened (`INBOX.orcamentado`, this defect); Sent is discovered; junk is excluded by
name *and* by `\Junk`; **Trash is not**; a pinned folder outranks the junk filter; a failed `LIST`
falls back instead of narrowing to nothing; and `fetch_account` end-to-end lands the message from
the unconfigured folder in `corpus/`.

Read-only is re-pinned across the wider folder set: the fake connection **asserts** on every
`SELECT` that `readonly=True`, and the test records every command issued so
`STORE`/`EXPUNGE`/`DELETE`/`APPEND`/`COPY` are checked against the wire, not only against the source
text as [tests/test_fetch_safety.py](../../tests/test_fetch_safety.py) does.

One more in [tests/test_admin_page.py](../../tests/test_admin_page.py): the mailbox panel does not
claim the pinned list is everything fetched.

## Verified (2026-08-03, on the real install)

Suite green (**1343 passed, 0 failed**) → `docker compose up -d --build` → `./bin/check-image-drift.sh`
**exit 0**, so the container runs this code and not an older image. Then, in order:

1. `email2data fetch` inside the container: **58 messages cached** (`luis` 51, `filipe` 7) on top of
   the ~40 the boot sync had already pulled — the one-time backfill.
2. The message is in the corpus at `corpus/f9435bb2fbd267b6775a652f9c5e2b17.eml`, carrying
   `X-Email2Data-Source: INBOX.orcamentado` — **the folder that was never opened before**.
3. `email2data triage` (57 processed, 52 via Tier-1, ≈ **$0.05**) → `crm` → `scopes backfill`. The
   thread `mid:cad7gy=…@mail.gmail.com` went from **3 messages to 6**, and the 20:02 proposal
   classifies `outbound · CLIENT · OUTBOUND_QUOTE`.
4. **Looked at the render, not just the store** — the DB is the proxy this project keeps warning
   about. A disposable copy of the stores served on **8043** (never 8042; the deploy was not
   disturbed): the Fila row for that thread now leads with «Proposta 2600476 - construção de
   cenografia "Órfãos da Lua"» and shows `📎PDF +1` — an attachment that exists only on the
   recovered message — and `/api/thread` renders the 20:02 entry in the timeline.

### Known regression this introduced — duplicate copies of the same email

Fetching Sent folders surfaced a defect that **pre-existed but was smaller**: Outlook mints a
**different `Message-ID` for the Sent copy** than for the delivered one, so `identity.canonical_id`
(deliberately Message-ID-based, per `identity.py`) sees two distinct messages and both are cached,
triaged and listed. The 17:22:53 reply now appears **twice** in this very thread.

Measured on the live `crm.db`, attributed by `(account, mailbox)` from the audit log rather than
guessed: **55 duplicate groups** over 1073 interactions (55 extra rows) — **31 involve a folder
ADR-049 newly opened**, **24 pre-date it** (the same newsletter delivered to two fetched inboxes).

**Not fixed here, on purpose.** The fold key is a design decision with its own trade (which copy
wins for direction; whether folding touches the contacts rollup and the interaction counts), and
changing `canonical_id` itself would break the eval join that `identity.py` exists to protect. It
wants its own ADR. Recorded here because it is visible in the thread this ADR was written to repair,
and a duplicate that nobody wrote down is how the next person concludes the fold never worked.
