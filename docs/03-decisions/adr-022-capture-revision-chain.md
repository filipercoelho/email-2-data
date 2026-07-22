# ADR-022 — Re-capture and content correction: a capture revision chain, never an in-place edit

| Field | Value |
| --- | --- |
| Status | Proposed |
| Date | 2026-07-19 |
| Extends | [ADR-019](adr-019-conversational-intake-capture-adapter.md) (silent on reopening), [ADR-020](adr-020-capture-egress-and-data-handling.md) §preserve-at-core, [ADR-015](adr-015-knowledge-capture-claim-ledger.md) (ledger untouched) |
| Supersedes | nothing |

## Context

Two owner requests (2026-07-19) both hit the same wall:

1. **"Once a capture is finally done, the user must still be able to re-capture it."** Today `applied`
   is terminal: `mark_applied`, `set_project` and `discard` are all guarded to `PENDING_STATUSES`, and
   `webapp.apply_capture` short-circuits a non-pending capture with **409**. That 409 was added by the
   M3 adversarial review to close two real defects — a double-click double-write, and an
   apply-**after-discard** leak of content the user deliberately kept out. A terminal capture is in
   fact *unreachable*: `CaptureStore`'s read surface is exactly `get` / `get_user` / `list_pending`,
   and no route returns a non-pending capture.
2. **"Correct a bad transcription and re-apply."** Editing the stored text in place would destroy the
   only record of what was originally captured. Once Telegram is scrubbed (ADR-020 §2
   persist-then-scrub) the stored capture **is the sole copy** — there is no upstream to re-read.

Naively relaxing the guard was simulated and **breaks four shipped tests**
(`test_terminal_captures_are_immutable`, `test_resolve_then_apply_lifecycle`,
`test_apply_appends_an_event_with_provenance_and_marks_applied`,
`test_double_apply_appends_exactly_one_event_and_409s_the_second`) while silently re-admitting applied
captures to the pending queue. ADR-019 and ADR-020 are **Accepted and therefore immutable**, so a
lifecycle change belongs in a new ADR — this one — and must say *extends*, never *complies*.

## Decision

1. **A re-capture MINTS A SUCCESSOR ROW. The applied row is never mutated.** `applied` and `discarded`
   remain terminal **for the row they name**; `PENDING_STATUSES`, `mark_applied`, `discard` and the M3
   409 stay byte-identical. Reversibility is expressed as a *new capture*, not a reopened one — so the
   four tests above stay green **unmodified**, which is the acceptance criterion for this ADR.

2. **Correction is a revision, never an in-place edit — this is how ADR-020 preserve-at-core is
   honoured.** A corrected transcript/text is the successor row's content; the original row's
   `raw_text`, `transcript` and `media_paths` are **immutable after apply**. The lineage therefore
   shows *what the model heard* next to *what the human corrected it to*, which is strictly more audit
   than an edit would leave. Media is shared **by reference** — never copied, never deleted.
   > **Standing constraint this creates:** no per-capture media deletion may ever be added without
   > first walking the revision chain. None exists today; this ADR is the reason it must not be added
   > carelessly.

3. **The reopen guard is a whitelist of exactly one status.** `POST /api/captures/{cid}/reopen`
   requires `status == STATUS_APPLIED` **literally** — never `status != pending`. A discarded capture
   fails this *structurally*, not by care, so the apply-after-discard leak the M3 review closed stays
   closed. Guard order: 404 unknown → 409 unless `applied` → 409 if a **live** successor exists.

4. **Intent is RECORDED, never inferred.** Re-capture has four owner-sanctioned meanings, and the
   system cannot know which applies. The user states it and it is stored on the successor
   (`reopen_intent`):

   | Intent (pt-PT UI) | Meaning | Effect |
   | --- | --- | --- |
   | `corrigir` | The original claim was wrong | Successor supersedes; chain renders `↻ substitui` |
   | `acrescentar` | Adds detail; the original stands | Successor is additive; nothing revoked |
   | `refazer` | Content fine, the *inference* was wrong | Re-runs transcription/extraction over the shared media |
   | *(see §6)* | Right content, wrong/second obra | Not a re-capture at all — a separate action |

   **No `__revogado__` ledger event is auto-written.** Asserting "the original is revoked" when the
   user may be *adding* detail is exactly the zero-hallucination failure this project exists to
   prevent (PROFILE.md). Supersession is rendered from `reopen_intent='corrigir'`, which is a recorded
   human statement — a FACT — not a machine inference.

