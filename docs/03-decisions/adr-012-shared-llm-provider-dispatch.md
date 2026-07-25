# ADR-012 — One shared LLM plumbing layer with provider dispatch

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-06-10 (back-filled; commit 5c04f42) |

## Context

Several stages call an LLM (triage classifier, job-spec draft, reply draft). Without a shared
layer, each would duplicate provider wiring, retry logic, and auth — and the project would
drift toward being locked to one vendor, contradicting the "not locked to one provider"
non-goal.

## Decision

All LLM access goes through a single plumbing module, `llm.py`, which owns **provider dispatch
and retry-on-empty**. The provider is configurable (`settings.json` `llm.provider`:
`vertex_gemini` (default) or `anthropic`); auth is ADC/service-account for Vertex or
`ANTHROPIC_API_KEY` for Anthropic. Every LLM stage uses this layer rather than calling an SDK
directly.

## Consequences

- Switching or adding a provider is a one-module change; stages are provider-agnostic.
- Retry/empty-response handling is consistent across all LLM calls.
- Trace: `src/email2data/llm.py`; consumers `classifier.py`, `specdraft.py`, `replydraft.py`;
  commit `5c04f42` ("extract shared llm.py — provider dispatch + retry-on-empty").
- **Vertex billing label (2026-07-25).** GCP project `materials-492723` (`llm.vertex_project`) is
  shared with the unrelated `materials-costing` app, which also defaults to `gemini-2.5-flash` —
  so project-level Cloud Monitoring/billing data cannot tell the two apps' spend apart. Every
  Gemini call now sets `labels={"app": "email2data"}` on `GenerateContentConfig`, the one thing
  Vertex lets you filter cost by, so this app's spend can be isolated going forward. Anthropic
  calls are unaffected (no equivalent label in that path). Trace: `llm._gemini`,
  `tests/test_llm.py::test_every_gemini_call_carries_the_app_billing_label`.
