# ADR-037 — The related-list stops letting contact volume drown out a topical match

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-07-25 |
| Refines | [ADR-034](adr-034-fila-chrome-fronts-and-scoped-rail.md) (the `.drel` jump-link block + `related` payload) |

## Context

The owner, looking at a counterparty's dossier: **«8 conversas relacionadas»** listing an invoice for
a recording, a training-course enquiry, an acrylic-plate quote, and an unrelated project code —
nothing in the list read as actually related, and nothing on the panel said *why* any of it was.

Checking the framing before building: `CrmStore.related()` (`crm.py`) computes three groups —
`thread` (same reply chain), `by_contact` (same sender, **any** topic, unbounded), `by_entity`
(shared `client_name`/`client_email`/`nif`/`iban`/`product_or_service`/`deadline`, exact
normalised-string match). `_fila_rows` (`webapp.py`) only ever surfaces `by_contact` + `by_entity`,
capped at 8, **filling from `by_contact` first** — and drops the match reason before it reaches the
template. The panel's headline copy ("conversas relacionadas") reads as topical relevance; the
mechanism underneath is "this person emailed us about anything."

That diagnosis was code-grounded, not data-grounded — the "always verify" standard doesn't let an
inference like that stand in for a fact. A read-only diagnostic against the real `out/crm.db` (365
threads, 542 interactions, run via a `mode=ro` SQLite connection reusing `CrmStore.related()`
unmodified) measured it directly:

- 284/365 threads (78%) have ≥1 `by_contact` hit; prolific counterparties (up to 256 total
  interactions) guarantee it alone fills all 8 slots.
- 56/365 threads (15%) had a **genuine** `by_entity` match that never surfaced — of 193 raw
  `client_email` matches system-wide, only 1 survived the cap; of 54 raw `deadline` matches, only 20.
- Of the 20 visible `deadline` matches, **8 (40%) were cross-contact** — two different people whose
  messages happened to name the same calendar date, nothing else in common. One was the owner's own
  example: a client project thread (subject naming the project and a named individual, plus a job
  code) linked to an unrelated administrative thread for a different named individual, by date
  coincidence alone. `client_name`/`client_email`/`nif` showed no comparable false-positive pattern
  in the sample (the one cross-contact `client_name` hit found — a supplier-portal notification
  linked to a separate PO email for the same real client, under a different sender system — reads
  as a legitimate same-client-different-system match, not noise).

A second question, from the owner, after the first cut of this fix shipped: **does "mesmo contacto"
factor in @lindoservico.pt addresses — it shouldn't, only external domains.** It did. `related()`
seeds `by_contact()` from `interaction["from_email"]` of the *dominant* message of a thread — and
`fold_threads` picks the dominant message by which verdict is most recently decisive, **not** by
direction. An outbound reply we sent (e.g. a quote emailed from `orcamentos@lindoservico.pt`) can be
the dominant message, so `by_contact` was frequently seeded with our own mailbox address. Measured on
the same real corpus: **113/366 threads (31%) have an outbound-dominant message**; the worst case —
an internal colleague's mailbox, CC'd or forwarding across many unrelated client threads — flooded
**257** completely unrelated subjects in as "related" — a bigger false-positive source than the
`deadline` bug above, and very likely the true cause behind several of the "crowded out" examples
already measured (the by_contact counts line up).

## Decision

1. **`deadline` is dropped from `_INDEXABLE_ENTITY_KEYS`** (`crm.py`). A shared deadline between
   genuinely related messages still surfaces via `client_name`/`client_email` — a deadline match
   gated on also sharing one of those fields would never add a message beyond what that field
   already surfaces, so gating is equivalent to dropping and dropping is the smaller change.
2. **The related-list reserves room for `by_entity` instead of letting `by_contact` fill first**
   (`_fila_rows`, `webapp.py`). Up to `_RELATED_ENTITY_RESERVE` (3) entity matches are taken before
   `by_contact` backfills the remaining slots up to the 8-item cap, with any leftover entity matches
   filling whatever room remains. A contact with zero entity matches is unaffected — `by_contact`
   still gets up to all 8 slots exactly as before.
