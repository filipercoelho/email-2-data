# reference — the client-email composer (purposes + the verbatim-fact guard)

The Projetos **Email para o cliente** composer, its purpose selector, and the deterministic guard
that keeps the AI polish from ever changing a number. The code
(`src/email2data/clientdraft.py`, the `/api/projects/{pid}/draft[/polish]` routes in
`webapp.py`, the composer JS in `projetos_page.py`) is the source of truth; this mirrors it.
Governing decisions: **[ADR-013](../03-decisions/adr-013-client-email-composer-deterministic.md)**
(deterministic base), **[ADR-027](../03-decisions/adr-027-ai-polish-sits-on-top-of-the-deterministic-draft.md)**
(checked polish), **[ADR-031](../03-decisions/adr-031-client-email-purpose-selector-and-verbatim-fact-guard.md)**
(purposes + the number guard).

## The purpose registry (`clientdraft.PURPOSES`)

`ask` is the default and its behaviour is unchanged from ADR-013. `ask`/`follow_up` reuse the
`{perguntas}` token, so `build_draft` and its tests are untouched.

| id | pt-PT label | token | input kind |
| --- | --- | --- | --- |
| `ask` | Pedir detalhes em falta | `{perguntas}` | questions |
| `reject` | Recusar o trabalho | `{motivo}` | reason |
| `quote` | Aceitar / enviar custos (orçamento) | `{conteudo}` | text |
| `follow_up` | Seguimento / sem resposta | `{perguntas}` | questions |
| `approval` | Pedir aprovação de arte final / maquete | `{conteudo}` | text |
| `payment` | Pedir sinal / pagamento | `{conteudo}` | text |
| `deadline` | Atualização de prazo / atraso | `{conteudo}` | text |
| `ready` | Pronto para entrega / recolha | `{conteudo}` | text |

**Input kinds** (what the user supplies, how the deterministic base draft is built):
- **questions** — the `jobspec.askables` checklist (+ custom questions); numbered into `{perguntas}`.
- **reason** — a reason chosen from the editable list + an optional free note; spliced into `{motivo}`.
- **text** — a free-text box the user writes (costs, dates, terms); spliced verbatim into `{conteudo}`.

## Editable config (bind-mounted, live — no rebuild)

| File | Purpose | Fallback in code |
| --- | --- | --- |
| `config/client_email_template.md` | `ask` template (historical filename) | `DEFAULT_TEMPLATE` |
| `config/client_email_<id>_template.md` | one per other purpose (`reject`, `quote`, …) | `DEFAULT_TEMPLATES[id]` |
| `config/client_email_reject_reasons.md` | the reject reason menu (one per line after `---`) | `DEFAULT_REJECT_REASONS` (8) |
| `config/client_email_polish_playbook.md` | the polish system prompt | `DEFAULT_POLISH_PLAYBOOK` |

Each template file keeps the historical shape: an editor note, a `\n---\n` fence, then the body
carrying the purpose's token. A file that is missing, unreadable, or has lost its token falls back to
the built-in default — a botched edit never ships a token-less email.

## Routes

- `GET /api/projects/{pid}/draft` → `{to, subject, askables, body, purpose:"ask", purposes:[{id,label,input_kind}], reject_reasons:[…]}`. The `to`/`subject`/`askables`/`body` keys are the ADR-013 `ask` starting point, unchanged.
- `POST /api/projects/{pid}/draft` — body `{purpose, selected, custom, reason, reason_note, content}` (only the fields the purpose uses; no `purpose` ⇒ `ask`) → `{body, facts}`. Deterministic, no LLM. `facts` = the protected tokens (empty for question purposes).
- `POST /api/projects/{pid}/draft/polish` — same body + `tier` → `{body, base, tier, missing, n_questions, n_facts, used_thread, used_facts}`. Button-only. `missing = missing_questions(…) + missing_values(…)`; a non-empty `missing` means the UI blocks the AI version. 400 when there is nothing to say or protect (before any model call); 502/503 on model/credential failure (never a faked draft).

## The verbatim-fact guard (`extract_values` / `missing_values`)

The zero-hallucination rule applied to money: the polish may reword prose but must carry every
price/number/date the user typed through **verbatim**. `extract_values` pulls those tokens with
`clientdraft._FACT_RE`; `missing_values` reports any that did not survive (whitespace-normalised, but
otherwise exact). A missing value blocks the AI version exactly like a dropped question.

