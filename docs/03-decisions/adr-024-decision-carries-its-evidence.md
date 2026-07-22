# ADR-024 — A decision carries its evidence: Para ti expands in place

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-07-19 |
| Extends | [ADR-023](adr-023-live-decision-queue-freshness.md) (the refresh this must survive), [ADR-014](adr-014-restful-deep-linkable-cockpit-urls.md) (the open gate is addressable) |
| Supersedes | nothing |

## Context

Owner feedback on `/para-ti` (2026-07-19): *"quite weak in terms of navigation and information
interaction UX. Clicking on each container doesn't expand the panel into a detailed view. The page
feels very static."*

Observed on the live queue, which was showing 8 gates:

- **Every card was the same.** All 8 read `PROPOR PROJETO` with an identical pair of buttons. The
  kind badge repeated 8 times carries no information — a label is only signal when it can differ.
- **The click affordance was a lie.** `.gate` set `cursor:pointer`, so every card *looked*
  interactive and did nothing. That is worse than a plainly static list: it invites a click and
  silently discards it.
- **Judging a decision meant leaving the page.** The card showed the AI's one-line summary and
  nothing else. To see the email it summarised — the actual evidence — you had to follow
  "ver na fila →" into another lens and find your way back. The queue could tell you *that*
  something needed you, never *why*, and never enough to act.
- **Correcting a wrong verdict was somewhere else entirely.** Reclassification lives on the Fila, so
  a misread gate could be recognised here and only fixed there.

The page was a notification list pretending to be a decision surface.

## Decision

1. **A gate expands in place into the evidence behind it.** Click (or `Enter`/`Space`/`E`) opens the
   thread the decision is about: every message, the cleaned body with quoted-text and
   "ver original" toggles, and the attachments as working links. **Inline, not a new page** — the
   cockpit's "pivot, don't navigate" principle, and the same interaction the Fila already uses.

2. **The panel is the shared thread kit, not a private copy.** It renders through `msgThreadHTML` /
   `msgHTML` in `cockpit_ui.py`, so the Fila, Projetos and Para ti stay one renderer. Likewise the
   pt-PT enum wording now comes from `labels.py`; the page's own drifting `_purposeLabel` dict is
   deleted.

3. **Accordion: at most one gate open.** Keeps the URL well-defined (`?item=<key>`, plus
   `?tipo=<kind>` for the filter — ADR-014), and keeps a long queue navigable instead of turning it
   into a wall of open threads.

4. **Expanding is ONE round-trip.** `/api/thread/<root>` now returns a `spec` block (merged job
   fields + Gate-1 readiness) alongside the messages. A second endpoint would mean a second spinner
   on the very thing the user is trying to judge. It is **lazy by construction** — only a human
   expanding a gate hits it, so the ADR-023 poll never pays for it. The spec is keyed off the
   thread's CRM interactions, not the rendered message list, because rendering drops messages with
   no body and no attachments and the spec belongs to the thread.

5. **The extraction summary folds per FIELD, never per line item.** A 3-piece job with the same
   unanswered must-have is *one* fact — "material: em falta" — not three. Rendering it per item
   produced ~25 near-identical rows that buried the handful of known values: technically complete,
   completely unreadable, and therefore a worse honesty failure than showing less. Known values are
   listed with deduped content and **partial coverage stated** ("150 · 1 de 5"); unanswered
   must-haves collapse into one line of chips. Provenance stays explicit per ADR-007 — `IA` in
   dashed ink for INFERENCE, `EXTRAÍDO` for deterministic, `CONFIRMADO` once a human commits — and
   an incomplete spec never renders as estimável.

6. **The decision is actionable where it is made.** Reclassify counterparty/purpose (`/api/reclassify`,
   with "↺ auto" to revert), assign owners, mark handled, open the project it already belongs to —
   all without leaving the card. This is why Para ti items now carry `message_id`, `auto`, `owners`
   and `project`: reclassification is keyed by *message*, and without that handle the card can only
   send the user elsewhere.

7. **Kinds are grouped with counts, and filterable — but only when there is more than one kind.** A
   filter that can only ever select everything is chrome that does nothing, so with a single-kind
   queue no chips render at all.

8. **Everything remembered about a gate is keyed by content.** `openKey`, `dismissed` and focus all
   key off `itemKey(item)`, so the ADR-023 refresh can reorder the queue underneath an open panel
   without closing it or re-pointing it at a different decision. The open gate closes only when its
   decision genuinely leaves the queue.

## Consequences

- The queue answers "why does this need me?" on the spot; "ver na fila →" becomes a pivot, not a
  prerequisite.
- `/api/thread` grew a field. Additive and lazy: the Fila ignores it and pays nothing.
- Nav links carry `data-nav` (added in ADR-023) so badges update in place; `.gate` cards are now real
  `role="button"` targets with `aria-expanded`.
- **Not addressed here:** `client_identity` renders as the literal enum (`cliente: CLIENT`) because
  that is what extraction stored. It reads oddly in the new panel but it is an extraction-layer data
  question, not a rendering one, and was left alone rather than patched on a guess.

## Verification

- Real-browser (Playwright) acceptance in `tests/test_para_ti_live_e2e.py`, against a fixture with a
  genuine corpus (bodies + an attachment) and a 3-item job spec: clicking expands into the thread;
  only one gate opens at a time; the panel survives the refresh poll and closes only when its own
  decision leaves; `Esc` closes; action buttons never toggle the card; a reclassification and a
  "Tratado" both persist server-side; attachment links actually serve bytes; grouping/filter appear
  only with more than one kind.
- The per-item explosion was **deliberately reintroduced** and confirmed to fail
  `test_spec_panel_folds_per_field_not_per_line_item` (19 rows vs the expected 3).
- Live against the real mailbox: a gate expanded to 2 messages, 3 attachments, and a spec panel of
  6 known fields + 5 folded "em falta" chips — down from 35 rows.