3. **Each related item now carries its match `reason`** (`"contacto"`, or the entity field name) and
   the **related thread's own `momentum`** (`active`/`slowing`/`stalled`, via the existing
   `cockpit.momentum()`). The `.drel` block renders this as a small coloured dot (reusing the
   dossier's own `MOM` momentum→colour map) and a badge (`por nome`/`por NIF`/`por produto`/`mesmo
   contacto`), reusing the PT-label vocabulary the legacy `/inbox` report's relations panel already
   established (`report.py`'s `_KEY_PT`, now centralised as `crm.ENTITY_LABEL_PT`).
4. **`related()` gains an explicit `contact_email` override** for the `by_contact` lookup key,
   defaulting to the old (seed's own `from_email`) behaviour when not given — so the CLI/legacy
   `/inbox` relations panel, which have no notion of a thread's resolved external contact, are
   unaffected. `_fila_rows` (`webapp.py`) passes the Fila row's own `contact` field — the EXTERNAL
   counterparty address it already resolves per row (inbound sender, or the ADR-033 P4a to:
   fallback for outbound-only threads) — instead of letting `related()` infer one from a possibly-
   internal `from_email`. Passing `contact_email=""` (no external contact known, e.g. a fully
   internal thread) skips the `by_contact` lookup entirely rather than falling back to an internal
   address.

### Why not the alternatives

- **A full two-group visual split** (separate "Mesmo contacto" / "Ligado por…" sections) was the
  first design on the table. The measured data didn't justify it: the actual defects were a greedy
  fill order and one noisy match key, both fixable without restructuring the panel. Building the
  larger redesign first would have solved a problem that hadn't been measured yet — the "strike
  narrowly" standard.
- **Renaming the header only** ("conversas relacionadas" → "outras conversas com este contacto")
  was considered and rejected: it would be actively wrong for `by_entity` hits, which can (and do)
  come from a different sender than the seed message.

## Consequences

- `crm.db` is regenerable (no `ALTER TABLE`; `cmd_crm` rebuilds it clean each run), so no migration
  is needed — a rebuild simply stops re-indexing `deadline` and the stale rows fall out.
- `report.py`'s `_KEY_PT` no longer lists `deadline` (dead entry — `by_entity` can never return it
  now).
- `_RELATED_ENTITY_RESERVE = 3` is a judgment call ("guarantee entity matches a look-in"), not
  empirically tuned beyond that — worth revisiting from real daily use, not further analysis, if it
  ever feels wrong in either direction.
- The `deadline` field stays in `schema.Entities` and keeps rendering as the dossier's `⚑` chip
  (`entities.deadline`, per the Fila row spec) — only its use as a **cross-thread linking key** is
  removed; the per-message deadline display is unaffected.
- `by_entity` is unaffected by the internal-domain fix — entity values are about the *client's*
  identity/attributes as extracted from message content, not the participant address, so an internal
  forward discussing a client is still a legitimate topical link. Only `by_contact` (participant-
  identity matching) had the internal-address leak.
- Threads whose dominant message is outbound-only with no resolvable external recipient (`contact ==
  ""`) now get **no** `by_contact` matches at all, rather than a misleading internal-address flood.
  This is a strict reduction in false positives; no case was found (or expected) where that's a loss.

## Verification

- `tests/test_crm_relations.py::test_by_entity_deadline_is_not_indexable` and
  `..._two_strangers_sharing_only_a_deadline_are_not_linked` — the exact false-positive reproduced
  from the real corpus, pinned as a regression.
- `tests/test_fila.py::test_related_list_reserves_slots_for_entity_matches` — 9 same-sender threads
  plus 1 cross-sender entity match; asserts the entity match survives the cap and the contact/entity
  split is `7`/`1` as designed.
- `tests/test_fila.py::test_related_items_carry_momentum` — each related item exposes `reason` and
  `momentum`.
- `tests/test_crm_relations.py::test_related_contact_email_override_replaces_seed_from_email` and
  `..._contact_email_empty_string_skips_by_contact` — the `contact_email` override contract at the
  `CrmStore` layer.
- `tests/test_fila.py::test_related_by_contact_ignores_our_own_domain` — an outbound-dominant seed
  thread, three unrelated threads sharing only the internal sender, one genuinely related external-
  client thread; asserts the flood is gone and the real relation survives.
- Full suite: **786 passed, 0 failed**; `ruff check src tests` clean.
