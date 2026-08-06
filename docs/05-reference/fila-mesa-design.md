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

```text
┌ header ──────────────────────────────────────────────────────────────────┐
│ email-2-data  Fila·112  Para ti·15  Projetos  Contrapartes  Capturas     │
│                        correio há 4 min   ⌘K   Sincronizar   densidade   │
├ command strip ───────────────────────────────────────────────────────────┤
│ 41 a responder · 13 a aguardar [rever 1]     Hoje│Clientes│Fornec.│Leads │
│                                              [1‑5 vistas]   /procurar    │
├──────────────┬───────────────────────────────────────────────────────────┤
│ vistas rail  │  list pane (~40%)        │  dossier pane (~60%)           │
│ Em risco 54  │  ▾ PRECISAM DE RESPOSTA 8│  [Cliente] devemos há 13 d     │
│ € em jogo 9  │    row · row · row …     │  Subject · verb bar (E R H A P)│
│ Prazos 7     │  ▸ À ESPERA DELES 58     │  IA · registo · história·draft │
│ A aguardar 13│  ▸ INTERNOS —            │  timeline ⇵ + mensagens        │
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
| **Honest headline** «41 a responder · 13 a aguardar» | `clock.state==WE_OWE ∧ band∈{red,amber}` ; chase = `AWAITING ∧ age≥72 h` | Counts only what demands the user. Replaces the inflated «88 em risco» (which counted every red+amber row including the passive AWAITING pile). Each half is a click-filter. |
| **«rever N» chip** | interactions with `priority==NEEDS_REVIEW` | Quiet amber chip; NEEDS_REVIEW finally gets a surface. Click → Para ti. Hidden at 0. |
| **Freshness stamp** «correio há N min» | `_sync.last_ts` (same source as `/api/para-ti.synced_at`) | In the header; amber + louder once age > 45 min (ingestion stalled while the poll works — ADR-023's failure case). «Sincronizar» refreshes **in place**, never `location.reload()`. |
| **Counterparty tabs** | `counterparty` per row (post-reclassification overlay) | See §3. |
| **Search** | subject+contact+counterparty+purpose haystack (existing filter) | `/` focuses it (lens override; ⌘K keeps the palette). URL-synced as today. |

## 3 · Counterparty fronts (tabs)

Tabs are **client-side subsets of the one queue in the one order** — no second data structure.
`Hoje` is the landing tab so the riskiest item is mounted at load (principle 1).

| Tab | Predicate | Grouping inside | Framing / verb emphasis |
| --- | --- | --- | --- |
| **Hoje** | all active rows | Precisam de resposta · A pagar · A cobrar (billing) · A aguardar (open) · À espera deles · Informações (collapsed) · Internos (collapsed) | The cross-front risk view; default. |
| **Clientes** | `counterparty==CLIENT` | obligation (ADR-029) | Job context: project chip + readiness, gaps; R → quote/ask composers. |
| **Fornecedores** | `counterparty==SUPPLIER` | **A aguardar first** (chase ≥72 h), then **A pagar** (inbound bills), then Precisam de resposta, then À espera (collapsed) | Chase-first; R → follow-up template; the passive pile becomes an actionable follow-up list. |

> **ADR-036 Stage 0 relabel.** The chase pile (`AWAITING ∧ age ≥ 72 h`) was mislabeled **«A cobrar»**
> (to collect payment) when it is really *our proposal awaiting the client's decision* — renamed **«A aguardar»**
> (`G_CHASE`). A **genuine «A cobrar»** group (`G_BILL`) is split off for our unpaid `OUTBOUND_INVOICE` only, so
> "cobrar" now always means real money owed to us. The group id is derived in `cockpit.fila_group()` (one source
> of truth in Python); the JS `semGroup` just renders `row.group`. Stage 1 adds **«A pagar»** (inbound bills,
> `G_PAY`). **Stage 2** makes the group the folded **obligation** (`cockpit.derive_obligation` from the new
> `speech_act` axis, ADR-036, amended by **ADR-051** — our own outbound reply discharges an owed reply, never an
> owed payment): `OWE_REPLY`→«Precisam de resposta», `OWE_PAYMENT`→«A pagar», `COLLECT`→«A cobrar»,
> `AWAIT_THEM`→«A aguardar»/«À espera deles», `FYI`→**«Informações»** (`G_INFO`, quiet, collapsed); `ACK`/`CLOSE`
> self-close. The clock only colours+sorts. Before a user-run `triage --full`, `_legacy_obligation` reproduces the
> deterministic Stage 0/1 routing.
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
| 4 | **A aguardar** | `AWAITING ∧ age ≥ 72 h` (`cockpit._AWAITING_CHASE_H`) — spans `G_CHASE` + genuine billing `G_BILL` | age desc |
| 5 | **Tratados** | the ADR-028 ledger (`?include=resolved`) | recency |

Below the vistas, **read-only facet counts** orient before any click (tipo top-4, sem dono, com
rascunho ✍, com anexo) — each is a one-click filter chip (the same `filters` keys that exist today,
finally visible). **No view builder** — five presets, period.

## 5 · Row anatomy (single line, ~34 px)

```text
│rail│ clock │ name            ↻3 │ scan line………………… │ €4 900? ⚑3d ✍ 📎PDF novo │
```

| Fragment | Signal | Rule |
| --- | --- | --- |
| 3 px color rail | counterparty | CLIENT/SUPPLIER/LEAD hue; redundant with tab (kept for Hoje). |
| Clock chip | `clock` | Filled dot = devemos; hollow = à espera (ADR-029). Color = band. Text counts **up past breach** for chases («+6 d»). The sort key and the visual state are one mechanism. |
| Name | `display_name` (cluster override → contact name → raw address) | Bold, never the raw address when a human name exists (ADR-028 v8 names). |
| ↻N | `crm.related()` count | Only when >0. The dossier expands it into a `.drel` block of jump-links (`related` list in the payload, up to 8 `{thread_root, subject, reason, momentum}`); clicking focuses that thread in place or navigates to `?thread=<root>` — prevents double-answering one client from two threads. Up to 3 slots are reserved for a shared-entity match before same-contact volume backfills the rest (ADR-037: a prolific contact's routine traffic was crowding out the rarer, more specific match). Each item shows why it's linked (`reason`: «mesmo contacto» or the shared field — nome/e-mail/NIF/IBAN/produto) and a momentum dot for the OTHER thread, so a long-stalled same-contact hit reads differently from an active one. `deadline` is not a link key (date-only coincidence isn't a relation). «mesmo contacto» is always the row's own resolved EXTERNAL contact (never an internal @lindoservico.pt mailbox, even when the thread's dominant message was outbound — ADR-037). |
| Scan line | `entities.product_or_service` → `trust.reason` → `subject` | The readable "what this is"; kills «RE: FW:» archaeology. The fallback chain is deterministic and stated in the dossier. |
| «€ 4 900?» | `entities.money` | **Dashed** chip; tiebreak within band only; absent when unextracted. |
| «⚑ 3 d» | `entities.deadline` | Amber; red when past due. |
| ✍ | `can_draft` | The row says a reply is one keystroke away — before expansion. |
| 📎CAD +N | `attach_kinds` (crm.db v4) | Category from each attachment's **filename extension** (`cad>vetor>pdf>folha>img>doc>zip`, ranked by what a quote needs), unioned across the thread. «📎CAD +N» shows the top kind + a count of the rest; bare 📎 when `has_attachment` but no typed kinds (pre-v4 db). Tells whether quoting means opening a file. |
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
4. **«Registo do fio»** — the thread ledger (`.dledger`, ADR-033 P4a). *This replaced the original
   2×4 signal tiles*, which spent prime space printing «— · sem valor associado» and had nowhere to
   accumulate what the thread had taught us. **Ritmo** survives inline on the clock line; the rest is:
   - **Facts**, one row per key in `FK` order (Valor · Prazo · Produto/serviço · Pedido · Nome ·
     NIF · IBAN), each the **latest** mention across *all* the thread's messages plus its source
     date and «+N menções». `Object.keys(FK)` drives render order, so reordering `FK` moves the
     screen. Checksum FACTs (NIF/IBAN, ADR-007) render **solid**; everything LLM-extracted renders
     **dashed with a «?»** — the load-bearing proposed-vs-confirmed convention.
   - **Human decisions** — reclassifications, owner, tratado, adiada, project.
   - Absence is **one quiet line** («sem factos extraídos deste fio»), never a grid of dashes.

   Values **wrap**; they are not truncated (owner request, 2026-08-05). Measured over the live
   corpus, `product_or_service` runs median 25 / p90 51 / **max 226** characters, so an ellipsis was
   hiding the job itself. Because grid rows are auto-sized, the wrap is clamped to **3 lines** — one
   long value would otherwise inflate the height of every other cell in its row — and a value past
   **60 chars** spans the full grid (`.lg-r.wide{grid-column:1/-1}`), where 3 lines actually hold it.
   The threshold is on **length, not key**, so a short «corte MDF» keeps its compact cell. Since the
   clamp *hides* text, the untruncated value is always in `title=`. One builder, `_lgRow`, emits
   every row and is executed (not grepped) by the suite.

   **Each value is a button onto its own evidence** (fila-evidence §Phase 3, decision D2). Click it
   and the sentence that produced it lights up in the message body below, scrolled into view; click
   again to put it out. **One** value is lit at a time and there is **one** highlight colour —
   deliberately, because colour in this pane is already fully committed (clock bands are urgency,
   the trio is counterparty, `--int` is a checksum FACT, dashed-vs-solid is proposed-vs-confirmed),
   so a per-field palette would make a green «Prazo» read as *on time*. Mechanics:
   - **Zero LLM, and never a server-computed offset.** `extract.py`'s `_AMOUNT`/`_NIF`/`_IBAN` are
     mirrored client-side (`evMatches`) over the string actually on screen, mod-11 check included.
     `extract_values` folds (NFKD → strip combining → casefold) *before* matching, so its outputs
     are not substrings of the body and folding changes string length on Portuguese text — an offset
     computed server-side drifts silently on exactly the mail this app handles.
   - **Format-locked keys match by normalised form**, which is why a body writing an IBAN in groups
     of four still matches the space-stripped stored value. Everything else falls back to a
     fold-tolerant literal search — rejected as a *primary* strategy (37% hit rate) but correct as a
     user-initiated secondary, where a miss costs nothing.
   - **Painted with the CSS Custom Highlight API** over live `Range`s (`::highlight(evid)`), never
     `<mark>`: `esc()` does not escape `'` and indexing escaped text drifts 4 chars per `&`, and a
     wrapper element would repoint the `nextElementSibling` toggles (§7).
   - **Evidence inside a collapsed «assinatura»/«mensagem citada» block opens it** — painting text
     nobody can see is indistinguishable from finding nothing.
   - **Absence is stated: «sem evidência visível».** 40% of extracted values are in the email text
     in no form at all, so this is the *common* answer, not an edge case. Never a nearest match.
   - The picked key is **module state, re-applied by `renderDossier()`** — not a field on the row,
     because `refresh()` replaces every row object every 15 s and hand-copies a fixed field list.
     The click itself calls `applyEvidence`, **not** `renderDossier`, so it cannot throw away a
     block the reader had opened.

   Measured over the live corpus by driving a real browser: **43 of 83 ledger clicks (52%) paint
   evidence; the other 40 say «sem evidência visível»; none is silent.**

   **The located sentence (fila-evidence §Phase 4, ADR-054) is a strict FALLBACK to all of the
   above.** When the value itself is nowhere on screen — the common case, and structurally
   unreachable for `Prazo` (0% of ledger rows paint deterministically) and `Pedido` (20%) — the
   highlight falls back to the sentence the locate pass stored for that value, found with the same
   fold-tolerant search plus whitespace collapse (`evLocateQuote`). The order is the design: the
   deterministic span is *more precise*, and on rows it already paints, the model's quote is a
   useless echo of the value 89% of the time. A row with neither still says «sem evidência visível».
   The stored quote is **the email's own text at the matched span**, never the string the model
   typed, and the server-side matcher (`locate.find_spans`) and the client painter are pinned to
   agree by a test that executes both.
