# ADR-054 — an LLM-derived body fragment is a derived store, not a log line

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-08-06 |

## Context

Phases 4 and 5 of [fila-evidence-and-narrative-phases.md](../04-implementation/fila-evidence-and-narrative-phases.md)
were gated on this ADR, for one reason: they are the first thing in this app that takes a **fragment
of an email body**, sends it to a model, and **writes the result back to disk in a derived store**.

Non-negotiable #5 says: *"Secrets via `.env` / ADC only — never committed, never logged. Raw
bodies/addresses never logged. Derived results are personal data."* The question this ADR has to
answer is narrow and real: **is a model-selected sentence from a client's email a "raw body" that may
never be persisted, or a "derived result" that may?**

Three facts, each verified rather than assumed, decide it:

1. **The app already persists whole raw bodies, deliberately.** `corpus/` holds every fetched
   message as a complete `.eml` (1271 files, ~850 MB), by design — ADR-053 makes the filename a pure
   function of the message id. `crm.db` stores subjects and extracted entities. A one-sentence quote
   is **strictly less exposure** than the file it was copied out of, which is sitting next to it.
2. **The rule that is actually load-bearing is about the *log*, not the disk.** `out/audit.jsonl` is
   the one derived store outside every control this project has: no route serves it, ADR-045's
   visibility gate never sees it, it is **not** in `bin/backup-workspace.sh`'s `STORES`, and nothing
   in `src/` rotates or prunes it (it is 10 MB and append-only). A body fragment written *there* is
   permanent, unreachable by any per-person scope, and invisible to every mechanism that governs the
   rest of the data.
3. **`audit.py`'s no-raw-body rule is a docstring with zero enforcement and zero test coverage.**
   `log()` serialises whatever `meta` it is handed, with `ensure_ascii=False`, so a Portuguese
   sentence lands readable. There is no `tests/test_audit.py`. The two existing call-site conventions
   *disagree* — `specbuild` audits the exception **type**, `fetch` audits a free-text detail string —
   so "follow the existing pattern" was not an answer for a failure event that wants to explain *why*
   a quote was rejected.

## Decision

**A model-derived body fragment is a derived result. It may live in a sidecar under `out/`, and
nowhere else.**

Concretely, and in force for both phases:

1. **Two new sidecars, both under `out/`**: `out/evidence.jsonl` (keyed by `message_id`) and
   `out/narratives.jsonl` (keyed by `thread_root`). `out/*` is gitignored wholesale
   (`.gitignore:53`, verified with `git check-ignore`, including at depth), so neither can be
   committed. A store placed **anywhere else** is not ignored — verified: `git check-ignore` returns
   nothing for `evidence.jsonl` at the repo root. `captures/` is the precedent that needed its own
   rule precisely because it deliberately lives outside `out/`.
2. **Never `results.jsonl`.** It is body-free *by contract*, stated in `report.py`'s module docstring.
3. **Never `crm.db`.** `build_crm` rebuilds it whole into a temp file and `os.replace`s it on every
   sync (`crm.py:589-655`), so anything cached there is destroyed.
4. **Never `out/audit.jsonl`.** A failure event carries `message_id`, the rejection **reason code**
   and counts — never the quote, never the value, never the body. This is the rule the two phases
   were most likely to break, because a rejected quote is exactly what a debugger wants in the log.
   It is now **enforced and pinned**, not just documented: `audit.log` refuses to serialise a `meta`
   value longer than `audit.MAX_META_CHARS` (200) and raises `ValueError`, and
   `tests/test_audit.py` fails if that guard is removed. The docstring became a guard because this
   ADR is the first change that makes violating it useful.
5. **Never a new route.** Neither sidecar gets an endpoint of its own. Evidence rides inside
   `/api/thread`'s existing `facts` entries and the narrative rides as one more key on the same
   single `JSONResponse` — so both inherit ADR-045's `_may_open_thread` gate exactly as the bodies
   they came from already do, with no new surface to forget to protect.
6. **The stores stay regenerable, and are NOT added to `bin/backup-workspace.sh`.** Item 4.6 of the
   plan asked for this decision explicitly; the answer is no, and the reason is measured, not
   stylistic: `snapshot_and_verify` is sqlite3-only (it opens the source as a SQLite URI, queries
   `sqlite_master`, and copies with `VACUUM INTO`), and `sqlite3` raises
   `DatabaseError: file is not a database` on a `.jsonl` with no `except` around it. Adding either
   sidecar to `STORES` makes the **whole** pre-migration backup exit 1 and print
   *"BACKUP FAILED — do not migrate"* even though `workspace.db` and `auth.db` snapshotted fine. The
   `out/jobspecs.jsonl` precedent is already unbacked-up for the same reason; this ADR states that
   rather than inheriting it silently. Both files are rebuildable from `corpus/` + `results.jsonl`
   for the price of the LLM pass ($0.68 for the full corpus, measured — see below).

