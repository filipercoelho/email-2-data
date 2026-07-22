# ADR-031 — The client-email composer gains a purpose selector, guarded by a verbatim-fact check

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-07-20 |

## Context

The Projetos client-email composer (ADR-013) could write exactly **one** kind of email: *ask the
client for the missing must-have details* — the `jobspec.askables` checklist spliced into
`config/client_email_template.md`. Real client project-management communication is more than that. A
fabrication shop (corte laser, CNC, gravação, sinalética) also **rejects** jobs, **sends costs /
accepts**, **chases** for a reply, **asks for proof approval**, **requests a deposit/payment**,
**announces a deadline change**, and says the work is **ready**.

The user asked for a purpose selector on the composer covering these, and — explicitly — "implement
all suggested [rejection] reasons and other subjects very common in this type of email communication,
regarding project management."

The framing question this raised: several of these emails carry **numbers the shop owner types** — a
price, a total, a deposit, a delivery date, a quantity. The composer already lets an optional AI
"melhorar tom" pass rewrite the prose (ADR-027), checked so every ticked question survives verbatim
(`missing_questions`). But a polish that rewrites a *quote* could silently change `160€` to `170€` or
round `1.250,00€` — a wrong commitment to a client, which is exactly the "costly error" the
zero-hallucination non-negotiable exists to prevent. The one-kind composer never faced this because
its content was questions, not money.

## Decision

**Add a purpose selector; keep every purpose's base draft deterministic (ADR-013) and the optional
polish on-top-and-checked (ADR-027); and extend the verbatim check from questions to every
price/number/date the user typed.** No new module — this lives in `clientdraft.py` and the existing
`/api/projects/{pid}/draft[/polish]` routes.

1. **Eight purposes; `ask` is the default and is unchanged.** `PURPOSES` (in `clientdraft.py`) is the
   single source of truth — id, pt-PT label, template token, input kind — mirrored to the page via the
   GET response, never hand-kept in JS. The set: `ask`, `reject`, `quote`, `follow_up`, `approval`,
   `payment`, `deadline`, `ready`. `ask`/`follow_up` reuse the `{perguntas}` token, so `build_draft`
   and every existing test are untouched.

2. **Three input kinds, each deterministic (no LLM in the base).** `questions` — the existing checklist
   (`ask`, `follow_up`). `reason` — a reason chosen from an **editable** list
   (`config/client_email_reject_reasons.md`, 8 defaults in `DEFAULT_REJECT_REASONS`) plus an optional
   free note (`reject`). `text` — a free-text box the user writes (`quote`, `approval`, `payment`,
   `deadline`, `ready`). `build_purpose_draft` splices the input into the purpose's template; the `ask`
   path is byte-identical to `build_draft`. Each purpose has an editable template
   (`config/client_email_<id>_template.md`; `ask` keeps its historical filename) with a built-in
   default fallback (`DEFAULT_TEMPLATES`) — a missing/token-less file degrades gracefully, never ships
   token-less.

3. **The money/text purposes match the user's chosen model: write a rough draft, refine with AI.** The
   free-text box is the substance the shop owner types; the optional "Melhorar com IA" button improves
   the prose — the same ADR-027 mechanism, now available for every purpose.

4. **The verbatim check is extended from questions to numbers.** `extract_values` pulls the
   money/number/date tokens from what the user typed (currency `€160`/`160€`/`160 euros`, percent,
   units `2mm`/`10 dias`/`20 un`, dates `30/09`/`2026-09-30`) via a deliberately conservative regex;
   `missing_values` checks each survived the polish verbatim (whitespace-normalised, but a reformat
   like `160€`→`160 €` **is** reported — a reformat is treated as an alteration so a rounding can never
   slip through). A missing value blocks the AI version in the UI exactly like a dropped question. The
   polish prompt labels the block `VALORES A MANTER` (vs `PERGUNTAS`) and the playbook forbids altering
   them; the deterministic `missing_values` is what makes that promise real, not the prompt.

5. **Backward compatibility is load-bearing and explicit.** A request with no `purpose` behaves as
   `ask`; GET/POST response shapes keep their existing keys (GET adds `purpose`/`purposes`/
   `reject_reasons`; POST adds `facts`; polish adds `n_facts`). The `ask`/questions polish path passes
   **no** `keep_values` kwarg, so it calls `polish_draft` with its historical argument set.

6. **Still never sends; still no LLM in the base; still costs zero on empty.** Delivery stays clipboard
   / `mailto:` (purpose-agnostic). The polish empty-guard generalises: nothing to say or protect → 400
   before any model call.

## Consequences

- Covered by `tests/test_clientdraft.py` (registry; per-kind `build_purpose_draft`; the
  `extract_values`/`missing_values` guard incl. false-positive and reformat cases; per-purpose
  template + reasons loaders; the labelled `VALORES A MANTER` block; `keep_values` forwarding) and
  `tests/test_webapp.py` (GET exposes purposes+reasons with the ask keys intact; reject/quote POST;
  **backward-compat POST without `purpose`**; the money-guard reporting an altered number; the empty
  400 with zero model calls; the page ships the selector + per-kind inputs + the money-guard wording).
- **The guard has documented boundaries** (see the reference doc): a bare total with no `€`/unit
  (`Total: 160`) and written-out numbers ("cento e sessenta euros") are **not** guarded — the
  templates and playbook nudge the user to write `€`. A reformat is intentionally treated as an
  alteration; that strictness is the point (rounding must not pass) and must not later be "fixed" into
  leniency without superseding this ADR.
- **Never silently bin a client (VISION non-negotiable) holds:** rejection is a human-reviewed draft
  with a chosen reason, never an automatic action; the reasons are an editable menu, not model output.
- **Egress is unchanged from ADR-027**: the polish sends the same four grounded blocks (draft ·
  must-keep · confirmed facts minus internal flags · last 6 thread messages @1200 chars); the fifth
  behaviour here (numbers) is a *check on the output*, adding nothing to what is sent.
- **Extends ADR-013 and ADR-027; supersedes nothing.** If a future decision lets the polish author or
  reformat a money value, or makes rejection anything other than a human-reviewed draft, it must
  supersede this ADR explicitly.

**Status update (2026-07-21):** extended by
[ADR-032](adr-032-output-language-and-email-translation.md) — the composer gains an **output-language
selector** (PT/EN/FR/ES). The base stays PT and deterministic; a non-PT choice makes the polish
translate. The number/date guard here is **language-independent** and keeps protecting a translated
quote verbatim; the verbatim *question* check is skipped for non-PT (it can't cross languages) and the
result is marked "traduzido — rever".