5. **«Evolução da conversa»** (`.dnarr`, fila-evidence §Phase 5, ADR-054) — how this negotiation got
   here, so someone arriving now understands it without reading the fio. At most 6 one-sentence
   beats, each dated and each **clickable to the message it came from** (`data-nmid` → `data-tmid`,
   scrolled into view, no re-render), plus one «estado» line about whose move it is. Its **own**
   container, deliberately not a child of «Análise IA»: that block is conditional on
   `decided||tr.reason||en.action_requested` and a narrative inside it would vanish on exactly the
   threads where all three are empty. Solid border, not the dashed INFERENCE one — every beat cites
   a message id that was checked against the thread before it was stored, and a beat citing anything
   else is discarded. **Absent is silent**: only threads with ≥ 2 messages are ever narrated (157 of
   767), and a heading over nothing would be noise on the other 610.
6. **Análise IA** (dashed border): `Pedem: <entities.action_requested>` + `trust.reason`, with the
   confidence chip («Gemini · 91%») and «Porquê?». The always-visible version of what was a hidden
   click.
7. **Counterparty history card**: initials avatar · display name · contact · N conversas ·
   € em aberto (cluster rollups `we_owe_count`/`response_risk` — already computed server-side) ·
   «↻ N relacionadas» links · project line with **readiness ring + «faltam N campos» /
   «pronto a orçamentar»** (`projects.coverage/estimable`, denormalized v3 columns) · spec-conflict
   alert when `merge_job_fields` reports a contradiction.
