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
