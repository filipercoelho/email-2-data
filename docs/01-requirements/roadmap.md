# Roadmap — email-2-data

Companion to [VISION.md](../../VISION.md) and [approach.md](../02-architecture/approach.md).
Status: ✅ done · 🔄 in progress · ⬜ planned (scaffolded = contracts exist, bodies not implemented).

> **Status audit — 2026-07-26 (code evidence, not memory).** This file had drifted: Phase 6 was still
> marked ⬜ although the cockpit shipped, Phase 5's context-cache lever was marked unbuilt although it is
> live in [`llm.py`](../../src/email2data/llm.py), and several `design/*.md` links pointed at paths that
> moved to the `docs/` shelves. Every status below was re-checked against `src/` and the live stores;
> where a phase is partly built, **which half** is now stated. The 2026-05-29 review section is kept as a
> dated historical record — read it as "what we believed then", not as current status.
>
> **Two things to know before reading.** (1) The sections are **not in numeric order** — Phase 7 sits
> between 4 and 5 (0 · 1 · 2 · 3 · 4 · **7** · 5 · 6), because 7 was inserted where it was being worked
> on. Left as-is rather than reordered, so `git blame` on each phase survives; use this list to navigate.
> (2) **Phase numbers stopped tracking execution order** some time ago — Phase 6 finished before 3, 4 and
> 5 were started. Read the ✅/🔄/⬜ marks, never the numbering, for what is done.

---

## Review — 2026-05-29 (what testing on the full inbox changed)

From hand-labeling and the 265-email runs, and how they reshape the plan:

- **The counterparty/purpose/direction model is the real target — and it's validated** (95% counterparty,
  100% CLIENT recall, 0 clients-binned on a hard sample). But the *shipped* classifier still emits the
  older single-axis `type` taxonomy. **Migrating classifier + structured schemas + `eval` + playbook to
  counterparty/purpose/direction is now the top Phase-1 item** — it wasn't previously called out.
- **Taxonomy grew from real mail:** added `LEAD`, `OUTBOUND_INVOICE`, `LOW`; counterparty is Lindo-POV;
  priority is partly **dynamic** (an awaited outbound request starts LOW, escalates with days-without-reply).
- **Reliability gap:** 3/265 proposals were empty responses that **recovered on retry** — the SDK's
  `max_retries` doesn't catch a 200-with-empty-text. Needs an explicit **retry-on-empty** (small, do soon).
