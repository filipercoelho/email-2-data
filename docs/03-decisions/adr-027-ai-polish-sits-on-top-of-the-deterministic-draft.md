# ADR-027 — The AI email polish sits on top of the deterministic draft, and that is checked

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-07-20 |

## Context

ADR-013 made the client-email composer deterministic: the body is assembled by splicing the ticked
clarifying questions into an editable pt-PT skeleton, with no model in the loop, because a guessed
commitment to a client is a costly error. It also reserved the extension explicitly:

> An *optional* "melhorar tom" polish through the Gemini reply playbook may wrap this later, but by
> design it sits **on top of** the deterministic draft and never replaces it.

That extension is now wanted. The deterministic draft is correct but reads like a form: it opens the
same way regardless of what the client wrote, it cannot acknowledge *"como referiu, a peça é em inox"*,
and it cannot match a client who writes in English or informally. The user asked for a button that
reads the thread and the confirmed facts and writes a better email.

The interesting question is not whether to build it, but what "sits on top of" means operationally. If
an LLM is handed the deterministic draft and asked to improve it, nothing structurally prevents it from
dropping question 3 — and the email exists to ask those questions. "On top of" is a claim about the
output, and an unchecked claim about a model's output is a wish.

## Decision

**The model rewrites the prose around a fixed question list, and the result is verified against that
list before the user sees it.**

1. **Button only.** `POST /api/projects/{pid}/draft/polish` is reached from one click on
   `#_aibtn`. Nothing on page load, checkbox toggle, keystroke or tab switch calls it. The composer
   opens with `ai: null` and stays that way until asked. (Pinned by a test that slices `loadDraft` out
   of the served HTML and asserts the route does not appear in it.)

2. **The server rebuilds the deterministic draft; the browser's text is not the input.** The request
   carries `{selected, custom, tier}` — the same contract as `POST …/draft` — and the route re-derives
   the questions from `jobspec.FIELDS` in registry order. The questions therefore enter the prompt as a
   **fixed list the model did not choose**, which is the mechanism behind "on top of". (The user's
   hand-edits stay in the browser, as they always have; adopting the AI version marks the draft dirty
   like any other manual edit, so a later toggle offers *Regenerar* rather than silently overwriting.)

3. **The output is checked, deterministically, with no second model call.**
   `clientdraft.missing_questions` asserts every question survives verbatim, whitespace-normalised —
   loose enough that a legitimate re-wrap passes, tight enough that a reword fails. Anything missing is
   named in an orange warning telling the user **not** to use that version as-is. A grader model would
   have been a second unchecked claim; a substring check is a fact.

4. **Both versions are returned; adopting is a second click.** The response carries `base` (the
   deterministic draft) and `body` (the polish), rendered side by side. The polish never becomes the
   draft on its own — that is the difference between "on top of" and "replaces".

5. **Grounding is bounded and labelled.** The prompt gets four explicitly-fenced blocks: the draft, the
   questions, the confirmed facts (`_confirmed_facts`, with `client_identity`/`design_ready`/`process`
   withheld — internal flags are not things one says to a client), and the last 6 thread messages at
   1200 chars each. An empty block reads `(nada confirmado ainda)` / `(sem histórico disponível)` rather
   than being absent: an unlabelled gap is an invitation to fill it. The cap is also the cost control —
   the composer needs tone, not the corpus; sending the whole thread would re-bill the spec pass.

6. **A new playbook, not the reply playbook.** ADR-013 guessed this would reuse `reply_playbook.md`.
   It does not: that playbook drafts a reply *from* a spec, whereas this rewrites *a given text*, and
   the hard rules differ. `config/client_email_polish_playbook.md` is its own editable config, treated
   like any playbook (a change is a behaviour change: test + doc). Its fallback constant carries the
   same hard rules — a missing config must not quietly become a permissive prompt.

7. **Failure is loud.** `polish_draft` raises on an empty completion; the route answers 502 (model
   failed) or 503 (no client/credentials) and never falls back to returning `base` with a 200. The user
   explicitly paid for a call and must know whether they got one. Tier is chosen per call from the same
   `light`/`standard`/`heavy` selector, defaulting to `standard`.

## Consequences

- ADR-013 is **extended, not superseded**: its three rules (deterministic assembly, config-driven,
  reviewed and human-sent) all still hold. The deterministic path is unchanged and remains what the
  composer produces by default; the system still never sends.
- The zero-hallucination rule is enforced by prompt *and* structure: the model may only restate facts
  it was given, and the questions it must carry are checked mechanically. It is not enforced for the
  free prose it writes around them — a reviewer still reads before sending, which is the standing
  contract for this composer.
- Egress: the client thread text now reaches Vertex on this path too. Same target and same explicit
  human trigger as ADR-026; the same bodies already go through the spec pass.
- If the model routinely drops questions at a given tier, the warning makes that visible rather than
  silent — which is the signal to fix the playbook, not to trust it harder.
- Trace: `clientdraft.polish_draft` / `missing_questions` / `build_polish_message` /
  `load_polish_playbook`, `config/client_email_polish_playbook.md`, `webapp.project_draft_polish`
  (+ `_confirmed_facts`, `_thread_excerpts`), `projetos_page` (`aiBarHTML`, `aiResultHTML`,
  `polishDraft`, `useAIDraft`). Tests: `test_clientdraft.py` (ADR-027 block), `test_webapp.py`
  (`test_polish_*`, `test_projetos_page_ships_the_ai_polish_control_as_a_button_only_path`).

**Status update (2026-07-20):** extended by
[ADR-031](adr-031-client-email-purpose-selector-and-verbatim-fact-guard.md). The polish now serves
every composer purpose, and the "sits on top of" check is widened from questions to money: the
prices/numbers/dates the user typed are extracted (`clientdraft.extract_values`) and verified to
survive the polish verbatim (`missing_values`) — a reformat like `160€`→`160 €` is treated as an
alteration, so a rounding can never slip. The button-only, both-texts-returned, loud-failure contract
above is unchanged.
