# ADR-009 — Incremental and idempotent by default

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-06-10 (back-filled; commit 7714993) |

## Context

Re-running the pipeline must be cheap and safe. Re-fetching every message on each run wastes
IMAP bandwidth; re-classifying already-seen mail re-spends Tier-1 LLM tokens for no new
information — directly violating [ADR-001](adr-001-compute-proportional-to-uncertainty-impact.md).

## Decision

The pipeline is **incremental and idempotent by default**:

- `fetch` pulls only mail arrived since the last run, tracked by a per-mailbox **IMAP UID
  watermark** persisted in `out/sync.db`; a `UIDVALIDITY` change re-bootstraps by date.
- `triage` classifies only messages not already in `out/results.jsonl` (append-only; never
  re-spends Tier-1 tokens on processed mail).
- `--full` on either overrides the watermark / reclassifies everything. `sync` does
  fetch-new + triage-new in one shot (what the webapp runs on boot and on **Sincronizar**).

Re-running with no new mail yields the same result and costs no tokens.

## Consequences

- Token cost scales with *new* mail, not inbox size — the cost curve the project optimizes.
- The cursor store (`sync.db`) is disposable: deleting it just re-bootstraps by date.
- Trace: `src/email2data/fetch.py`, `sync.py`; README §"Incremental by default".
