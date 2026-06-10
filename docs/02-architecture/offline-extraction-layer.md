# Offline High-Precision Context-Extraction Layer — v1 (red-teamed)

Synthesis of three industry-research passes + one adversarial review, applied to email-2-data.

> **What changed v0.1 → v1.** The first draft imported the apparatus of a 100k-email/day pipeline
> (Snorkel, spaCy NER, calibration, conformal prediction, drift/canary, JWZ threading, DMARC) onto a
> **262-email / 3-year inbox with ~43 labels**, where a single Gemini-Flash pass already scores
> 95% counterparty / 100% client recall / 0 clients-binned. The red-team was blunt: that machinery is
> statistically inert at this scale and would be a maintenance monster. **At SMB scale, "best practice"
> means knowing what *not* to build.** This v1 is the minimum viable layer that still protects the one
> sacred decision (don't bin a client) — everything else is deferred with explicit re-trigger conditions.

## 0. Scope & scale reality

The offline layer = the deterministic, free stage before any LLM. **Volume is ~1.7 emails/day; 43
labels is a checklist, not a dataset.** The cheap LLM (Flash) is already accurate and ~free at this
volume. So the offline layer's job is **not** to classify — it's to (a) **safely remove obvious
bulk** with zero LLM cost, and (b) **attach cheap deterministic facts** to the message so the Flash
pass decides better. Everything genuinely ambiguous **escalates by default**.

## 1. Principles that survived the red-team

1. **Precision over recall.** Emit nothing rather than a wrong verdict.
2. **Abstain is the DEFAULT, not the exception.** The offline layer decides only what it can prove
   from headers; everything else passes through to Flash. (v0.1 had this backwards.)
3. **Domain never decides counterparty; the body does.** (Vision Box / corticoenetos lesson.) So the
   offline layer **never** resolves client-vs-supplier — it hands that to Flash with hints attached.
4. **Only header-derived signals may fire IGNORE.** Content/prose never bins anything offline.
5. **Evidence-always.** Every signal carries `(field, value, evidence, source)` for explainability.

## 2. The MVP offline layer — `signals.py`, ~150 lines, no ML, no new heavy deps

```
RAW .eml
  1. parse/normalize  (stdlib email policy=default; cp1252 fallback; charset-normalizer; keep raw+normalized)
  2. bulk/automated IGNORE   ← the ONLY offline verdict
  3. direction      ┐
  4. gazetteer hint ├─ FEATURES attached to the message; never a verdict, never a short-circuit
  5. looks-forwarded┘
  6. → escalate to Flash with (2–5) attached     [default path]
```

1. **Parse/normalize.** stdlib `email` with `policy=email.policy.default` (auto-decodes RFC 2047 —
   fixes the `Header`/encoded-word bugs we hit). Charset fallback: declared → **cp1252** (PT Outlook
   mislabels `iso-8859-1`) → `charset-normalizer` → utf-8 `errors="replace"`; wrap `LookupError`.
   Keep raw + normalized. Dedup by normalized Message-ID.
2. **Bulk / automated IGNORE — the only offline decision.** Fire IGNORE *only* on header signals:
   `List-Unsubscribe`/`List-*`/`Feedback-ID` → bulk; `Auto-Submitted != no` (RFC 3834) /
   `X-Auto-Response-Suppress` → automated; null `Return-Path <>`; `no-reply@` lexicon. This is the
   ~20–25%, empirically-100%-precise lever. Nothing else is eligible to bin.
3. **Direction** (sender domain vs `lindoservico.pt`) — a *feature passed to Flash*, not a verdict. No DMARC.
4. **Gazetteer** (exact-match only) — hand-curated, versioned `domain→counterparty` in SQLite, attached
   as **evidence/hint** to the Flash call. Never short-circuits client-vs-supplier. Encodes the
   irreplaceable business facts (corticoenetos = CLIENT) the shop owns.
5. **"Looks forwarded" flag** — detect a forward/quote banner and **escalate** (don't parse it). Flash
   reads the body anyway; banner-parsing is deferred (see §4).
6. **Default = escalate to Flash** with signals 2–5 attached. Abstaining is fine because Flash is cheap
   and already scores 95/100/0.

## 3. The IGNORE-safety target, stated so it's enforceable

Not "≥0.99 precision" — that's **unmeasurable at n=43** (Wilson CI on a handful of IGNORE positives is
~[0.8, 1.0]; you can't tell 0.99 from 0.90). Restate operationally:

> **Offline IGNORE fires only on rules with zero observed client false-positives across the entire
> labeled corpus, and only header-derived bulk/automated signals are eligible.** The 0.99 figure
> becomes a real release gate *once* label volume can measure it (years out at this cadence).

## 4. Deferred — with explicit re-trigger conditions (don't build until)

| Deferred | Build it when… |
| --- | --- |
| Calibration (Platt/isotonic), conformal prediction, risk-coverage thresholds | ≥ ~300 labels with a held-out split exist |
| Snorkel weak supervision | hand-labeling the corpus is no longer feasible in an afternoon |
| spaCy PT NER | regex + gazetteer demonstrably miss entities that matter |
| Verdict cache (content-hash) | volume produces real near-duplicate repeats (not at 1.7/day) |
| Forwarded-banner parsing (crisp-oss port) | the labels show internal-forwards-of-client-orders are *frequent* (verify the count first) |
| JWZ threading | Phase-4 dynamic priority needs it |
| DMARC/anti-spoof | there's an actual spoofing threat (a known-correspondent SMB inbox isn't one) |
| Drift monitoring / PSI / canary | there's enough traffic for a distribution to drift |

## 5. Sequencing — this is NOT next

The red-team's sharpest point: building an offline gate that feeds a classifier we're **about to
rewrite** is backwards. Correct order:

1. **Ship the Phase-1 taxonomy migration** (classifier + schemas + eval + playbook: `type` →
   counterparty/purpose/direction).
2. **Cache the playbook prompt** (biggest token lever — re-sent every call).
3. **Retry-on-empty** (the 3/265 transient-empty finding).
4. **Then** add this MVP offline layer (steps in §2) — mostly the bulk-IGNORE pre-filter + signal hints.

The MVP here is essentially the already-scaffolded `signals.py`, scoped down — **not a new architecture.**

## 6. Tech: keep / avoid

**Keep:** stdlib `email` (policy=default) · `charset-normalizer` · hand-curated versioned gazetteer ·
SQLite · header bulk/automated lexicon.
**Avoid/defer:** `talon` (breaks on Py 3.11+) · `flanker` (unmaintained) · Snorkel · spaCy · conformal/
calibration libs · drift/canary infra — none earn their keep at 262/43.

## Sources (consolidated)

Extraction/precision: deterministic sieves (MIT CL coref), NER/gazetteer survey (arXiv 2401.10825),
Snorkel (1711.10160), reject-option survey (2107.11277), calibration (sklearn), IAA (κ/α). Email:
Python `email.policy` docs, Mailgun talon + issue #234, crisp-oss/email-forward-parser (PT locales),
JWZ threading, RFC 3834/2369 auto/bulk headers, Gmail Priority Inbox (research.google 36955). Routing/
resilience: Gatekeeper calibrate-then-threshold (2502.19335), FrugalGPT, cross-model disagreement
(2603.25450), conformal (2402.04344), DLQ/idempotency, version-keyed cache (Uber stale-answer).
*Caveat: WebFetch was unavailable; citations are from search results, not full-text — verify the
load-bearing routing papers before any future calibration work.*
