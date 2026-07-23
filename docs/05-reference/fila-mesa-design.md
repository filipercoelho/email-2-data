# Fila «Mesa com Foco» — design reference

Decision: [ADR-033](../03-decisions/adr-033-fila-mesa-com-foco.md) · phases:
[fila-mesa-phases.md](../04-implementation/fila-mesa-phases.md) · principles:
[cockpit-design.md](cockpit-design.md). Visual reference: the interactive mockup artifact
(claude.ai/code/artifact/85d1f170-3b51-4813-aba4-3a21efedb3d1, fictional data) and the owner's
«Fila Cockpit» design study (claude.ai/design project `4986dfa6…`, inspiration for the € em jogo
KPI, dossier signal tiles, counterparty history card, momentum grouping, conversation timeline).

Every UI element below names the **signal it consumes** and where that signal already lives.
A rule used throughout: **absent means absent** — a chip whose signal was not extracted does not
render (never an em-dash placeholder), and every LLM-derived value renders **dashed** until a human
commits it (the cockpit trust grammar).

## 1 · Screen regions

```
┌ header ──────────────────────────────────────────────────────────────────┐
│ email-2-data  Fila·112  Para ti·15  Projetos  Contrapartes  Capturas     │
│                        correio há 4 min   ⌘K   Sincronizar   densidade   │
├ command strip ───────────────────────────────────────────────────────────┤
│ 41 a responder · 13 a cobrar   [rever 1]     Hoje│Clientes│Fornec.│Leads │
│                                              [1‑5 vistas]   /procurar    │
├──────────────┬───────────────────────────────────────────────────────────┤
│ vistas rail  │  list pane (~40%)        │  dossier pane (~60%)           │
│ Em risco 54  │  ▾ PRECISAM DE RESPOSTA 8│  [Cliente] devemos há 13 d     │
│ € em jogo 9  │    row · row · row …     │  Subject · verb bar (E R H A P)│
│ Prazos 7     │  ▸ À ESPERA DELES 58     │  tiles · IA · história · draft │
│ Cobranças 13 │  ▸ INTERNOS —            │  timeline ⇵ + mensagens        │
│ Tratados     │                          │                                │
├ footer ──────┴───────────────────────────────────────────────────────────┤
│ J/K mover · Enter abrir · E tratado · R responder · H adiar · A dono ·   │
│ X selecionar · Z desfazer · 1‑5 vistas · F tratar agora · ⌘K comandos    │
└──────────────────────────────────────────────────────────────────────────┘
```

- Full width (the 1000 px `.wrap` cap is lifted for this lens). List pane min 560 px, own scroll;
  dossier own scroll. Below ~1100 px viewport width the panes **stack** (list above dossier).
- The rail is compact (~200 px) and holds the five vistas + read-only facet counts; on narrow
  viewports it collapses into the command strip as chips.

## 2 · Command strip

