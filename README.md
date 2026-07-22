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
cp config/settings.example.json config/settings.json   # set IMAP host/accounts + Vertex project
cp .env.example .env                                    # fill secrets (gitignored, auto-loaded)

docker compose up -d --build   # THE deploy: webapp on http://127.0.0.1:8042 + the intake worker
docker compose ps              # email2data (healthy) + intake-bot
```

**Docker is the only deployment target.** Both long-running processes are compose services —
`email2data` (the UI, published to loopback only, **never port 8000**) and `intake-bot` (the
Telegram capture worker, no port). **Do not run `email2data serve` or `email2data intake-bot` from
the host:** the container already holds `127.0.0.1:8042`, so a host `serve` silently loses the bind
and you end up testing the container's image while believing you are testing your working tree.
A code change needs `docker compose up -d --build` to take effect (`src/` is baked into the image;
`config/`, `corpus/`, `out/` and `.env` are mounted, so playbook edits are live immediately).

The batch commands run inside the container (or in a dev shell for tests):

```bash
docker compose exec email2data email2data fetch    # read-only IMAP pull (incremental) → corpus/*.eml
docker compose exec email2data email2data triage   # Tier-0 signals → Tier-1 Gemini, new mail only
docker compose exec email2data email2data sync     # fetch-new + triage-new (also on boot + button)
docker compose exec email2data email2data eval     # score counterparty/priority vs labels
#   add --full to fetch/triage/sync to re-bootstrap / reclassify everything
```

For the test suite and linting you still want a local dev install
(`pip install -e ".[dev,web,vertex]"` — the repo also ships a `.venv`); that is development, not a
deployment. Provider/auth options and the stores model are documented in
[docs/07-operations/running.md](docs/07-operations/running.md) and
[docs/05-reference/data-stores.md](docs/05-reference/data-stores.md).
