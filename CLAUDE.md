# email-2-data

> Lindo Serviço project — type: **data-extraction**. Scaffolded 2026-06-10 from
> `project-scaffolding-roadmap` (standards-v1.1.0).

## Read first — inherited company standards

This project carries a frozen copy of the company standards in **`./standards/`**.
**Read them before doing anything**; they define how we work here, not just this
project. Order:

1. `standards/00-company-context.md` — who we build for, ways of working, PT/EN locale
2. `standards/03-engineering-principles.md` — how to think about the code (zero
   hallucination, strike narrowly, surprise protocol, traceability)
3. `standards/04-always-verify.md` — the checklist to run before declaring anything done
4. `standards/01-hardware-baseline.md` + `standards/02-network-lan.md` — the hardware
   and LAN this runs on (**honor the port table; never guess specs**)
5. `standards/06-tech-stack-defaults.md` — what to reach for unless told otherwise
6. `standards/07-project-taxonomy.md` — this is a **data-extraction** project; load that
   profile's emphasis

The standards are a **frozen snapshot** (standards-v1.1.0). The live source of truth is
`project-scaffolding-roadmap/standards/`. To refresh this copy, run
`bin/sync-standards.sh` from that repo against this directory. **Do not edit
`./standards/` here** — edit it upstream.

## This project specifically

- **Goal:** Read-only email triage for Lindo Serviço inboxes — classify each message by
  **counterparty · purpose · direction · priority** (deterministic pre-filter → Gemini on
  Vertex, driven by an editable playbook) and surface a prioritized decision queue.
- **Profile:** `data-extraction` — see [PROFILE.md](PROFILE.md). The dominant risk here
  is **zero-hallucination**: never "complete" a missing classification from a plausible
  pattern. Every output is FACT (with provenance) / INFERENCE / UNKNOWN.
- **Port:** **8042**, **loopback by default**; a **LAN-only bind is opt-in** behind the
  ADR-039 auth gate (`serve --host 0.0.0.0` + `--tls-cert/--tls-key`; see `bin/make-cert.sh`).
  **"Never public" is unchanged and still firm** — no port-forward, no inbound webhook.
  Claimed in `standards/02-network-lan.md` §C. **NEVER use port 8000** (serve/bind/curl).
- **Deploy target:** local / single-user; Docker image carries no secrets or inbox data
  (`.env` + `config/` bind-mounted read-only). See [README.md](README.md) §Docker.

## Read these first, every session

