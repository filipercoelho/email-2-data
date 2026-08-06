"""Phase 5 — «Evolução da conversa»: one narrative pass per thread.

Decision D3 (owner-approved): a **thread-level** call, not a stack or a diff of the per-message
``reason``. The rejected alternative is recorded in the plan with its number — a change-diff over
`purpose`/`speech_act`/`counterparty` does not compress (491 entries for 523 messages), and
rendering per-message verdict flips as history would present model variance as fact, since
``classifier.classify`` sees one envelope with no thread context and ``purpose`` flips on 58% of
adjacent pairs.

Only threads with **two or more messages** qualify (157 of 767, measured 2026-08-06). A single email
has no evolution; describing one would be inventing a story about a message.

Storage is ``out/narratives.jsonl``, keyed by ``thread_root`` (ADR-054 §1), with a content watermark
so a thread is re-narrated when it actually changes and not otherwise.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Optional

from . import audit, classifier, llm
from .config import paths
from .envelope import clean_email_body, parse_eml
from .locate import corpus_resolver, write_sidecar

SIDECAR_NAME = "narratives.jsonl"

MIN_MESSAGES = 2          # a one-message thread has no evolution to describe
MAX_STEPS = 6             # the playbook asks for at most 6; enforced here too, not just requested
BODY_CHARS = 2500         # per message, in the prompt
MAX_TOKENS = 2048
MAX_TEXT_CHARS = 400      # one sentence; a runaway step is dropped, not truncated into nonsense

GEMINI_NARRATIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {"type": "object",
                      "properties": {"message_id": {"type": "string"},
                                     "text": {"type": "string"}}},
        },
        "state": {"type": "string", "nullable": True},
    },
}
NARRATIVE_TOOL = {
    "name": "narrate_thread",
    "description": "Describe how one email conversation evolved, citing the message each beat came from.",
    "input_schema": {
        "type": "object",
        "properties": {
            "steps": {"type": "array",
                      "items": {"type": "object",
                                "properties": {"message_id": {"type": "string"},
                                               "text": {"type": "string"}},
                                "required": ["message_id", "text"]}},
            "state": {"type": ["string", "null"]},
        },
        "required": [],
    },
}


def _sort_key(row: dict[str, Any]) -> tuple[str, str]:
    """Chronological-ish, then stable. ``date`` is an ISO string that KEEPS its original UTC offset
    (12 distinct offsets live in ``crm.db``), so a plain string sort is not truly chronological —
    ``message_id`` is the tiebreak that makes the order deterministic across runs, which is what the
    watermark needs. The narrative's own ordering comes from the model citing messages in order."""
    return (str(row.get("date") or ""), str(row.get("message_id") or ""))


def watermark(rows: list[dict[str, Any]]) -> str:
    """A content hash of everything a narrative is derived from.

    Deliberately NOT ``len(rows)`` + ``last_date``: ``CrmStore.record`` is ``INSERT OR REPLACE`` on
    ``message_id``, so a re-triage can change ``speech_act`` / ``purpose`` / ``counterparty`` /
    ``entities`` — the exact inputs a narrative summarises — while the count and the last date stay
    identical. A count-and-date watermark would freeze a narrative describing verdicts that no longer
    exist.
    """
    h = hashlib.sha256()
    for r in sorted(rows, key=_sort_key):
        for field in ("message_id", "date", "direction", "purpose", "speech_act",
                      "counterparty", "entities", "subject"):
            h.update(str(r.get(field) or "").encode("utf-8"))
            h.update(b"\x1f")
        h.update(b"\x1e")
    return h.hexdigest()


def load_narratives(out_dir: Path) -> dict[str, Any]:
    """``thread_root -> row``. Tolerant for the same reason :func:`locate.load_evidence` is: this
    file is read at ``create_app`` time, before the lifespan, so a raise here would make the app
    unconstructable and the container would crash-loop on a single bad line."""
    out: dict[str, Any] = {}
    p = out_dir / SIDECAR_NAME
    if not p.exists():
        return out
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        if not line.strip():
            continue
        try:
            j = json.loads(line)
        except ValueError:
            continue
        root = j.get("thread_root") if isinstance(j, dict) else None
        if root:
            out[root] = j
    return out


