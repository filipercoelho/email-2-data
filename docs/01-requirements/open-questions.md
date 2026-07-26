# Open Questions

> Seeded from the data-extraction profile, resolved 2026-06-10 during scaffold adoption.
> Answers carry a provenance trace (FACT) or are marked UNKNOWN; never let an UNKNOWN
> silently become an invented FACT (standards/03 §1).

- [x] **What is the source, and where does it live?** → FACT. Live **IMAP mailboxes** on
      `mail.lindoservico.pt` (provider mail.pt), accessed **read-only**. Host/port/accounts
      are configured per-deployment in `config/settings.json` (gitignored;
      `config/settings.example.json` is the template, default `port: 993`, `mailbox: INBOX`).
      Passwords come from `.env` (`EMAIL2DATA_<ACCOUNT>_PASSWORD`), never stored in config.
      Fetched messages land as `corpus/*.eml`. Trace: `src/email2data/fetch.py`,
      `config/settings.example.json`.
- [x] **What is the target format/schema?** → FACT. Per-message `TriageResult`
      (`src/email2data/schema.py:83`) appended to `out/results.jsonl`, plus three SQLite
      stores: `out/crm.db` (regenerable), `out/sync.db` (UID cursor), `out/workspace.db`
      (**precious** — human decisions + Projects). Schema versions are pinned per store
      (README §Stores & schema). Export shell → JSON or the materials-costing API
      (`src/email2data/export.py`).
- [x] **How is each output classified (FACT/INFERENCE/UNKNOWN) and provenance recorded?**
      → FACT. Every verdict stamps `decided_by` (`schema.py:97`, e.g. `tier0:bulk`,
      `tier1:gemini-2.5-flash`) — *who/which engine+version decided*. Tier-0 deterministic
      facts (`nif`/`iban` authoritative, `src/email2data/extract.py`) and header signals
      (`src/email2data/signals.py`) are FACT; the gazetteer (`src/email2data/store.py`) is a
      **PRIOR, not a verdict**; the Tier-1 LLM produces the classification where uncertainty
      warrants the spend. Governing principle: VISION.md tenet 8 ("every verdict explains
      itself").
- [x] **What is out of scope — left UNKNOWN rather than guessed?** → FACT. The cardinal sin
      here is "completing" a missing classification from a plausible pattern: never do it.
      **Only deterministic header signals may bin mail offline**; an uncertain message
      escalates, never disappears (VISION.md tenet 2). Non-goals (VISION.md): not an email
      client, never sends mail, not autonomous (human stays in the loop), not locked to one
      provider/mailbox.
- [x] **Is the source read-only guaranteed?** → FACT, **guaranteed and pinned**. Mailbox
      opened with `EXAMINE` (`readonly=True`) and fetched with `BODY.PEEK[]` only; the client
      never issues STORE/EXPUNGE/DELETE/APPEND/COPY or fetches RFC822/BODY[] (which would set
      `\Seen`). Belt-and-suspenders. Trace + forbidden-verb list: `src/email2data/fetch.py:29`;
      regression coverage: `tests/test_fetch_safety.py`.
- [x] **What does the reconciliation/coverage report need to show?** → FACT (partial).
      `email2data eval` (`src/email2data/cli.py:96`) scores counterparty/priority against the
      hand-labelled ground truth in `labels/worksheet.csv`. The success bar (VISION.md §What
      success looks like): **~100% recall on client job requests / POs**, **≈0
      real-clients-binned**, and tokens-per-email trending down at constant-or-better accuracy.

- [x] **IMAP port** → FACT, **confirmed 2026-07-26 against the live account** (was the last open
      question here; it had stayed ⬜ long after we started fetching mail daily). **`993` over
      SSL**, host `mail.lindoservico.pt`. Evidence, in order of strength: (1) the live
      `config/settings.json` has `imap.port: 993`, `use_ssl: true`, and `fetch.py:86-88` opens
      `imaplib.IMAP4_SSL(host, port)` with `993` as the default — so a successful fetch *is* a
      successful 993/SSL login; (2) `out/sync.db` `fetch_cursor` holds server-issued
      **UIDVALIDITY** values per mailbox (e.g. `orcamentos/INBOX` → `1653756219`, `last_uid`
      `9743`) stamped `2026-07-26T02:00:19+00:00` — a UIDVALIDITY can only come back from the
      server's `EXAMINE` response, so those rows are proof of a completed session, not of a
      config file; (3) **948** attributed messages in `sync.message_scope` across **10**
      addresses and **553** `.eml` files in `corpus/`. Trace: `src/email2data/fetch.py`,
      `config/settings.json`, `out/sync.db`.

## Genuinely open (confirm before relying on)

*None.* Every question seeded at scaffold adoption is now answered above. Add new ones here
rather than leaving a resolved one marked open — a stale ⬜ next to something we do daily is
how this file stops being trusted.