1. [VISION.md](VISION.md) — the north star and the **governing principle** ("spend compute
   in proportion to `uncertainty × business impact`") plus the 8 tenets. If a change
   contradicts a tenet, stop and flag it.
2. [docs/00-overview.md](docs/00-overview.md) → [docs/02-architecture/module-map.md](docs/02-architecture/module-map.md)
   — what it is, then the modules + data flow.
3. [docs/03-decisions/index.md](docs/03-decisions/index.md) — the **ADR registry**: the
   load-bearing "why" behind every invariant. Read once top-to-bottom.
4. [docs/05-reference/triage-schema.md](docs/05-reference/triage-schema.md) +
   [data-stores.md](docs/05-reference/data-stores.md) — exact vocabularies, `derive_priority`,
   `TriageResult`, the stores. [docs/01-requirements/roadmap.md](docs/01-requirements/roadmap.md)
   — phased status (Phases 0–2 done). Deeper engineering detail:
   [docs/02-architecture/approach.md](docs/02-architecture/approach.md) +
   [offline-extraction-layer.md](docs/02-architecture/offline-extraction-layer.md).
5. The classifier brain, **editable, not code**: [config/triage_playbook.md](config/triage_playbook.md),
   [config/gazetteer.csv](config/gazetteer.csv), [config/spec_playbook.md](config/spec_playbook.md),
   [config/reply_playbook.md](config/reply_playbook.md),
   [config/signature_template.md](config/signature_template.md) (the install-wide reply closing, used
   for anyone who has not written their own in «A minha conta» — ADR-047),
   [config/locate_playbook.md](config/locate_playbook.md) +
   [config/narrative_playbook.md](config/narrative_playbook.md) (ADR-054: the evidence-locate and
   thread-narrative prompts). A playbook change is a behavior change — treat it like a code change
   (test + doc).

## Non-negotiables (from VISION + README — violating any is a defect)

1. **Read-only IMAP, always.** EXAMINE + `BODY.PEEK`; never STORE / DELETE / APPEND. The
   one unrecoverable mistake. Pinned by [tests/test_fetch_safety.py](tests/test_fetch_safety.py).
   **Scope, clarified by ADR-042 (2026-07-26):** this binds the IMAP client and the mailboxes we
   triage. The app now has exactly **one** outbound path — [src/email2data/mailer.py](src/email2data/mailer.py),
   a password-reset link from a dedicated write-only account that is deliberately absent from
   `imap.accounts[]`. It opens no IMAP and never APPENDs to a Sent folder, pinned by
   `tests/test_mailer.py::test_the_mailer_never_touches_imap`. "The app never sends" was never what
   ADR-002 said; adding a second kind of outbound message still needs its own ADR.
2. **Never silently bin a client.** Only deterministic **header signals** may IGNORE mail
   offline; an uncertain message escalates, never disappears. A false IGNORE loses revenue.
3. **The body decides counterparty, not the domain.** Domain is at most a prior. Counterparty
   is from **Lindo's POV** (CLIENT / SUPPLIER / LEAD).
4. **Direction ≠ counterparty.** An internal forward of a client PO is still *about a client*.
5. **Secrets via `.env` / ADC only** — never committed, never logged. **Raw bodies/addresses
   never logged.** Derived results are personal data.
6. **Auth is default-deny.** Every route is gated by middleware with a closed allowlist; a new
   route is protected by existing, not by remembering a decorator. `/healthz` MUST stay public
   (the image HEALTHCHECK probes it, and `intake-bot` waits on that health). A person who
   cannot sign in MUST have a responsible user. Pinned by [tests/test_auth_gate.py](tests/test_auth_gate.py).
7. **The install can never reach zero active administrators** (ADR-041). `/setup` 404s once any
   credential exists, so that state cannot be repaired from the app — only by deleting `auth.db` and
   re-onboarding everyone. Enforced in `workspace.py`, not in a route, so the CLI inherits it.
   And **what the app shows must agree with what it allows**: a locked door is not offered
   (`page(person=…)`; `person=None` is default-deny).
8. **One roster: `people`.** `settings.team` and the legacy `roster` table are a seed folded in once
   (ADR-041 §5). Someone leaves by being **deactivated**, never deleted — `name` is the join key in
   `thread_owners` / `project_owners` / `captures.asserted_by`, so a delete orphans their history
   instead of cascading. Removal is only for names with no history, and purges `auth.db` too.
9. **`workspace.db` is precious** (human decisions + projects) — never auto-rebuilt.
   `crm.db` / `sync.db` are regenerable. See README §Stores & schema.

## Definition of done

This project uses `standards/05-definition-of-done.md` (tiered L0–L4) **plus** the
[PROFILE.md](PROFILE.md) data-extraction must-verify list. On every change that is L2+:

1. **Test.** A regression test that fails before and passes after, in the matching
   `tests/test_<module>.py`. No stub tests, no `pytest.skip`, no "tests would go here".
2. **Docs.** Update every doc the change invalidates **in the same commit** — VISION/README,
   [docs/01-requirements/roadmap.md](docs/01-requirements/roadmap.md), the relevant
   `docs/02-architecture/*.md` or `docs/05-reference/*.md`, or the right `config/*_playbook.md`.
   (`design/` holds only scripts now — there are no `design/*.md` docs to update.) New durable
   facts go on a `docs/` 00–09 shelf (see "Docs" below), not into scratch files.
   **A phase you completed is a doc the change invalidated.** The roadmap sat with Phase 6 marked
   ⬜ for months after the cockpit shipped, and with Phase 5's context cache marked unbuilt while it
   ran in production — so the one file meant to answer "what is left to build" answered it wrong.
   If your change closes or partly closes a roadmap phase, say which half, in the same commit.
3. **QA self-review.** ruff (`ruff check src tests`), edge cases (empty/None/malformed MIME,
   non-ASCII charsets), the non-negotiables above, and idempotency (re-running yields the
   same result). Report findings explicitly.
4. **Redeploy.** If the change touched anything baked into the image (`src/**`, `pyproject.toml`,
   `Dockerfile`, `README.md`) or read at boot (`docker-compose.yml`, `.env`), the work is **not
   done until it is running**: tests green → `docker compose up -d --build` →
   `./bin/check-image-drift.sh` clean. See "The rebuild rule" below for the full table. A change
   that exists only in the working tree is a change nobody is using — never report it as live, and
   never cite `docker compose ps` or `/healthz` as evidence that it is.

**Stop-and-report rule.** If you cannot do all four, or the request is ambiguous about a
classification rule or source of truth, **STOP** and ask in chat (use `AskUserQuestion`).
Never mark a change done with an item hand-waved or deferred to a follow-up that does not exist.

## How to run and verify

```bash
.venv/bin/python -m pytest -q          # full suite (fast)
ruff check src tests                   # lint
docker compose up -d --build           # THE deploy — webapp on http://127.0.0.1:8042 + intake worker
docker compose ps                      # both services: email2data (healthy) + intake-bot
docker compose logs -f email2data      # or: logs -f intake-bot
./bin/backup-workspace.sh              # verified snapshot of the precious DB (before migrations)
email2data gazetteer status            # ADR-005 priors: is config/gazetteer.csv there? (exit 1 = frozen)
email2data gazetteer export            # recover that CSV from the live table (it is gitignored, no 2nd copy)
email2data assets status               # ADR-048: which recurring signature art the funnel OMITS (exit 1 = no register)
email2data locate                      # ADR-054 P4: the sentence justifying each extracted value → out/evidence.jsonl (SPENDS TOKENS)
email2data narrate                     # ADR-054 P5: «Evolução da conversa» per multi-message thread → out/narratives.jsonl (SPENDS TOKENS)
# …both incremental by default, and both also run automatically after every TRIAGING sync (never a
# fetch-only one — that run promises to spend nothing). `--all` discards the gate and re-bills the
# corpus; `--only <id>` is the cheap way back after a failure. ≈$0.68 for a full backfill of both.
email2data auth list                   # people, access, inbox grants (ADR-039)
email2data auth reset --name X         # temporary password; the app funnels until it is changed (ADR-041)
# …day to day, people are managed in the app: Administração → Pessoas (admin-only)
```

### Docker is the only deployment target (2026-07-20)

**Every long-running process for this project runs as a `docker-compose` service — nothing is
deployed any other way.** Two services: `email2data` (the webapp, published to `127.0.0.1:8042`
only) and `intake-bot` (the ADR-019/-021 Telegram worker, no port, `depends_on: email2data healthy`
because it opens `workspace.db` with `migrate=False` and must not migrate behind the webapp).

- **Never run `email2data serve` or `email2data intake-bot` from the host.** The container already
  holds `127.0.0.1:8042`, so a host `serve` **silently loses the bind and logs nothing** — you then
  curl 8042 and verify the *container's* code while believing you are testing your working tree.
  This has already produced one wrong "the change isn't there" conclusion. Two intake-bot pollers on
  one token also fight over Telegram `getUpdates`.
- **To see a source change, rebuild:** `docker compose up -d --build`. Editing `src/` alone changes
  nothing that is running — `src/` is baked into the image; only `config/`, `corpus/`, `out/` and
  `.env` are bind-mounted, so a *playbook* edit is live but a *code* edit is not.

#### The rebuild rule — what changes require what (2026-07-20)

**Any change to something baked into the image is not deployed until you rebuild.** Do not reason
about which category a file is in from memory — use the table, then *verify* with the drift check.

| You changed | Deployed by | Why |
| --- | --- | --- |
| `src/**`, `pyproject.toml`, `Dockerfile`, `README.md` | `docker compose up -d --build` | `COPY`'d into the image at build time |
| `docker-compose.yml`, `.env` | `docker compose up -d` | mounted or compose-level, but read at process boot |
| `config/**` (playbooks, `settings.json`, `gazetteer.csv`), `corpus/**`, `out/**` | nothing — already live | bind-mounted and read per call (no `lru_cache` anywhere in `src/`) |

**Verify, never assume** — this check compares the running container's code against the working tree
and is the only trustworthy answer to "is what I'm looking at current?":

```bash
./bin/check-image-drift.sh      # exit 0 = container matches worktree; exit 1 = STALE, rebuild
```

**Scope of the check: every `COPY`'d file in the image** — `src/**/*.py`, `pyproject.toml`,
`README.md`, `bin/healthcheck.py`. It does **not** and cannot see `docker-compose.yml` or `.env`
(compose-level, not in the image), and it does not check the bind mounts (they are always live). It
covered only Python + `pyproject.toml` until 2026-07-25, which meant a `README.md` edit left the
image stale while this check reported OK — a green result on a partial comparison, which is exactly
the proxy failure this section exists to prevent. **If you add a `COPY` line to the Dockerfile, add
the path to both `find` expressions in the script**, or you reintroduce the blind spot.

**Order of operations is not optional.** Rebuilding deploys code, so the suite gates it — a red
working tree must never reach the container, and `--build` also bounces `intake-bot` (it
`depends_on: email2data healthy`), so a broken image takes the worker down with it:

```bash
.venv/bin/python -m pytest -q && docker compose up -d --build && ./bin/check-image-drift.sh
```

**Never answer "is it up / is the latest UI running?" from `docker compose ps` or `/healthz`.** Both
report the *container* as fine while it serves code from an image built days ago — that is exactly
how a stale UI gets reported as live. `ps` proves a container is running; only the drift check proves
it is running *your* code.

- **To verify against a live server without disturbing the deploy**, either drive the app in-process
  with `TestClient` (see `tests/test_webapp.py`) or serve on a **different free port** (e.g. 8043 —
  **never 8000**). Do not stop the user's containers to free 8042. **Point that second server at
  COPIES of `out/*.db`** — see the next bullet for why; a copy in a scratch dir plus the real
  `corpus/` (read-only) gives you real data with no shared writer.

#### Never write to `out/*.db` from the host while a container has it open (2026-07-30)

`out/` is bind-mounted, and **SQLite WAL is not coherent across that mount on Docker Desktop macOS**.
Writing one row to `out/auth.db` from the host while `email2data` was running produced a real
`PRAGMA integrity_check` failure — *"wrong # of entries in index idx_sessions_person"* — and left
host and container reading **different data from the same file** (28 sessions vs 29; a token the host
could resolve, the container could not). Restarting the container did **not** re-sync its view.
Repaired with `REINDEX` after `docker compose stop`, verified against a backup: every table identical,
credentials untouched — the phantom row was a miscount *through* the broken index, not lost data.

- **Reads are far safer than writes, but the rule is simply: stop the writers first.**
  `docker compose stop email2data intake-bot` → touch the DB → start again. Back the file up first if
  it is precious (`out/auth.db`, `out/workspace.db`).
- **Do not mint a session on the host to curl the container.** That is the exact move that broke it.
  Verify through the container itself (`docker compose exec email2data python -c …`) or against a
  copy on another port.
- `email2data crm` is the *survivable* case — `build_crm` writes a temp file and `os.replace()`s it,
  so no reader sees a half-written DB. But the running webapp keeps the **old inode** until it
  restarts or syncs, and a container running pre-change code will happily rebuild `crm.db` back to
  its own schema underneath you. **Rebuild the image before rebuilding `crm.db`**, not after.

All tests must pass before handing a change back.

### Baseline pin

| | |
| --- | --- |
| **Measured** | 2026-08-06, branch `feat/fila-mesa`, commit `eb998bc` + working tree (fila-evidence **Phases 4–5**, ADR-054, over the Phases 1–3 and ADR-048/-049/-051/-052 work already in the tree) |
| **Full run** (Chrome present) | **1475 passed, 0 failed, 0 skipped** (57.6 s) — at `corpus/` = **1315** files |
| **Without the browser e2e modules** | **1433 passed** — inferred as 1475 − 42, NOT measured |
| **Browser e2e** | **42**, unchanged — 19 `test_para_ti_live_e2e.py` · 19 `test_cockpit_urls_e2e.py` · 4 `test_session_expiry_e2e.py`. Phases 4–5 added no e2e module. |

**The delta from 1403 is +72, counted per module with `--collect-only` and reconciling exactly** —
`test_locate.py` **26** (new module) · `test_narrate.py` **18** (new module) · `test_fila.py`
132→**144** · `test_cockpit_ui.py` 69→**75** · `test_cli.py` 33→**38** · `test_webapp.py` 94→**98** ·
`test_visibility.py` 22→**23**. 26+18+12+6+5+4+1 = 72. **The corpus contributed 0 again**: it grew
1259 → 1315 and `test_attachments.py` held at **55**, its parametrised sets still saturated.
**The pinned 1402 was stale by 1** — the true pre-change baseline, measured rather than read off this
table, was **1403** (`test_fila.py` was 132, not the 131 recorded above). That is the fifth time this
pin has been wrong; re-measure before quoting it.

**69 of the first 70 fail on the before-tree; the one green is green BY DESIGN.** Built the same way
as the rows below — `scratchpad/unbuild054.py` reverse-applies exactly this change's source edits into
a scratch copy of `src/`, every reversal asserting its anchor is present **and** unique. The single
green-on-both-sides is `test_a_tree_with_no_evidence_store_serves_exactly_what_it_served_before`,
which pins that Phase 4 is purely **additive** — a tree with no `evidence.jsonl` must serve byte-for-byte
what it served before. A guard that only starts passing after the change is not guarding anything.

**The last 2 of the 72 are defects the live run found that the suite did not, and both are the reason
this section insists on running the real thing.** Neither was caught by 70 passing tests:

1. `test_a_re_triaged_message_is_located_once_from_its_freshest_line` (`test_locate.py`).
   `results.jsonl` is **append-only** — a re-triage adds a line rather than replacing one — so
   `rebuild_evidence` iterated 806 rows for 763 distinct messages and **paid Gemini twice for 43 of
   them**, writing duplicate sidecar rows whose winner `load_evidence` then picked by file order.
   Visible only because the run reported *806 messages · 1647 quotes* while the file held *763 ids ·
   1543 quotes*. Fixed by folding last-wins, the convention `report.py` and `specbuild.py` already use.
2. `test_a_step_whose_message_has_no_date_renders_no_empty_date_chip` (`test_fila.py`, executes the
   shipped renderer in node). 2 of the 533 live narrative steps cite an interaction whose `date` is
   genuinely `''` in `crm.db`, and the step rendered an empty `<span class="nd">` — which reads as a
   broken render, not as a missing date.

**The behavioural claim was measured twice, and the browser is the one that counts.** Offline, at the
ledger-click unit over the whole corpus: **805 rows, 44% painted before → 87% after** (`deadline`
**0% → 86%**, `action_requested` 20% → 89%). Then driving a real Chrome against the **running
container** over 14 key-rich threads: **58 of 69 ledger clicks (84%) paint, 11 do not, and all 11 say
«sem evidência visível» — none silent.** `deadline` was **14/14**. The 84% and the 87% are different
samples, not a discrepancy; the offline figure is a Python re-implementation of the client matcher and
therefore a **proxy**, which is why the browser number is the one quoted first in any summary.

**The earlier delta from 1364 was +38, counted per module with `--collect-only` and reconciling exactly** —
`test_fila.py` 122→**131** · `test_envelope.py` 30→**37** · `test_cockpit_ui.py` 55→**69** ·
`test_webapp.py` 92→**94** · `test_cockpit_urls_e2e.py` 13→**19**. **The corpus contributed 0**:
`corpus/` grew 1256 → 1259 during the work and `test_attachments.py` held at **55**, its parametrised
sets still saturated — so for once a subtraction would have been safe, and it was still done by name.
(The pre-existing pin of 1358 @ 1116 was stale by 6 before this change started: the tree measured
**1364 @ 1256**, the +6 being ADR-053, unclaimed by whoever landed it.)

**35 of the 39 fail before; the other 4 are green on both sides BY DESIGN.** The "before" tree was
built by reverse-applying exactly this change's source edits into a scratch copy of `src/`
(`scratchpad/unbuild.py`, every reversal asserting its anchor is present **and** unique) and running
the new tests against it with `PYTHONPATH` — **not** against a `HEAD` worktree, which would also
strip ADR-048/-049/-051/-052 and the corpus and so prove nothing about this change. The seven
`test_envelope.py` additions fail there at *import* (`clean_email_body_parts` does not exist).
The four deliberate greens are `test_a_message_with_no_signature_grows_no_toggle`,
`test_ver_original_still_appears_on_a_message_whose_signature_was_kept`,
`test_a_signature_only_message_still_renders_its_content` and — the important one —
`test_the_reply_prompt_never_receives_the_signature_lines`, which pins that
`clean_email_body`'s **LLM-facing** output is unchanged. A guard that only starts passing after the
change is not guarding anything; do not "fix" these into failing.

**The unbuild caught itself being wrong once, which is the argument for the uniqueness asserts.**
A first pass missed the one-line `focusTo` edit, so
`test_changing_thread_drops_the_previous_threads_evidence` read as green-by-design when it was
really an incomplete reversal. If a test you expected to be red comes back green here, suspect the
unbuild before you believe the test.

**The behavioural claims were measured on the live corpus, not just asserted in tests.** Phase 2:
reachable extracted values **37% → 40%** (779 → 847 of 2095), all of it `client_name` (35% → **48%**,
+68) — measured against `body_clean + body_sig`, because `probe_region.py` is blind to this change by
design. Phase 3: driving a real Chrome over 25 real threads, **43 of 83 ledger clicks (52%) paint
evidence and the other 40 say «sem evidência visível» — none silent**.

**Read this entry before you trust any total in this file: the suite count here is DATA-DEPENDENT,
and quoting it without its corpus size is meaningless.** Three full runs inside one hour, with **no
test written between the last two**, read **1328 → 1343 → 1347**. Nothing was flaky.
`test_attachments.py` **parametrises off the real corpus** (ADR-046 measured its band rules against
actual messages), so the ADR-049 backfill growing `corpus/` **981 → 1074 → 1079** moved that module
**41 → 55** on its own, and the periodic container sync keeps moving it. **A total is a function of
`corpus/`, not a constant** — so pin the corpus size beside the number, and subtract the corpus
effect before attributing a single test to a code change.

**ADR-051's own contribution is exactly +8, and it is inside the 1358 above** — the row above
attributes that total to ADR-052, which is only half true: this tree had **two sessions writing it at
once** on 2026-08-03, and `--collect-only` after ADR-051 landed still read **1358**, so whichever run
produced the row, the number quoted there now contains these eight. Counted by name, not subtracted:
`test_cockpit.py` 64→**71** (+7: the live thread's own shape, the act-driven-vs-legacy agreement pin,
the bill that a reply must not discharge, the internal forward that is not an answer, a fresh ask
after our reply, an inbound FYI that still cannot override, and the clock's new
`gap_hours`/`anchored_at_last`) · `test_fila.py` 121→**122** (+1: the timeline chip branching on
`anchored_at_last`). **Neither module parametrises off `corpus/`**, so unlike ADR-052's count these
two are corpus-independent and a subtraction would have been safe here — it was still done by name.

**4 of the 8 fail before; the other 4 are green on both sides BY DESIGN and that is the point.** The
"before" tree was built by reverse-applying exactly the two source edits into a scratch copy of
`src/` (`scratchpad/pre-src`) and running the new tests against it with `PYTHONPATH` — not against a
`HEAD` worktree, which would also strip ADR-048/-049/-052 and prove nothing about this change. The
four reds are `..._our_reply_discharges_an_inbound_ask_even_when_it_reads_as_fyi`,
`..._the_act_driven_fold_agrees_with_the_legacy_fold...`, `..._the_clock_says_whether_the_debt_covers_the_segment...`
and `test_fila.py::..._the_debt_chip_only_speaks_as_debt...`. The four greens
(`..._a_reply_never_discharges_an_inbound_bill`, `..._an_internal_forward_is_not_an_answer_to_the_client`,
`..._a_new_ask_after_our_reply_owes_again`, `..._an_inbound_fyi_still_never_overrides_their_live_ask`)
pin how **narrow** the strike is — a guard that only starts passing after the change is not guarding
anything. Do not "fix" them into failing.

**The behavioural claim was also measured against the live corpus, not just asserted in tests**: 675
threads folded under both the old and the new `derive_obligation`, **6 move** (all `OWE_REPLY` →
`AWAIT_THEM`, all CLIENT), «Precisam de resposta» **36 → 30**, and the active Fila is **185 rows
before and 185 after** — which is the non-negotiable #2 check («never silently bin a client») done by
counting, not by reasoning that nothing should have been dropped.

**ADR-052's own contribution is exactly +22, and it was NOT counted by subtracting totals** — the
warning above makes a total useless for attribution, and the obvious shortcut is wrong twice over: a
`HEAD` worktree has **no `corpus/` at all** (it is gitignored), so `test_attachments.py` collects
**32** there against **55** here and a naive subtraction credits this change with +23 in that module
alone. Counted by **name**, from the diff, minus the concurrent work already claimed below:
`test_attachments.py` **+14** · `test_cockpit_urls_e2e.py` **+3** (browser: 33 → **36**) ·
`test_webapp.py` **+2** · `test_visibility.py` **+2** · `test_para_ti.py` **+1**.

**21 of the 22 were confirmed to fail before, and the 22nd is green on both sides by design.** Not
against a `HEAD` worktree — that would also strip ADR-048/-049 and the corpus, so a red test there
proves nothing about *this* change. Instead a **"before" tree** was built by reverse-applying exactly
these edits into a scratch copy of `src/`, every reversal asserting its anchor is present **and
unique** (a duplicated section comment silently spliced 900 lines the first time, which is why the
uniqueness assert exists), then the new tests were run against it with `PYTHONPATH`. It differs from
the working tree in ADR-052 and nothing else. Script: `scratchpad/unbuild.py`.

The one green on both sides is `test_visibility.py::test_the_files_tab_shows_no_file_from_an_ungranted_thread`,
and that is the point of it: it pins the property that **made** the client-side fold the right design
— `_may_open_project` is ANY-thread, `/api/thread` 404s per root, and there is no server-built project
attachment collection to go around it. It was true before and must stay true; it is stated here rather
than dressed up as a regression caught.

**Two of the 22 are the ones that would have shipped a lie.**
`test_the_merge_keeps_the_chronologically_first_carrier` **executes** `attMerge` in node rather than
grepping it: the merge was first-**block**-wins (`project_threads.added_ts` order) while inheriting
`fold_thread`'s "the first occurrence supplies `from_email`" contract, so the moment a tile names a
sender it names whoever's thread was attached first. And
`test_the_tile_helper_is_never_passed_the_array_index_as_options` both greps for `.map(` + a bare
function reference **and** runs the funnel, because `Array.map` passes `(item, index, array)` — the
bare form hands the tile its **index** as options, falsy for tile 0 and truthy after, breaking **the
Fila and Para Ti, not Projetos**, and rendering while wrong.

**A source comment cost two red runs, in both directions.** `test_fila.py` greps every lens for the
old rootward `?thread=` shape and `test_attachments.py` greps the kit for the bare `.map` form — and
the comments explaining *why not to write them* contained the literals. Both comments now say so
in-line. If you are here because one of those greps fired on prose, that is the guard working.

**ADR-049's own contribution is exactly +21, counted per module with `--collect-only`, and 21/21 fail
against `HEAD`** (verified by running them with `PYTHONPATH` pointed at a pristine `HEAD` worktree,
not assumed): [tests/test_fetch_discovery.py](tests/test_fetch_discovery.py) **20** (new module —
folder discovery, the junk-only exclusion, Trash deliberately kept, the pinned-list escape hatch and
`LIST`-failure fallback, and `fetch_account` end-to-end landing the message from an unconfigured
folder in `corpus/`) · `test_admin_page.py` 17→**18** (the mailbox panel no longer claims the pinned
list is everything fetched).

**`--collect-only` and a full run agree at 1343 only because Chrome is present here.** They diverge
on a machine without it: the three e2e modules `pytest.importorskip` **at import time**, so they are
never collected rather than collected-and-skipped — which is why a collect-only figure taken on a
bare machine reads 1310 and looks like 33 tests went missing. Expect **1310 passed** there, inferred.

**The delta from 1290 is +17, all of it ADR-048, counted per module with `--collect-only` against a
pristine `HEAD` worktree rather than inferred:** `test_attachments.py` 32→**41** (+9: register scope
and the own-domain/subdomain test, the threshold-in-the-measured-gap arithmetic, omission with no
surviving count, index stability across a drop, hash-matching regardless of forwarder, the end-to-end
`/api/thread` omission, per-message chips staying complete, and fail-open with no register) ·
`test_crm.py` 7→**11** (+4: threads-not-messages, external senders excluded, the register rebuilt
whole, and a pre-v6 DB degrading instead of raising) · `test_cli.py` 29→**33** (+4: `assets status`
exiting 1 with no register and with a v5 DB, plus the omitted and the kept sides of the threshold).
The browser-e2e count is **unchanged at 33** — no e2e module was touched.

**All 17 were confirmed to fail against `HEAD`** by running them with `PYTHONPATH` pointed at a
`HEAD` worktree, not by assuming: 13 in `test_attachments.py` + `test_crm.py` and 4 in
`test_cli.py`, every one of them red there and green here.

**The load-bearing one is `test_omitting_an_item_does_not_shift_the_indices_the_chips_address`.**
The per-message 📎 chips are positional against `/api/attachment/{message_id}/{index}`, so dropping a
part anywhere before that counter increments repoints every later link at the **wrong file under the
right name** — which looks perfect on screen and is invisible to every other test in the suite.

**+1 more is the «Rever classificação» count** (`test_fila.py` 121→**122**, net: one added, one rewritten).
`_needs_review_count` counted NEEDS_REVIEW-priority interactions while the group its chip opens lists
rows under the confidence floor — **disjoint** populations, reproduced against `HEAD` as *"the rail
promises 2 but «Classificações a rever» holds 1"*. It now counts `para_ti.low_confidence_items` over
the caller's already-visible queue, so chip and destination share one builder and one ADR-045 scoping.
The new test **compares the two** rather than asserting a literal, so they cannot drift apart again.
`test_api_fila_carries_freshness_badges_and_needs_review` changed meaning (its NEEDS_REVIEW row is
confident, so the honest answer is now 0) — a rewrite, not a regression.

**+2 of the 1289 are the locked-door fix**, both in `test_auth_gate.py` (78→**80**): a member is offered
no `/admin` or `/inbox` door on any of the seven signed-in pages, and an admin still is. Both fail
against `HEAD`. `IS_ADMIN` is now injected by `cockpit_ui.page()` for **every** lens, so a new palette
entry is gated by asking it rather than by remembering to thread a parameter through.
**Read what that test actually asserts before trusting it:** the palettes are JS, so the entry ships as
source either way and a grep cannot separate "offered" from "offered only to admins". It pins the two
halves that make the gate hold (every door construction sits behind an `IS_ADMIN` guard; `IS_ADMIN` is
correct for who is asking) — a source-shape assertion, said plainly rather than dressed up. The
**rendered** behaviour was confirmed once in real Chrome against a live app: admin `/fila` palette 18
items incl. «Abrir inbox», member 17 without it; admin `/projetos` 10 incl. «Admin», member 9 without.

**The delta from 1281 is +6, all of it the ADR-044 cross-page deep-link repair, counted per module with
`--collect-only` against a pristine `HEAD` worktree rather than inferred:** `test_para_ti.py` 17→**19**
(+2: «Ver na Fila» targets `/fila?thread=`, and the Message-ID survives URL-encoding) · `test_auth_gate.py`
76→**78** (+2: `?next=` carries the query string so a signed-out deep link completes, and a query cannot
smuggle an off-site `next`) · `test_fila.py` 118→**121** (+3: the Para-ti «ver na fila →» anchor, the
«Rever classificação» chip landing on the group it names, and the browser-e2e count is unchanged at 33
because `test_cockpit_urls_e2e.py` was **edited**, not added). Every behavioural claim was confirmed to
**fail before and pass after** by running the new tests against `HEAD` source via `PYTHONPATH` — 6 of the
7 fail there. The 7th (`test_a_query_string_cannot_smuggle_an_off_site_next`) passes on both **by design**:
it re-pins an open-redirect guard that was already correct, against the wider input the fix now feeds it.

**One test that was GREEN before and after is the reason this shipped at all.**
`test_cockpit_urls_e2e.py` waited on `location.pathname=='/' && search.includes('thread=')` — still true
after ADR-044 moved the Fila, while the user landed on Início and lost the thread. It now asserts
`/fila` **and** that a row actually mounted (`.row.on`). Asserting the URL without asserting what
rendered is the proxy failure this file warns about, caught here in its own suite.

**The delta from 1188 is +93, and only 75 of it is ADR-047.** Counted per module with `--collect-only`,
not inferred: **`tests/test_signature.py` 32** (new module) · `test_people.py` **+10** (the v12 columns,
`set_person_profile`, and a v11→v12 migration on a DB with rows) · `test_fila.py` **+5** (the `mailto:`
hand-off, the `Re:` non-stacking, and the deterministic draft closing) · `test_cockpit_ui.py` **+5**
(«A minha assinatura», incl. a guard that its form does not become a second login form for a password
manager) · `test_auth_gate.py` **+5** (the new route is default-denied, refuses an unfillable token,
cannot rewrite the ADR-042 reset address, and survives the forced-change funnel) · `test_webapp.py`
**+1** (the reply memo holds the UNSIGNED body). **A second round added 16 more** for the pasted-HTML
signature — `test_signature.py` **+10**, `test_people.py` **+3**, `test_auth_gate.py` **+2**,
`test_cockpit_ui.py` **+1** — plus **+1 browser e2e** (`test_cockpit_urls_e2e.py`, so the three-module e2e count is now **33**, not 32) that EXECUTES the `Re:` regex instead of grepping for it — a case found by **looking at the rendered page**, not by a test: a real
signature is copied out of Outlook, so it arrives as an HTML table and was being stored verbatim into
a client-facing draft. **The remaining +18 is a concurrent session's** — this
tree moved *between two runs minutes apart* (1214 → 1232) with no test of mine added in between, which
is the honest reason to trust only the attributed part. Every behavioural claim was confirmed to **fail
before and pass after** by reverting that one change alone and re-running: removing the strip (4 fail),
signing before the memo instead of after (2 fail), and dropping the «Abrir no mail» button (1 fail).

**That remaining +18 is the gazetteer-management fix** (2026-07-26), now claimed rather than left
floating. Counted per module with `--collect-only`, not inferred: `test_store.py` 5→**12** (+7: the
export↔seed round-trip, quoting of notes containing commas/quotes, the comment-preamble stays seedable,
and the warn/quiet split) · `test_cascade.py` 3→**7** (+4: `build_store` warns instead of silently
serving frozen priors, stays quiet on a first run, `open_store` does not seed) · `test_cli.py` 22→**29**
(+7: `gazetteer status` exits 1 while frozen, never prints the keys, and the two `export` refusals).
The behavioural claim was confirmed by reverting the fix alone — restoring the bare `if gaz.exists()`
in `build_store` fails `test_build_store_warns_instead_of_silently_serving_frozen_priors` on
`assert 'MISSING' in ''`, which is precisely the silence that hid the defect for three days.

**Attribution of the earlier delta, stated honestly.** Two sessions were writing this tree at the same time on
2026-07-26, so the totals above are NOT all one change and nobody should read them as one. What can be
counted exactly, per module, is the ADR-042 + ADR-045 work: **101 tests** — `test_mailer.py` **18**,
`test_password_recovery.py` **24**, `test_visibility.py` **20**, plus appended `test_auth.py` **7**,
`test_people.py` **17** (11 functions, one parametrised ×7), `test_cli.py` **9**, `test_auth_gate.py`
**5**, `test_admin_page.py` **1**. The remainder (`test_home_page.py`, `test_attachments.py`, and
additions to existing modules) belongs to the concurrent ADR-043/-044/-046 work and is not mine to
claim. **Of that remainder, ADR-046 (the attachment funnel) is exactly 32**, all in the new
[tests/test_attachments.py](tests/test_attachments.py) — counted with `--collect-only`, not inferred:
11 band-rule (incl. the parametrised content/signature sets measured off the corpus), 3 content-hash
dedup, 2 index-stability (one of which refetches bytes through `attachment_part` and compares sha256),
4 endpoint/thread-API, 3 shared-kit source checks, and 2 guards against a future density-based
demotion. It contributes **0** to the browser-e2e count. Every one of the four behavioural fixes was
confirmed to **fail before and pass after** by reverting that fix alone and re-running — the postcard
arm, the RFC 6266 filename header, the `{ref:path}` route, and the hash-keyed message dedup. **The previous pin of 1020 was itself stale by 34** — the true pre-change baseline, measured by
`--collect-only` rather than read off this table, was **1054**. That is the fourth time this pin has
been wrong; re-measure before quoting it.

The 33 browser e2e checks are opt-in — **19 in
[tests/test_para_ti_live_e2e.py](tests/test_para_ti_live_e2e.py), 10 in
[tests/test_cockpit_urls_e2e.py](tests/test_cockpit_urls_e2e.py), 4 in
[tests/test_session_expiry_e2e.py](tests/test_session_expiry_e2e.py)** — they need the `e2e` extra plus
a Chrome/Chromium and `importorskip` themselves otherwise. **The 1088 is a `--ignore` of those three
modules, not an observed skip count**: this machine has system Chrome, and the fixtures reach it via
`channel: "chrome"`, so `PLAYWRIGHT_BROWSERS_PATH` does not suppress them here. Expect ~1088 passed +
32 skipped on a machine without Chrome — inferred, not measured.

**The delta from the previous pin (1038) is +97, and it is NOT one change.** Two efforts were landing
in this working tree **simultaneously**, so the count is split rather than credited to whoever wrote
the pin last. It also moved *while being measured* (1120 → 1135 across two runs minutes apart, as
ADR-042 tests kept arriving) — which is the honest reason to distrust the total and trust only the
attributed part:

- **+16 is ADR-044 (Início), verified test by test:** 14 in
  [tests/test_home_page.py](tests/test_home_page.py) (new module) · +1
  `test_fila.py::test_inicio_and_its_api_are_built_from_the_same_queue` · +1
  `test_cockpit_ui.py::test_the_logo_is_the_way_back_to_inicio`. `test_home_serves_fila` was
  **renamed**, not added (`test_fila_lives_at_its_own_path_and_no_longer_owns_the_root`), so it
  contributes 0 — and eight `get("/")` call sites across `test_fila.py` / `test_webapp.py` /
  `test_cockpit_urls_e2e.py` moved to `/fila` without changing the count.
- **+81 is ADR-042 (the password-reset mail path)**, authored concurrently in this same tree — chiefly
  [tests/test_mailer.py](tests/test_mailer.py) and
  [tests/test_password_recovery.py](tests/test_password_recovery.py) plus additions across the auth
  modules. **Not verified test by test here**, deliberately: it is not this change's work to account
  for, and guessing a breakdown would be exactly the authoritative-looking wrongness this section
  warns about. Whoever lands ADR-042 should replace this line with a measured one.

**The previous delta from 1020 was +18, all of it ADR-043 (raw 8-bit header decoding):**
+13 [tests/test_headers.py](tests/test_headers.py) (new module) · +4
[tests/test_envelope.py](tests/test_envelope.py) (the envelope-wide decode, storability, and the
malformed encoded-word that used to abort a whole parse) · +1
[tests/test_identity.py](tests/test_identity.py) (an 8-bit `Message-ID` used to raise
`AttributeError`). All 18 fail against the pre-fix code — verified by running them with
`PYTHONPATH` pointed at a `HEAD` worktree, not by assuming.

**The previous delta, from 944 to 1020, was +76, all of it ADR-041 (Phase C completion):**
+13 identity-in-the-shell — 9 in [tests/test_cockpit_ui.py](tests/test_cockpit_ui.py), 4 in
[tests/test_auth_gate.py](tests/test_auth_gate.py) (W3) · +15 «A minha conta» + `auth reset` — 10
`test_auth_gate`, 4 [tests/test_cli.py](tests/test_cli.py), 1 `test_cockpit_ui` (W4) · +33 «Pessoas» —
13 [tests/test_people.py](tests/test_people.py), 14 `test_auth_gate`, 4
[tests/test_admin_page.py](tests/test_admin_page.py), 2 `test_cockpit_ui` (W5) · +11 one-roster — 6
`test_people`, 4 [tests/test_webapp.py](tests/test_webapp.py), 1 `test_auth_gate` (W8) · **+2 for two
defects found by looking at the rendered page, not by a test** — buttons on your own row that the
server always refuses, and a `#fff` label washed out on the dark theme's pale accent · **+2 for a
third the owner found by locking himself out twice** — a change-password form with no
`autocomplete="username"` anchor, which NordPass read as a *create*-password form.

**Treat every number in this file as suspect until you have re-run it.** This pin has been wrong three
times in a row, each time in the direction of looking authoritative: it read *330 passed (2026-06-15)*
while the suite had more than doubled across ADR-030…-037; it was then corrected to *912* (off by one)
with a no-Chrome figure of *903* that counted only one of the two e2e modules. A stale pin is worse
than no pin — it converts "the count moved" from a signal into noise you learn to ignore. If your
change moves either count, **say why explicitly**; if you cannot explain the delta, you have found a
bug, not a rounding error. "Tests pass" is a claim that must be backed by shown output.

## Conventions

- **User-facing strings**: Portuguese (pt-PT) — the landing page (Início, at `/`) and the web UI tabs
  (Fila at `/fila`, Para Ti, Projetos, Contrapartes), reports. Code, comments, commit messages, and
  these docs: English.
- **Commits** small and self-contained; explain *why*, not *what*; reference the test that
  would have caught a bug.
- **Idempotent by default** — `fetch`/`triage`/`sync` never re-spend Tier-1 LLM tokens on
  processed mail. Preserve this when touching the cascade.

## Docs

Canonical knowledge base under **`./docs/`** (00–09 structure, the documentation-gatekeeper
convention). New durable facts go there, not in scratch files: decisions → `03-decisions/`
ADR, exact values/contracts → `05-reference/`, how-to → `04-implementation/`.

> **Adoption status (2026-06-10, counts refreshed 2026-07-30):** this project was retrofitted to
> the scaffold standard via `bin/adopt-project.sh`, and the `docs/` 00–09 shelves are now
> **populated and canonical** — **51 ADRs** ([registry](docs/03-decisions/index.md); 53 files, one
> of which is `adr-000-template.md` and one the index), the [reference schemas](docs/05-reference/index.md), the
> [architecture map](docs/02-architecture/index.md), and QA/ops/requirements shelves. The old
> `design/` reports were migrated onto the shelves (the superseded draft report lives in
> [docs/09-archive/](docs/09-archive/); `design/` now holds only the two validation **scripts**,
> `poc-diagnose.py` + `labelsheet.py`, which are still referenced and still correct). `VISION.md`
> stays at root as the north-star; the `config/*_playbook.md` files stay put as live runtime config.
> `README.md` is a thin front-door pointing here.
>
> **The "one open item" is closed** (2026-07-26). It named the stale `DIRECTION` constant; that was
> fixed and is now pinned: `schema.py:48` lists all three emitted values
> (`["inbound", "internal", "outbound"]`) and
> `tests/test_signals.py:49::test_schema_direction_constant_covers_every_emitted_value` asserts the
> set `header_signals` actually emits is a subset of it — so the constant cannot silently drift
> behind `signals.py` again. [triage-schema.md](docs/05-reference/triage-schema.md) already records
> the fix. **No known docs-vs-code gap is outstanding on this shelf.**
