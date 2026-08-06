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

## Signing in (ADR-039 · ADR-040 · ADR-041)

The app is **default-deny**: every page and API route requires a session, and a signed-out visitor
receives the login page and nothing else — no triage data is rendered, not even hidden.

**Day to day, people are managed in the app** — **Administração → Pessoas** (admin-only) adds a
person, promotes or demotes, deactivates a leaver, edits inbox grants, and mints an invite link you
copy straight from the page. The CLI below is the bootstrap and the recovery path.

```bash
# first run, once — creates the first administrator (the /setup page 404s afterwards)
docker compose exec email2data email2data auth setup --name Filipe

# add the rest
docker compose exec email2data email2data auth add --name Pedro --login \
    --scopes pedro.ferreira@lindoservico.pt,orcamentos@lindoservico.pt
docker compose exec email2data email2data auth add --name Rita --responsible Filipe   # no login
docker compose exec email2data email2data auth invite --name Pedro   # single-use link, 72h
docker compose exec email2data email2data auth reset  --name Pedro   # temporary password
docker compose exec email2data email2data auth list
```

**People vs users.** Anyone in `people` can be assigned work. `--login` grants platform access;
without it the person is still assignable but **must** name a `--responsible` user who is accountable
for their queue — a database constraint, so work can never land in a queue nobody opens.

**One roster (ADR-041).** The owner picker *is* `people` — every active person, nobody else.
`settings.json team` and the legacy in-app roster are a **seed**, folded in once (as assignable-only,
accountable to the first admin) and then never read again; remove someone by **deactivating** them in
Administração, not by editing config.

**Everyone owns their own account.** The header names who is signed in; **«A minha conta»** changes
your password (the current one is required, and the other sessions end while yours survives), lists
your open sessions, and ends the rest. `auth reset` hands out a **temporary** password — the app then
holds every other page until that person replaces it.

**Sessions are rows, not signed tokens.** Logout revokes server-side, so a copied cookie dies with
it; `auth revoke --name X` ends every session that person has. There is no server secret to manage.

**The install can never be left without an administrator.** Demoting, deactivating or removing the
last active admin is refused, in the store — `/setup` 404s once any credential exists, so that state
cannot be repaired from the app at all. You also cannot demote or deactivate *yourself*: ask another
admin, or you would be locked out of the screen you would need to undo it.

### Reaching it from another workstation

Loopback stays the default. To expose it on the workshop LAN, generate a certificate and bind wide —
both opt-in, and intended to be turned on together:

```bash
bin/make-cert.sh                       # self-signed, SANs for loopback + this host's LAN IP
email2data serve --host 0.0.0.0 --port 8042 \
    --tls-cert certs/server.crt --tls-key certs/server.key
```

A LAN bind **without** `--tls-*` warns loudly: the session cookie would travel in clear text. The
self-signed certificate encrypts the transport but does not prove identity, so each workstation shows
a one-time browser warning. **"Never public" is unchanged** — no port-forward, no inbound webhook.

### Before any migration

```bash
./bin/backup-workspace.sh              # VACUUM INTO + verified restore of the precious DB
```

`workspace.db` runs in WAL mode, so a bare `cp` of the `.db` yields an **empty** database — the live
rows are in the `-wal` sidecar. This script asks SQLite for a consistent copy and then re-opens it to
prove the row counts match.

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

docker compose exec email2data email2data locate   # ADR-054: the sentence justifying each extracted value
docker compose exec email2data email2data narrate  # ADR-054: «Evolução da conversa» per multi-message thread
#   both SPEND TOKENS, both are incremental, and both already run after every triaging sync —
#   you only need them by hand for a backfill (--all) or to retry one item (--only <id>).
```

For the test suite and linting you still want a local dev install
(`pip install -e ".[dev,web,vertex]"` — the repo also ships a `.venv`); that is development, not a
deployment. Provider/auth options and the stores model are documented in
[docs/07-operations/running.md](docs/07-operations/running.md) and
[docs/05-reference/data-stores.md](docs/05-reference/data-stores.md).
