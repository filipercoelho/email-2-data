# ADR-013 — The client-email composer is deterministic, server-assembled, and never auto-sent

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-06-10 |

## Context

On the Projetos page the user asks the client for the missing must-haves before quoting. The
original implementation derived a fixed question list from the Gate-1 gaps and assembled the
email body in a hard-coded JavaScript function (`clientEmailText()` in `projetos_page.py`):
take-it-or-leave-it text, no way to choose *which* gaps to raise, add a question of one's own,
or review/edit before copying. The pt-PT prose lived in JS — a behaviour change hidden in the
view layer — and none of it was testable without a browser.

## Decision

The Perguntas block becomes a **composer**, with three rules:

- **Deterministic, no LLM.** The body is assembled by splicing the selected clarifying questions
  into an editable skeleton — nothing is invented. This is the data-extraction zero-hallucination
  rule applied to outbound text: a guessed commitment to a client is a costly error (cf. the
  reply playbook). An *optional* "melhorar tom" polish through the Gemini reply playbook may wrap
  this later, but by design it sits **on top of** the deterministic draft and never replaces it.
- **Server-assembled, config-driven.** The selectable prompts come from `jobspec.askables(spec)`
  (one entry per registry gap: must-gaps pre-ticked, should-gaps offered, the internal `process`
  note flagged and excluded). The body is built by `clientdraft.build_draft()` from the pt-PT
  skeleton in `config/client_email_template.md` — editable config next to the playbooks, not JS.
  The endpoints are `GET/POST /api/projects/{id}/draft`. The JS only renders state and posts the
  selection, so it cannot drift from the registry.
- **Reviewed and human-sent.** The assembled draft is a starting point in an editable textarea;
  the user edits wording (a manual edit is sticky — toggling a prompt no longer overwrites it,
  it offers *Regenerar*), then copies or opens in their mail client. The system never sends.

## Consequences

- The "(interno)" exclusion is now an explicit `internal` flag on each askable, not a fragile
  client-side string-prefix check.
- Tunable wording is a config edit (`client_email_template.md`), treated like a playbook change
  (test + doc), not a code change.
- Trace: `src/email2data/clientdraft.py`, `jobspec.askables`, `webapp.py` (`/api/projects/{id}/draft`),
  `projetos_page.py` (composer). Tests: `tests/test_clientdraft.py`, `test_jobspec.py`
  (`test_askables_*`), `test_webapp.py` (`test_client_email_draft_compose_and_rebuild`).

## Status update (2026-07-20)

The optional "melhorar tom" layer reserved in the first Decision bullet is now **built** — see
[ADR-027](adr-027-ai-polish-sits-on-top-of-the-deterministic-draft.md). This ADR is **extended, not
superseded**: all three rules above still hold, the deterministic draft is still what the composer
produces by default, and the system still never sends. ADR-027 makes "sits *on top of* the
deterministic draft" mechanically checkable (`clientdraft.missing_questions`) rather than a claim, and
it uses its own playbook rather than `reply_playbook.md` as guessed here — that playbook drafts a reply
*from* a spec, whereas the polish rewrites *a given text*.

**Status update (2026-07-20):** further extended by
[ADR-031](adr-031-client-email-purpose-selector-and-verbatim-fact-guard.md) — the composer gains a
**purpose selector** (8 kinds; `ask` stays the default and unchanged). Every purpose's base draft is
still deterministic and server-assembled from an editable template; the money/text purposes add a
free-text input the user writes. All three rules above still hold, and the system still never sends.