5. **One live successor per chain, and no dead ends.** Enforced in schema by a partial unique index
   (`ON captures(supersedes) WHERE supersedes IS NOT NULL`) plus a compare-and-swap on
   `superseded_by`, giving two independent locks against a double-click. `reopen` targets the chain's
   currently-live **applied tip**, so the chain stays linear (`c-2-5 → -r1 → -r2`). A *discarded*
   successor releases the lock: a fresh `-r<n+1>` is permitted, and the abandoned branch stays visible
   in the chain — better lineage, not worse.

6. **Filing into an additional project is a DIFFERENT action, not a re-capture.** `POST
   /api/captures/{cid}/also-apply` appends an event to a second project **without** changing the
   capture's status and **without** minting a successor — the content was right, it simply belongs to
   two obras. Guarded idempotent per `(capture, project)` pair: 409 if this capture already has an
   event in that project. This is called out separately because folding it into re-capture was the
   modelling error that broke two of the three competing designs.

7. **`source_mid` keeps its shipped meaning; its value space is frozen at four cases.**
   `<message-id>` | `capture:<cid>` | `'user'` | `''`. **Correction of an earlier claim in this
   project's own review notes:** ADR-015 §2 does *not* forbid `capture:<cid>` — §2 mandates real
   columns for the provenance *bundle* (`channel`/`asserted_by`/`acquired_at`) and explicitly treats
   `source_mid` as the reference holder with `''`/`'user'` sentinels; **ADR-020 line 68 blesses
   `source_mid="capture:<cid>"` by name**. Typed `source_kind`/`source_ref` columns would be an
   *extension needing its own ADR*, not a compliance fix, and are **out of scope here**. The `'user'`
   sentinel is live (guarded at `workspace.py:277`) and must never be parsed as an email origin.

   > **Extended 2026-07-20 by [ADR-026](adr-026-re-extraction-reads-the-whole-project.md):** the value
   > space is now **five** cases — `event:<rowid>` cites a timeline knowledge event a model read a
   > field out of. It is the one case that lands on the *machine* side of `is_machine_provenance`
   > alongside a bare `<message-id>`; `capture:<cid>` remains human (a person confirming a capture).
   > This is exactly the "extension needing its own ADR" this clause anticipated.

8. **Schema v8 → v9, additive and backfill-free.** Three guarded `_add_column` calls on `captures`
   (`supersedes TEXT`, `superseded_by TEXT`, `revision INTEGER`) plus the partial unique index via
   `CREATE ... IF NOT EXISTS` in `SCHEMA`. Absent `revision` reads as `0`, so **no backfill touches
   the precious DB** (ADR-010). `telegram_message_id`/`chat_id` are **NULL** on a successor — it is
   honestly not a Telegram message — and SQLite treats NULLs as distinct under the existing
   `UNIQUE(telegram_message_id, telegram_chat_id)`, so successors coexist.

## Consequences

- **The `applied` status stops meaning "finished" and starts meaning "filed as of this revision."**
  Every reader of `status == 'applied'` must tolerate a superseded row. Audited call sites: the
  Caixa de Capturas queue (unaffected — reads `list_pending`), the project timeline, and the lineage
  view (ADR-023, unwritten).
- **A newly discovered, unguarded hazard this ADR does not fix.** `workspace.py:230` is
  `if version < SCHEMA_VERSION` — it refuses a DB *behind* the code. The reverse case is **unguarded**:
  a **v8 worker against a v9 DB connects silently** and keeps writing revision-less captures. Earlier
  notes in this project asserted a lockstep lockout exists; **it does not.** Adding a
  `version > SCHEMA_VERSION` refusal to the `migrate=False` path is a prerequisite of WP-F and is
  tracked in the implementation plan, not assumed.
- **Explicitly NOT done, and why** (both were proposed and fatally refuted):
  - *Auto-writing a reversal event* — see §4; the system cannot know intent.
  - *Adding `AND status IN (PENDING_STATUSES)` to `set_transcript` / `set_extracted_fields`.* The
    worker's ordering (`add()` commits → the capture is instantly appliable → scrub → two Vertex
    round-trips → `set_transcript`) means a user applying inside that window would **silently lose the
    transcript**, inside a bare `except Exception` that only logs, with **no re-transcribe path
    anywhere in the codebase**. The audio survives but nothing shipped can turn it back into text.
    Today's unconditional UPDATE is what makes the preserve-at-core comment on that method true. It
    stays, and gains the inverse pin `test_late_transcript_is_never_lost_when_the_user_applies_first`.
- **Trace:** *(pending implementation — this ADR is Proposed. Graduate to Accepted only once WP-F has
  shipped and is pinned, per the ADR-019 precedent.)*
