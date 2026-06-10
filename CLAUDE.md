# email-2-data

> Lindo Serviço project — type: **data-extraction**. Scaffolded 2026-06-10 from
> `project-scaffolding-roadmap` (standards-v1.1.0).

## Read first — inherited company standards

This project carries a frozen copy of the company standards in **`./standards/`**.
**Read them before doing anything**; they define how we work here, not just this
project. Order:

1. `standards/00-company-context.md` — who we build for, ways of working, PT/EN locale
2. `standards/03-engineering-principles.md` — how to think about the code (zero
   hallucination, strike narrowly, surprise protocol, traceability)
3. `standards/04-always-verify.md` — the checklist to run before declaring anything done
4. `standards/01-hardware-baseline.md` + `standards/02-network-lan.md` — the hardware
   and LAN this runs on (**honor the port table; never guess specs**)
5. `standards/06-tech-stack-defaults.md` — what to reach for unless told otherwise
6. `standards/07-project-taxonomy.md` — this is a **data-extraction** project; load that
   profile's emphasis

The standards are a **frozen snapshot** (standards-v1.1.0). The live source of truth is
`project-scaffolding-roadmap/standards/`. To refresh this copy, run
`bin/sync-standards.sh` from that repo against this directory. **Do not edit
`./standards/` here** — edit it upstream.

## This project specifically

- **Goal:** Read-only email triage for Lindo Serviço inboxes — classify each message by
  **counterparty · purpose · direction · priority** (deterministic pre-filter → Gemini on
  Vertex, driven by an editable playbook) and surface a prioritized decision queue.
- **Profile:** `data-extraction` — see [PROFILE.md](PROFILE.md). The dominant risk here
  is **zero-hallucination**: never "complete" a missing classification from a plausible
  pattern. Every output is FACT (with provenance) / INFERENCE / UNKNOWN.
- **Port:** **8042**, bound to **127.0.0.1 loopback only** — single-user, never public.
  Claimed in `standards/02-network-lan.md` §C. **NEVER use port 8000** (serve/bind/curl).
- **Deploy target:** local / single-user; Docker image carries no secrets or inbox data
  (`.env` + `config/` bind-mounted read-only). See [README.md](README.md) §Docker.

## Read these first, every session

1. [VISION.md](VISION.md) — the north star and the **governing principle** ("spend compute
   in proportion to `uncertainty × business impact`") plus the 8 tenets. If a change
   contradicts a tenet, stop and flag it.
2. [README.md](README.md) — product overview, the **module/pipeline table**, the
   incremental-by-default model, run commands, and the store/schema table.
3. [ROADMAP.md](ROADMAP.md) — phased plan and current status (Phases 0–2 done).
4. [design/approach.md](design/approach.md) — right-sized v1 engineering detail;
   [design/offline-extraction-plan.md](design/offline-extraction-plan.md) — the red-teamed
   offline layer.
5. The classifier brain, **editable, not code**: [config/triage_playbook.md](config/triage_playbook.md),
   [config/gazetteer.csv](config/gazetteer.csv), [config/spec_playbook.md](config/spec_playbook.md),
   [config/reply_playbook.md](config/reply_playbook.md). A playbook change is a behavior
   change — treat it like a code change (test + doc).

## Non-negotiables (from VISION + README — violating any is a defect)

1. **Read-only IMAP, always.** EXAMINE + `BODY.PEEK`; never STORE / DELETE / APPEND. The
   one unrecoverable mistake. Pinned by [tests/test_fetch_safety.py](tests/test_fetch_safety.py).
2. **Never silently bin a client.** Only deterministic **header signals** may IGNORE mail
   offline; an uncertain message escalates, never disappears. A false IGNORE loses revenue.
3. **The body decides counterparty, not the domain.** Domain is at most a prior. Counterparty
   is from **Lindo's POV** (CLIENT / SUPPLIER / LEAD).
4. **Direction ≠ counterparty.** An internal forward of a client PO is still *about a client*.
5. **Secrets via `.env` / ADC only** — never committed, never logged. **Raw bodies/addresses
   never logged.** Derived results are personal data.
6. **`workspace.db` is precious** (human decisions + projects) — never auto-rebuilt.
   `crm.db` / `sync.db` are regenerable. See README §Stores & schema.

## Definition of done

This project uses `standards/05-definition-of-done.md` (tiered L0–L4) **plus** the
[PROFILE.md](PROFILE.md) data-extraction must-verify list. On every change that is L2+:

1. **Test.** A regression test that fails before and passes after, in the matching
   `tests/test_<module>.py`. No stub tests, no `pytest.skip`, no "tests would go here".
2. **Docs.** Update every doc the change invalidates **in the same commit** — VISION/README/
   ROADMAP, the relevant `design/*.md`, or the right `config/*_playbook.md`. New durable
   facts go on a `docs/` 00–09 shelf (see "Docs" below), not into scratch files.
3. **QA self-review.** ruff (`ruff check src tests`), edge cases (empty/None/malformed MIME,
   non-ASCII charsets), the non-negotiables above, and idempotency (re-running yields the
   same result). Report findings explicitly.

**Stop-and-report rule.** If you cannot do all three, or the request is ambiguous about a
classification rule or source of truth, **STOP** and ask in chat (use `AskUserQuestion`).
Never mark a change done with an item hand-waved or deferred to a follow-up that does not exist.

## How to run and verify

```bash
.venv/bin/python -m pytest -q          # full suite (fast)
ruff check src tests                   # lint
email2data serve --port 8042           # local workspace UI on http://127.0.0.1:8042
```

All tests must pass before handing a change back. **Baseline pin (2026-06-10, `feat/cockpit`):
244 passed, 3 failed** — the 3 failures are in [tests/test_webapp.py](tests/test_webapp.py)
(`test_index_renders_the_live_report`, `test_faceted_filter_panel_wired`,
`test_sync_endpoint_refreshes_render_state`) and reflect in-progress cockpit UI work on this
branch, not a regression in your change. Re-confirm against this pin; if your change moves the
count, say why explicitly. "Tests pass" is a claim that must be backed by shown output.

## Conventions

- **User-facing strings**: Portuguese (pt-PT) — the web UI tabs (Fila, Para Ti, Projetos,
  Contrapartes), reports. Code, comments, commit messages, and these docs: English.
- **Commits** small and self-contained; explain *why*, not *what*; reference the test that
  would have caught a bug.
- **Idempotent by default** — `fetch`/`triage`/`sync` never re-spend Tier-1 LLM tokens on
  processed mail. Preserve this when touching the cascade.

## Docs

Canonical knowledge base under **`./docs/`** (00–09 structure, the documentation-gatekeeper
convention). New durable facts go there, not in scratch files: decisions → `03-decisions/`
ADR, exact values/contracts → `05-reference/`, how-to → `04-implementation/`.

> **Adoption status (2026-06-10):** this project was retrofitted to the scaffold standard via
> `bin/adopt-project.sh`. The `docs/` 00–09 shelves are **created but mostly empty** — the
> existing knowledge still lives in [README.md](README.md), [VISION.md](VISION.md),
> [ROADMAP.md](ROADMAP.md), and [design/](design/). **Deferred, tracked work:** migrate that
> content onto the shelves and back-fill ADRs in `docs/03-decisions/` for decisions already
> shipped (outbound classification by recipient, gazetteer-lookup-by-recipient-domain, the
> two-tier triage cascade). Until then, the files above remain the real source of truth.