8. **Staged draft** (dashed card, only when a draft exists/was requested): template name, body,
   `[Copiar]` + `[✉ Abrir no mail]`, footer «rascunho — revê antes de enviar · a app nunca envia».
   **«Abrir no mail»** hands `to` + `Re: <assunto>` + body to the OS default client via `mailto:`
   (which opens a composer and cannot send); «Copiar» stays as the fallback where no client is
   registered. The body arrives already closed with the reader's **own signature** — see
   [reply-signature.md](reply-signature.md) and
   [ADR-047](../03-decisions/adr-047-the-signature-belongs-to-the-person-not-the-playbook.md).
9. **Conversa** — vertical timeline + messages (§7).

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

**Each message shows three collapsible regions, in this order, and every one of them exists because
hiding text outright is the thing this app does not do:** «▸ assinatura» (`.tsig`) · «▸ mensagem
citada» (`.tquote`) · «ver original» (`.rawbody`). All three are wired by `msgWireQuoteToggles` and
all three find their target with `nextElementSibling`, so **nothing may ever be inserted between a
toggle and the block it reveals** — the failure is silent (a button that opens someone else's text),
which is why `tests/test_cockpit_ui.py` asserts the adjacency positionally after executing `msgHTML`.

«assinatura» is the newest (fila-evidence §Phase 2, 2026-08-05). `clean_email_body` used to *delete*
the closing salutation and everything after it; that block is where a sender's name, role and NIF
live, and its removal was invisible. The signature now arrives in its own field, `body_sig`, beside
an unchanged `body_clean` — see [module-map](../02-architecture/module-map.md) for why it is a
separate opt-in entry point and not a flag. Measured over the corpus: **546 of 1259 messages carry
one; 96% hold at least one non-closing line** (a name, role or NIF); median 45 chars, p90 804,
capped at 1500 on the wire. Reachable extracted values move **37% → 40%**, all of it `client_name`
(**35% → 48%, +68**). That gain exceeds `probe_region`'s 15-value `signature` bucket because the
block is collected from anywhere in the body, **including signatures inside quoted replies** — which
were doubly hidden before (deleted from `body_clean`, so expanding «mensagem citada» did not reveal
them either).

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
| `AWAITING ∧ chase` (A aguardar) | `follow-up` | thread context |
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
| `related[].reason` / `related[].momentum` | match field name or «contacto»; other thread's own `cockpit.momentum()` | ADR-037 |
| `novo` | `contacts.first_seen` within 14 d | P2 |
| `chase` | `AWAITING ∧ age ≥ 72 h` | P2 |
| `momentum` | §8 | P2 |
| `project.coverage` / `project.estimable` | `projects` denormalized columns | P2 |
| top-level `synced_at` / `syncing` / `nav_counts` / `needs_review` | `_sync` state + interactions | P2 |
| `snoozed_until` | workspace v9 `thread_snooze` | P3 |

## 12 · PT-PT strings (new)

«a responder» · «a aguardar» · «rever N» · «correio há N min» · «Hoje» · «Clientes» · «Fornecedores» ·
«Leads» · «Em risco» · «€ em jogo» · «Prazos» · «A aguardar» · «Tratados» · «A aguardar — sem resposta
há 72 h+» · «A cobrar» (billing: unpaid OUTBOUND_INVOICE) · «faltam N campos» · «pronto a orçamentar» · «valor estimado (IA)» · «novo contacto» ·
«N relacionadas» · «assinatura» (colapsada, §7) · «sem evidência visível» (§6, valor sem origem
localizável no texto) ·
«Evolução da conversa» (§6, a narrativa do fio) · «ver a mensagem de onde saiu» (title de cada passo) ·
«Registo do fio» · «sem factos extraídos deste fio» · «sem decisões humanas
registadas» · «+N menções» · «a carregar registo…» ·
«sem resposta há N dias» (timeline gap) · «agora» · «Adiar» · «acorda antes se
responderem» · «Tratar agora» · «N de M» · «selecionadas: N» · «rascunho — revê antes de enviar · a
app nunca envia» · zero states: «Tudo tratado · nada em risco» / «Sem leads novos — bom sinal».

## 13 · Palette (canonical tokens — shipped 2026-07-23)

The design-proposal palette is the app's token system (`cockpit_ui.py` `:root`, synced to
`report.py`; pinned by `test_mesa_palette_tokens_are_canonical`):

