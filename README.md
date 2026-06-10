# email-2-data

Read-only email triage for Lindo Serviço inboxes: scan accounts over IMAP, classify each message by
**counterparty · purpose · direction · priority** (Gemini on Vertex, driven by an editable playbook,
with a free deterministic pre-filter), and surface a prioritized decision queue so the team sees
what's critical fast.

## Status

Phases **0 (foundation)**, **1 (taxonomy migration + baseline)**, and **2 (Tier-0 signals + gazetteer)**
done. The pipeline emits the counterparty/purpose/direction model and runs a two-tier cascade:
deterministic header signals decide obvious bulk offline (zero tokens); everything else goes to the
cheap LLM with those signals + a gazetteer hint attached. See [ROADMAP.md](ROADMAP.md).

## Docs

- **Vision (the north star):** [VISION.md](VISION.md) — what this is and the governing principle.
- **Roadmap (where we are / next):** [ROADMAP.md](ROADMAP.md) — phased plan and status.
- **Offline-layer design (red-teamed):** [design/offline-extraction-plan.md](design/offline-extraction-plan.md).
- **Approach (engineering detail):** [design/approach.md](design/approach.md).
- The classifier brain (editable): [config/triage_playbook.md](config/triage_playbook.md) ·
  the gazetteer (editable): [config/gazetteer.csv](config/gazetteer.csv).

## Pipeline / modules

```text
fetch.py    read-only IMAP → corpus/*.eml        (M0; incremental — UID watermark per mailbox)
sync.py     incremental glue: UID-cursor store (out/sync.db) + run_sync (fetch new → triage new)
envelope.py raw .eml → normalized fields          (robust MIME/charset)
signals.py  Tier-0 header facts: direction, bulk/automated, looks-forwarded
extract.py  Tier-0 deterministic values: nif/iban (authoritative) + amount/date/doc candidates
store.py    gazetteer: email-or-domain → counterparty hint (SQLite, hand-curated, a PRIOR not a verdict)
cascade.py  Tier-0 bulk-IGNORE offline  →  Tier-1 classifier.py (Gemini) with facts+values+hint attached
crm.py      CRM PoC: interactions (event log) + contacts (person rollup) from headers+verdicts (no LLM)
jobspec.py  Phase A: JobSpec (14 vars + provenance + confirmed) + Gate-1 readiness (deterministic)
specdraft.py Phase B: tiered LLM spec draft for LEAD/PO/estimate only (editable config/spec_playbook.md)
replydraft.py Phase C: draft a clarifying reply grounded in confirmed-vs-missing fields (never sends; copy/paste)
llm.py      shared LLM plumbing: provider dispatch (Gemini/Anthropic) + retry-on-empty, used by all LLM stages
workspace.py write layer: human decisions (SQLite) that overlay job specs and survive re-runs
project.py  cross-thread Projects: group MANY threads into ONE canonical spec + lifecycle (SQLite, precious)
export.py   offload a finished Project to an external estimating system (JSON dry-run | materials-costing API)
webapp.py   FastAPI "confirm one lead" workspace (localhost; never sends; copy/paste)
schema.py   TriageResult + structured-output contracts + priority derivation
cli.py      fetch | triage | sync | eval | crm | jobspec [--draft] [--reply] [--score] | project | serve
```

**Incremental by default.** `fetch` pulls only mail arrived since the last retrieve (a per-mailbox
IMAP **UID watermark** persisted in `out/sync.db`; a UIDVALIDITY change re-bootstraps), and `triage`
classifies only emails not already in `out/results.jsonl` (appends, never re-spends Tier-1 LLM tokens
on processed mail). Pass `--full` to either to ignore the watermark / reclassify everything. `email2data
sync` does fetch-new + triage-new in one shot. The webapp runs the same `sync` automatically in the
background on every boot (`settings.sync.on_startup`, default `true`) and on the **Sincronizar** button.

## Quick start

```bash
pip install -e ".[dev,web,vertex]"          # (repo ships a .venv; or make your own)
cp config/settings.example.json config/settings.json   # set IMAP host/accounts + Vertex project
cp .env.example .env                          # then fill in secrets — NO MORE `export VAR=...`
#   .env holds EMAIL2DATA_<ACCOUNT>_PASSWORD (read-only IMAP) and the LLM auth; it is gitignored and
#   loaded automatically (config.load_dotenv). A real exported env var still overrides the file.

email2data fetch       # read-only pull (incremental: only mail since last retrieve) → corpus/*.eml
email2data triage      # Tier-0 signals → Tier-1 Gemini, only new emails → appends out/results.jsonl
email2data sync        # fetch-new + triage-new in one shot (what the webapp runs on boot + button)
email2data serve       # local workspace UI on http://127.0.0.1:8042  (NEVER port 8000)
email2data eval        # score counterparty/priority vs labels/worksheet.csv
#   add --full to fetch/triage/sync to re-bootstrap / reclassify everything
```

LLM provider is configurable in `settings.json` (`llm.provider`: `vertex_gemini` or `anthropic`).
This project uses Vertex Gemini. Authenticate with either a **service-account JSON**
(`GOOGLE_APPLICATION_CREDENTIALS` in `.env`) or, for purely local runs, `gcloud auth
application-default login`. For `anthropic`, set `ANTHROPIC_API_KEY` in `.env` instead.

