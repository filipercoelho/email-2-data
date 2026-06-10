# email-2-data

Read-only email triage for Lindo Serviço inboxes: scan accounts over IMAP, classify each message by
**counterparty · purpose · direction · priority** (Gemini on Vertex, driven by an editable playbook,
with a free deterministic pre-filter), and surface a prioritized decision queue so the team sees
what's critical fast. Downstream it accretes cross-thread **Projects** into estimable job specs and
exports a finished brief to the materials-costing estimator.

> **Read-only, always. Never sends mail. Never silently bins a client.** The body — not the domain —
> decides counterparty, from Lindo's point of view.

## Status

Phases **0 (foundation)**, **1 (taxonomy migration + baseline)**, and **2 (Tier-0 signals +
gazetteer)** done. See [docs/01-requirements/roadmap.md](docs/01-requirements/roadmap.md).

## Documentation

This project follows the company docs standard (`docs/` 00–09). The agent contract is
**[CLAUDE.md](CLAUDE.md) — read it first.** Then:

| Start here | For |
| --- | --- |
| [VISION.md](VISION.md) | The north star + the governing principle ("spend compute ∝ uncertainty × business impact") |
| [docs/00-overview.md](docs/00-overview.md) | What this is and who uses it |
| [docs/02-architecture/module-map.md](docs/02-architecture/module-map.md) | The modules and the data flow |
| [docs/03-decisions/](docs/03-decisions/index.md) | The ADR registry — the *why* behind every invariant |
| [docs/05-reference/](docs/05-reference/index.md) | Exact schemas (`TriageResult`, vocabularies, priority) + the data stores |
| [docs/07-operations/running.md](docs/07-operations/running.md) | Running it: CLI, Docker, Vertex auth |
| [docs/06-qa/](docs/06-qa/index.md) | How we prove it works (eval gate + tests) |

The classifier brain is **editable config, not code**: [config/triage_playbook.md](config/triage_playbook.md),
[config/gazetteer.csv](config/gazetteer.csv), [config/spec_playbook.md](config/spec_playbook.md),
[config/reply_playbook.md](config/reply_playbook.md).

## Quick start

```bash
pip install -e ".[dev,web,vertex]"                     # (repo also ships a .venv)
cp config/settings.example.json config/settings.json   # set IMAP host/accounts + Vertex project
cp .env.example .env                                    # fill secrets (gitignored, auto-loaded)

email2data fetch       # read-only IMAP pull (incremental) → corpus/*.eml
email2data triage      # Tier-0 signals → Tier-1 Gemini, only new emails → appends out/results.jsonl
email2data sync        # fetch-new + triage-new in one shot (what the webapp runs on boot + button)
email2data serve --port 8042   # local workspace UI on http://127.0.0.1:8042  (NEVER port 8000)
email2data eval        # score counterparty/priority vs labels/worksheet.csv
#   add --full to fetch/triage/sync to re-bootstrap / reclassify everything
```

Docker, provider/auth options, and the stores model are documented in
[docs/07-operations/running.md](docs/07-operations/running.md) and
[docs/05-reference/data-stores.md](docs/05-reference/data-stores.md).