| Role | Tokens |
| --- | --- |
| Ground / surface / lines | `--bg` `#F1F3F6` · `--card` `#fff` · `--bd` `#DCE2E9` · `--bd2` `#EAEEF2` |
| Ink | `--tx` `#182027` · `--mut` `#46525E` · `--mut2` `#7C8894` |
| Accent (steel blue) | `--ac` `#2C5E80` · `--ac-soft` `#E3EDF4` · `--ac-line` `#BDD3E2` |
| Bands | `--red` `#B3392E` · `--amber` `#96660F` · `--green` `#2E7D4F` (each with `-bg`/`-line`) |
| **Cliente** | `--cli` `#0A8F72` / `--cli-bg` `#DFF1EC` |
| **Fornecedor** | `--forn` `#3B5FC0` / `--forn-bg` `#E5EAF9` |
| **Lead** | `--lead` `#A16207` / `--lead-bg` `#F6ECD7` |
| **Evidence highlight** | `--hl-bg` `#FFDA47` / `--hl-tx` `#1A1405` (dark: `#8A6A12` / `#FFF6DF`) |

The counterparty trio is **CVD-validated** (dataviz six-checks: worst adjacent pair ΔE 19.4 deutan /
21.0 normal-vision, chroma ≥ 0.1, all ≥ 3:1 on white). **Lead-purple is rejected** — ΔE 2.9 protan
against fornecedor blue makes them indistinguishable for protanopia; amber also carries the
new/hot semantics («novo» shares the family). `--purple` survives only for non-counterparty
identities (Para ti gates, Prazos vista dot). Dark mode is future work; the artifact carries a
validated dark variant (`#219980`/`#6E85DE`/`#BA8628`) when it lands.

**`--hl-bg`/`--hl-tx` is the one token in this palette that means nothing on its own** (fila-evidence
§Phase 3), and that is the point: every other hue is committed — bands are urgency, the trio is
counterparty, `--int` is a checksum FACT, `--ac` is «selected» — so a highlight reusing any of them
would read as a *claim about* the text it lands on. It appears only as a background inside message
body text, only while a ledger value is picked, and so never sits beside the counterparty trio the
CVD validation constrains. Amber-yellow is the find-a-match convention and carries no meaning here.

## 14 · Non-goals

No view builder · no manual reorder · no bulk IGNORE · no sender screener · no chat interface ·
no second list surface · no digest replacing the live queue · no fake placeholder values — a missing
extraction renders as absence, and «€ em jogo» is labeled AI-estimated wherever it appears.