| Element | Signal | Behaviour |
| --- | --- | --- |
| **Honest headline** «41 a responder · 13 a cobrar» | `clock.state==WE_OWE ∧ band∈{red,amber}` ; chase = `AWAITING ∧ age≥72 h` | Counts only what demands the user. Replaces the inflated «88 em risco» (which counted every red+amber row including the passive AWAITING pile). Each half is a click-filter. |
| **«rever N» chip** | interactions with `priority==NEEDS_REVIEW` | Quiet amber chip; NEEDS_REVIEW finally gets a surface. Click → Para ti. Hidden at 0. |
| **Freshness stamp** «correio há N min» | `_sync.last_ts` (same source as `/api/para-ti.synced_at`) | In the header; amber + louder once age > 45 min (ingestion stalled while the poll works — ADR-023's failure case). «Sincronizar» refreshes **in place**, never `location.reload()`. |
| **Counterparty tabs** | `counterparty` per row (post-reclassification overlay) | See §3. |
| **Search** | subject+contact+counterparty+purpose haystack (existing filter) | `/` focuses it (lens override; ⌘K keeps the palette). URL-synced as today. |

## 3 · Counterparty fronts (tabs)

Tabs are **client-side subsets of the one queue in the one order** — no second data structure.
`Hoje` is the landing tab so the riskiest item is mounted at load (principle 1).

| Tab | Predicate | Grouping inside | Framing / verb emphasis |
| --- | --- | --- | --- |
| **Hoje** | all active rows | Precisam de resposta (open) · A cobrar (open) · À espera deles (collapsed) · Internos (collapsed) | The cross-front risk view; default. |
| **Clientes** | `counterparty==CLIENT` | obligation (ADR-029) | Job context: project chip + readiness, gaps; R → quote/ask composers. |
| **Fornecedores** | `counterparty==SUPPLIER` | **A cobrar first** (chase ≥72 h), then Precisam de resposta, then À espera (collapsed) | Chase-first; R → follow-up template; the passive pile becomes an actionable chase list. |
| **Leads** | `counterparty==LEAD` | flat (rare) | Respond-same-day framing; «novo contacto» badge; dashed propor-projeto action; tab badge pulses on a new lead. Empty state says an empty tab is a good day. |

Honesty rules: counterparty comes from the body (ADR-003) after the human reclassification overlay;
a low-confidence counterparty is a Para ti decision (ADR-006), never a guessed tab placement; a tab
count is the count of the *same rows the list shows* (no separate arithmetic).

## 4 · Vistas rail (fixed, keys 1–5)

| # | Vista | Definition (over active rows, current tab) | Order |
| --- | --- | --- | --- |
| 1 | **Em risco** (default) | the grouped queue as in §3 | risk tuple |
| 2 | **€ em jogo** | `WE_OWE ∧ entities.money present` | money desc (dashed values; vista is explicitly “AI-estimated”) |
| 3 | **Prazos** | `entities.deadline present` | days-left asc; overdue first |
| 4 | **Cobranças** | `AWAITING ∧ age ≥ 72 h` (`cockpit._AWAITING_CHASE_H`) | age desc |
| 5 | **Tratados** | the ADR-028 ledger (`?include=resolved`) | recency |

Below the vistas, **read-only facet counts** orient before any click (tipo top-4, sem dono, com
rascunho ✍, com anexo) — each is a one-click filter chip (the same `filters` keys that exist today,
finally visible). **No view builder** — five presets, period.

## 5 · Row anatomy (single line, ~34 px)

```
│rail│ clock │ name            ↻3 │ scan line………………… │ €4 900? ⚑3d ✍ 📎PDF novo │
```

| Fragment | Signal | Rule |
| --- | --- | --- |
| 3 px color rail | counterparty | CLIENT/SUPPLIER/LEAD hue; redundant with tab (kept for Hoje). |
| Clock chip | `clock` | Filled dot = devemos; hollow = à espera (ADR-029). Color = band. Text counts **up past breach** for chases («+6 d»). The sort key and the visual state are one mechanism. |
| Name | `display_name` (cluster override → contact name → raw address) | Bold, never the raw address when a human name exists (ADR-028 v8 names). |
| ↻N | `crm.related()` count | Only when >0; popover with jump links (prevents double-answering one client from two threads). |
| Scan line | `entities.product_or_service` → `trust.reason` → `subject` | The readable "what this is"; kills «RE: FW:» archaeology. The fallback chain is deterministic and stated in the dossier. |
| «€ 4 900?» | `entities.money` | **Dashed** chip; tiebreak within band only; absent when unextracted. |
| «⚑ 3 d» | `entities.deadline` | Amber; red when past due. |
| ✍ | `can_draft` | The row says a reply is one keystroke away — before expansion. |
| 📎PDF/IMG | attachment content-type | Type tells whether quoting means opening a file (95/112 rows have attachments). |
| «novo» | `contacts.first_seen` recent | Flags the rarest, highest-value event (a new lead/contact). |
| Trust dot | `trust.committed` | 2 px dot: dashed ring = proposed, solid = committed. The full chip appears only on the **focused** row (repeated-label rule). Per-row «sem dono» is deleted; the rail facet counts it once. |

Focused row grows to two lines (subject, sender, purpose chip, trust chip + Porquê, owner chip).
Per-row ✓/@ icon buttons are deleted — verbs live on keys and the dossier bar.

## 6 · Dossier pane

Auto-mounts the top-risk row at load; re-renders on focus change; next-3 rows prefetched (P2).
Top→bottom:

1. **Identity strip** — counterparty pill · clock label («devemos resposta há 13 d») · purpose ·
   owner chip. Reclassify moves here (badge clicks in the list now *filter*).
2. **Subject** (h1) — full, untruncated.
3. **Verb bar** — `[✍ Responder R] [✓ Tratado E] [Adiar H] [@ Dono A] [▦ Projeto P]`, keys printed
   on the buttons (the palette-as-trainer pattern). Ignorar is deliberately *not* here — it stays in
   the palette with a forced reason (making IGNORE harder than tratado is a feature).
4. **Signal tiles** (2×4→2×2): **Em jogo** (`entities.money`, dashed note "valor estimado (IA)") ·
   **Prazo** (`entities.deadline` + human note) · **Resposta** (clock age vs the normal band) ·
   **Ritmo** (momentum: Ativo / A abrandar / Parado — deterministic from message-date deltas, §8).
5. **Análise IA** (dashed border): `Pedem: <entities.action_requested>` + `trust.reason`, with the
   confidence chip («Gemini · 91%») and «Porquê?». The always-visible version of what was a hidden
   click.
6. **Counterparty history card**: initials avatar · display name · contact · N conversas ·
   € em aberto (cluster rollups `we_owe_count`/`response_risk` — already computed server-side) ·
   «↻ N relacionadas» links · project line with **readiness ring + «faltam N campos» /
   «pronto a orçamentar»** (`projects.coverage/estimable`, denormalized v3 columns) · spec-conflict
   alert when `merge_job_fields` reports a contradiction.
7. **Staged draft** (dashed card, only when a draft exists/was requested): template name, body,
   `[Copiar C]`, footer «rascunho — revê antes de enviar · a app nunca envia».
8. **Conversa** — vertical timeline + messages (§7).

The message renderer is the **existing** `msgHTML`/`_threadCache` kit — the dossier is a new *mount
point*, not a new renderer (the named drift risk).

## 7 · Conversation timeline (vertical)

A slim vertical rail to the left of the message list; adopted from the owner's design study and
re-grounded in the deterministic clock:

- One **marker per message**, top = newest (matching the newest-first message order). Direction is
  the encoding: filled accent = recebido, filled teal = enviado, grey = interno, dashed ring =
  `via reencaminhamento` (embedded). Click scrolls to and flashes that message.
- **Silences are the information.** Gaps between consecutive messages ≥ 24 h render as a labeled
  spacer on the rail («6 dias sem resposta») instead of being compressed — the rhythm of the thread
  is visible without reading dates. Spacer height is banded (1 d / 3 d / 7 d+), not linear, so one
  long silence cannot push the thread off-screen.
- The rail **terminates at «agora»**: the segment between the newest message and now renders in the
  clock's band color with the clock label — the *current open silence is the response debt*, drawn
  as the growing tail of the conversation. For AWAITING threads the tail is hollow-styled (their
  move), for WE_OWE filled (ours).
- Pure client-side render over the already-fetched `messages` array + `clock` — no new endpoint.

## 8 · Momentum («Ritmo») — deterministic definition

Over the thread's message dates (any direction), with `now`:
`gap = now − last_date`; `cadence` = median gap of the last 3 message-pairs (or the single pair).

- **Ativo** — `gap ≤ max(48 h, 1.5 × cadence)`
- **A abrandar** — `gap ≤ 3 × cadence` (and > Ativo bound)
- **Parado** — otherwise, or single-message threads older than 72 h.

No LLM input; computed in `cockpit.py` beside the clock and shipped per row (P2). Used by the Ritmo
tile and as the grouping for «À espera deles» inside the Fornecedores tab.

## 9 · Keyboard map (complete)

| Key | Action | Notes |
| --- | --- | --- |
| `J` / `K` | next / previous row | skips group headers; scrolls `nearest` |
| `Shift+J` / `Shift+K` | next / previous **group header** | P0 |
| `Enter` / `O` | focus dossier on row (opens thread) | in stacked layout: scrolls to dossier |
| `E` | tratado (reabrir in Tratados) | **act-and-advance** |
| `R` | responder — contextual composer (P3) | purpose×state mapping, §10 |
| `H` | adiar — wake on date OR new inbound (P3) | menu: amanhã · 2ª feira · +7 d · data |
| `A` | dono picker | act-and-advance on assign |
| `P` | abrir/criar projeto | existing behaviour |
| `C` | copiar rascunho | when staged |
| `X` / `Shift+X` | select row / select group | bulk verbs: E/H/A only — **no IGNORE** |
| `Z` | undo (global stack) | ADR-028 |
| `1`–`5` | vistas | §4 |
| `T` / `Shift+T` | next / previous counterparty tab | |
| `F` | «Tratar agora» overlay | iterates the *same* filtered order, one-at-a-time, progress «3 de 41»; skip is free |
| `/` | focus search | lens override; ⌘K keeps the palette |
| `⌘K` | palette (every action, key printed inline) | trainer |
| `Esc` | close overlay → clear filters | existing cascade |

## 10 · Contextual R mapping (P3)

| Row condition | Composer (ADR-031 kinds) | Pre-fill |
| --- | --- | --- |
| `can_draft` (JobSpec exists) | existing `/api/reply` honest-conditional ask draft | askables pre-ticked |
| `purpose==ESTIMATE_REQUEST_FROM_CLIENT`, project attached, gaps | `ask` | readiness questions |
| project `estimable` | `quote` | confirmed facts |
| `AWAITING ∧ chase` (Cobranças) | `follow-up` | thread context |
| `purpose==OUTBOUND_INVOICE` | `payment` | |
| otherwise | `follow-up` generic | |

The composers stay deterministic + checked (ADR-013/-027/-031); the Fila only *routes* to them. The
draft always lands in the dossier's staged slot — nothing is ever sent.

## 11 · Data contract additions (`/api/fila` rows)

Joined server-side in `_fila_rows` (P1–P2), each absent when unknown:

| Field | Source | Phase |
| --- | --- | --- |
| `display_name` | clusters (`counterparty_names` override → derived) via contact→cluster map | P1 |
| `entities` `{money, deadline, product_or_service, action_requested}` | `crm.interactions.entities` (dominant verdict) | P2 |
| `related_count` | `crm.related(dominant_mid)` sizes | P2 |
| `novo` | `contacts.first_seen` within 14 d | P2 |
| `chase` | `AWAITING ∧ age ≥ 72 h` | P2 |
| `momentum` | §8 | P2 |
| `project.coverage` / `project.estimable` | `projects` denormalized columns | P2 |
| top-level `synced_at` / `syncing` / `nav_counts` / `needs_review` | `_sync` state + interactions | P2 |
| `snoozed_until` | workspace v9 `thread_snooze` | P3 |

## 12 · PT-PT strings (new)

«a responder» · «a cobrar» · «rever N» · «correio há N min» · «Hoje» · «Clientes» · «Fornecedores» ·
«Leads» · «Em risco» · «€ em jogo» · «Prazos» · «Cobranças» · «Tratados» · «A cobrar — sem resposta
há 72 h+» · «faltam N campos» · «pronto a orçamentar» · «valor estimado (IA)» · «novo contacto» ·
«N relacionadas» · «sem resposta há N dias» (timeline gap) · «agora» · «Adiar» · «acorda antes se
responderem» · «Tratar agora» · «N de M» · «selecionadas: N» · «rascunho — revê antes de enviar · a
app nunca envia» · zero states: «Tudo tratado · nada em risco» / «Sem leads novos — bom sinal».

## 13 · Palette (canonical tokens — shipped 2026-07-23)

The design-proposal palette is the app's token system (`cockpit_ui.py` `:root`, synced to
`report.py`; pinned by `test_mesa_palette_tokens_are_canonical`):

