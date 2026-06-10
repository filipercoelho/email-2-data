# 06 — Tech Stack Defaults

| Field | Value |
|---|---|
| Type | Reference / Standard |
| Audience | All agents, all projects |
| Status | Active (defaults observed from existing projects) |
| Owner | Filipe Coelho |

> What to reach for **unless the task says otherwise**. These are observed from your
> existing projects (notably `calculo-custos-producao`), so a new project that
> follows them will look and behave like the rest of the shop. Deviating is allowed —
> but it's a decision, so record it as an ADR.

## Backend (Python)

| Concern | Default | Seen in |
|---|---|---|
| Language | Python 3 (via `pyenv`) | global setup |
| Web framework | **FastAPI** | `fastapi==0.115.0` |
| ASGI server | **uvicorn** (`uvicorn[standard]`) | `uvicorn api.main:app --host 0.0.0.0 --port 8080` |
| Validation / schemas | **Pydantic v2** | `pydantic==2.9.2` |
| ORM | **SQLAlchemy 2.x** | `SQLAlchemy==2.0.36` |
| Migrations | **Alembic** | `alembic==1.14.0`, `api/migrations/` |
| HTTP client | **httpx** | `httpx==0.28.1` |
| Tests | **pytest** (`PYTHONPATH=. python3 -m pytest`) | global perms |

**Conventional backend layout** (from `calculo-custos-producao`):

```
api/
├── main.py            # FastAPI app entry (uvicorn api.main:app)
├── routes/            # routers per resource
├── services/          # business logic (pricing_service, project_status, …)
├── models/ (or models.py)  # SQLAlchemy ORM
├── schemas            # Pydantic request/response (kept separate from ORM)
├── dependencies.py    # FastAPI deps: auth, gates (require_project_mutable, …)
├── migrations/        # Alembic
└── tests/             # pytest, mirrors routes/services
```

Patterns in use: per-request `contextvars.ContextVar` for the acting user (flows to
audit writes); FastAPI dependency gates for auth/authorization; ORM and Pydantic
schemas kept **separate**.

## Frontend

| Concern | Default | Seen in |
|---|---|---|
| Framework | **React 18** | `react ^18.3.1` |
| Build tool | **Vite 6** | `vite ^6.0.0`, `@vitejs/plugin-react` |
| i18n | `src/i18n.js` with **`pt` / `en`** locales | observed |

> Default user-facing language **pt-PT**, en fallback. EUR currency, PT date/number
> formatting (see `00`).

## Packaging & runtime

| Concern | Default |
|---|---|
| Containers | **Docker / `docker compose`** available; use for anything with services to orchestrate |
| Health check | expose **`GET /health`** — smoke tests rely on it (`/health` seen on 8080/8766/8767) |
| Default dev port | **8080** for the primary API — but **claim the real port in `02`** before hard-coding |
| Deploy to NAS | `deploy_nas.sh`-style copy script when the target is `LS-NETDISK` |

## Edge / machine-side

| Concern | Default |
|---|---|
| Microcontroller | **ESP32** (project `lindo-servico-esp32`) — toolchain UNKNOWN, confirm |
| CNC / G-code | projects exist (`gcode`, `controlador-cnc-azulejos`, `svg-to-dxf-optimizer`) — machine dialect must be **verified against the real controller**, never assumed |

## When to deviate

Use a different tool only when the task genuinely needs it, and then:

1. Note it as a **Surprise/decision**, not a silent swap.
2. Write an **ADR** (`docs/03-decisions/`) — "chose X over the FastAPI default
   because…".
3. Keep the rest of the conventions (health check, tests, i18n, port claim) intact.

> Update this file when a default genuinely shifts across projects, and bump
> `VERSION`. Don't encode a one-off project's choice here.
