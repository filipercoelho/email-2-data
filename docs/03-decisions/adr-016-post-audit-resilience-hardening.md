# ADR-016 — Post-audit resilience & token-reuse hardening

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-06-15 |

## Context

A multi-agent performance/best-practices audit (multi-account sync, dedup, LLM caching, incremental
project/chain review, Docker readiness) confirmed the architecture is sound but found one **deploy
blocker** and a set of resilience/cost gaps. None overturns an existing ADR; each **strengthens** one.
This ADR records the decisions taken to close them so the invariants now hold under failure, not just
on the happy path.

## Decision

1. **Boot never crashes on a fresh `out/` volume.** `report.prepare()` guards the `results.jsonl`
   read like its `contacts/cost/jobspecs` siblings — `create_app` runs before the lifespan boot-sync
   writes that file, so a clean `docker compose up` (empty volume) must construct and serve, not
   crash-loop. Pins: `tests/test_report.py`, `tests/test_webapp.py::test_from_settings_builds_on_fresh_out_dir`.

2. **One failing account never starves the others.** `fetch.fetch_all` wraps each account in
   `try/except FetchError`: the failure is **audited** (`fetch_account_failed`, credential-safe detail)
   and skipped so every healthy account still syncs and advances its watermark. Only a **total** outage
   (every account failed) re-raises — surfaced by the CLI as a tidy `Fetch error:` line, not a raw
   traceback. Strengthens [ADR-002](adr-002-read-only-imap-guarantee.md)/
   [ADR-009](adr-009-incremental-idempotent-by-default.md). Pins: `tests/test_sync.py`.

3. **A Tier-1 failure escalates, it does not disappear.** When classification raises (e.g. the LLM is
   unauthenticated/down after `llm.py` exhausts its retries), the message is written as a
   **`NEEDS_REVIEW`** fallback (`decided_by="tier1:error"`) instead of being dropped — so a credential
   outage can never make client mail silently vanish from the Fila. It is final; `triage --full`
   reclassifies once the LLM is back. Strengthens [ADR-003](adr-003-counterparty-from-body-not-domain.md)
   ("never silently bin a client") and [ADR-006](adr-006-two-tier-cascade-anti-ignore-guardrail.md)
   (uncertain → `NEEDS_REVIEW`). Pin: `tests/test_sync.py::test_triage_escalates_to_needs_review_when_tier1_fails`.

4. **The stable playbook prefix is billed once, not per call.** The Gemini path caches the large
   `system_instruction` (the ~2.4K-token triage playbook) as a Vertex `CachedContent`, keyed by
   `(model, sha256(system))`, reused across a sync's batch. **Best-effort**: below the model's cache
   floor, with caching disabled, or on an expired/absent cache it falls back to the plain path inside
   the retry loop — classification never depends on caching. Tunable via `llm.context_cache*` in
   settings. Strengthens [ADR-001](adr-001-compute-proportional-to-uncertainty-impact.md) within
   [ADR-012](adr-012-shared-llm-provider-dispatch.md). Pin: `tests/test_llm.py`.

5. **The reply draft is reused, not re-billed.** `/api/reply[/stream]` memoize the generated draft
   server-side, keyed by `(message_id, hash of the exact reply prompt)`. A reload / second client / re-
   open for an unchanged spec is served from the memo (0 tokens); any spec/readiness change re-keys and
   regenerates. Regenerable and in-process (cold on restart) — it caches LLM output, never precious
   state. Strengthens [ADR-001](adr-001-compute-proportional-to-uncertainty-impact.md). Pin:
   `tests/test_webapp.py`.

6. **Dedup is folder/order-independent even without a Message-ID.** `fetch` hashes the canonical id
   from the **original** bytes, before the `X-Email2Data-Source` header is injected, so a Message-ID-less
   email present in both INBOX and a Sent folder maps to **one** corpus file (Sent copy wins →
   `outbound`). Message-ID-bearing mail (the norm) is unaffected. Strengthens
   [ADR-009](adr-009-incremental-idempotent-by-default.md). Pin:
   `tests/test_sync.py::test_no_message_id_dedups_across_inbox_and_sent`.

7. **Container ops hygiene.** `serve` refuses to silently rebind off the requested port in container
   mode (`--host 0.0.0.0/::`) — the published compose port would otherwise have no listener; it fails
   loudly instead. A `/healthz` route + a Dockerfile `HEALTHCHECK` report a crash-looping boot as
   *unhealthy* rather than a silent restart loop. The container runs as **root by design** (single-user
   loopback; `out/`+`corpus/` are host bind mounts a non-root UID typically can't write) — documented in
   the Dockerfile with the non-root migration path. Pins: `tests/test_cli.py`,
   `tests/test_webapp.py::test_healthz_liveness_probe`.

## Consequences

- A first-run Docker deploy on a clean volume works with no pre-seed step.
- An expired/expanded credential on one of several accounts degrades that account only; the others keep
  syncing, and the failure is in the audit log.
- A persistent LLM outage produces a queue of visible `NEEDS_REVIEW` items (clear via `--full`), never a
  silently-empty Fila.
- Recurring token cost drops: the playbook prefix and unchanged reply drafts are no longer re-billed.
- Trace: `report.py`, `fetch.py`, `cascade.py`, `llm.py`, `webapp.py`, `cli.py`, `Dockerfile`.