| Role | Token | Value |
| --- | --- | --- |
| Ground / surface / lines | `--bg` `#F1F3F6` · `--card` `#fff` · `--bd` `#DCE2E9` · `--bd2` `#EAEEF2` |
| Ink | `--tx` `#182027` · `--mut` `#46525E` · `--mut2` `#7C8894` |
| Accent (steel blue) | `--ac` `#2C5E80` · `--ac-soft` `#E3EDF4` · `--ac-line` `#BDD3E2` |
| Bands | `--red` `#B3392E` · `--amber` `#96660F` · `--green` `#2E7D4F` (each with `-bg`/`-line`) |
| **Cliente** | `--cli` `#0A8F72` / `--cli-bg` `#DFF1EC` |
| **Fornecedor** | `--forn` `#3B5FC0` / `--forn-bg` `#E5EAF9` |
| **Lead** | `--lead` `#A16207` / `--lead-bg` `#F6ECD7` |

The counterparty trio is **CVD-validated** (dataviz six-checks: worst adjacent pair ΔE 19.4 deutan /
21.0 normal-vision, chroma ≥ 0.1, all ≥ 3:1 on white). **Lead-purple is rejected** — ΔE 2.9 protan
against fornecedor blue makes them indistinguishable for protanopia; amber also carries the
new/hot semantics («novo» shares the family). `--purple` survives only for non-counterparty
identities (Para ti gates, Prazos vista dot). Dark mode is future work; the artifact carries a
validated dark variant (`#219980`/`#6E85DE`/`#BA8628`) when it lands.

## 14 · Non-goals

No view builder · no manual reorder · no bulk IGNORE · no sender screener · no chat interface ·
no second list surface · no digest replacing the live queue · no fake placeholder values — a missing
extraction renders as absence, and «€ em jogo» is labeled AI-estimated wherever it appears.
