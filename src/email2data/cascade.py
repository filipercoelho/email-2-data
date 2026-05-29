"""Lean cascade — Phase 2/3 (v1).

  Tier 0 (offline, free): header signals. Bulk/automated mail with no client hint -> IGNORE, no LLM.
  Tier 1 (cheap LLM):     everything else, with the Tier-0 facts + gazetteer hint attached.

Per the red-teamed plan there is no heavy-LLM Tier 2 yet — ambiguity is handled by Flash (already
~95%) and the anti-IGNORE guardrail routes uncertain bins to NEEDS_REVIEW (a human-review signal).
"""

from __future__ import annotations

import email
import json
from pathlib import Path
from typing import Any

from . import audit, classifier, signals as sig
from .config import paths
from .envelope import parse_eml
from .schema import EXTRACTOR_VERSION, Entities, TriageResult
from .store import KnowledgeStore



def build_store(settings: dict[str, Any]) -> KnowledgeStore:
    p = paths(settings, settings["__settings_path__"])
    base = Path(settings["__settings_path__"]).parents[1]
    store = KnowledgeStore(p["out_dir"] / "knowledge.db").connect()
    gaz = base / "config" / "gazetteer.csv"
    if gaz.exists():
        store.seed_gazetteer(gaz)
    return store


def _offline_ignore(env: dict[str, Any], signals: sig.Signals) -> TriageResult:
    return TriageResult(
        message_id=env["message_id"],
        counterparty="BULK",
        purpose="PUBLICITY",
        direction=signals.direction,
        priority="IGNORE",
        urgency=5,
        confidence=1.0,
        reason=f"offline: header signal '{signals.bulk_evidence}'",
        entities=Entities(),
        extractor_version=EXTRACTOR_VERSION,
        subject=env.get("subject", ""),
        from_addr=env.get("from", {}).get("email", ""),
        decided_by=f"tier0:{signals.bulk_evidence or 'bulk'}",
    )


def triage(raw: bytes, playbook: str, store: KnowledgeStore, client: Any, settings: dict[str, Any]) -> TriageResult:
    env = parse_eml(raw)
    msg = email.message_from_bytes(raw)
    signals = sig.enrich(sig.header_signals(msg), env.get("subject", ""), env.get("body_text", ""))
    hint = store.lookup(signals.sender_domain)

    # Tier 0: offline IGNORE only for bulk mail from an UNKNOWN domain. ANY gazetteer knowledge
    # (client / supplier / internal) vetoes the header-bin -> escalate to the LLM with the hint.
    # Measured: transactional senders (Amazon order confirmations, invoicing platforms) set
    # List-Unsubscribe too, so a known supplier must never be binned on headers alone.
    if signals.ignorable_offline and hint is None:
        return _offline_ignore(env, signals)

    # Tier 1: cheap LLM, given the deterministic facts + the gazetteer hint.
    result = classifier.classify(env, signals, hint, playbook, client, settings)
    result.decided_by = f"tier1:{settings['llm']['model']}"
    return result


def triage_corpus(settings: dict[str, Any], store: KnowledgeStore, client: Any | None = None) -> dict[str, int]:
    p = paths(settings, settings["__settings_path__"])
    playbook = classifier.load_playbook(p["playbook"])
    client = client or classifier.make_client(settings)
    eml_files = sorted(p["corpus_dir"].glob("*.eml"))
    out_path = p["out_dir"] / "results.jsonl"
    offline = llm = failed = 0
    audit.log(p["audit_log"], "triage_started", "corpus", {"corpus": len(eml_files)})

    with out_path.open("w", encoding="utf-8") as out:
        for eml in eml_files:
            try:
                r = triage(eml.read_bytes(), playbook, store, client, settings)
                out.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")
                if r.decided_by.startswith("tier0"):
                    offline += 1
                else:
                    llm += 1
            except Exception as exc:  # noqa: BLE001 — isolate per-email failures
                failed += 1
                audit.log(p["audit_log"], "triage_failed", eml.name, {"error": type(exc).__name__})

    counts = {"corpus": len(eml_files), "offline": offline, "llm": llm, "failed": failed}
    audit.log(p["audit_log"], "triage_done", "corpus", counts)
    return counts
