# ADR-030 — The DESCRIÇÃO composer is deterministic and renders the corpus average style

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-07-20 |

## Context

The user wanted a module that drafts the **descritivo do produto/serviço** — the free text in the
`DESCRIÇÃO` column of an ARTSOFT proposta (C100) or fatura (V001) — from what the system already knows
about a job, so the person issuing the document redacts a draft instead of writing from scratch. The
stated goal was **"ensure the same style of writing is kept across all documents."**

We first analysed the real documents to learn the style. A read-only IMAP sweep of all four mailboxes
(ADR-002) found 59 sent PDFs matching the `C100-26…` / `V001-26…` numbering; from those a stratified
40-document sample (20 propostas + 20 faturas, three authors, six months) was extracted
coordinate-scoped to the `DESCRIÇÃO` column only, verified byte-exact against a ground-truth image
(`C100-26000455`). Two multi-agent analysis passes and an adversarial round-trip produced
`out/wording-analysis/` (kept out of git — client data).

The analysis returned a result that **inverts the naive design**:

- **There is no single house style.** The corpus carries at least three (a labelled `Suporte:` /
  `Medidas Gerais:` / `Acabamento:` block, a `Material:` / `Técnica:` block, and free prose), and two
  different styles appear in documents written by the same person on the same day. Separator, label
  casing and terminator vary freely even within one author.
- **Faithful reproduction is the wrong target.** Regenerating held-out documents byte-for-byte scored
  11/30 lines, and every line that reproduced did so because it was caller-supplied text passed
  through verbatim; every line the generator *authored* (separators, prepositions, agreement) tended
  to diverge. A generator that models the historical variation therefore reproduces the very
  inconsistency the user wants gone.

So the requirement "keep the same style across all documents" is a **normalisation** requirement, not
a reproduction one. The style has to be applied uniformly — which also means the generator's value is
in the connective tissue (ordering, gap-marking), not in inventing the nouns, because the nouns are
client-specific and must come from confirmed facts.

**Which style?** The user's answer was explicit: **the average of all documents analysed** — the style
should *represent* the corpus, not a hand-pick. So the target is the modal (most-frequent) form on each
independent dimension, measured over the 59 clean-extracted documents:

| Dimension | Average | Rendered |
| --- | --- | --- |
| macro-structure | **prose** 39/59 (66%) vs labelled 20 | one flowing sentence, not bullet lines |
| shape | **header + 1 body segment** is the mode (32/59) | title line + one sentence |
| opener | **`Produção de`** dominant (15) | default opener |
| material preposition | **`em`** 45 vs `de` 10 | `em {material}` |
| header casing | **title/sentence** 33 vs UPPER 12 | passed through as typed (not upper-cased) |

This **reverses an earlier working assumption** in this same change: a first cut fixed the *labelled*
block because it reproduced best under adversarial testing. But "best-reproduced" is not "most
representative", and the user asked for the average — which is prose. The labelled and
`Material:`/`Técnica:` styles are the minority and are **not** emitted.

## Decision

**A deterministic composer renders the corpus AVERAGE style from confirmed JobSpec fields; an optional
LLM polish sits on top of it and is checked.** This is the ADR-013/-027 pattern applied to the
DESCRIÇÃO column. New module `descdraft.py`; editable house style in
`config/description_playbook.md`.

1. **The average style, encoded in the playbook.** A header line passed through as typed, then one
   flowing sentence: `{processo} {item} em {material}, c/ {dimensoes}, {acabamento}.`, with at most one
   trailing `Obs.:`. Each setting is the measured modal form (table above), not a preference. The joins
   are kept deliberately simple (comma-separated clauses) because the deterministic layer supplies the
   representative *structure* and the fact-checked polish naturalises the connectors — that is the
   honest division of labour, since prose connective tissue is exactly what a deterministic layer
   cannot reliably author. Changing the style is a playbook edit, treated as a behaviour change (test +
   doc), never a per-document toggle.

2. **Every value comes from a confirmed JobSpec field.** The composer reads the fields humans already
   confirmed on the Projetos page (`item`, `material`, `thickness`, `dimensions`, `colour_finish`);
   it extracts nothing. A field whose only value is an unconfirmed LLM draft is treated as absent
   (`require_confirmed=True`) and reported in `unconfirmed`, so a model guess never reaches a document
   with a price on it. This is the zero-hallucination non-negotiable applied to outbound text.

3. **A missing fact is a visible gap, never an omission or a guess.** An absent `material` renders
   `em [[MATERIAL?]]` inline — deliberately un-sendable — not a silently dropped clause. A silently
   absent thickness reads as a deliberate choice; a marked one reads as work to finish. The one
   exception is `Obs.:`, dropped when empty because its absence is normal.

4. **The internal `process` field is never client-facing.** The registry `process` field is an
   *internal* manufacturing note ("(interno) Definir o processo de fabrico"). The opener falls back to
   the style default `Produção de` — true of everything the shop makes, hence a style choice, not a
   factual claim. A specific opener (`Corte Laser de`) is emitted only when the caller passes one.

5. **Optional AI polish sits on top and is checked (ADR-027 shape).** `polish_description` may rewrite
   the prose but must carry every confirmed fact through verbatim; `missing_facts` verifies each value
   survived (whitespace-normalised — a re-wrap passes, an altered `485 mm → 480 mm` fails) and
   `dropped_gaps` counts any gap marker the model tidied away. A dropped or altered measurement is the
   error that costs money, so it is surfaced, not trusted. Failure raises rather than silently
   returning the unpolished draft.

## Consequences

- The composer is pure and idempotent; re-rendering the same spec yields identical text. Covered by
  `tests/test_descdraft.py`: the average prose output, inline gap-marking, the confirmed-vs-unconfirmed
  boundary, the internal-`process` leak regression, multi-item, the polish coverage checks.
- **It standardises going forward; it does not reproduce the past.** Existing documents will not match
  its output, by design — even the average form differs from any individual document. "Same style
  across all documents" is achieved by convergence on the average, not imitation of any one.
- **Wired to the Projetos page.** `GET /api/projects/{pid}/description` builds the deterministic
  average draft from the canonical spec; `POST …/description/polish` runs the checked polish, mirroring
  ADR-027's `#_aibtn` (button-only, both texts returned, loud failure). Covered in `tests/test_webapp.py`.
- The controlled vocabularies (materials, finishes, openers) extracted from the corpus live in the
  playbook as reference pick-lists and in `out/wording-analysis/`. They inform the drafter and the
  polish prompt; the module never selects a term on its own.
- Extends ADR-013 (deterministic composer) and ADR-027 (checked polish) to a second surface; supersedes
  nothing. If a future decision offers multiple styles or lets the polish author facts, it must
  supersede this ADR explicitly.
