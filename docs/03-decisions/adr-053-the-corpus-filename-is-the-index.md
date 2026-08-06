# ADR-053 — the corpus filename IS the index

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-08-04 |

## Context

A **live** perf diagnosis of the thread mechanism (2026-08-03, `feat/fila-mesa`) traced every "2 to 10
second" click to two mechanisms in the same place. DB queries were milliseconds, no LLM was called on a
click, the browser/serving side was negligible. What burned the seconds was **Python re-parsing the
850 MB `.eml` corpus, on a schedule**.

Measured against a shadow server on 8043 (copies of `out/*.db` + real `corpus/`, containers untouched
per the rebuild rule):

| What | Result |
| --- | --- |
| `/api/thread` warm, 40 real threads | median **102 ms**, p90 333 ms, max 848 ms |
| `/api/thread` first click after any sync or restart | **9.31 s** |
| Any request during a sync's state rebuild (`/api/fila`) | 2.2–3.0 s (57 ms when idle) |
| Inline images the dossier then loads (up to 21 per thread) | ≤ 0.26 s total |
| Per-message `.eml` read + parse + attachment funnel (16-msg thread) | 0.26 s |

Two culprits sat back-to-back in the code, both descended from the same missing assumption — that
resolving `message_id → .eml` requires searching:

1. **`_file_for` in `webapp.py`** globbed `corpus/*.eml` and fully parsed all 1094 files on first cold
   call (9.0 s), populating an in-process `_idx`. `_rebuild_state` then called `_idx.clear()` after
   **every** sync — which runs every 15 min plus on every deploy — so a warm click was recurrently
   rearmed as a 9-second click. End-to-end reproduction: warm 0.36 s → zero-work sync → next click
   9.40 s → the one after 0.33 s.
2. **`report.prepare()` in `report.py`** and **`_corpus_index()` in `specbuild.py`** did the same
   whole-corpus scan on the sync path. `prepare` compounded it: 9.04 s to build its own `mid2file`
   map, then 9.61 s re-parsing each matched file for the body. `build_crm` added 22.5 s of pure CPU.
   All of it ran in a daemon thread inside the one uvicorn process, so every click landing in that
   window was 40× slower — a **~60 s degraded window every 15 minutes**.

The load-bearing observation the diagnosis surfaced: **the corpus filename is already the answer**.
`fetch.py` writes each `.eml` under `safe_filename(canonical_id_from_raw(raw))`
([`identity.py:43-49`](../../src/email2data/identity.py)) which is
`sha256(canonical_id)[:32] + ".eml"` — a **pure function** of the message id. And
`envelope.parse_eml(...)["message_id"]` returns the same canonical id
([`envelope.py:717`](../../src/email2data/envelope.py)). So `corpus_dir / safe_filename(mid)` is not
"a candidate name" — it *is* the file. Verified over the whole corpus: **1116 / 1117 files land on
the computed name**; the one miss is a `sha256:`-fallback id from an older derivation.

## Decision

**Every `message_id → .eml` lookup is O(1) compute + one filesystem stat**. The whole-corpus glob-and-parse
survives only as a lazy fallback for the ~1-in-1000 legacy case, and never runs when compute-first
succeeds.

Three call sites are refactored:

1. **`webapp._file_for(mid)`** ([`webapp.py:273-303`](../../src/email2data/webapp.py))
   - Compute-first: `corpus_dir / safe_filename(mid)`; on hit, cache in `_idx` and return.
   - Fallback: on miss, one-time full scan; then latch `_idx_state["scanned"]` so an unknown mid
     never re-scans on the next click.
   - **`_rebuild_state` no longer calls `_idx.clear()`** — corpus files are content-addressed and
     entries can never go stale. New files land at their own computed path and are indexed lazily on
     first access.
