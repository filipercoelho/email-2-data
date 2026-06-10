# email-2-data

| Field | Value |
|---|---|
| Type | Overview |
| Audience | All |
| Status | Active |
| Owner | Filipe Coelho |
| Project type | data-extraction |
| Last reviewed | 2026-06-10 |

## What this is

**email-2-data** turns the mail flowing through Lindo Serviço's inboxes into a
**trustworthy, prioritized decision queue**. It scans IMAP accounts **read-only**, then
classifies each message on four axes — **counterparty** (CLIENT / SUPPLIER / LEAD, from
*Lindo's* point of view), **purpose**, **direction** (inbound/outbound), and **priority** —
so the team instantly sees what is a paying client needing action, what is routine, and what
is noise. A free deterministic pre-filter handles the obvious; only the uncertain, high-stakes
mail is escalated to an LLM (Gemini on Vertex). Downstream it accretes cross-thread **Projects**
into estimable job specs and offloads a finished brief to the materials-costing estimator.

The governing principle is one rule: **spend compute in proportion to
`uncertainty × business impact`** (see [VISION.md](../VISION.md)).

## Scope

- **In scope:** read-only triage + classification; deterministic Tier-0 extraction
  (NIF/IBAN/amounts/dates); a CRM rollup; cross-thread Projects → estimable job spec; a
  local single-user workspace UI; export of a job *shell* to materials-costing.
- **Out of scope:** not an email client; **never sends mail** (strictly read-only); not
  autonomous (human stays in the loop); not locked to one provider or mailbox. Missing
  classifications are escalated or left UNKNOWN — **never invented** (see [PROFILE.md](../PROFILE.md)).

## Where it runs

- Local / single-user; the web UI binds **127.0.0.1:8042** (loopback only — never public,
  never port 8000). Hardware target: `../standards/01-hardware-baseline.md`; port claim:
  `../standards/02-network-lan.md` §C.
- Docker image carries no secrets or inbox data (`.env` + `config/` bind-mounted read-only).

## Inherited standards

This project follows the company standards in `../standards/` (frozen standards-v1.1.0).
The agent contract is [../CLAUDE.md](../CLAUDE.md) — read it first.

## Reading order

1. [VISION.md](../VISION.md) — north star, the governing principle, the 8 tenets.
2. This overview, then the [decisions registry](03-decisions/index.md) (the load-bearing "why").
3. [02-architecture](02-architecture/index.md) — module map and data flow.
4. [05-reference](05-reference/index.md) — the exact schemas, stores, and contracts.
