# Architecture — module map & data flow

| Field | Value |
| --- | --- |
| Type | Architecture |
| Status | Active |
| Last reviewed | 2026-06-10 |

How the pipeline is structured and why. The deeper engineering rationale is in
[approach.md](approach.md) (right-sized v1) and
[offline-extraction-layer.md](offline-extraction-layer.md) (the red-teamed offline tier); the
invariants are in the [decisions registry](../03-decisions/index.md).

## Data flow

```text
IMAP (read-only)                                  ADR-002
   │  fetch.py        EXAMINE + BODY.PEEK, UID-watermark incremental   ADR-009
   ▼
corpus/*.eml
   │  envelope.py     raw MIME/charset → normalized fields
   ▼
normalized message
   │  signals.py      Tier-0 header facts: direction, bulk, is_forward  ADR-004
   │  extract.py      Tier-0 values: nif/iban (FACT) + amount/date (candidate)  ADR-007
   │  store.py        gazetteer hint (prior, not verdict)               ADR-005
   ▼
cascade.py  ── Tier 0: bulk + no hint → IGNORE (free) ──► out/results.jsonl
   │         └ Tier 1: classifier.py → Gemini via llm.py ─┘   ADR-001, ADR-006, ADR-012
   ▼
out/results.jsonl  (TriageResult, append-only, decided_by stamped)      ADR-008
   │  crm.py          interactions + contacts rollup (no LLM)
   ▼
out/crm.db
   │  jobspec.py      JobSpec (14 vars + Gate-1 readiness)
   │  specdraft.py    Phase-B tiered spec draft (LEAD/PO only)
   │  replydraft.py   Phase-C clarifying reply (never sends)
   ▼
project.py  cross-thread Projects → one canonical spec (workspace.db, precious)  ADR-010
   │  export.py       shell-only offload → JSON | materials-costing API  ADR-011
   ▼
webapp.py   FastAPI workspace UI on 127.0.0.1:8042 (live) + static report.html
```

## Module responsibilities

| Module | Tier | Responsibility |
| --- | --- | --- |
| `fetch.py` | I/O | read-only IMAP → `corpus/*.eml`; per-mailbox UID watermark |
| `sync.py` | glue | UID-cursor store (`out/sync.db`) + `run_sync` (fetch-new → triage-new) |
| `envelope.py` | parse | raw `.eml` → normalized fields (robust MIME/charset) |
| `signals.py` | Tier-0 | header facts: direction, bulk/automated, looks-forwarded |
| `extract.py` | Tier-0 | deterministic values: nif/iban authoritative + amount/date/doc candidates |
| `store.py` | knowledge | gazetteer: email-or-domain → counterparty hint (SQLite, hand-curated) |
| `cascade.py` | router | Tier-0 offline IGNORE → Tier-1 `classifier.py` with facts+hint attached |
| `classifier.py` | Tier-1 | the LLM triage call (via `llm.py`) |
| `llm.py` | plumbing | provider dispatch (Gemini/Anthropic) + retry-on-empty, shared by all LLM stages |
| `crm.py` | rollup | interactions (event log) + contacts (person rollup) from headers+verdicts |
| `jobspec.py` | Phase A | JobSpec (14 vars + provenance + confirmed) + Gate-1 readiness |
| `specdraft.py` | Phase B | tiered LLM spec draft for LEAD/PO/estimate only |
| `replydraft.py` | Phase C | clarifying reply grounded in confirmed-vs-missing fields (never sends) |
| `workspace.py` | write | human decisions (SQLite) overlaying job specs; survive re-runs |
| `project.py` | entity | cross-thread Projects: many threads → one canonical spec + lifecycle |
| `export.py` | offload | shell-only export to JSON (dry-run) or materials-costing API |
| `webapp.py` | UI | FastAPI workspace (localhost; never sends; copy/paste) |
| `schema.py` | contract | `TriageResult` + structured-output schemas + priority derivation |
| `cli.py` | entry | `fetch \| triage \| sync \| eval \| crm \| jobspec \| project \| serve` |

## Why two tiers

The split is the direct expression of
[ADR-001](../03-decisions/adr-001-compute-proportional-to-uncertainty-impact.md): certain,
low-stakes mail is decided offline for free (Tier 0); only uncertain, high-stakes mail reaches
the model (Tier 1). The anti-IGNORE guardrail
([ADR-006](../03-decisions/adr-006-two-tier-cascade-anti-ignore-guardrail.md)) ensures the
free path can never silently bin a client.

## Web surface

`webapp.py` serves a **live** workspace (Fila / Para Ti / Projetos / Contrapartes) on
`127.0.0.1:8042`; it runs an incremental `sync` on boot and on **Sincronizar**. Project actions
hit the API, so they are inert in the static `out/report.html`. UI/UX spec:
[../05-reference/cockpit-design.md](../05-reference/cockpit-design.md).
