# Architecture — module map & data flow

| Field | Value |
| --- | --- |
| Type | Architecture |
| Status | Active |
| Last reviewed | 2026-06-14 |

How the pipeline is structured and why. The deeper engineering rationale is in
[approach.md](approach.md) (right-sized v1) and
[offline-extraction-layer.md](offline-extraction-layer.md) (the red-teamed offline tier); the
invariants are in the [decisions registry](../03-decisions/index.md).

## Data flow

```text
IMAP (read-only)                                  ADR-002
   │  fetch.py        EXAMINE + BODY.PEEK, UID-watermark incremental   ADR-009
   ▼
corpus/*.eml
   │  headers.py      header bytes → text (RFC 2047 + raw 8-bit)          ADR-043
   │  envelope.py     raw MIME/charset → normalized fields
   │  attachments.py  parts → banded, hash-deduped file list            ADR-046
   ▼
normalized message
   │  signals.py      Tier-0 header facts: direction, bulk, is_forward  ADR-004
   │  extract.py      Tier-0 values: nif/iban (FACT) + amount/date (candidate)  ADR-007
   │  store.py        gazetteer hint (prior, not verdict)               ADR-005
   ▼
cascade.py  ── Tier 0: bulk + no hint → IGNORE (free) ──► out/results.jsonl
   │         └ Tier 1: classifier.py → Gemini via llm.py ─┘   ADR-001, ADR-006, ADR-012
   ▼
out/results.jsonl  (TriageResult, append-only, decided_by stamped)      ADR-008
   │  crm.py          interactions + contacts rollup (no LLM)
   ▼
out/crm.db
   │  jobspec.py      JobSpec (14 vars + Gate-1 readiness)
   │  specdraft.py    Phase-B tiered spec draft (LEAD/PO only)
   │  replydraft.py   Phase-C clarifying reply (never sends)
   │  signature.py    per-person closing appended to a reply draft (deterministic)  ADR-047
   │  clientdraft.py  Phase-C client-email composer (deterministic, 8 purposes, PT/EN/FR/ES)  ADR-013/-031/-032
   │  translate.py    Phase-C translate-received-email-to-English reading aid (display-only)  ADR-032
   ▼
project.py  cross-thread Projects → one canonical spec (workspace.db, precious)  ADR-010
   │  export.py       shell-only offload → JSON | materials-costing API  ADR-011
   ▼
webapp.py   FastAPI workspace UI on 127.0.0.1:8042 (live) + static report.html
```

## Module responsibilities

