# Roadmap — email-2-data

Companion to [VISION.md](VISION.md) and [design/approach.md](design/approach.md).
Status: ✅ done · 🔄 in progress · ⬜ planned (scaffolded = contracts exist, bodies not implemented).

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

## Phase 1 — Ground truth & baseline 🔄

**Goal:** replace eyeballing with a number.
- Hand-label ~40 emails (incl. the ambiguous `corticoenetos`) → `labels/labels.csv`.
- Establish the eval baseline (counterparty/type accuracy, client recall, binned rate).
- Replace the playbook's synthetic examples with **real anonymized ones** (error-driven few-shot).
**Exit:** a baseline score we can regress against; playbook v2 grounded in real mail.

## Phase 2 — Tier-0 signals & knowledge store ⬜ (scaffolded)

**Goal:** decide the easy/known mail offline, for free; hand the LLM *facts*, not guesses.
- `signals.py` — direction (internal/inbound) + bulk (`List-Unsubscribe`). Header-cheap, reliable.
- `forwarding.py` — mine the **original external sender/subject** from forwarded/quoted bodies, so an
  internal forward of a client order is attributed to the client, not to "internal".
- `store.py` — SQLite knowledge store schema: `sender_reputation`, `verdict_cache`, `exemplars`,
  `thread_state`.
**Exit:** % of corpus resolved with **zero LLM calls**; forwarded-order cases correctly attributed.

## Phase 3 — Cost-tiered cascade ⬜ (scaffolded)

**Goal:** spend compute ∝ uncertainty × impact.
- `cascade.py` — Tier 0 (offline) → Tier 1 (Flash / Flash-Lite) → Tier 2 (Pro / Claude).
- Escalation when: low confidence, **or** high-impact + medium confidence, **or** rule/LLM disagree.
- Provenance: each verdict tags the deciding tier + version.
**Exit:** thresholds tuned on eval; a tokens-saved-vs-accuracy curve; escalation rate reported.

## Phase 4 — Knowledge reuse & feedback ⬜

**Goal:** get smarter (and cheaper) with every message.
- Domain reputation as a **prior** (human-confirmed authoritative; the body always overrides).
- Verdict cache (content hash) for repeat / templated mail.
- Exemplar retrieval (embeddings) for few-shot on hard cases.
- Human-correction loop updates reputation + exemplars.
**Exit:** measurable accuracy lift + token drop from reuse; corrections persist across runs.

## Phase 5 — Token minimization ⬜

**Goal:** lowest tokens/email at constant accuracy.
- Forward-aware body trimming (strip signatures/quotes/footers; **keep** the forwarded original).
- Context caching of the playbook system prompt.
- Near-duplicate dedup.
**Exit:** a target tokens/email, hit without accuracy regression.

## Phase 6 — Delivery ⬜

**Goal:** the team triages from the queue, not the mailbox.
- Priority dashboard (M2): queue sorted by urgency; review/correct UI feeds Phase 4.
- Tasks/reminders (M3) spawned from high-priority verdicts.
- Notifications; multi-inbox; Synology hosting.

---

**Cross-cutting (every phase):** read-only safety, privacy/retention, observability (per-tier
metrics, confidence drift), schema/version discipline.

**Validation artifacts:** `design/poc-diagnose.py` (body-aware counterparty/direction PoC, validated
the Phase 2/3 theory on 154 emails).
