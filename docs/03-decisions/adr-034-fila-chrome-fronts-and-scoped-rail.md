# ADR-034 — Fila chrome: counterparty fronts as hero, one scope for every number, an iconic rail

- **Status:** Accepted (owner-directed 2026-07-23; P5a–P5d shipped; P5e gap-closing — typed 📎 +
  related jump-links + timeline/header time-agreement — shipped 2026-07-24)
- **Date:** 2026-07-23
- **Extends:** ADR-033 (Mesa) — this redesigns the Mesa's *framing* (nav · command strip · left rail),
  not its work surface (list + dossier).

## 1 · Context

After ADR-033 landed, the owner audited the three framing areas and found the chrome "speaks three
dialects at once". Verified against the live HTML:

- **Numbers without a shared scope.** The rail counted the whole queue (`const act=rows`) while the
  headline and tabs counted the active front — so the screen honestly showed «Em risco 58» next to
  «32 a responder» next to «Fila 121» next to «96 threads»: four numbers, four scopes, none labeled.
- **Badges carry inventory, not demand.** «Fila 121» is the total active count; it reads as 121 fires
  when what demands the operator is 58.
- **The command strip gives ten controls equal weight**, with an abstract «N a responder · N a
  cobrar · rever 1 · N threads» headline dominating — the exact thing the owner said was
  over-weighted — while the counterparty fronts (the operator's real first question, "which front is
  burning?") sat as small secondary pills.
- **The rail shows two numbers per row** (count + keyboard digit) and lists facets that don't
  discriminate («Sem dono 121/121», «Com anexo 101/121»).

## 2 · Decision

Reframe the three areas around one rule — **critical demand dominates, orientation guides, ambient
whispers** — and one invariant: **no number appears without its scope visible in the same gestalt
group.**

1. **Counterparty fronts are the hero** *(owner-directed)*. Each front (Hoje · Clientes ·
   Fornecedores · Leads) is a status-bearing card whose **own demand** — «N a responder · N a
   cobrar», computed per-counterparty regardless of the active front — lives **inside the button it
   describes**. Navigation and status in one glance; a count physically inside «Fornecedores» can
   never be misread as global. The abstract strip headline (`#_risk`/`#_cobrar`/`#_count`) and the
   secondary tab pills are deleted. Cards are **calm at zero** («em dia» green whisper) — colour only
   appears when something actually demands you.
2. **One scope for every number.** The rail counts are scoped to the active front and the rail
   declares it («Vistas · Fornecedores»). The 58-vs-32 contradiction disappears because there is no
   unscoped number left on screen.
3. **An iconic rail.** Each vista carries a stroke glyph (clock · euro · flag · chase-cycle · check)
   so the rail scans by shape before words; the keyboard digit moves to a **hover-only** chip,
   ending the two-numbers-per-row illusion.
4. **Honest facets.** A facet earns a row only when it would filter to a meaningful subset
   (`0 < count < total`) — «Sem dono» at 121/121 discriminates nothing, so it hides. «rever N» leaves
   the strip entirely (it is Para ti's business) and lands as a quiet Estado facet, hidden at zero.
5. **Nav.** *(P5b shipped:)* a stroke icon per lens (scan by shape) + an `e2d` monogram; the Fila
   badge shows **demand** (WE_OWE red+amber — computed in `webapp._nav_counts`, the same number the
   «Hoje» front shows as «N a responder»), never the total active count. *(P5d shipped:)* **Admin +
   densidade + the theme toggle fold into one gear menu** (config, not lenses — Admin leaves the
   main strip; the gear is active on /admin), and **«Sincronizar» + «correio há N min» merge into one
   status pill** (a dot: green fresh / amber stale / spinning while syncing) fed by the lens poll —
   one control for the action and its own status.

6. **The thread viewer is a vertical in/out timeline** *(P5c shipped, owner-directed).* The dossier's
   «Conversa» is rendered newest→oldest down a left **spine** with a direction-coloured dot per
   message; **inbound and outbound are offset to opposite sides and tinted** (recebido = fornecedor
   blue, flush-left; enviado = cliente teal, indented + tinted), each tagged with a **↓/↑ arrow
   icon**; a **gap chip** between cards shows the time difference (minutes < 1 h, hours < 24 h, days
   above, connector height banded not linear); and the segment from the newest message up to
   **«agora»** is the **open response debt** in the clock's band colour. It still renders each card
   through the shared `msgHTML` kit (one render path) — the direction class + arrow were added to
   that kit, so every surface (Projetos, Para ti) gets the same in/out legibility.

## 3 · Consequences

- `fila_page.py`: the strip becomes `#_fronts` (hero cards) + search + the retreated order/owner
  selects; `renderTabs`→`renderFronts` (per-front `frontDemand`); `renderRail` gains the scope
  caption, `V_ICON` glyphs, hover-key chips, front-scoped counts, and the honest-facet guards; the
  risk/chase filters stay reachable via the palette and the rail vistas.
- The demand-vs-inventory nav-badge change and the gear/freshness-pill are P5b (shared shell,
  `cockpit_ui.py` + `webapp._nav_counts`).
- No change to the list rows, the dossier, the data model, or any invariant (read-only, never-sends,
  precious stores). This is a projection/legibility change on the framing only.
- **P5e (gap-closing, 2026-07-24)** touches only the *regenerable* `crm.db`: an `attach_kinds TEXT`
  column (`SCHEMA_VERSION` 3→4) derived deterministically from attachment filename extensions, unioned
  per-thread in `cockpit.fold_threads` and rendered as a typed 📎 chip (`fila_page._attChip`, bare-📎
  fallback on a pre-v4 db). Related-threads gained a labelled `related` list in the payload → a `.drel`
  jump-link block in the dossier. The timeline's open-debt chip now floors days via `_humanizeAge`
  (mirrors `cockpit._humanize_age`) so it never disagrees with the dossier clock. `workspace.db`
  (precious) is untouched; typed 📎 populates on the next `build_crm` (no ALTER, no migration).

## 4 · Rejected / deferred

- **Keeping the abstract headline** — it answers no question the operator asks; the demand belongs on
  the front it describes.
- **A build-your-own rail / draggable fronts** — the fronts and vistas are fixed (ADR-033 §4 holds).
- **Freshness as a separate strip label** — merged into the sync pill (P5b); one concern, one control.
