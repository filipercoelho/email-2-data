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
   for anyone who has not written their own in «A minha conta» — ADR-047). A playbook change is a
   behavior change — treat it like a code change (test + doc).

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
  **never 8000**). Do not stop the user's containers to free 8042.

All tests must pass before handing a change back.

### Baseline pin

| | |
| --- | --- |
| **Measured** | 2026-07-26, branch `feat/fila-mesa`, commit `3d65d39` + working tree (ADR-041 … **ADR-047**) |
| **Full run** (Chrome present) | **1281 passed, 0 failed** (82.6 s) |
| **Without the browser e2e modules** | **1248 passed** (61.6 s) — measured with `--ignore`, not inferred by subtracting 33 |

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

> **Adoption status (2026-06-10, counts refreshed 2026-07-26):** this project was retrofitted to
> the scaffold standard via `bin/adopt-project.sh`, and the `docs/` 00–09 shelves are now
> **populated and canonical** — **46 ADRs** ([registry](docs/03-decisions/index.md); 47 files, one
> of which is `adr-000-template.md`), the [reference schemas](docs/05-reference/index.md), the
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
