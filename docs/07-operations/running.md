# Operations — running email-2-data

| Field | Value |
| --- | --- |
| Type | Operations |
| Status | Active |
| Last reviewed | 2026-07-20 |

Day-to-day running of the service. For dev setup (install/test/lint) see
[../04-implementation/dev-workflow.md](../04-implementation/dev-workflow.md).

## Docker is the only deployment target (2026-07-20)

Both long-running processes are `docker-compose` services. Nothing is deployed any other way.

| Service | What | Port |
| --- | --- | --- |
| `email2data` | the webapp (boot-sync + UI + periodic sync) | published `127.0.0.1:8042` only |
| `intake-bot` | the Telegram capture worker (ADR-019/-021) | **none** — outbound long-poll only |

```bash
docker compose up -d --build   # THE deploy (and how a code change takes effect)
docker compose ps              # email2data (healthy) + intake-bot
docker compose logs -f intake-bot
```

**Do not run `email2data serve` or `email2data intake-bot` from the host.** The container already
holds `127.0.0.1:8042`, so a host `serve` **silently loses the bind and logs nothing** — you then curl
8042 and verify the container's image while believing you are testing your working tree. (That
failure is real: it produced a wrong "the change isn't there" conclusion on 2026-07-20.) Two
intake-bot pollers on one bot token also fight over Telegram `getUpdates`. To exercise a live server
without disturbing the deploy, drive the app in-process with `TestClient`, or serve on a different
free port (8043 — **never 8000**).

`src/` is baked into the image, so **editing code changes nothing that is running** until you rebuild.
`config/`, `corpus/`, `out/` and `.env` are bind-mounted, so a *playbook* edit is live immediately.

## CLI

Run inside the container:

```bash
docker compose exec email2data email2data fetch    # read-only IMAP pull (incremental) → corpus/*.eml
docker compose exec email2data email2data triage   # Tier-0 → Tier-1, only new emails
docker compose exec email2data email2data sync     # fetch-new + triage-new (also on boot)
docker compose exec email2data email2data eval     # score counterparty/priority vs labels
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

## Docker — first run and details

```bash
cp config/settings.example.json config/settings.json   # fill in
cp .env.example .env                                    # fill in secrets (gitignored)
docker compose up -d --build                            # → http://127.0.0.1:8042 + intake worker
```

- **`intake-bot` starts after `email2data` is healthy**, via `depends_on: condition: service_healthy`.
  That is a correctness constraint, not tidiness: the worker opens `workspace.db` with
  `migrate=False` and exits with `WorkspaceVersionError` rather than upgrade the schema behind the
  webapp's back ([ADR-021](../03-decisions/adr-021-intake-lan-binding-minimal-auth.md)'s
  single-migrator gate). The webapp migrates first; the worker then attaches to a current schema.
- **`intake-bot` disables the inherited `HEALTHCHECK`.** The image's check probes `/healthz` on 8042,
  which is right for the webapp and meaningless for a worker that serves no HTTP — left inherited it
  fails forever and reports a healthy worker as `unhealthy`, which is worse than no signal. Its
  liveness *is* the main process: if the poller dies the container exits and `restart: unless-stopped`
  brings it back.

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
