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

## Genuinely open (confirm before relying on)

- [ ] **IMAP port** — `config/settings.example.json` defaults to `993/SSL` but notes "verify
      the IMAP port with the provider before first run." UNKNOWN until confirmed against the
      live mail.pt account; do not assume.
