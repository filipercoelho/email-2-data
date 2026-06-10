# ADR-007 — Deterministic extraction: NIF/IBAN authoritative, amounts/dates as candidates

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-06-10 (back-filled; commit 6e24dd4) |

## Context

Some values in mail can be extracted with certainty by structure (a Portuguese NIF has a
check digit; an IBAN validates), while others (amounts, dates, document numbers) are
ambiguous in free text. Treating all extracted values with equal confidence would either
over-trust guesses or under-use the certain ones.

## Decision

Tier-0 deterministic extraction classifies its outputs by confidence:

- **NIF and IBAN are FACT (authoritative)** — validated by structure/check-digit, so they are
  trusted directly.
- **Amounts, dates, document numbers are candidates (INFERENCE)** — surfaced for the LLM /
  human to confirm, never promoted to FACT on their own.

This is the data-extraction profile's FACT / INFERENCE / UNKNOWN discipline applied at the
field level — missing or ambiguous values stay candidates, never invented.

## Consequences

- Downstream consumers (JobSpec, CRM) can rely on NIF/IBAN but must treat amounts/dates as
  provisional until confirmed.
- Trace: `src/email2data/extract.py`; commit `6e24dd4` ("NIF/IBAN authoritative,
  amounts/dates/docs as candidates"). Source: [PROFILE.md](../../PROFILE.md).
