"""Cost-tiered cascade — Phase 3 (SCAFFOLD: contract defined, body stubbed).

Implements the governing principle: spend compute in proportion to uncertainty x impact.

    Tier 0  offline rules + knowledge store      free    -> bulk, known-domain, cached, internal-only
    Tier 1  cheap LLM (Gemini Flash / Flash-Lite) cheap   -> the bulk of real classification
    Tier 2  strong LLM (Gemini Pro / Claude)      costly  -> escalations only

A verdict produced at a tier is accepted unless the ESCALATION PREDICATE fires, in which case we go
up one tier. Each verdict records ``decided_by`` (tier + engine) for audit/replay.
"""

from __future__ import annotations

from typing import Any

from .schema import TriageResult

# Tuned against the eval set in Phase 3; placeholders until then.
TIER1_CONFIDENCE_FLOOR = 0.75
TIER2_IMPACT_CONFIDENCE_FLOOR = 0.90  # higher bar when the call is high-impact (possible client)
HIGH_IMPACT_TYPES = {"CLIENT_JOB_REQUEST", "QUOTE_FOLLOWUP"}


def should_escalate(result: TriageResult, *, offline_hint: str | None = None) -> bool:
    """CONTRACT — escalate to the next tier when ANY holds:
      1. confidence < TIER1_CONFIDENCE_FLOOR, or
      2. high-impact (type in HIGH_IMPACT_TYPES or counterparty CLIENT) and
         confidence < TIER2_IMPACT_CONFIDENCE_FLOOR  (asymmetric: a possible client is worth a Pro
         call even at decent confidence), or
      3. offline_hint disagrees with the LLM verdict (rule vs model conflict).
    Pure function — no I/O."""
    raise NotImplementedError("Phase 3")


def classify_cascaded(
    env: dict[str, Any],
    playbook: str,
    *,
    store: Any,            # KnowledgeStore
    tier1_client: Any,     # cheap LLM client
    tier2_client: Any,     # strong LLM client
    settings: dict[str, Any],
) -> TriageResult:
    """CONTRACT — the routing spine:
      1. Tier 0: compute signals (signals.header_signals + enrich_with_forward); consult store
         (verdict_cache by content hash; sender_reputation as PRIOR). If a confident offline verdict
         exists (bulk -> IGNORE; cached; known domain + unambiguous), return it tagged
         decided_by="tier0:rule". No tokens spent.
      2. Tier 1: classify with the cheap model, GIVEN the Tier-0 signals as facts (direction,
         bulk, forwarded-original counterparty). If not should_escalate -> return (tier1:...).
      3. Tier 2: re-classify with the strong model; return (tier2:...).
    Always: apply the shared anti-IGNORE guardrail (classifier._coerce), persist the verdict to the
    cache, and feed any new high-confidence domain observation back into the store."""
    raise NotImplementedError("Phase 3")
