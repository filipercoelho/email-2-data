# Roadmap — email-2-data

Companion to [VISION.md](VISION.md) and [design/approach.md](design/approach.md).
Status: ✅ done · 🔄 in progress · ⬜ planned (scaffolded = contracts exist, bodies not implemented).

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
  biggest token lever; can land before the full cascade).
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
- ✅ `store.py` (lean) — hand-curated `domain → counterparty` **gazetteer** in SQLite, seeded from
  `config/gazetteer.csv`; a **hint passed to the LLM, never a short-circuit** (body overrides).
- ✅ `cascade.py` — Tier-0 bulk-IGNORE offline → Tier-1 Gemini with facts + hint; a known CLIENT/LEAD
  domain **vetoes** an offline bin. Each verdict tags `decided_by`.
- **Measured precision fix:** offline IGNORE fires **only on true marketing-list signals**; letting
  `automated` bin offline over-binned supplier invoices as BULK (caught by the functional re-score).
- *Deferred per the red-teamed plan:* `forwarding.py` banner parsing (we only flag + escalate),
  verdict cache, reputation learning, NER/Snorkel/calibration/drift — see `design/offline-extraction-plan.md`.

**Exit (met):** ~30% of the 265-email corpus resolved with **zero LLM calls** (Tier-0); **no client
binned** (the gazetteer veto + the automated≠bulk fix protect transactional supplier mail too).

## Phase 3 — Cost-tiered cascade ⬜ (scaffolded)

**Goal:** spend compute ∝ uncertainty × impact.

- `cascade.py` — Tier 0 (offline) → Tier 1 (Flash / Flash-Lite) → Tier 2 (Pro / Claude).
- Escalation when: low confidence, **or** high-impact + medium confidence, **or** rule/LLM disagree.
- Provenance: each verdict tags the deciding tier + version.
- **Target (projected from this session's distribution): ~40% offline / ~50% light / ~10% heavy**, with
  heavy reserved mainly for ambiguous client-vs-supplier calls.

**Exit:** thresholds tuned on eval; tokens-saved-vs-accuracy curve vs this session's all-Flash baseline;
escalation rate reported.

## Phase 4 — Knowledge reuse & feedback ⬜

**Goal:** get smarter (and cheaper) with every message.

- Domain reputation as a **prior** (human-confirmed authoritative; the body always overrides).
- Verdict cache (content hash) for repeat / templated mail.
- Exemplar retrieval (embeddings) for few-shot on hard cases.
- Human-correction loop updates reputation + exemplars.
- **Dynamic, thread-aware priority:** priority is not static. An outbound request we're awaiting a reply on
  **starts LOW and escalates with days-without-response**; thread/relationship state (who owes the next
  reply, how long it's been) is a first-class input. Uses `thread_state` + timers (the draft's SLA/staleness).
  "The relationship between emails is critical."

**Exit:** measurable accuracy lift + token drop from reuse; corrections persist; priority reflects elapsed
time on awaited threads.

## Phase 5 — Token minimization ⬜

**Goal:** lowest tokens/email at constant accuracy.

- **Context caching of the playbook system prompt** — the single biggest lever (it's re-sent on every call);
  **can land early, even before the full cascade.**
- Forward-aware body trimming (strip signatures/quotes/footers; **keep** the forwarded original).
- Near-duplicate dedup.

**Exit:** a target tokens/email, hit without accuracy regression.

## Phase 6 — Delivery ⬜

**Goal:** the team triages from the queue, not the mailbox.

- Priority dashboard (M2): queue sorted by urgency; review/correct UI feeds Phase 4.
- Tasks/reminders (M3) spawned from high-priority verdicts.
- Notifications; multi-inbox; Synology hosting.

---

**Cross-cutting (every phase):** read-only safety, privacy/retention, observability (per-tier metrics,
confidence drift), schema/version discipline.

**Validation artifacts:** `design/poc-diagnose.py` (body-aware counterparty/direction PoC, validated on
154 emails) · `design/labelsheet.py` (Phase-1 baseline labeling + scoring).