| Module | Tier | Responsibility |
| --- | --- | --- |
| `fetch.py` | I/O | read-only IMAP → `corpus/*.eml`; per-mailbox UID watermark |
| `sync.py` | glue | UID-cursor store (`out/sync.db`) + `run_sync` (fetch-new → triage-new) |
| `headers.py` | parse | header value → text: `parse_message` (the parser that keeps 8-bit bytes), RFC 2047 encoded-words, raw 8-bit repair. No surrogate leaves it — ADR-043 |
| `envelope.py` | parse | raw `.eml` → normalized fields (robust MIME/charset) |
| `attachments.py` | present | a thread's parts → one **deduped, banded** file list: FICHEIROS / IMAGENS / ASSINATURAS, each an INFERENCE carrying its own `band_evidence`. Dedup is sha256 of the decoded bytes, never the filename. Walks parts under the exact predicate `attachment_part` re-walks, so banding is additive and no 📎 link is repointed — ADR-046 |
| `signals.py` | Tier-0 | header facts: direction, bulk/automated, looks-forwarded |
| `extract.py` | Tier-0 | deterministic values: nif/iban authoritative + amount/date/doc candidates |
| `store.py` | knowledge | gazetteer: email-or-domain → counterparty hint (SQLite, hand-curated) |
| `cascade.py` | router | Tier-0 offline IGNORE → Tier-1 `classifier.py` with facts+hint attached |
| `classifier.py` | Tier-1 | the LLM triage call (via `llm.py`) |
| `llm.py` | plumbing | provider dispatch (Gemini/Anthropic) + retry-on-empty, shared by all LLM stages |
| `crm.py` | rollup | interactions (event log) + contacts (person rollup) from headers+verdicts |
| `jobspec.py` | Phase A | JobSpec (14 vars + custom fields + provenance + confirmed) + Gate-1 readiness + `askables` (selectable client-email prompts) |
| `specdraft.py` | Phase B | tiered LLM spec draft for LEAD/PO/estimate only |
| `replydraft.py` | Phase C | clarifying reply grounded in confirmed-vs-missing fields (never sends) |
| `translate.py` | Phase C | on-demand translate-a-received-email-to-English reading aid (`POST /api/translate`, in-memory cache); display-only, never sends/stores — ADR-032 |
| `clientdraft.py` | Phase C | deterministic client-email composer: 8 purposes (ask · reject · quote · follow-up · approval · payment · deadline · ready), each splicing its input into an editable per-purpose template (no LLM, never sends) — ADR-013; optional checked polish extended with a verbatim number guard (`extract_values`/`missing_values`) — ADR-027/-031 ([reference](../05-reference/client-email-composer.md)) |
| `workspace.py` | write | human decisions (SQLite) overlaying job specs; survive re-runs — **and `people`**, the one assignable-identity namespace + its lifecycle (promote/deactivate/remove, the never-zero-admins invariant) — ADR-039/-041 |
| `scopes.py` | Phase A (multi-user) | which of *our* inboxes a message reached (`fetch`/`header` FACT, `participant` INFERENCE, else `sem-atribuicao`); `visible()` is consumed by `webapp._visible_roots` since ADR-045 (Phase D) — one choke point in `_fila_rows` plus route-level guards for bodies/relations/attachments/projects — ADR-038/-045 |
| `auth.py` | Phase B (multi-user) | credentials + sessions + invites in a separate `auth.db`; stdlib scrypt, sessions are ROWS (opaque token, SHA-256 at rest), no server secret — ADR-039 |
| `auth_page.py` | Phase B (multi-user) | the signed-out screens only (login · first-run setup · invite redemption · **password recovery**). Signed-in surfaces use the cockpit shell — ADR-039/-042 |
| `signature.py` | Phase C | the closing of a reply draft, as a property of the **signed-in person** rather than of `reply_playbook.md`: a closed placeholder vocabulary (`{nome}` `{cargo}` `{telefone}` `{email}`) resolved only from that person's own `people` row, an all-empty line dropped rather than left dangling, and **strip-then-append** so a model-written or template-carried sign-off is replaced instead of stacked. Deterministic — the model never touches the block. An empty render strips nothing. Applied AFTER the reply memo (which is keyed on the spec, so a signed cache entry would leak the previous reader's name) — ADR-047 |
| `mailer.py` | Phase B (multi-user) | **the only outbound mail path in the app** — one message (a password-reset link), one template, no caller-supplied body, recipients restricted to a `people` address. Stdlib `smtplib` over implicit TLS; a single `_send` seam so the suite never opens a socket. Imports no `imaplib` and issues no `APPEND`, pinned by a source-level test — ADR-042 sits *beside* ADR-002, not against it |
| `cockpit_ui.py` | UI kit | the shared shell every lens renders through: nav + identity control (`page(person=…)`, `person=None` is default-deny), the single `fetchJSON` network seam, `forbidden_page()`, `account_page()` — ADR-034/-040/-041 |
| `home_page.py` | UI (landing) | **Início at `/`** — four big buttons and no rows: the three counterparty fronts (→ `/fila?tab=…`) + Para ti, over exactly two highlights (the demand split · the longest-stalled thread *we owe*). Pure presentation over `cockpit.home_summary`; renders from embedded data with **no fetch on load** — ADR-044 |
| `project.py` | entity | cross-thread Projects: many threads → one canonical spec + lifecycle + provenance-rich append-only field/event timeline + contradiction detection (ADR-015) |
| `export.py` | offload | shell-only export to JSON (dry-run) or materials-costing API |
| `webapp.py` | UI | FastAPI workspace (localhost; never sends; copy/paste) |
| `schema.py` | contract | `TriageResult` + structured-output schemas + priority derivation |
| `cli.py` | entry | `fetch \| triage \| sync \| eval \| crm \| jobspec \| project \| serve` |