2. **`report.prepare()`** ([`report.py:31-100`](../../src/email2data/report.py))
   - The whole-corpus `mid2file` scan is deleted; each email's `.eml` is resolved via
     `safe_filename(mid)`.
   - A module-level **`_env_cache: dict[str, dict]`** memoizes parsed envelopes across `prepare()`
     calls in one process. A second `prepare()` on the same corpus parses **zero** envelopes; a
     typical sync brings ~5 new messages, so we parse 5 instead of `len(corpus)`. Cleared on process
     restart — no persistence of derived personal data (no new store, ADR-042-friendly).
3. **`specbuild.rebuild_jobspecs()`** ([`specbuild.py:145-165`](../../src/email2data/specbuild.py))
   - The `mid2file = _corpus_index(...)` call is replaced with a per-mid `_corpus_file_for(mid)`
     that tries `safe_filename(mid)` and falls back to `_corpus_index` only on miss.
   - `_corpus_index` is retained as the fallback helper; it is no longer part of the hot path.

**Fail-open**: no `safe_filename` match ⇒ the fallback scan runs (correctness preserved for the
legacy case). Both `_idx_state["scanned"]` (webapp) and `fallback_idx` (report/specbuild) are one-way
latches: a single scan per process (webapp) or per `prepare()` call (report/specbuild).

## Consequences

**Perf.** Cold `/api/thread` click after any sync or restart drops from **9.31 s → 0.42 s** (measured
in the diagnosis's throwaway patch against the shadow server; payload byte-identical, 27 messages,
10 files, same sha256). `report.prepare`'s scan burn drops from **9.04 s → 0.01 s**; the second half
(re-parse) drops from **9.6 s → ~0 s** on incremental runs. The 2-3 s contention window during
periodic syncs collapses because the sync itself no longer parses the whole corpus twice.

**Rearmed clicks.** The 15-minute periodic slowdown ends — a warm `_idx` survives the sync. Every
subsequent click resolves via cached path.

**One invariant now spans three modules.** `sha256(canonical_id)` being both the on-disk name AND
the index key is a *load-bearing* property of the system, not an implementation detail of
`fetch.py`. A future change to `safe_filename` (e.g. shortening the hex slice) or to
`canonical_id` (e.g. changing normalization) would silently break every compute-first lookup — files
still exist, but nobody finds them until the fallback scan runs. Any such change must ship a
migration and update this ADR.

**Fallback exists for a reason.** The ~1-in-1000 legacy case is a real .eml whose derived canonical
id no longer matches its filename (likely from an earlier form of `canonical_id`). Correctness is
preserved: the fallback scan finds it on first miss and caches for future lookups.

**Bounded memory.** `_idx` (webapp) and `_env_cache` (report) grow with corpus size; each is a small
dict entry (a Path + a small dict of parsed envelope fields). Today's ~1116-file corpus is under a
few MB total — no eviction needed. Process restart clears both.

**What this does NOT change**:
- The `sync` still runs as a daemon thread in the FastAPI process; a click that lands during
  `build_crm`'s 22.5 s of CPU still contends for the GIL. Moving sync into a subprocess is a
  separate decision (deferred from the perf diagnosis's fix #5). This ADR removes the two
  parse-heavy contention windows, which alone eliminates the click-during-sync slowdown in normal
  operation.
- ADR-002 (read-only IMAP) — no IMAP behavior changes.
- ADR-009 (idempotent by default) — the caches are pure memoization; a re-run over the same corpus
  yields the same output.
- ADR-046 (attachment funnel) — the funnel's byte identity is untouched; only the file-locating
  step is faster.

**Pinned by**:
- [tests/test_webapp.py](../../tests/test_webapp.py) — `_file_for` computes with zero parses; the
  fallback runs exactly once and latches; `_rebuild_state` does not clear `_idx`.
- [tests/test_report.py](../../tests/test_report.py) — `prepare()` never scans the whole corpus;
  a second `prepare()` on the same corpus is zero-parse.
- [tests/test_specbuild.py](../../tests/test_specbuild.py) — `rebuild_jobspecs` resolves corpus
  files by computed name; 15 noise `.eml`s are never parsed.

<!-- Immutable once Accepted. Supersede with a new ADR; don't edit history. -->
