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

## Phase 1 — Baseline + taxonomy migration 🔄

**Goal:** a regressable number AND the shipped model = the validated model.

- ✅ **Baseline** on a stratified 43-email sample: counterparty 95%, priority 95%, CLIENT recall 100%,
  real-clients-binned 0. Tooling: `design/labelsheet.py` (build/score), labels in `labels/worksheet.csv`.
- ⬜ **Migrate classifier + structured-output schemas + `eval` from single-axis `type` →
  counterparty / purpose / direction** (the validated axes; `schema.py` enums already updated). *The big one.*
- ⬜ **Playbook v2:** real anonymized exemplars (error-driven) + the framing rules (Lindo-POV
  CLIENT/SUPPLIER, `LEAD`, `OUTBOUND_INVOICE`, dynamic priority, "decide by body not domain").
- ⬜ **retry-on-empty** fix (hardening debt above).

**Exit:** the shipped pipeline emits counterparty/purpose/direction and scores ≥ baseline; playbook grounded
in real mail.

## Phase 2 — Tier-0 signals & knowledge store ⬜ (scaffolded)

**Goal:** decide the easy/known mail offline, for free; hand the LLM *facts*, not guesses.

- `signals.py` — direction (internal/inbound) + bulk (`List-Unsubscribe`, the anchor IGNORE lever).
- `forwarding.py` — mine the **original external sender/subject** from forwarded/quoted bodies, so an
  internal forward of a client order is attributed to the client, not "internal".
- `store.py` — SQLite knowledge store: `sender_reputation`, `verdict_cache`, `exemplars`, `thread_state`.
- **Structural rule:** Tier 0 runs **before any LLM call** (makes the wasted-calls lesson permanent).

**Exit:** % of corpus resolved with **zero LLM calls**; forwarded-order cases correctly attributed.

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
