# Approach — v1 (right-sized)

Companion to [draft-architectural-report.md](draft-architectural-report.md). That document is the
long-term, enterprise-grade target. **This document is what we actually build first**, and why it is
deliberately smaller.

## 1. The one question v1 must answer

> Can Claude, given an editable playbook, reliably tell a real client job request from polished
> publicity — and score urgency — on our actual (Portuguese) emails?

Everything else (IMAP polling, storage, dashboard, tasks) is known, boring engineering. The single
unproven risk is classification quality on our real mail. **The architecture's job in v1 is to put
that question in front of us as fast as possible, then get out of the way.**

## 2. Non-negotiables (cheap now, painful to retrofit)

| Rule | Why |
| --- | --- |
| **Read-only IMAP**: `select(readonly=True)` (EXAMINE) **and** fetch only with `BODY.PEEK[]` — never `RFC822`/`BODY[]`, never STORE/DELETE/EXPUNGE/APPEND/COPY | It is the live business mailbox. `RFC822`/`BODY[]` implicitly set `\Seen`, and read-only EXAMINE is only a *server-side* promise — some shared hosts persist it anyway. PEEK is the client-side guarantee. Mutating real client mail is the one unrecoverable mistake. |
| **Bias against `IGNORE`** | A false "publicity" = a lost client. A false "needs attention" costs 5 seconds. `IGNORE` requires high confidence (`ignore_confidence_floor`); below it → `NEEDS_REVIEW`. |
| **Structured output with a `reason` field** | You cannot tune a playbook you cannot debug. Every verdict explains itself. |
| **Playbook is an editable file, separate from code** | The tuning loop *is* the product. |
| **Secrets via env vars, never in code or git; raw bodies never logged** | Trivial now, embarrassing later. `settings.json` is gitignored; passwords/keys come from env. |
| **`extractor_version` stamped on every verdict** | Lets us re-run an improved playbook over the same corpus and compare. |

**Where the data actually lives (the real privacy boundary).** "No bodies in logs" is necessary but
not the whole story: `out/results.jsonl` deliberately holds subjects, sender addresses, and extracted
client name/email/amounts — that *is* personal data (GDPR; EU business). The boundary is therefore
"everything under `corpus/`, `out/`, `labels/` is local-only and gitignored." The **audit log
(`out/audit.jsonl`) carries counts/ids/timings only — no subjects, no bodies, no addresses.**

## 3. Postponed without guilt (YAGNI for one business, few inboxes, local-first)

Multi-tenancy · event store / append-only log / outbox · Redis/NATS/Kafka · OpenSearch · per-tenant
KMS / field-level encryption · provider-agnostic connector interface (we have exactly one provider:
IMAP) · perfect signature/quote stripping · the web dashboard *as a gate* (see milestones) ·
external calendar sync.

## 4. Testing strategy — a fixed evaluation corpus

The highest-leverage investment is not code:

1. **Decouple fetch from classify.** Fetch real emails once → store as `.eml` in `corpus/`. Iterate
   on the playbook **offline** (fast, repeatable, no IMAP, deterministic input).
2. **Hand-label ~40 of them** (domain expert, one afternoon) → `labels/labels.csv`. Now playbook
   tuning has a *score*, not vibes.
3. **Loop:** `classify` → `eval` against labels → edit playbook → re-run. No infrastructure touched.

That labeled corpus is worth more than any framework choice. It turns "does the AI work?" from an
argument into a number.

> **Determinism caveat.** We call the model at `temperature=0` for stability, but LLM tool calls are
> not perfectly reproducible. Treat a ~1–2 point accuracy wobble between identical runs as noise, not
> a playbook regression — only act on larger, repeated moves.

### Eval metric (defined precisely, so the gate isn't fudged)

`eval` joins `out/results.jsonl` to `labels/labels.csv` on the canonical `message_id` and **reports
unmatched rows on both sides loudly** (a silent inner-join would hide a degraded run). It also prints
how many corpus emails produced no verdict (API/parse failures) so a partial run can't masquerade as
a clean score. Headline numbers:

* **CLIENT_JOB_REQUEST recall** — of emails the human labeled as job requests, what fraction did the
  model also type as such. Missing a real job is the costly error.
* **"Real client binned" rate** — model `priority == IGNORE` while the human type is
  `CLIENT_JOB_REQUEST` or `QUOTE_FOLLOWUP`. Target: zero.
* **Review-queue volume** — fraction of verdicts that came back `NEEDS_REVIEW`. Counted as
  "not ignored" (safe), but too many defeats the purpose, so it's tracked separately.

Labels use only the three actionable priorities (`HIGH`/`MEDIUM`/`IGNORE`); `NEEDS_REVIEW` is a
model-only routing state, never a ground-truth label.

## 5. Milestones (each demoable; M1 is the go/no-go gate)

- **M0 — read-only fetch.** Pull N recent emails → `corpus/*.eml`. Proves credentials + connectivity.
- **M1 — classify + eval (THE GATE).** Run the playbook over `corpus/` → `out/results.jsonl` +
  a table; score against labels. Validates the whole premise before any UI.
- **M2 — persist + dashboard.** SQLite + stateful poller (UID watermark); FastAPI page sorted by urgency.
- **M3 — tasks/reminders.** Spawn actionable items from high-priority verdicts. Built *last*, only
  once classification is trusted.
- **Later.** Calendar sync, more inboxes, Synology hosting, extra notification channels.

## 6. Data flow (v1 = M0 + M1)

```
Lindo IMAP (read-only)
   │  fetch.py  (EXAMINE, SINCE filter, dedupe by Message-ID)
   ▼
corpus/<safe-id>.eml          # immutable local cache — re-run classify offline
   │  envelope.py  (MIME decode, HTML→text, header extract)
   ▼
EmailEnvelope (dict)          # trimmed version of the draft's envelope.v1
   │  classifier.py  (Claude + triage_playbook.md, forced structured output)
   ▼
out/results.jsonl             # one TriageResult per email (type, priority, urgency, confidence, reason, entities)
   │  cli.py eval  vs labels/labels.csv
   ▼
accuracy number + confusion table
```

## 7. File map

```
config/
  settings.example.json     # template; copy → settings.json (gitignored)
  triage_playbook.md        # the editable classifier brain
src/email2data/
  config.py                 # load settings + resolve secrets from env
  audit.py                  # append-only JSONL audit (no bodies)
  schema.py                 # TriageResult + Claude tool/output JSON schema
  fetch.py                  # M0: read-only IMAP → .eml
  envelope.py               # .eml → EmailEnvelope
  classifier.py             # M1: envelope + playbook → TriageResult
  cli.py                    # fetch | classify | eval
corpus/  labels/  out/      # local data (gitignored except templates)
tests/
```
