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


def _tier1_failed(raw: bytes, env: dict[str, Any], exc: Exception) -> TriageResult:
    """Fallback verdict when Tier-1 classification raises (e.g. LLM/auth down, after llm.py already
    exhausted its retries). The message is NOT dropped — it ESCALATES to NEEDS_REVIEW so it stays
    visible in the human queue (VISION non-negotiable: an uncertain message escalates, never
    disappears; a credential outage must never make client mail vanish from the Fila). Written as a
    final row, so re-run ``triage --full`` once the LLM is back to reclassify it properly. Direction is
    the one fact we still have offline (header signal); everything else is unknown."""
    try:
        direction = sig.header_signals(email.message_from_bytes(raw)).direction
    except Exception:  # noqa: BLE001 — even the offline signal failed; default to the safe inbound bin
        direction = "inbound"
    return TriageResult(
        message_id=env["message_id"],
        counterparty="OTHER",
        purpose="OTHER",
        direction=direction,
        priority="NEEDS_REVIEW",
        urgency=50,
        confidence=0.0,
        reason=f"tier1 failed ({type(exc).__name__}) — escalated for human review",
        entities=Entities(),
        extractor_version=EXTRACTOR_VERSION,
        subject=env.get("subject", ""),
        from_addr=env.get("from", {}).get("email", ""),
        decided_by="tier1:error",
    )


def triage(raw: bytes, playbook: str, store: KnowledgeStore, client: Any, settings: dict[str, Any]) -> TriageResult:
    env = parse_eml(raw)
    msg = email.message_from_bytes(raw)
    signals = sig.enrich(sig.header_signals(msg), env.get("subject", ""), env.get("body_text", ""))
    # For outbound emails the useful gazetteer hint is the RECIPIENT domain (we're writing to them);
    # the sender is always @lindoservico.pt and will never match a supplier/client entry.
    if signals.direction == "outbound" and env.get("to"):
        first_to = (env["to"][0].get("email") or "") if env["to"] else ""
        to_domain = first_to.rsplit("@", 1)[-1].lower() if "@" in first_to else ""
        hint = store.lookup(first_to) or store.lookup(to_domain) or store.lookup(signals.sender_domain)
    else:
        hint = store.lookup(env.get("from", {}).get("email") or signals.sender_domain)

    # Tier 0: offline IGNORE only for bulk mail from an UNKNOWN domain. ANY gazetteer knowledge
    # (client / supplier / internal) vetoes the header-bin -> escalate to the LLM with the hint.
    # Measured: transactional senders (Amazon order confirmations, invoicing platforms) set
    # List-Unsubscribe too, so a known supplier must never be binned on headers alone.
    if signals.ignorable_offline and hint is None:
        return _offline_ignore(env, signals)

    # Recipient domains: derived ONLY for outbound (Sent folder) so the LLM can identify the external
    # counterparty from the To: field rather than from the @lindoservico.pt sender. For inbound/internal
    # the To: is always @lindoservico.pt and would confuse, not help.
    recipient_domains: list[str] | None = None
    if signals.direction == "outbound":
        recipient_domains = list(dict.fromkeys(
            a.get("email", "").rsplit("@", 1)[-1].lower()
            for a in (env.get("to") or []) + (env.get("cc") or [])
            if "@" in (a.get("email") or "")
        )) or None

    # Tier 1: cheap LLM, given the deterministic facts + the gazetteer hint.
    result = classifier.classify(env, signals, hint, playbook, client, settings,
                                 recipient_domains=recipient_domains)
    result.decided_by = f"tier1:{settings['llm']['model']}"
    return result


def _processed_ids(out_path: Path) -> set[str]:
    """message_ids already in results.jsonl — the source of truth for what triage has classified.

    Keying off the result file (not a side cursor) is self-healing: delete a line and that email is
    reprocessed next run. A message whose Tier-1 classification FAILED is written as a NEEDS_REVIEW
    fallback (it escalates, it never disappears — see ``_tier1_failed``), so reclassify it with
    ``triage --full`` once the LLM is back, or delete its line to retry just that one.
    """
    done: set[str] = set()
    if not out_path.exists():
        return done
    for line in out_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            mid = json.loads(line).get("message_id")
        except json.JSONDecodeError:
            continue
        if mid:
            done.add(mid)
    return done


def triage_corpus(settings: dict[str, Any], store: KnowledgeStore, client: Any | None = None,
                  *, full: bool = False) -> dict[str, int]:
    """Classify the corpus. Incremental by default: only emails whose message_id is not already in
    results.jsonl are processed (and appended), so Tier-1 LLM tokens are spent once per email. The
    offline Tier-0 path is unaffected — already-processed mail is simply skipped before either tier.
    A Tier-1 failure does not drop the message: it is written as a NEEDS_REVIEW fallback (escalate,
    never disappear) and counted in ``failed``. ``full=True`` overwrites results.jsonl and
    reclassifies everything (rebuild escape hatch — also the way to clear NEEDS_REVIEW fallbacks)."""
    p = paths(settings, settings["__settings_path__"])
    playbook = classifier.load_playbook(p["playbook"])
    client = client or classifier.make_client(settings)
    eml_files = sorted(p["corpus_dir"].glob("*.eml"))
    out_path = p["out_dir"] / "results.jsonl"

    done = set() if full else _processed_ids(out_path)
    offline = llm = failed = skipped = new = 0
    mode = "full" if full else "incremental"
    audit.log(p["audit_log"], "triage_started", "corpus",
              {"corpus": len(eml_files), "mode": mode, "already_done": len(done)})

    with out_path.open("w" if full else "a", encoding="utf-8") as out:
        for eml in eml_files:
            # Parse FIRST — it gates already-classified mail AND gives us the message_id needed to
            # escalate (not drop) a message whose Tier-1 classification later fails.
            try:
                raw = eml.read_bytes()
                env_min = parse_eml(raw)
            except Exception as exc:  # noqa: BLE001 — unparseable .eml: nothing coherent to escalate
                failed += 1
                audit.log(p["audit_log"], "triage_failed", eml.name, {"error": type(exc).__name__})
                continue
            mid = env_min.get("message_id")
            if mid in done:  # already classified, or a duplicate .eml within this run
                skipped += 1
                continue
            try:
                r = triage(raw, playbook, store, client, settings)
                if r.decided_by.startswith("tier0"):
                    offline += 1
                else:
                    llm += 1
            except Exception as exc:  # noqa: BLE001 — Tier-1 failed: escalate to NEEDS_REVIEW, never drop
                r = _tier1_failed(raw, env_min, exc)
                failed += 1
                audit.log(p["audit_log"], "triage_failed", eml.name, {"error": type(exc).__name__})
            out.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")
            done.add(r.message_id)  # guard against duplicate .eml files within one run
            new += 1

    counts = {"corpus": len(eml_files), "new": new, "skipped": skipped,
              "offline": offline, "llm": llm, "failed": failed}
    audit.log(p["audit_log"], "triage_done", "corpus", counts)
    return counts