def build_message(rows: list[dict[str, Any]], bodies: dict[str, str],
                  decisions: list[str]) -> tuple[str, dict[str, str]]:
    """``(user_turn, ordinal -> real message_id)``.

    Messages are presented as ``m1..mN``, not by their real ``Message-ID``. Two reasons, both
    practical: a real id is a 60-to-120-char opaque string the model has no reason to reproduce
    faithfully, and an ordinal makes the citation check exact — a step citing anything outside
    ``m1..mN`` is discarded, so a fabricated citation cannot survive.
    """
    ordinals: dict[str, str] = {}
    parts: list[str] = []
    for i, r in enumerate(sorted(rows, key=_sort_key), 1):
        tag = f"m{i}"
        ordinals[tag] = r.get("message_id") or ""
        who = {"inbound": "recebida", "outbound": "enviada por nós",
               "internal": "interna"}.get(r.get("direction") or "", r.get("direction") or "?")
        body = (bodies.get(r.get("message_id") or "") or "").strip()
        parts.append(f"[{tag}] {(r.get('date') or '')[:10]} · {who} · «{r.get('subject') or ''}»\n"
                     + (body[:BODY_CHARS] or "(sem corpo disponível)"))
    dec = ("\n\nDECISÕES HUMANAS JÁ REGISTADAS NESTA CONVERSA:\n"
           + "\n".join(f"  - {d}" for d in decisions)) if decisions else ""
    return ("MENSAGENS DA CONVERSA, POR ORDEM:\n\n" + "\n\n".join(parts) + dec), ordinals


def coerce_steps(got: Any, ordinals: dict[str, str],
                 dates: dict[str, str]) -> tuple[list[dict[str, Any]], Optional[str], int]:
    """``(steps, state, dropped)`` — the model's answer clamped to what can be grounded.

    A step is kept only when it cites an ordinal that was actually sent. The ``date`` is attached
    HERE from the real message row, never taken from the model: a narrative that renders a date the
    model invented is a zero-hallucination violation at the presentation layer, and it would look
    exactly like a correct one.
    """
    if not isinstance(got, dict):
        return [], None, 0
    steps: list[dict[str, Any]] = []
    dropped = 0
    for s in (got.get("steps") or [])[: MAX_STEPS * 3]:
        if not isinstance(s, dict):
            dropped += 1
            continue
        tag = str(s.get("message_id") or "").strip()
        text = str(s.get("text") or "").strip()
        if tag not in ordinals or not text or len(text) > MAX_TEXT_CHARS:
            dropped += 1
            continue
        mid = ordinals[tag]
        steps.append({"message_id": mid, "date": (dates.get(mid) or "")[:10], "text": text})
        if len(steps) >= MAX_STEPS:
            break
    state = got.get("state")
    state = str(state).strip() if state and str(state).strip() else None
    if state and len(state) > MAX_TEXT_CHARS:
        state = None
    return steps, state, dropped


def narrate_thread(root: str, rows: list[dict[str, Any]], bodies: dict[str, str],
                   decisions: list[str], *, client: Any, cfg: dict[str, Any], playbook: str,
                   audit_log: Optional[Path] = None) -> dict[str, Any]:
    """One thread -> one ``narratives.jsonl`` row. Never raises."""
    row: dict[str, Any] = {"thread_root": root, "watermark": watermark(rows),
                           "n_messages": len(rows), "steps": [], "state": None}
    user, ordinals = build_message(rows, bodies, decisions)
    dates = {r.get("message_id") or "": str(r.get("date") or "") for r in rows}
    try:
        got = llm.call(client, cfg, playbook, user, schema=GEMINI_NARRATIVE_SCHEMA,
                       tool=NARRATIVE_TOOL, temperature=0.0)
    except Exception as exc:  # noqa: BLE001 — one bad thread must not sink the pass
        row["error"] = f"{type(exc).__name__}"
        if audit_log is not None:
            # ADR-054: ids/types/counts only. Never a step's text, never a body.
            audit.log(audit_log, "narrate_failed", "narrate",
                      {"thread_root": root[:120], "error": type(exc).__name__, "messages": len(rows)})
        return row
    steps, state, dropped = coerce_steps(got, ordinals, dates)
    row["steps"] = steps
    row["state"] = state
    if dropped:
        row["dropped"] = dropped
    return row


