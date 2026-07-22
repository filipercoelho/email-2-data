# Implementation plan — capture revision chain (ADR-022) + the lineage prerequisites

| Field | Value |
| --- | --- |
| Status | Execution plan for [ADR-022](../03-decisions/adr-022-capture-revision-chain.md) (Proposed) |
| Date | 2026-07-19 |
| Baseline | `feat/conversational-intake` — **444 passed** (the CLAUDE.md pin of 330 is stale; correct it in the first commit that lands here) |
| Method | Each WP is one commit, gated by build → **fail-before/pass-after** test → adversarial review → fix |

## Why the order inverts the request

The owner asked for re-capture and a lineage view. **Both depend on write-path fixes that must land
first**, and the reason is time-sensitive rather than aesthetic:

> There is **no derivable join key** from an existing `project_field_history` row back to its
> originating capture. `(channel, asserted_by, acquired_at)` is not a key — it collides whenever one
> person reports two facts from one call. `workspace.db` is never rebuilt (ADR-010), so **every day
> the current code runs mints permanently opaque rows.** No backfill can repair them, and the
> tempting heuristic would fabricate links.

So WP-A/B stop the bleeding, WP-C/D make terminal captures reachable and apply atomic, and only then
do re-capture (WP-F) and the page (WP-G) have honest data to stand on.

## Confirmed defects (all verified by execution, not by reading)

| # | Location | Defect |
| --- | --- | --- |
| 1 | `project.py:433` + `jobspec.py:214` | `provenance[addr] = source_mid or "user"` plus an unconditional `SpecField(value, "user", True)`. A Gemini-extracted deadline accepted with one click reaches the estimable gate as `source='user', confirmed=True`. The system does not *lose* the model origin — it **asserts the wrong one**, on the business-critical gate. **Partially fixed by WP-A** (`field_provenance` now reports `capture:<cid>` instead of the literal `'user'`), but **still open**: `jobspec.confirm()` hardcodes `SpecField(value, "user", True)`, so the canonical spec still reports a model-extracted value as user-authored. **WP-B closes it.** |
| 2 | `webapp.py:986` | `source_mid=f"capture:{cid}" if cap.get("media_paths") else ""` — a **text-only** capture loses its link. Contradicts its own docstring three lines above; the ternary was written for a thumbnail renderer. |
| 3 | `webapp.py:609-613` | `_provenance()` omits the reference, so **every** capture-confirmed field is unlinked. Verified: POSTing `/field` *with* `source_mid` returns 200 and stores `''` — silently discarded. The email path *does* pass it (`project.py:515`), which settles that this is a bug, not a design choice. |
| 4 | `webapp.py:991-996` | `discard` returns `200 {"ok": true}` on an applied capture while changing nothing; the UI toasts "descartado". Becomes visible the moment terminal captures get a UI. |
| 5 | `projetos_page.py:590` | Gated on `sm.indexOf('capture:')===0` with no content-class check → a **voice** capture renders as a broken `<img>`. `captures_page.py:148-154` already handles this correctly. |
| 6 | `project.py:236` | `clear_field` logs with a positional `""` and no provenance kwargs. A removal that can flip estimability has no actor, and `participants()` skips unattributed rows. |

## Work packages