### What `_FACT_RE` matches (the contract)

| Shape | Examples |
| --- | --- |
| currency (symbol either side, incl. word) | `€160`, `160€`, `160 €`, `160 euros`, `1.250,00€` |
| percent | `50%` |
| number + unit (unit list, longest-first) | `2mm`, `2 m`, `10 dias`, `20 un`, `1 semana`, `3 meses` |
| ISO date | `2026-09-30` |
| slashed date | `30/09`, `30/09/2026` |

The number core is `\d+(?:[.,]\d+)*` — internal thousands/decimal separators only, so a trailing
comma/period is left out (`€160,` → `160€`) and `1.250,00` stays whole.

### Boundaries (deliberate — do not "fix" without superseding ADR-031)

- **False positives avoided:** list markers `1.`/`2.`, a bare thousands `1.250`, and phone runs
  `912 345 678` do **not** match — a token must carry a currency symbol, a unit, a percent, or a date
  shape. (Residual: `24/7` matches as a date-ish pair — harmless.)
- **False negatives (unguarded):** a bare total with no `€`/unit (`Total: 160`) and written-out
  numbers ("cento e sessenta euros"). The templates + playbook nudge the user to write `€`; a value
  not written with a symbol/unit is simply not protected.
- **Reformat = alteration:** `160€` → `160 €`, or `160,00 €`, is **reported** as missing. This
  strictness is the point — a rounding must never pass as a harmless reformat.

## Output language — PT/EN/FR/ES (ADR-032)

`clientdraft.LANGUAGES` = `pt` (default) / `en` / `fr` / `es`. The deterministic base draft is **always
Portuguese** — choosing a non-PT language does not translate the base (there is no deterministic
translator). Instead the **Melhorar com IA** pass becomes a *translate + polish* pass:
`build_polish_message`/`polish_draft` take `language=` and, when non-PT, prepend an `IDIOMA DE SAÍDA`
directive (translate faithfully; keep the VALORES A MANTER exactly — never translate/reformat a number).

`POST …/draft/polish` accepts `lang` (∈ `LANGUAGES`; invalid → 400) and returns `lang` + `translated`.
The guards split by what is checkable across languages:

- **Numbers/dates** — the `missing_values` guard is language-independent, so it **still runs verbatim**
  for every language (this is what protects a translated quote).
- **Questions** — the verbatim `missing_questions` check **cannot** survive translation, so it is
  **skipped** for non-PT; the response is `translated:true` and the UI shows a **"traduzido — rever"**
  banner, claiming no question coverage it did not verify. PT is byte-identical to before.

## Translation reading aid — `POST /api/translate` (ADR-032)

Separate feature, module `translate.py`, editable `config/translation_playbook.md`. Turns a received
email body into English on demand (a display aid — never sent, never stored). Body `{message_id, text}`
→ `{text, cached}`. In-memory `_translate_cache` keyed by `(message_id, sha256(text))`: a re-click costs
0 tokens, cold on restart, nothing persisted. Guards: empty text → 400, no `settings.json` → 503,
`LLMError` → 502. Egress is the same posture as the triage/spec passes (ADR-012/-020); the body and
translation are **never logged**. UI: one `traduzir (EN)` button in the shared `cockpit_ui.msgHTML`
renderer + a capture-phase delegated handler (`translateMsg`) → covers Fila, Projetos → Origem, Para ti.

## Invariants

- The base draft has **no LLM** for any purpose (ADR-013). The polish is opt-in, button-only (ADR-027).
- The system **never sends** — delivery is clipboard / `mailto:` (purpose-agnostic).
- A client is **never silently binned**: rejection is a human-reviewed draft with a chosen reason from
  an editable menu, never an automatic action or model output.
- Backward-compatible: a request with no `purpose` behaves as `ask` and no `lang` behaves as `pt`;
  response keys are preserved; the `ask`/PT polish path passes no `keep_values`/`language` and is
  byte-identical to before.

Tests: `tests/test_clientdraft.py` (ADR-031/-032 blocks), `tests/test_webapp.py` (ADR-031/-032 blocks),
`tests/test_translate.py`.