## Why an echo may be discarded without losing anything

The validation stack rejects a quote equal to the value it justifies. That looked like it might throw
away half of every run (the handover's re-count: 29 of 58 spike quotes are echoes). It does not, and
the argument is worth writing down because it is what makes Phase 4 cheap:

**If `quote == value`, then searching the rendered text for the quote is the same search as looking
for the value — which the Phase-3 client already performs on every ledger click.** So an echo can
only ever paint a span that is already painted. Rejecting it removes a duplicate, never a result.

Measured on the paid-for spike, re-partitioned by whether Phase 3 already paints the row:

| | pairs | echo | genuine + literal + reachable + unique |
| --- | --- | --- | --- |
| rows Phase 3 already paints | 28 | 25 (89%) | **0 (0%)** |
| **rows Phase 3 leaves dark** | 30 | 8 (27%) | **21 (70%)** |

The 50% pooled echo rate is an artifact of mixing the two halves. On the population Phase 4 actually
serves the yield is **70%**, and on the two keys Phase 3 structurally cannot reach it is higher still
(`action_requested` 14/16, `deadline` 3/3).

## Consequences

- **A body fragment now exists in two more places on disk.** Both are under `out/`, both are
  gitignored, both are served only through `/api/thread` behind `_may_open_thread`, and both are
  deleted by deleting the file. `out/` is bind-mounted read-write into the container, so the
  never-write-`out/*.db`-from-the-host rule (CLAUDE.md, 2026-07-30) does **not** apply to these two:
  they are JSONL, written atomically with `os.replace`, and no reader holds an open handle.
- **`audit.log` can now raise.** It is called from inside `except` blocks whose whole purpose is to
  not fail the caller. Every existing call site passes short ids and type names and is unaffected
  (verified by running the suite), but a future call site that hands it a body **fails loudly at the
  point of the mistake** instead of writing personal data to a file nothing governs. That trade is
  the point: this is the one guard where silence is the expensive failure.
- **A rejection is stored, not dropped.** Plan item 4.5 says "anything failing validation stores
  nothing". Taken literally that collides with the incremental gate, which keys on *presence of a
  row*: a message with no row is indistinguishable from one never attempted, so every sync would
  re-bill the LLM for every message that ever failed validation, forever. The sidecar therefore
  stores a row for every **attempted** message, carrying the accepted quotes and a
  `{key: reason_code}` map for the rejected ones. No quote text is stored for a rejected key — the
  reason code (`echo` / `not_in_body` / `not_unique` / `value_not_in_quote` / `absent`) is enough to
  explain the decision and carries no body text at all.
- **Neither pass may run inside `rebuild_jobspecs`.** `tests/test_specbuild.py:186-198` spies
  `os.replace` and asserts **exactly one** atomic write per rebuild — and `specbuild.os` *is* the
  stdlib `os` module, so the spy is process-wide and would catch a second write from any module.
  Both passes are top-level functions called beside it, never within it.
- **The triage prompt and schema are untouched**, so no `EXTRACTOR_VERSION` bump and no verdict
  churn. The context cache is keyed on `(model, sha256(system_instruction))`, so a new system prompt
  can never collide with the triage playbook's cached prefix.

## Alternatives rejected

| Rejected | Why |
| --- | --- |
| Store the quote in `crm.db` beside the entities | Destroyed on every sync (`build_crm` os.replaces the whole file). |
| Store the quote in `results.jsonl` | Body-free by contract (`report.py:7`). |
| Store **character offsets** instead of the quote text | Cannot survive the trip. `msgHTML` re-splits the body **client-side** (`msgSplitQuote(body_clean)`), **trims** both halves, renders `body_clean` and `body_sig` into separate boxes and slices each (`.tbody` ≤2000, `.tquote` ≤3000, `.tsig` ≤1500) — after the server already cut at 3000/1500. Every one of those shifts an offset, silently. The quote text is re-found client-side by the same fold-tolerant search Phase 3 already ships. |
| Log the rejected quote in the audit event so rejections are debuggable | The one thing this ADR forbids. A reason code answers the same question with no body text. |
| Add the sidecars to `bin/backup-workspace.sh` | Breaks the entire backup (measured — see Decision §6). |
| Emit evidence from the triage call by adding a field to the schema | It lands in `_ENTITY_PROPS_NULLABLE`, which feeds **both** provider contracts, so it changes verdicts and demands an `EXTRACTOR_VERSION` bump — the corpus-split failure the roadmap already records. |
| A new `/api/evidence/{message_id}` route | A message-keyed route must first join to a thread via `_root_for_message` before it can be scoped, and thread-keyed routes get **no** middleware coverage (only project-shaped paths are gated by literal prefix). Riding on `/api/thread` inherits a gate that is already correct and already tested. |