| WP | Scope | Schema | Fail-before / pass-after test |
| --- | --- | --- | --- |
| **A** ✅ **SHIPPED** | Dropped the `media_paths` ternary (#2); `_provenance()` gains `source_mid` — fixed `/field`, `/custom-field`, `/event` in one edit — and **400s** an unrecognised capture reference instead of discarding it (#3); `confirmField` sends `'capture:'+c.capture_id`; `clear_field` carries the bundle (#6); `capture_ref()`/`capture_id_from_ref()` centralised in `captures.py` (the prefix was a literal in three files) | none | 4 tests in `tests/test_captures_api.py`, **all four verified failing against HEAD and passing after**: `test_apply_links_a_text_only_capture`, `test_confirm_field_records_the_originating_capture`, `test_an_unknown_capture_reference_is_refused_not_silently_dropped`, `test_a_removal_is_attributable`. Suite **444 → 448**, ruff clean |
| **B** — the honesty column | `authored_by` on **both** `project_fields` and `project_field_history` (a history-only column cannot fix #1 — `build_canonical` reads `fields_for()` → `project_fields`); `confirmField` computes it client-side: unchanged from the prefill → `model`, edited → `model_edited`, no prefill → `human`. **This is the only place the distinction can be computed** — the POST body is byte-identical today | **v8** | `test_a_model_authored_value_is_never_reported_as_a_human_fact` — assert `canonical` no longer says `'user'` **and that `readiness`/`estimable` is UNCHANGED**. Pins that the fix corrects the *claim* without moving the gate |
| **C** — the lying endpoints | `discard` gains apply's terminal 409 (#4); `GET /api/captures/{cid}` (any status); `GET /api/captures?status=` (default stays `pending`, byte-identical) | none | `test_discard_409s_a_terminal_capture` — **fails now: 200 + no change** |
| **D** — atomicity | Apply's three writes in one transaction on a **short-lived dedicated connection** (`commit: bool = True` plumbing) | none | `test_apply_is_atomic_under_a_concurrent_writer` |
| **E** — pipeline steps | `capture_pipeline_steps` table + `log_step()` (one INSERT, no read-modify-write); call sites in `intake.py` (transcribe/extract/resolve) + `webapp.py` (apply) | v8 (from B) | `test_two_processes_interleaving_lose_no_step` — two connections interleaved; **fails against a JSON blob, passes against the table** |
| **F** — re-capture (**ADR-022**) | `reopen()` + `also-apply`; chain-tip targeting; CAS + partial unique index; `reopen_intent`; **plus the `version > SCHEMA_VERSION` refusal on the `migrate=False` path** | **v9** | `test_reopen_refuses_a_discarded_capture` (the leak pin) + `test_reopen_is_allowed_again_after_the_successor_is_discarded` (dead-end repair) + `test_double_reopen_mints_exactly_one_successor_and_409s_the_second` + `test_late_transcript_is_never_lost_when_the_user_applies_first` |
| **G** — the lineage page | `GET /capturas/{cid}`; tier chips; chain; reverse index; timeline `<img>`/`<audio>` split + click-through (#5) | none | `test_capturas_detail_route_serves_page_and_404s` + `test_lineage_never_infers_a_link_for_a_pre_fix_capture` — plant a legacy `source_mid=''` row sharing the exact `(channel, asserted_by, acquired_at)` tuple a heuristic would join on; assert the page reports **DESCONHECIDO** |

### Design notes carried from the adversarial review

- **WP-D honest cost.** `with conn:` on the *shared* connection does **not** work here, and the
  single-threaded test proposed for it passes anyway — `invalidate_summaries()` commits from off the
  event loop, landing a partial transaction and making the rollback a no-op. The dedicated-connection
  repair is a real extra cost (open/close per apply, `PRAGMA busy_timeout`) and must not be hand-waved.
- **WP-E rejected alternative.** `captures.pipeline_json` (a mutable blob) is read-modify-written by
  **two processes** on separate connections — a lost update. A dropped step would render as an
  *absent* row rather than a `DESCONHECIDO` chip: silent under-reporting on the page whose entire
  value is honesty. The table is genuinely *less* migration surface than the column it replaces.
- **WP-G rendering rule (non-negotiable).** An unrecorded hop renders as a **present row saying
  `desconhecido — nunca registado`**, never as an absent or empty one. Zero rows in "Onde foi usado"
  renders an explicit *"Ligação ao projeto desconhecida"*. And **never** label the project pick
  "inferido": the column is named `inferred_project_id` but stores the *user's* choice.
- **WP-G page shape.** Follows the **Contrapartes** shape (separate server-rendered builder +
  back-link), reuses `active='capturas'` — a 6th `_NAV` entry breaks the shipped
  `html.count('nlink on') == 1` assertion. Must ship `onKey(e)` and `paletteItems(q)`;
  `cockpit_ui.py` calls both unguarded. `esc()` does not escape single quotes → double-quote every
  JS-built attribute.
- **Coordination.** WP-E edits `intake.py`, which is dirty from the UX-fix work in this branch. Land
  A–D first and thread E last, or coordinate.

## Owner decisions (2026-07-19)

- **Q2 — `confirmed=True` survives a model-authored value. DECIDED: keep it.** A human *did* click; the
  gate means "a human signed off". WP-B therefore fixes only the **false `source='user'` claim** and
  must leave `readiness`/`estimable` byte-identical — pinned by
  `test_a_model_authored_value_is_never_reported_as_a_human_fact`, which asserts the gate does **not**
  move. Demoting model-authored values to unconfirmed is explicitly **not** done: it would silently
  flip projects out of ESTIMABLE, and would need its own ADR.
- **Q3 — an honest-but-mostly-empty page is acceptable on day one. DECIDED: ship it.** Every
  pre-existing ledger row is permanently unlinkable, so `/capturas/{cid}` will render
  *"Origem desconhecida"* for captures applied before WP-A. This is accepted deliberately: a rigorous
  "não sei" is the correct output, and WP-G's `test_lineage_never_infers_a_link_for_a_pre_fix_capture`
  exists to keep it that way when it would be easy to fabricate a plausible arrow instead.

## Open questions still owned by the OWNER

1. **Q4 — is the email half in scope?** Recommended capture-only (`/capturas/{cid}`). A unified
   `/origem/{kind}/{ref}` stays permanently sparse: the gazetteer hint, offline candidates and the
   prompt/response pair are unpersisted, and `EXTRACTOR_VERSION` is a hand-edited constant, not a hash
   of the playbook. Persisting prompts means persisting raw bodies — contradicting ADR-020 egress and
   creating a **new PII surface that deserves its own ADR and must not be folded into this work**.
2. **Q5 — fix `triage --full` truncation?** `cascade.py:162` opens `out/results.jsonl` in `"w"` on
   `--full`, destroying every prior verdict — and `--full` is the documented recovery path. Contradicts
   the "append-only" label in `data-stores.md:15`. Same shape at `specbuild.py:135`. Unrelated to
   lineage, contradicts nothing to fix, but it bounds what any lineage view can claim for the email half.

## Not yet verified (flagged, not passed through as fact)

The cross-process lost-update and concurrent-rollback reproductions (the *topology* that makes both
possible is confirmed — shared connection, daemon-thread committer, separate worker process — but the
probes were not re-run); the claim that widening `PENDING_STATUSES` breaks exactly the four named
tests; and `capture_infer.py:92` returning a single batch-level scalar. Each is load-bearing for
WP-D, WP-F and WP-B respectively and is worth a 5-minute confirmation first.
