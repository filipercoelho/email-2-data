# ADR-026 — Re-extraction reads the whole project, not just its emails

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-07-20 |

## Context

ADR-025 gave the Projetos page a scoped re-extraction: a button that re-runs the Tier-1 spec pass over
one project's linked emails, at a chosen model tier, without re-billing the corpus. It fixed the case
it was built for — a project whose emails were mis-read the first time.

It could not fix a second, quieter case. ADR-015 gave the project a **claim ledger**: notes,
decisions, opinions and to-dos captured in Registar, plus everything the ADR-019 intake bot files from
Telegram — the deadline agreed on a phone call, the material settled in a WhatsApp exchange, the
correction dictated from the workshop floor. That knowledge is stored verbatim and rendered on the
timeline, and **no model has ever read it**.

The consequence is not cosmetic. `canonical_spec` builds the Gate-1 readiness from `project_fields`.
A deadline that exists only as the sentence *"ficou combinado prazo 15 de março"* in a note contributes
nothing to coverage, so a project can be sitting at `GATHERING` with the missing must-have already
written down two rows below on the same screen. The user has to re-type, by hand, into the
Especificação tab, knowledge the system already holds. The intake bot's capture path *does* extract
fields (`capture_infer.extract_fields`), but only at capture time and only for captures — a note typed
straight into Registar, or any capture predating that feature, is never revisited.

So "reprocess this project with the LLM" meant "reprocess 60% of this project", and the untouched 40%
was precisely the part a human had bothered to write down.

## Decision

**One button re-reads everything the project knows.** `POST /api/projects/{pid}/reextract` keeps its
email pass and gains a **timeline pass** over the project's `op='event'` rows.

1. **Two sources, one action, one tier.** The chosen cost tier (`light`/`standard`/`heavy`) applies to
   both passes. The tier selector is labelled by **cost** (`Leve · custo baixo` …), because that is
   what the choice actually is; depth is the consequence, not the control.

2. **Events are read oldest→newest, *after* the messages.** A recorded note therefore beats a value
   parsed out of an email. That ordering is deliberate: someone chose to write the note down, which is
   the stronger signal. Within the notes, later wins — the same rule the message pass already uses.

3. **A field parsed out of a note is machine provenance, never the human's assertion.** It is written
   with `source_mid="event:<rowid>"` and **no** `channel`/`asserted_by`, even though the note itself
   carries both. This is the load-bearing detail. Copying the note's attribution onto the parsed field
   would be wrong twice over: the person asserted the *sentence*, not this parsed value, and
   `is_machine_provenance` would then classify the field as human — freezing a model guess inside
   `human_touched_fields`, where no later re-extract could ever correct it, and rendering it as a
   confirmed FACT that no one confirmed (the ADR-025 / non-negotiable-4 failure, reintroduced).

4. **`event:<rowid>` joins the frozen `source_mid` vocabulary.** ADR-022 §7 froze it at
   `<message-id> | capture:<cid> | 'user' | ''`; this adds a fifth case. It sits on the *machine* side
   of `is_machine_provenance`, whereas `capture:<cid>` stays human — a capture ref means a person
   confirmed that capture, an event ref means a model read that note.

5. **The three safety properties of ADR-025 hold identically on the new path.** `apply_event_fields`
   skips every address in `human_touched_fields`, never deletes, and skips values identical to what is
   stored — so re-running over unchanged notes writes no history rows and leaves the project
   byte-for-byte the same.

6. **A note the model could not read is reported, not swallowed.** `capture_infer.extract_fields`
   degrades to an empty result on `LLMError`, which is right for the capture path (a dead LLM must
   never cost the user a capture) and wrong here: across a whole timeline it makes "held no spec
   values" indistinguishable from "failed". `extract_fields_strict` raises; the route collects the
   failures into `events.failed` and the result block lists them in red, mirroring the ADR-025
   per-message `spec_error`.

7. **Cost is stated before the click and reported after.** The button names its scope (*"5 emails + 3
   registos da linha do tempo"*, from `n_events` on the project view) and its cost model (*"uma chamada
   por email + uma por registo"*); the result reports `read` (what was paid for) separately from
   `applied` (what changed). Blank notes are dropped before the call — they can yield nothing, so
   sending them is pure spend. No other length heuristic: a note as short as *"prazo: 15 março"* is
   exactly the knowledge this pass exists to recover.

8. **A project with no emails at all is now serviceable.** Previously it 400'd as *"projeto sem emails
   ligados"*. A project that is pure off-email knowledge — calls and WhatsApp, no thread — is a real
   shape here, and with no linked emails the Tier-1 email pass is not billed at all.

## Consequences

- **Registar's promise narrows, and the UI says so.** Its placeholder read *"guardado tal e qual, sem
  IA"*. Storage is still verbatim and still AI-free — nothing rewrites what you typed — but a model may
  now *read* it, on an explicit click. The copy is now *"guardado tal e qual — a IA só lê isto se
  pedires Reprocessar"*. Leaving the old blanket promise up would have been the dishonest option.
- Egress: note text now reaches Vertex on this path. It is the same EU inference target and the same
  owner-signed egress decision as ADR-020, and it happens only on an explicit human click — but it is a
  wider set of text than ADR-020 contemplated (Registar notes, not just intake captures).
- One LLM call per note is linear in timeline length. Bounded in practice (Registar is manual entry)
  and always visible in the pre-click scope line, but it is the cost ceiling to watch if the ledger
  ever grows automatically.
- The extracted values land as unconfirmed `llm` fields, so they raise **coverage** but not
  `readiness['confirmed']` — the user still validates each one, exactly as with email-seeded values.
- Trace: `project.event_ref` / `apply_event_fields` / `ProjectStore.knowledge_events`,
  `capture_infer.extract_fields_strict`, `webapp.project_reextract`, `projetos_page` (rexbar copy,
  `rexResultHTML`, Registar placeholder). Tests: `test_project.py` (ADR-026 block),
  `test_capture_infer.py` (`*_strict_*`), `test_webapp.py` (`test_reextract_reads_timeline_events_*`,
  `*_only_timeline_knowledge`, `*_could_not_read`, `*_never_lets_a_note_overwrite_a_human_decision`,
  `*_over_unchanged_notes_is_idempotent`, `*_states_the_widened_scope_and_its_cost`).