### Docker

```bash
cp config/settings.example.json config/settings.json   # fill in
cp .env.example .env                                    # fill in secrets (gitignored)
docker compose up --build                               # → http://127.0.0.1:8042
```

The image carries **no secrets or inbox data**: `.env` is **bind-mounted read-only and parsed by
the app's own `config.load_dotenv`** (not compose `env_file:`, which would collapse `$$`→`$` and
corrupt a secret containing `$`), and `config/` (read-only), `corpus/`, and `out/` are bind-mounted
so the UID watermark + results persist across restarts. The container binds `0.0.0.0:8042` internally but is published only to host loopback
(`127.0.0.1:8042`) — single-user, never public, never 8000. On boot it runs one incremental `sync`
(fetch-new + triage-new) automatically.

**Vertex/Gemini auth in the container** (this project's default provider): the Gemini SDK uses
Application Default Credentials. `docker-compose.yml` mounts your host gcloud login
(`$HOME/.config/gcloud`, read-only) into the container, so after a one-time `gcloud auth
application-default login` on the host, Vertex works inside Docker with **nothing to create**. For a
server with no developer login, drop a **service-account JSON** at `config/sa-key.json` (gitignored,
needs the *Vertex AI User* role) and uncomment `GOOGLE_APPLICATION_CREDENTIALS` in
[docker-compose.yml](docker-compose.yml). The `anthropic` provider needs only `ANTHROPIC_API_KEY` in
`.env` and no Google auth at all.

## Projects (cross-thread)

The per-message pipeline keys everything by `message_id`. A real estimating **job**, though, accretes
across several threads before it can be costed. A **Project** ([project.py](src/email2data/project.py))
is that first-class entity: it owns an explicit set of `thread_root`s, merges everything known across
them into **one canonical job spec**, tracks a lifecycle stage, and is eventually offloaded to an
external estimating system ([export.py](src/email2data/export.py)).

**Prerequisite:** thread expansion needs the CRM. Run `email2data crm` first (builds `out/crm.db`).
Without it, projects still work in a degraded single-thread mode (an attached `message_id` is treated
as a lone message — no sibling expansion).

**Lifecycle:** `LEAD → GATHERING → ESTIMABLE → QUOTED → WON | LOST`, plus `ARCHIVED` (soft-retire —
hidden from lists by default). Export advances a project to `QUOTED`.

**Merge policy:** job-level fields (deadline, budget, …) auto-merge across messages by source
precedence (`user > llm > offline`, recency breaks ties); divergent values are surfaced as *conflicts*,
never silently dropped. Line items are **project-owned** — seeded once from a source message then
hand-curated; never auto-unioned across threads. Hand edits (`project_fields`) always win, and every
edit is recorded append-only in `project_field_history`.

```bash
email2data project new --title "Troféus KIA" --from-message <message_id>   # create + attach + seed
email2data project attach p-0001 <message_id|thread_root>                  # add a thread
email2data project detach p-0001 <message_id|thread_root>                  # remove a thread
email2data project show   p-0001                                          # canonical spec + readiness + dangling warnings
email2data project list [--all]                                           # --all includes ARCHIVED
email2data project delete p-0001                                          # hard-delete (duplicates/mistakes)
email2data project export p-0001 [--adapter json|materials-costing] [--force]
```

The same actions are available live in the **Projetos** tab of `email2data serve` (create from a lead,
edit fields, attach/detach threads, change stage, export, delete). The web UI is **live-only** —
project actions hit the API, so they do nothing in the static `out/report.html`.

**Export honesty boundary:** materials-costing line items reference catalog materials + pricing
snapshots our free-text spec does not carry, so export sends only the **shell** (brief in
`project_name`/`cliente`/`descricao`/`notas`); the estimator builds the costed lines there. Export
never auto-fires — it is always an explicit human action, and refuses a non-estimable project or a
re-export unless `--force`.

### Stores & schema

| DB                 | Status                                     | Rebuilt?                                          | Versioned                                   |
| ------------------ | ------------------------------------------ | ------------------------------------------------- | ------------------------------------------- |
| `out/crm.db`       | regenerable                                | yes — `email2data crm` drops & rebuilds each run  | `crm.SCHEMA_VERSION`                        |
| `out/sync.db`      | cursor (per-mailbox IMAP UID watermark)    | deletable — next `fetch` re-bootstraps by date    | `sync.SCHEMA` (additive)                    |
| `out/workspace.db` | **precious** (human decisions + projects)  | **never**                                         | `workspace.SCHEMA_VERSION` (`user_version`) |

`workspace.db` evolves **in place**: `Workspace.connect` runs `_migrate`, which stamps `user_version`
and is where future breaking migrations go (additive table changes are handled by `CREATE TABLE IF NOT
EXISTS`). Because `project_threads.thread_root` references the regenerable CRM, a rebuild can orphan a
reference — `project show` / the web UI flag these as **dangling** so the project never silently loses
messages.

## Non-negotiables

Read-only IMAP (EXAMINE + `BODY.PEEK`; never STORE/DELETE/APPEND) · counterparty is from **Lindo's
POV**, decided by the body not the domain · **only header signals may bin offline**; never silently
IGNORE a possible client · secrets via env/ADC only · raw bodies never logged. See
[VISION.md](VISION.md) and [design/approach.md](design/approach.md).
