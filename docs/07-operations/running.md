# Operations — running email-2-data

| Field | Value |
| --- | --- |
| Type | Operations |
| Status | Active |
| Last reviewed | 2026-06-10 |

Day-to-day running of the service. For dev setup (install/test/lint) see
[../04-implementation/dev-workflow.md](../04-implementation/dev-workflow.md).

## CLI

```bash
email2data fetch       # read-only IMAP pull (incremental) → corpus/*.eml
email2data triage      # Tier-0 → Tier-1, only new emails → appends out/results.jsonl
email2data sync        # fetch-new + triage-new in one shot (what the webapp runs on boot)
email2data serve --port 8042   # local workspace UI on http://127.0.0.1:8042
email2data eval        # score counterparty/priority vs labels/worksheet.csv
#   add --full to fetch/triage/sync to re-bootstrap / reclassify everything
```

Incremental + idempotent by default ([ADR-009](../03-decisions/adr-009-incremental-idempotent-by-default.md)).

## Configuration & secrets

- `config/settings.json` — IMAP host/accounts + LLM provider (copy from `settings.example.json`).
  Default provider `vertex_gemini` (project `materials-492723`); alternative `anthropic`. `llm.context_cache`
  (Vertex) caches the large stable playbook prefix so it is billed once per batch, not per call (ADR-016);
  best-effort, falls back to the plain path on any error.
- `.env` (gitignored, loaded by `config.load_dotenv`) — `EMAIL2DATA_<ACCOUNT>_PASSWORD` (read-only
  IMAP) and LLM auth. **Never** committed or logged. A real exported env var overrides the file.

## Docker

```bash
cp config/settings.example.json config/settings.json   # fill in
cp .env.example .env                                    # fill in secrets (gitignored)
docker compose up --build                               # → http://127.0.0.1:8042
```

- The image carries **no secrets or inbox data**. `.env` is bind-mounted **read-only and parsed by
  the app's own `config.load_dotenv`** (not compose `env_file:`, which would collapse `$$`→`$` and
  corrupt a secret containing `$`). `config/` (read-only), `corpus/`, and `out/` are bind-mounted so
  the UID watermark + results persist.
- Binds `0.0.0.0:8042` **inside** the container but is published only to host loopback
  `127.0.0.1:8042` — single-user, never public, never 8000. In container mode `serve` **fails loud**
  rather than silently rebinding off 8042 (the published port would otherwise have no listener).
- On boot it runs one incremental `sync` automatically. A **fresh/empty `out/` volume boots cleanly**
  (no pre-seed step) — the boot-sync populates it on first run; the UI just starts empty until then.
- A `HEALTHCHECK` hits `/healthz`, so a crash-looping boot shows as **unhealthy** instead of a silent
  restart loop. Runs as **root by design** (single-user loopback; `out/`+`corpus/` are host bind mounts
  a non-root UID can't write) — see the Dockerfile for the non-root migration path (ADR-016).

## Vertex / Gemini auth (default provider)

The Gemini SDK uses Application Default Credentials. `docker-compose.yml` mounts the host gcloud
login (`$HOME/.config/gcloud`, read-only), so after a one-time
`gcloud auth application-default login` on the host, Vertex works in the container with nothing to
create. For a server with no developer login, drop a **service-account JSON** at
`config/sa-key.json` (gitignored, *Vertex AI User* role) and uncomment
`GOOGLE_APPLICATION_CREDENTIALS` in `docker-compose.yml`. The `anthropic` provider needs only
`ANTHROPIC_API_KEY` in `.env` and no Google auth.

## Health & precious data

The web UI re-syncs on boot and on the **Sincronizar** button. `out/workspace.db` holds human
decisions and is **precious** — never delete it; `crm.db`/`sync.db` are regenerable
([ADR-010](../03-decisions/adr-010-workspace-db-precious-vs-regenerable.md)).