def rebuild_narratives(settings: dict[str, Any], *, incremental: bool = True, client: Any = None,
                       log: Optional[Callable[[str], None]] = None,
                       only: Optional[set[str]] = None,
                       tier: Optional[str] = None,
                       interactions: Optional[list[dict[str, Any]]] = None,
                       decisions_for: Optional[Callable[[str], list[str]]] = None
                       ) -> dict[str, int]:
    """(Re)build ``out/narratives.jsonl``. Returns ``{narrated, kept, steps, failed, total}``.

    The incremental gate is the **watermark**, not mere presence of a row: a thread is re-narrated
    when its content hash moves, which covers both a new message and a re-triage of an old one.
    ``only`` is a set of thread_roots forced to rebuild regardless.

    ``interactions`` / ``decisions_for`` are injectable so the pass is testable without a crm.db.
    """
    p = paths(settings, settings["__settings_path__"])
    out_dir = p["out_dir"]
    base = Path(settings["__settings_path__"]).parents[1]
    counts = {"narrated": 0, "kept": 0, "steps": 0, "failed": 0, "total": 0}

    if interactions is None:
        db = out_dir / "crm.db"
        if not db.exists():
            return counts
        from .crm import CrmStore
        store = CrmStore(db).connect()
        try:
            interactions = store.all_interactions()
        finally:
            store.close()

    by_root: dict[str, list[dict[str, Any]]] = {}
    for i in interactions:
        by_root.setdefault(i.get("thread_root") or i.get("message_id") or "", []).append(i)
    threads = {r: rows for r, rows in by_root.items() if r and len(rows) >= MIN_MESSAGES}

    existing = load_narratives(out_dir)
    todo: list[str] = []
    for root, rows in threads.items():
        prev = existing.get(root)
        stale = prev is None or prev.get("watermark") != watermark(rows)
        if stale or not incremental or (only and root in only):
            todo.append(root)

    if todo and client is None:
        try:
            client = classifier.make_client(settings)
        except Exception as exc:  # noqa: BLE001 — no creds → keep what we have, don't break the sync
            if log:
                log(f"narrate skipped (no LLM client: {type(exc).__name__})")
            todo = []
    playbook = ""
    if todo:
        try:
            playbook = (base / "config" / "narrative_playbook.md").read_text(encoding="utf-8")
        except OSError as exc:
            if log:
                log(f"narrate skipped (no playbook: {type(exc).__name__})")
            todo = []
    cfg = {**llm.with_tier(settings["llm"], tier), "max_tokens": MAX_TOKENS}
    file_for = corpus_resolver(p["corpus_dir"])
    todo_set = set(todo)

    def _bodies(rows: list[dict[str, Any]]) -> dict[str, str]:
        out: dict[str, str] = {}
        for r in rows:
            mid = r.get("message_id") or ""
            f = file_for(mid) if mid else None
            if not f:
                continue
            try:
                # The FROZEN entry point on purpose. Its signature-KEEPING sibling is opt-in for
                # /api/thread alone, so the closing block stays out of every LLM prompt. (Named
                # obliquely here because the guard for this asserts on the module namespace, and an
                # earlier draft of it grepped the source and fired on this very comment.)
                out[mid] = clean_email_body(parse_eml(f.read_bytes()).get("body_text") or "")
            except Exception:  # noqa: BLE001
                pass
        return out

    out_rows: list[dict[str, Any]] = []
    for root, rows in threads.items():
        if root not in todo_set:
            if root in existing:
                out_rows.append(existing[root])
                counts["kept"] += 1
            continue
        decs = list(decisions_for(root)) if decisions_for else []
        row = narrate_thread(root, rows, _bodies(rows), decs, client=client, cfg=cfg,
                             playbook=playbook, audit_log=p["audit_log"])
        out_rows.append(row)
        counts["narrated"] += 1
        counts["steps"] += len(row.get("steps") or [])
        counts["failed"] += int(bool(row.get("error")))
        if log and counts["narrated"] % 10 == 0:
            log(f"narrate {counts['narrated']}/{len(todo)} · {counts['steps']} passos")

    seen = {r["thread_root"] for r in out_rows}
    for root, row in existing.items():          # a thread that fell below MIN_MESSAGES keeps its row
        if root not in seen:
            out_rows.append(row)
    write_sidecar(out_dir / SIDECAR_NAME, out_rows)
    counts["total"] = len(out_rows)
    return counts