## Why two tiers

The split is the direct expression of
[ADR-001](../03-decisions/adr-001-compute-proportional-to-uncertainty-impact.md): certain,
low-stakes mail is decided offline for free (Tier 0); only uncertain, high-stakes mail reaches
the model (Tier 1). The anti-IGNORE guardrail
([ADR-006](../03-decisions/adr-006-two-tier-cascade-anti-ignore-guardrail.md)) ensures the
free path can never silently bin a client.

## Web surface

`webapp.py` serves a **live** workspace (Início / Fila / Para Ti / Projetos / Contrapartes) on
`127.0.0.1:8042`; it runs an incremental `sync` on boot and on **Sincronizar**. Project actions
hit the API, so they are inert in the static `out/report.html`. UI/UX spec:
[../05-reference/cockpit-design.md](../05-reference/cockpit-design.md).

**`/` is Início, not the Fila** ([ADR-044](../03-decisions/adr-044-inicio-the-landing-page-is-a-decision-not-a-cockpit.md)).
The queue lives at **`/fila`** and is otherwise unchanged — every query-string deep link still works,
because `syncURL()` rebuilds the address from `location.pathname`. `/` does not redirect: a redirect
would put the cockpit back on the landing URL, which is the thing the ADR removed.

Every view is **deep-linkable** ([ADR-014](../03-decisions/adr-014-restful-deep-linkable-cockpit-urls.md)):
a detail resource carries its id in the **path** — `/projetos/<pid>` and `/contrapartes/<key>`
(both 404 on an unknown id) — while list filters and inline view-state ride in the **query string**
— the Fila's `?counterparty=<CP>` filter and `?thread=<root>` expanded thread. So the address bar
always names what's on screen, and a view can be refreshed, shared, or bookmarked.

The **Contrapartes detail** (`/contrapartes/<key>`, served by `_contraparte_detail_data`) is a
*navigation hub*: an insight strip (messages / conversations / received-vs-sent / attachments / last
activity / response-debt) + a purpose breakdown, then every related record linked to where it lives —
open threads → the Fila (`/?thread=`), pending decisions → Para ti, projects → the workbench
(`/projetos/<pid>`), and a per-message timeline → the inbox report (`/inbox#tab=…&sel=…`). The same
payload is exposed at `GET /api/contrapartes/<key>` (`{cluster, stats, timeline, projects, fila_rows,
gates}`). The headline message count is the deduped timeline length, not the cluster's per-participant
`msg_count`.

The **Projetos workbench** (`/projetos/<pid>`) is a tab strip — *Especificação · Origem · Linha do
tempo · Registar* — so the page is never one long scroll; only the active panel renders, contradictions
sit in a banner above the tabs ([ADR-015](../03-decisions/adr-015-knowledge-capture-claim-ledger.md)).
**Registar** captures off-email knowledge deterministically (no LLM): channel + who + when + a
`note/decision/opinion/todo` text → `POST /api/projects/<pid>/event`, append-only. **Linha do tempo**
(`GET /api/projects/<pid>/timeline`) is the audit view — field edits and events newest-first by
`acquired_at`, with a *removed* state for clears. Custom fields (`POST …/custom-field`,
`custom:<label>`) render in Especificação as tier=context — never gating estimability. The capture tab
is deep-linkable as `?registar=nota` view-state (query, not path — it's inline state, not a resource).
All capture writes land in the precious `workspace.db` (`project_field_history`, provenance columns);
the projects **list** reads denormalized `coverage`/`estimable` off the project row instead of
recomputing `build_canonical` per row.
