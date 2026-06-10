# 07 — Project Taxonomy

| Field | Value |
|---|---|
| Type | Standard |
| Audience | All agents, all projects |
| Status | Active |
| Owner | Filipe Coelho |

> The kinds of applications Lindo Serviço builds. When the user says "build X," an
> agent should recognise **which type** it is and load that type's extra
> expectations — what to verify, what usually goes wrong, the default shape. Each
> type maps to a profile in `templates/project-profiles/` that the bootstrapper
> seeds.

## The four types

| Type | `profile` slug | What it is | Examples in your repos |
|---|---|---|---|
| **LAN internal tool** | `lan-tool` | An app that runs on the LAN for staff: inventory, asset tracking, time logging, requests. FastAPI + React, lives on a server host, reached by browser. | `inventory-management`, `it-asset-management`, `registo-horas`, `requisitos-departamento`, `gestor-projecto` |
| **Machine / CNC integration** | `machine-integration` | Software that talks to physical machines: CNC controllers, laser, ESP32, G-code, file→toolpath conversion. Correctness against the real machine is everything. | `controlador-cnc-azulejos`, `gcode`, `laser-machine`, `svg-to-dxf-optimizer`, `vectorial-file-machine-optimization`, `lindo-servico-esp32` |
| **Data extraction / migration** | `data-extraction` | Pull structured data out of an existing system (Excel, Outlook, vCards, email) into a usable format. Zero-hallucination is the dominant risk. | `email-2-data`, `outlook-backup-wizard`, `vcard-analyzer`, `calculo-custos-producao` (Excel reverse-engineering) |
| **Company web app** | `web-app` | A larger web application with real users, auth, a database, audit. The full FastAPI + SQLAlchemy + React stack. | `calculo-custos-producao`, `idea-submission-platform`, `website` |

> Some projects span two types (a web app that also drives a machine). Pick the
> **dominant** type for the profile, and pull in the second profile's checklist
> items manually.

## What every type inherits

All four inherit `00`–`06` in full: the principles, the always-verify checklist, the
definition of done, the stack defaults. The profile only **adds** type-specific
concerns — it never removes a base requirement (`04` is a floor).

## Per-type emphasis (full detail lives in each profile)

### `lan-tool`
- **Verify:** correct server host + claimed port (`02`); reachable on the LAN bind
  address; survives a server reboot; PT/EN UI.
- **Common failure:** localhost-only bind; port collision; "works on the dev Mac"
  but not on the server.
- **Default shape:** FastAPI API + React/Vite UI + `/health`, deployed to a server
  host.

### `machine-integration`
- **Verify:** output validated against the **real machine dialect/controller**, not
  assumed; safe failure modes (a wrong toolpath can break material or a machine);
  offline operation; exact units and coordinate conventions.
- **Common failure:** assuming a G-code/firmware dialect; silent unit mismatch;
  optimistic happy-path with no machine in the loop.
- **Default shape:** a converter/driver with a hardware-in-the-loop or recorded-trace
  test; UNKNOWNs about the device flagged loudly.

### `data-extraction`
- **Verify:** every extracted value is **FACT with a trace**, missing logic is
  UNKNOWN (`03 §1`); idempotent re-runs; the source is never mutated.
- **Common failure:** "completing" gaps from a pattern; inventing a formula that
  looked plausible; losing provenance of where a value came from.
- **Default shape:** read-only source access, a classified output (FACT / INFERENCE /
  UNKNOWN), and a reconciliation report.

### `web-app`
- **Verify:** auth/authorization gates; DB migrations (Alembic) up/down; audit of who
  did what; `/health`; PT/EN; data stays on-prem (`02`).
- **Common failure:** missing authz on a route; un-migratable schema change;
  acting-user context not propagated to audit.
- **Default shape:** the full `api/` layout from `06`, React/Vite front end, Alembic
  migrations, pytest covering routes + services.

## Adding a new type

If a real project doesn't fit these four, that's a signal — add a fifth type here and
a matching profile, then bump `VERSION`. Don't force-fit; a wrong profile seeds the
wrong checklist.