- **Cost reality:** this testing was **100% light LLM, 0% offline, 0 heavy**, and ~300 calls were *wasted*
  re-running before parse bugs were fixed. Two cheap, high-leverage moves: **run the deterministic
  parse/signals stage before any LLM call**, and **cache the playbook** (re-sent on every call — the single
  biggest token lever; can land before the full cascade). *(2026-07-26: **both landed** — the offline
  stage in Phase 2, the playbook cache in `llm.py` per Phase 5. The prediction that caching could land
  before the cascade was right; it landed, and the cascade still hasn't.)*
- **Bulk is the most reliable IGNORE lever** (header `List-Unsubscribe`; ~20% flagged deterministically,
  100% precise) — confirmed as Tier 0's anchor. Domain never decides counterparty; the body does.

---

## Phase 0 — Foundation ✅

**Goal:** prove the pipeline on real mail, read-only, cheaply.

**Delivered:**

- Read-only IMAP fetch → local `.eml` corpus (M0); `BODY.PEEK`, per-message dedupe by canonical id.
- MIME / HTML→text envelope, hardened against malformed real-world headers (265 real emails).
- Single-pass classifier: forced structured output + code-enforced anti-`IGNORE` guardrail.
- `eval` CLI: loud join vs labels, client-recall & real-clients-binned metrics.
- Pluggable provider: **Vertex Gemini** (gcloud ADC) or Anthropic. 17 tests.

**Exit (met):** 265 real emails classified end-to-end, 0 failures.

**Known debt (near-term, small):** retry-on-empty in the classifier; always run the free parse/signals
stage *before* spending an LLM call.

## Phase 1 — Baseline + taxonomy migration ✅

**Goal:** a regressable number AND the shipped model = the validated model.

- ✅ **Baseline** on a stratified 43-email sample: counterparty 95%, CLIENT recall 100%, binned 0.
  Tooling: `design/labelsheet.py`; labels in `labels/worksheet.csv`.
- ✅ **Migrated** classifier + structured-output schemas (Gemini + Anthropic) + `eval` from single-axis
  `type` → **counterparty / purpose / direction**. Priority is now derived (`schema.derive_priority`),
  direction set deterministically from signals, the model emits only counterparty/purpose/urgency/etc.
- ✅ **Playbook v2** — framing rules (Lindo-POV CLIENT/SUPPLIER, `LEAD`, `OUTBOUND_INVOICE`,
  forwarded-original, "decide by body not domain") + worked examples. *(Examples are illustrative;
  swapping in real anonymized error-driven exemplars is a follow-up.)*
- ✅ **retry-on-empty** in the classifier (covers the transient empty-response the SDK retry misses).

**Functional result (43-label set, migrated cascade):** counterparty **86%**, priority **81%**,
CLIENT/LEAD recall **89%**, **real-clients-binned 0**. (The earlier "95%" was self-agreement — labels
were pre-filled from the model's own proposals — so 86% vs corrected labels is the real, harder
number. The residual misses are Tier-1 playbook nuance: internal-sender invoices to clients, system
notices → addressed by real error-driven exemplars, a Phase-1 follow-up.)

## Phase 2 — Tier-0 signals & gazetteer (lean) ✅

**Goal:** decide the easy/known mail offline, for free; hand the LLM *facts*, not guesses.

- ✅ `signals.py` — direction (internal/inbound), bulk (`List-*`/`Feedback-ID`/`Precedence`),
  automated (Auto-Submitted/no-reply, a *feature* not a bin), looks-forwarded (flag only).
- ✅ `extract.py` — **deterministic structured values, as priors not verdicts** (Idea 2, kept). `nif`
  (mod-11 checksum) + `iban` are *authoritative* (code fills the entity); `amounts`/`dates`/`docs` are
  *candidates* attached to the prompt — the model picks which amount is the price / date is the deadline.
  Cannot bin (headers only). *Deferred:* phone, IBAN mod-97. (`EXTRACTOR_VERSION` → v3; playbook → v3.)
- ❌ `lexicon.py` (Idea 1: PT-PT keyword priors + `is_reply` thread flag) — **built, live-A/B-tested,
  then dropped.** A full 265-email run with the lexicon in-prompt showed **no measurable classification
  lift** (counterparty 79%→81%, within run-to-run noise; priority 81%→74%) and a *materialized harm* —
  the `lead`/`product` false-friends demoted a real client's orçamento to LEAD. The only clear flips
  (BULK→SUPPLIER) were attributable to the **gazetteer**, not the lexicon. Decision: keep Idea 2, drop
  Idea 1. Confirms the red-team: at this scale lexical priors don't move classification; relational
  (gazetteer) + structured-value signals do.
- ✅ `store.py` (lean) — hand-curated **gazetteer** in SQLite (`config/gazetteer.csv` = source of truth
  — ⚠ **but that CSV is currently missing and the store is running off a frozen 15-row snapshot**; see
  [data-stores.md § knowledge.db is live but unmanaged](../05-reference/data-stores.md));
  a **hint passed to the LLM, never a short-circuit** (body overrides). Hardened: **email-or-domain keys**
  (free-mail senders like `joao@gmail.com` — the gap behind a measured CLIENT miss), **table replaced on
  seed** (no stale rows when a key is removed), **counterparty enum-validated** on load, **multi-label
  TLDs** (`.com.pt`/`.co.uk`) and `www.`/case/trailing-dot normalized in lookup.
- ✅ `cascade.py` — Tier-0 bulk-IGNORE offline → Tier-1 Gemini with facts + extracted values + hint; a
  known CLIENT/LEAD key **vetoes** an offline bin. Each verdict tags `decided_by`.
- **Measured precision fix:** offline IGNORE fires **only on true marketing-list signals**; letting
  `automated` bin offline over-binned supplier invoices as BULK (caught by the functional re-score).
- *Deferred per the red-teamed plan (still deferred as of 2026-07-26):* `forwarding.py` banner parsing —
  we only flag + escalate; the module is a scaffold whose one function still
  `raise NotImplementedError("Phase 2")` (`forwarding.py:40`) and **has no callers in `src/`** — plus
  verdict cache, reputation learning, NER/Snorkel/calibration/drift. See
  [offline-extraction-layer.md](../02-architecture/offline-extraction-layer.md).

**Exit (met):** ~30% of the 265-email corpus resolved with **zero LLM calls** (Tier-0); **no client
binned** (the gazetteer veto + the automated≠bulk fix protect transactional supplier mail too).

## Phase 3 — Cost-tiered cascade ⬜ (Tier 2 never built; the triage cascade stops at Tier 1)

**Goal:** spend compute ∝ uncertainty × impact.

**Status, 2026-07-26 — verified in code.** The automatic escalation this phase describes does **not**
exist. [`cascade.py`](../../src/email2data/cascade.py) is Tier 0 → Tier 1 and says so in its own module
docstring (`cascade.py:6`: *"there is no heavy-LLM Tier 2 yet — ambiguity is handled by Flash"*). There is
no confidence threshold, no rule/LLM-disagreement check, and no escalation path to a heavier model in the
triage path. The only Tier-1 fallback is `_tier1_failed` (`cascade.py:53`), which writes `NEEDS_REVIEW`
when the call *raises* — that is an error escalation to a **human**, not to a bigger model.

- ⬜ `cascade.py` — Tier 0 (offline) → Tier 1 (Flash / Flash-Lite) → **Tier 2 (Pro / Claude): not built.**
- ⬜ Escalation when: low confidence, **or** high-impact + medium confidence, **or** rule/LLM disagree.
  None of the three escalates to a *model*. Confidence **is** branched on — twice — but both branches
  escalate to a **human**: the anti-IGNORE guardrail refuses a low-confidence bin (`classifier.py:70`,
  `ignore_confidence_floor` 0.85) and `para_ti.low_confidence_items` (`para_ti.py:64`) routes below-floor
  verdicts into the «rever classificação» lens. That is tenet 2 working as designed; it is not Tier 2.
- ✅ Provenance: each verdict tags the deciding tier + version (`decided_by`, e.g. `tier0:bulk`,
  `tier1:<model>` — `cascade.py:49,115`). This half shipped and is ADR-008.
- ⬜ **Target ~40% offline / ~50% light / ~10% heavy** — unmeasurable while heavy is 0% by construction.

**What exists instead — a *human*-driven model tier, not an automatic one.** `llm.with_tier`
([`llm.py:25`](../../src/email2data/llm.py)) + `llm.tiers` in `config/settings.json` give three cost tiers
(`light` / `standard` / `heavy`), surfaced in Projetos as **Leve · Normal · Profundo** for "Reprocessar
tudo com IA" (ADR-025/-026) and used by `specdraft.py:76`, `specbuild.py:121` and three `webapp.py` routes.
So the *mechanism* for spending more compute on a hard case is built and live — what is missing is the
cascade **deciding by itself** when to spend it. Anyone completing this phase should build on `with_tier`
rather than adding a second tier concept.

**Exit (unchanged, unmet):** thresholds tuned on eval; tokens-saved-vs-accuracy curve vs the all-Flash
baseline; escalation rate reported.

## Phase 4 — Knowledge reuse & feedback 🔄 (thread state shipped; the *learning* loop did not)

**Goal:** get smarter (and cheaper) with every message.

**Status, 2026-07-26 — verified in code.** This phase split cleanly in two. Everything about **thread and
relationship state** shipped (it became the cockpit's obligation model). Everything about **the system
learning from its own history** — cache, exemplars, reputation, corrections-feeding-back — did not, and
[`store.py:6-7`](../../src/email2data/store.py) still says so in the source: *"The learning loop
(reputation decay, verdict cache, exemplars, thread state) is Phase 4 and intentionally not built yet."*
That comment is now itself half-stale — thread state **is** built.

- ✅ **CRM substrate — no longer a PoC, and no longer off the live loop.** `crm.py` + `email2data crm`
  build the `interactions` event log + `contacts` rollup into `out/crm.db`. The "deferred: wiring it into
  the live triage loop" note is **stale**: `crm.build_crm` is a first-class stage of every sync
  (`sync.py:250`, `do_crm=True` by default) precisely so the Fila never reads a stale rollup, and the
  webapp opens a `CrmStore` per request (`webapp.py:197`). The live `crm.db` is 1.4 MB over a 553-message
  corpus. *Still deferred:* identity resolution (one person, many addresses — `crm.py:16`), the
  person↔person graph, org tables, an `is_automated` contact flag.
- ⬜ Domain reputation as a **prior** — not built. The gazetteer (`store.py`) is hand-curated and static;
  nothing writes back to it, and there is no decay or confirmation mechanism.
- ⬜ Verdict cache (content hash) for repeat / templated mail — not built. `schema.py:24` still refers to
  it in the future tense (the version bump exists so a future cache *would* invalidate correctly).
- ⬜ Exemplar retrieval (embeddings) for few-shot on hard cases — not built. No embedding store, no
  retrieval step; `config/triage_playbook.md` examples are still hand-written and illustrative.
- 🔄 **Human-correction loop — the capture half shipped, the feedback half did not.** Corrections *are*
  first-class and precious: `POST /api/reclassify` → `Workspace.reclassify` (`workspace.py:484`)
  stores the human value **beside** the auto value in `workspace.db`, the Fila renders both with a «↺ auto»
  revert (`para_ti.py:57`), and a correction overrides the live queue in either direction — into it or out
  of it — even after the thread's dominant message shifts (`cockpit.py:553-576`, pinned by four tests in
  `tests/test_cockpit.py:438-463`). `GET /api/reclassifications` (`webapp.py:1156`) exports them and its
  docstring already calls them *"training data"*. **What is missing is the consumer**: nothing reads that
  export. No reputation is updated, no exemplar is added, no playbook line is derived. The loop records;
  it does not learn. (Live count today: 1 correction — so there is also not yet a corpus to learn from.)
- ✅ **Dynamic, thread-aware priority — shipped, in a different shape than planned.** The plan was to
  mutate `priority` over time (LOW escalating with days-without-response). What was built instead leaves
  the per-message `priority` static and puts the time dimension in the **thread** layer, where ADR-029/-033/
  -036 put it: `cockpit.py` folds an obligation state (`WE_OWE` / `TO_PAY` / `AWAITING` / `INFO` /
  `HANDLED`, `cockpit.py:85-100`) and colours + sorts it with an age clock (`_RED_AFTER_H`,
  `_AWAITING_CHASE_H = 3×`, `cockpit.py:113-117`), so an awaited reply goes green → amber as it ages and
  `awaits_chase` (`cockpit.py:477`) hands it back as *our* move. `thread_state` + `thread_snooze` (workspace
  v9, ADR-033 P3) are the precious overlay; a new inbound after `handled_ts` reopens a thread
  (`cockpit.py:358`). **Deliberate deviation, not an omission** — ADR-036 is explicit that the clock only
  colours and sorts, it does not decide the obligation. Per-band SLAs remain a follow-up
  (`cockpit.py:113` → [cockpit-design.md](../05-reference/cockpit-design.md)).

> **⚠ "Shipped" was true of the code and false of the data, until 2026-07-26.** ADR-036's obligation fold
> reads `speech_act`, which only exists from `EXTRACTOR_VERSION` **v5** (`schema.py:25`). The corpus had
> never been re-triaged after the bump: **542 of 562 rows were still stamped v4** and carried no
> `speech_act` at all, so `derive_obligation` fell straight through to `_legacy_obligation`
> (`cockpit.py:301`) for **378 of 383 threads (98.7%)**, and `SUPPLIER_INVOICE` appeared on **0** rows —
> meaning the **TO_PAY band could not fire on any real thread**. The ✅ above was earned by the code and
> pinned by tests that build their own v5 fixtures; nothing pinned the *live* corpus, so the gap was
> invisible from the suite. Closed by `email2data triage --full` + `crm` (562/562 now v5). Measured on
> the real `/api/fila` before → after:
>
> | Fila state | before | after |
> | --- | --- | --- |
> | `WE_OWE` | 56 | **23** |
> | `AWAITING` | 58 | 53 |
> | `TO_PAY` | **0 (band dead)** | **1** |
> | `INFO` | **0 (band dead)** | **29** |
> | threads via `_legacy_obligation` | 378/383 (98.7%) | **172/383 (44.9%)** |
>
> 33 threads had been falsely claiming they owed a reply. The residual 44.9% is **correct** behaviour,
> not leftover staleness: those threads carry only `UNKNOWN`/`FYI` acts, and `derive_obligation` is
> written to fall back rather than invent a move. **The durable fix is not this re-run** — nothing
> prevents the next `EXTRACTOR_VERSION` bump from silently stranding the corpus again, because
> `_processed_ids` (`cascade.py:129`) keys the skip decision on `message_id` alone and never reads the
> version it writes. A version-aware re-triage gate is the open item; it needs an ADR, since re-spending
> Tier-1 tokens contradicts the "idempotent by default" convention in CLAUDE.md.

**Exit:** ⬜ measurable accuracy lift + token drop from reuse (nothing reuses yet) · ✅ corrections persist ·
✅ priority reflects elapsed time on awaited threads (via the thread clock, not the priority field) —
and, since 2026-07-26, on the **live corpus** and not only in test fixtures.

## Phase 7 — Estimation funnel (job-spec → readiness gate) 🔄

**Goal:** turn a LEAD/PO email into a structured, *estimable* job — knowing exactly what's missing.

**Empirical base (full 265-run):** only **12%** of mail is job-relevant (32 emails); **88%** carry an
attachment and **53%** put the spec *in* the attachment, not the body; clients state price 6% of the time.
So the email is a **trigger + partial draft**, not the spec — the system is a *readiness scaffold the
human completes*, not an auto-extractor. (Offline regex suite for dims/qty was **deferred** — body recall
6–34% at n=32 doesn't justify it.)

- ✅ **A — JobSpec + Gate-1 (deterministic):** `jobspec.py` — 14 variables, each `{value, source,
  confirmed}` from a one-place registry; `build_jobspec` reshapes existing signals (entities + attachment +
  counterparty); `readiness()` reports missing/unconfirmed must-haves, **estimable** (= all must-haves
  *confirmed*), **attachment-to-review**, and PT clarifying questions. Demo: 28/32 flag attachment-to-review.
- ✅ **B — tiered LLM spec draft:** `specdraft.py` + editable `config/spec_playbook.md` — drafts the
  semantic fields (item/material/dims/thickness/qty/finish/supplied-by/delivery) on LEAD/PO **only** (~3¢
  per 32). Raised coverage 11–33% → 11–67%; `item` filled on 28/32; returns null (no guessing) for the rest.
  `score_drafts` + `out/spec_labelsheet.csv` are the gold-set scaffold (label is the domain expert's).
- 🔄 **C — clarifying replies:** `replydraft.py` + editable `config/reply_playbook.md` draft an
  acknowledge-and-ask reply grounded in confirmed-vs-missing fields (never invents price/spec;
  **never sends** — copy/paste). Since this was written the **confirmation UI + persisted decisions
  shipped** — that is the Projetos client-email composer (ADR-013 deterministic base → ADR-027 AI polish →
  ADR-031's 8 purposes + verbatim-fact guard) and ADR-028's precious dispositions; see
  [client-email-composer.md](../05-reference/client-email-composer.md). **Still outstanding: per-account
  style** — `config/reply_playbook.md:20` still reads *"per-account style comes with the workspace"*, and
  the workspace came without it; tone is one global block. Confirmation friction remains the #1 adoption
  risk.
- ⬜ **D — estimate (Gate 2: margin + deadline feasibility) — never built, and arguably shouldn't be.**
  Verified: there is no cost model, no capacity model and no margin calculation anywhere in `src/` — a
  search for margin/pricing logic returns only CSS `margin:` rules. The boundary held instead at
  "human-ready spec": `export.py:156-167` gates on **Gate-1 `estimable`** (all must-haves *confirmed*) and
  ADR-011 makes it doctrine — export sends the job shell, **never costed lines**. The open decision this
  phase flagged ("consider stopping at human-ready spec rather than auto-pricing") has effectively been
  taken by default. Anyone reviving D should write an ADR first, because pricing crosses ADR-011's line.
  ⚠️ **Name collision to avoid:** `para_ti.py:102` also says "Gate 2", meaning a Para-ti bucket
  (LEAD/PO threads not yet attached to a project). It is unrelated to this margin gate.

**Exit:** ⬜ a measured spec-draft agreement vs a labeled gold-set (the `out/spec_labelsheet.csv` scaffold
exists; the expert labels do not) · 🔄 jobs route to estimate-or-clarify — they route to *clarify*, and
"estimate" ends at the human-ready spec handoff.

## Phase 5 — Token minimization 🔄 (the big lever landed; the trimming work did not)

**Goal:** lowest tokens/email at constant accuracy.

- ✅ **Context caching of the playbook system prompt — BUILT, live, and pinned.** This phase's "single
  biggest lever" did land, ahead of the cascade exactly as predicted. `llm.py:45-85` keeps a
  `(model, sha256(system)) → CachedContent` registry and creates a Vertex explicit cache from the playbook
  prefix, then passes `cached_content` instead of `system_instruction` (`llm.py:150,164-167`). It is
  **on in the live config** (`config/settings.json:160-162` — `context_cache: true`, `ttl 3600s`,
  `min_chars 4096`) and the real playbook is **14 041 chars**, comfortably over that floor, so it fires in
  production rather than silently no-op'ing. Best-effort by design: caching failure, an expired TTL or a
  sub-floor prefix all fall back to the plain path, and an error evicts the dead entry mid-run
  (`llm.py:177-181`) — a classification can never depend on the cache being up. Because the key includes
  the model, the ADR-025 tiers never collide (`llm.py:33-35`). Pinned by `tests/test_llm.py` (created once
  then reused; below-floor skipped; disable flag; create-failure fallback; expiry eviction). The Anthropic
  path had `cache_control: ephemeral` already (`llm.py:198`); this brought Gemini to parity.
- ⬜ Forward-aware body trimming (strip signatures/quotes/footers; **keep** the forwarded original) — not
  built **for the prompt**, and now deliberately pointed the other way for the UI. Nothing in
  `envelope.py` trims quoted history before the prompt. (`extract.py:74 _dedupe` is de-duplication of
  *extracted values*, not of body text — do not mistake it for this.)
  **Read this before implementing it (2026-08-05).** `envelope.clean_email_body` already strips
  signatures, and it is **frozen**: it is the text `clientdraft.polish_draft` receives, so trimming
  *more* there changes what a client-facing draft may quote. The display side went the opposite way —
  fila-evidence §Phase 2 stopped *deleting* the signature and now returns it via
  `clean_email_body_parts` for the dossier to collapse — because deleting the sender's own name and
  NIF is the same class of act as silently binning a message. So this item is now specifically
  **quoted-history trimming for the prompt**, not signature trimming, and it must not be implemented
  by widening `clean_email_body`.
- ⬜ Near-duplicate dedup — not built. Dedup today is exact-identity only (canonical `message_id`,
  `identity.py`), which is idempotency, not token minimization.

**Exit (unmet):** no target tokens/email has been set or measured. The cache's saving has **not** been
quantified against the all-Flash baseline — it is a verified code path, not a verified number.

## Phase 6 — Delivery ✅ (the core shipped; this entry was stale by many months)

**Goal:** the team triages from the queue, not the mailbox.

**Status, 2026-07-26.** This phase's headline item — the priority dashboard with a review/correct UI — is
the **cockpit**, and it has been the main surface of the app for months while this line still read ⬜. It
is not one page but a set of lenses behind the ADR-039 auth gate: **Início** (`/`, ADR-044) · **Fila /
«Mesa com Foco»** (`/fila`, ADR-029/-033/-034/-036/-037) · **Para ti** (ADR-024/-028) · **Projetos** ·
**Contrapartes**, with deep-linkable URLs (ADR-014), a 30 s freshness poll (ADR-023), dark mode (ADR-035)
and per-person visibility (ADR-045). The **Projetos** workbench is itself seven tabs — *Especificação ·
Origem · Ficheiros · Linha do tempo · Email ao cliente · Descritivo · Registar* — the seventh of which,
**«Ficheiros»** (ADR-052), is the project's whole file list across every attached conversation *and* its
intake captures, content-deduped, each tile naming the email that first carried it. That tab is where
Phase 7's own measurement lands in the UI: **88 % of job-relevant mail carries an attachment and 53 %
put the spec *in* the attachment, not the body** (see Phase 7's empirical base), so "consult the
project's files" is not a convenience next to the spec panel — for half the jobs it *is* the spec.
Reading this file to find out what is left to build was the failure mode; the sub-items are therefore
itemised honestly below.

- ✅ **Priority dashboard (M2): queue sorted by urgency** — built, then *superseded in design*. The Fila
  no longer sorts by a priority scalar: it partitions by **obligation** and lets the clock colour and sort
  within that (ADR-029 → ADR-036). Deliberate, and better than the plan.
- ✅ **Review/correct UI** — built (`/api/reclassify`, the «↺ auto» revert, the «rever classificação»
  lens). ⬜ **"feeds Phase 4"** — it does **not**. See Phase 4: corrections are captured and exported but
  nothing consumes them. This clause is the one part of the sentence still unbuilt.
- ⬜ **Tasks/reminders (M3) spawned from high-priority verdicts** — not built. No task store, no reminder
  scheduler, nothing spawns anything. The nearest shipped behaviour is human-initiated, not verdict-
  spawned: **Adiar** / snooze (`thread_snooze`, workspace v9, ADR-033 P3) wakes a thread on a date **or**
  on new inbound, and the age clock surfaces overdue threads on its own.
- ⬜ **Notifications** — not built. Do not read the Telegram bot as this: `telegram.py` is **inbound
  capture** (ADR-019/-021), long-polling `getUpdates` to *receive* dictation, and it deliberately opens no
  inbound port. Outbound mail exists but is scoped to **exactly one** message — the ADR-042 password-reset
  link — and adding a second kind requires its own ADR.
- ✅ **Multi-inbox** — shipped and in production. `imap.accounts[]` holds **4 configured accounts**
  (orcamentos, luis, filipe, pedro; orcamentos alone spans ~78 mailboxes), and ADR-038 attribution records
  which of our inboxes each message actually reached — **10 distinct addresses** across **948
  `message_scope` rows** in the live `out/sync.db`, because mail also arrives by Cc, alias and forward.
  ADR-045 then made that the basis of per-person visibility.
- ⬜ **Synology hosting** — not done, and the target moved. Deployment is `docker-compose` on the local
  host (CLAUDE.md §"Docker is the only deployment target", 2026-07-20): `email2data` published to
  `127.0.0.1:8042` + `intake-bot`. A LAN bind is opt-in behind the ADR-039 gate with TLS. Anyone reviving
  a NAS target should re-decide it against that posture rather than treat this line as a live plan.
- ✅ **The dossier carries its own evidence, and the thread tells its story** — the five-phase
  [fila-evidence plan](../04-implementation/fila-evidence-and-narrative-phases.md) is now **complete**.
  Phases 1–3 shipped 2026-08-05 (values wrap; the signature is collapsed rather than deleted; clicking a
  ledger value highlights it in the body deterministically). Phases 4–5 shipped **2026-08-06** behind
  [ADR-054](../03-decisions/adr-054-llm-derived-body-fragments-live-in-out-sidecars.md): a separate
  **locate** pass stores the sentence that justifies each extracted value (`out/evidence.jsonl`), and a
  **narrate** pass writes «Evolução da conversa» per multi-message thread (`out/narratives.jsonl`). Both
  are `email2data locate` / `narrate` **and** incremental on every triaging sync. Neither touches the
  triage prompt or schema, so no verdict churns. The measured reason Phase 4 exists at all: the
  deterministic Phase-3 search paints **350 of 790 ledger rows**, and **431 of the 440 it leaves dark are
  absent from the email text in any form** — a search cannot reach them, and a sentence can.

---

**Cross-cutting (every phase):** read-only safety, privacy/retention, observability (per-tier metrics,
confidence drift), schema/version discipline.

**Validation artifacts:** [`design/poc-diagnose.py`](../../design/poc-diagnose.py) (body-aware
counterparty/direction PoC, validated on 154 emails) · [`design/labelsheet.py`](../../design/labelsheet.py)
(Phase-1 baseline labeling + scoring). **These two paths are still correct** — `design/` was emptied of
`.md` reports when the `docs/` shelves became canonical, but it kept the scripts. Only the prose that used
to live there moved: `design/approach.md` → [02-architecture/approach.md](../02-architecture/approach.md),
`design/offline-extraction-plan.md` →
[02-architecture/offline-extraction-layer.md](../02-architecture/offline-extraction-layer.md).
