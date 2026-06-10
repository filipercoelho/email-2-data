# Decisions — ADR registry

The architectural invariants that protect email-2-data, one decision per ADR. This index is
the discoverability surface; read it top-to-bottom once, refer back when a change touches an
invariant. ADRs are **immutable once Accepted** — supersede with a new ADR, don't edit history
(`adr-000-template.md` is the template).

If your change touches an ADR's invariant, the ADR must hold or be explicitly superseded by a
new ADR in the same commit. A new architectural rule means a new ADR + a row here.

| ADR | Slug | One-line invariant |
| --- | --- | --- |
| [001](adr-001-compute-proportional-to-uncertainty-impact.md) | compute-proportional-to-uncertainty-impact | Spend compute (tokens, model power) in proportion to `uncertainty × business impact` — the governing principle. |
| [002](adr-002-read-only-imap-guarantee.md) | read-only-imap-guarantee | IMAP is read-only, enforced twice: `EXAMINE` + `BODY.PEEK[]`; STORE/EXPUNGE/DELETE/APPEND/COPY forbidden. Pinned by `test_fetch_safety.py`. |
| [003](adr-003-counterparty-from-body-not-domain.md) | counterparty-from-body-not-domain | The body decides counterparty (CLIENT/SUPPLIER/LEAD from Lindo's POV); domain is a prior, never a verdict. Never silently bin a client. |
| [004](adr-004-direction-by-recipient-not-sender.md) | direction-by-recipient-not-sender | Direction is its own header-derived axis; outbound mail is classified by the recipient, not the sender. |
| [005](adr-005-gazetteer-is-prior-not-verdict.md) | gazetteer-is-prior-not-verdict | The gazetteer is a prior attached to the LLM input; any gazetteer knowledge vetoes an offline IGNORE. |
| [006](adr-006-two-tier-cascade-anti-ignore-guardrail.md) | two-tier-cascade-anti-ignore-guardrail | Tier-0 header signals bin obvious bulk offline (free); only header signals may IGNORE, and only when no gazetteer hint exists; uncertain → NEEDS_REVIEW. |
| [007](adr-007-nif-iban-authoritative-rest-candidates.md) | nif-iban-authoritative-rest-candidates | Deterministic extraction: NIF/IBAN are FACT (validated); amounts/dates/docs are candidates (INFERENCE), never auto-promoted. |
| [008](adr-008-every-verdict-records-decided-by.md) | every-verdict-records-decided-by | Every `TriageResult` stamps `decided_by` (tier + engine/version) so verdicts are debuggable, auditable, replayable. |
| [009](adr-009-incremental-idempotent-by-default.md) | incremental-idempotent-by-default | fetch/triage/sync are incremental (UID watermark in `sync.db`) and idempotent; never re-spend Tier-1 tokens on processed mail. `--full` overrides. |
| [010](adr-010-workspace-db-precious-vs-regenerable.md) | workspace-db-precious-vs-regenerable | `workspace.db` is precious (human decisions) and never auto-rebuilt; `crm.db`/`sync.db` are regenerable; orphaned refs surface as dangling. |
| [011](adr-011-export-honesty-boundary.md) | export-honesty-boundary | Export sends only the job shell (brief), never costed lines; always an explicit human action; refuses non-estimable / re-export without `--force`. |
| [012](adr-012-shared-llm-provider-dispatch.md) | shared-llm-provider-dispatch | All LLM access goes through `llm.py` (provider dispatch + retry-on-empty); provider configurable (vertex_gemini default / anthropic). |
