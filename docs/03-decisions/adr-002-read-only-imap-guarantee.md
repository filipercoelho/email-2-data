# ADR-002 — Read-only IMAP is an absolute, pinned guarantee

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-06-10 (back-filled; red-team B1, since Phase 0) |

## Context

The system reads live, production mailboxes that humans depend on. Mutating the source — a
flipped `\Seen` flag, a deleted message, an accidental move — is the one unrecoverable
mistake. A data-extraction tool must guarantee the source is unchanged after a run
(`PROFILE.md`, `standards/03 §1`).

## Decision

Access is **strictly read-only, enforced two ways (belt and suspenders)**:

1. The mailbox is opened with `EXAMINE` (`conn.select(..., readonly=True)`), not `SELECT`.
2. Bodies are fetched with `BODY.PEEK[]` only — never `RFC822` or `BODY[]`, which set `\Seen`.

The client **never** issues `STORE / EXPUNGE / DELETE / APPEND / COPY`; these verbs are a
forbidden-list constant. PEEK is the client-side guarantee; the read-only SELECT is the belt.

## Consequences

- Any code path that could mutate the mailbox is a defect, not a trade-off.
- Pinned by a regression test — adding a forbidden verb must fail the suite.
- Trace: `src/email2data/fetch.py:29` (`_FORBIDDEN_IMAP`), `fetch.py:162` (`readonly=True`);
  test: `tests/test_fetch_safety.py`. Source: [VISION.md](../../VISION.md) tenet 1.
