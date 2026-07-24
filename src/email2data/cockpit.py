"""Response cockpit (D1) — fold per-message verdicts into per-THREAD state with a response clock.

The Fila (queue) can be sorted by *response risk* — "who owes the next reply, and for how long" — not
by per-message priority. This is the core of [docs/05-reference/cockpit-design.md]: it turns *"we classified it right"* into
*"someone must answer this, and the clock is running."*

Two orderings, one queue (``build_fila(..., order=...)``)
---------------------------------------------------------
``"risk"`` (the DEFAULT since ADR-033, owner-approved 2026-07-23) — the response-risk tuple
(``sort_key``): who owes a reply, and for how long. A queue whose top item is not the highest-stakes
item fails "the next move is never a question" on every load. ``"recent"`` — newest thread activity
first, the mailbox-shaped view — stays a first-class, always-available option (it was the default
before ADR-033; the flip only decides which lens opens first, never what the cockpit knows).
Every row carries BOTH keys under ``order_keys`` so the UI can re-sort client-side without re-deriving
(and drifting from) this logic — one source of truth for the risk tuple, here in Python.

Pure functions over CRM interaction rows (``crm.CrmStore.all_interactions``) + the precious thread_state
overlay (owner/handled, from ``workspace.Workspace``). No I/O, no LLM — fully unit-testable.

How the Fila detects replies from lindoservico.pt (the "we answered" signal)
---------------------------------------------------------------------------
When Diogo or any colleague sends a reply from their mail client, that reply lands in Lindo's Sent
folder on the IMAP server. ``signals.header_signals()`` derives ``direction="outbound"`` for any
message whose ``X-Email2Data-Source`` header names a Sent or Enviados folder. ``fold_threads()``
tracks ``last_outbound_date`` per thread, and ``thread_clock()`` at line ~182 says:

    if last_outbound_date >= last_inbound_date → AWAITING (we replied last, ball in their court)

This means the Fila auto-updates — no human action needed — as long as:
  a. The Sent folder is configured in settings.json accounts.mailboxes (it is: ``INBOX.Sent``).
  b. The next sync has run (latency = interval between syncs; the startup + button sync are instant).
  c. The sent reply has a proper ``In-Reply-To`` / ``References`` header matching the original thread.

Gaps that prevent auto-detection:
  • A reply sent from a mobile client that does NOT save to IMAP Sent.
  • A NEW composition (no References header) to the same client — this becomes a separate thread_root.
  • An internal forward that shares a thread_root with the client email: correctly left as WE_OWE
    (the forward is not a reply TO the client).

In all gap cases the fallback is the manual ``tratado`` mark (key E in the Fila), which also reopens
the thread if a new inbound arrives after it was marked handled.

The "we answered" signal under a read-only mailbox
--------------------------------------------------
We observe inboxes, so usually we see only inbound/internal mail, never our own sent reply. A thread's
response state therefore has two sources, in order:

  1. a human **marca tratado** (handled) — always available; a NEW inbound after the handled timestamp
     REOPENS the thread (you never permanently silence a live conversation).
  2. an observed **outbound** message (``direction == "outbound"``) — present only when the Sent folder
     is fetched (``signals.py`` already derives it from ``X-Email2Data-Source``). When present it
     auto-resolves "we replied" with no human action, so the clock upgrades for free the day Sent is fetched.

State machine (latest message wins):
  ``WE_OWE``   — a counterparty wrote last and we haven't replied/handled it. The revenue-risk case.
  ``AWAITING`` — we replied last (outbound), or it's an awaited-outbound purpose (we're chasing them).
  ``HANDLED``  — a human marked it done and nothing new arrived since.
  ``INTERNAL`` — no external counterparty in the thread (colleague-only); low salience.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterable, Optional

from .schema import AWAITED_OUTBOUND_PURPOSES, CLOSING_PURPOSES, HIGH_VALUE_COUNTERPARTIES

# Response states (string constants, like the rest of the codebase's enums).
WE_OWE = "WE_OWE"
AWAITING = "AWAITING"
HANDLED = "HANDLED"
INTERNAL = "INTERNAL"

# Counterparties that carry no relationship clock (not "someone waiting on us").
_NON_COUNTERPARTY = {"INTERNAL", "BULK", "OTHER", ""}

# Sort rank per state (higher = nearer the top of the Fila). Explicit, not magic.
_STATE_RANK = {WE_OWE: 3, AWAITING: 2, INTERNAL: 1, HANDLED: 0}

# Fila orderings (see the module docstring). Both are ORDER BY DESC over their key.
ORDER_RECENT = "recent"   # newest thread activity first
ORDER_RISK = "risk"       # response-risk tuple (``sort_key``) — the default since ADR-033
ORDERS = (ORDER_RECENT, ORDER_RISK)

# Clock-colour thresholds, in hours-in-state. FIRST-DRAFT — calibrate against how the shop actually
# triages (a client estimate is hours; a supplier chase is days). One curve for the MVP; per-counterparty
# SLAs are a follow-up (see docs/05-reference/cockpit-design.md).
_AMBER_AFTER_H = 4.0
_RED_AFTER_H = 24.0
_AWAITING_CHASE_H = _RED_AFTER_H * 3  # we only nudge an awaited reply once a chase is overdue


def _parse_dt(value: Any) -> Optional[datetime]:
    """Parse a CRM/envelope date to an aware datetime.

    Primary path is ISO 8601 (what ``envelope._date_iso`` writes); falls back to RFC2822 for any raw
    header that slipped through, and assumes UTC for a naive value so age arithmetic never raises.
    Returns ``None`` for empty/garbage rather than throwing."""
    if not value or not isinstance(value, str):
        return None
    dt: Optional[datetime]
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        try:
            dt = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _age_hours(since: Optional[datetime], now: datetime) -> float:
    """Hours between ``since`` and ``now``, clamped to >= 0 (clock skew / out-of-order dates can't go negative)."""
    if since is None:
        return 0.0
    return max(0.0, (now - since).total_seconds() / 3600.0)


def _latest_where(rows_asc: list[dict[str, Any]], pred) -> Optional[dict[str, Any]]:
    """The last row (rows are date-ascending) matching ``pred``, or None."""
    for r in reversed(rows_asc):
        if pred(r):
            return r
    return None


_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


@dataclass
class ThreadSummary:
    """One email thread folded from its interaction rows."""
    thread_root: str
    subject: str = ""
    counterparty: str = ""        # dominant EXTERNAL counterparty (CLIENT/LEAD/SUPPLIER/…), else last verdict's
    last_purpose: str = ""
    n_messages: int = 0
    has_attachment: bool = False
    attach_kinds: list[str] = field(default_factory=list)  # union of typed categories across the thread
    last_date: Optional[datetime] = None          # latest message, any direction
    last_inbound_date: Optional[datetime] = None  # latest external/inbound message
    last_outbound_date: Optional[datetime] = None # latest observed sent reply (None unless Sent fetched)
    last_direction: str = ""
    participants: list[str] = field(default_factory=list)  # external sender addresses seen
    # Trust (B5): the verdict that set counterparty/purpose — its id (for the reclassification overlay)
    # and its self-explanation (VISION tenet 8: confidence + who decided + why).
    dominant_mid: str = ""
    confidence: float = 0.0
    decided_by: str = ""
    reason: str = ""
    # All message_ids in the thread (date-ascending). Used as a fallback when dominant_mid shifts
    # to a new message that has no reclassification — the correction on an older message survives.
    all_message_ids: list[str] = field(default_factory=list)
    # Parsed message dates (ascending) — the momentum («Ritmo») input (ADR-033 §8).
    dates: list[datetime] = field(default_factory=list)


def fold_threads(interactions: Iterable[dict[str, Any]]) -> list[ThreadSummary]:
    """Group CRM interaction rows into one :class:`ThreadSummary` per ``thread_root``.

    Each row needs: ``thread_root, message_id, date, direction, counterparty, purpose, subject,
    has_attach, from_email``. Order-independent (we track max-date ourselves)."""
    by_root: dict[str, list[dict[str, Any]]] = {}
    for r in interactions:
        root = r.get("thread_root") or r.get("message_id") or ""
        if root:
            by_root.setdefault(root, []).append(r)

    summaries: list[ThreadSummary] = []
    for root, rows in by_root.items():
        rows_asc = sorted(rows, key=lambda r: _parse_dt(r.get("date")) or _EPOCH)
        last = rows_asc[-1]
        last_in = _latest_where(rows_asc, lambda r: r.get("direction") == "inbound")
        last_out = _latest_where(rows_asc, lambda r: r.get("direction") == "outbound")

        # Dominant counterparty: the most-recent verdict that names a real external party; fall back
        # to the latest verdict (which may be INTERNAL/BULK/OTHER → an internal-only thread).
        cp, purpose, dom = "", "", last
        for r in reversed(rows_asc):
            c = r.get("counterparty") or ""
            if c and c not in _NON_COUNTERPARTY:
                cp, purpose, dom = c, (r.get("purpose") or ""), r
                break
        if not cp:
            cp, purpose = (last.get("counterparty") or ""), (last.get("purpose") or "")

        senders = [r.get("from_email") for r in rows_asc
                   if r.get("direction") == "inbound" and r.get("from_email")]
        summaries.append(ThreadSummary(
            thread_root=root,
            subject=last.get("subject") or "",
            counterparty=cp,
            last_purpose=purpose,
            n_messages=len(rows),
            has_attachment=any(int(r.get("has_attach") or 0) for r in rows),
            attach_kinds=sorted({k for r in rows for k in (r.get("attach_kinds") or "").split(",") if k}),
            last_date=_parse_dt(last.get("date")),
            last_inbound_date=_parse_dt(last_in["date"]) if last_in else None,
            last_outbound_date=_parse_dt(last_out["date"]) if last_out else None,
            last_direction=last.get("direction") or "",
            participants=list(dict.fromkeys(senders)),
            dominant_mid=dom.get("message_id", "") or "",
            confidence=float(dom.get("confidence") or 0.0),
            decided_by=dom.get("decided_by") or "",
            reason=dom.get("reason") or "",
            all_message_ids=[r.get("message_id", "") for r in rows_asc if r.get("message_id")],
            dates=[d for d in (_parse_dt(r.get("date")) for r in rows_asc) if d],
        ))
    return summaries


def momentum(dates: list[datetime], now: datetime) -> str:
    """The thread's «Ritmo» — deterministic from message-date deltas, no LLM (ADR-033 §8).

    ``gap`` = hours since the last message; ``cadence`` = median gap of the last (up to) 3
    message-pairs. ``active`` while the gap fits the thread's own rhythm (with a 48h floor so slow
    email cadence never flags a 2-day-old thread as slowing); ``slowing`` up to 3× cadence;
    ``stalled`` beyond — or for a single message older than 72h."""
    ds = sorted(d for d in dates if d)
    if not ds:
        return "stalled"
    gap = _age_hours(ds[-1], now)
    pairs = [(ds[i + 1] - ds[i]).total_seconds() / 3600.0
             for i in range(max(0, len(ds) - 4), len(ds) - 1)]
    if not pairs:
        return "active" if gap <= 72.0 else "stalled"
    pairs.sort()
    n = len(pairs)
    cadence = pairs[n // 2] if n % 2 else (pairs[n // 2 - 1] + pairs[n // 2]) / 2.0
    if gap <= max(48.0, 1.5 * cadence):
        return "active"
    if gap <= 3.0 * cadence:
        return "slowing"
    return "stalled"


_PT_THOUSANDS_RE = re.compile(r"^\d{1,3}(\.\d{3})+$")


def money_value(raw: Any) -> Optional[float]:
    """Parse an LLM-extracted money STRING into a float for the € vista's ordering — or ``None``.

    Handles the pt-PT shapes the corpus actually contains («€ 1.234,56», «1.200», «160€»,
    «1200,50 EUR»). The value stays a *proposed* tiebreak/vista key only — it never enters the
    default risk order (ADR-033 §2.6), so a mis-parse can never reorder the queue above the clock."""
    if not raw:
        return None
    t = re.sub(r"[^\d,.]", "", str(raw))
    if not any(ch.isdigit() for ch in t):
        return None
    if "," in t and "." in t:           # 1.234,56 → dots are thousands, comma is decimal
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:                      # 1200,50 → comma is decimal
        t = t.replace(",", ".")
    elif _PT_THOUSANDS_RE.match(t):     # 1.200 → PT thousands, not one-point-two
        t = t.replace(".", "")
    elif t.count(".") > 1:              # 1.234.567 → thousands
        t = t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        return None


def thread_clock(s: ThreadSummary, now: datetime,
                 *, handled: bool = False, handled_ts: Optional[str] = None) -> dict[str, Any]:
    """Response state + age + colour band + PT label for one thread.

    ``handled``/``handled_ts`` come from the precious thread_state overlay. A new inbound that arrives
    AFTER ``handled_ts`` reopens the thread (back to ``WE_OWE``)."""
    handled_dt = _parse_dt(handled_ts) if handled_ts else None
    reopened = bool(handled and handled_dt and s.last_inbound_date and s.last_inbound_date > handled_dt)

    if s.counterparty in _NON_COUNTERPARTY:
        state, since = INTERNAL, s.last_date
    elif handled and not reopened:
        state, since = HANDLED, (handled_dt or s.last_date)
    elif s.last_outbound_date and (not s.last_inbound_date or s.last_outbound_date >= s.last_inbound_date):
        # We replied last (Sent observed) → ball is in their court.
        state, since = AWAITING, s.last_outbound_date
    elif s.last_purpose in CLOSING_PURPOSES:
        # Client sent a closure (thank-you after our rejection, or explicit decline) — auto-resolve.
        # No human action needed; a new inbound with a different purpose will reopen the thread.
        state, since = HANDLED, s.last_inbound_date or s.last_date
    elif s.last_direction == "inbound" or reopened:
        state, since = WE_OWE, s.last_inbound_date
    elif s.last_purpose in AWAITED_OUTBOUND_PURPOSES:
        # No reply observed, but the purpose says we're awaiting them (e.g. our order to a supplier).
        state, since = AWAITING, s.last_date
    else:
        # Internal forward / unclear last move on an external thread — still our move.
        state, since = WE_OWE, (s.last_inbound_date or s.last_date)

    age_h = _age_hours(since, now)
    return {
        "state": state,
        "age_hours": round(age_h, 2),
        "band": _band(state, age_h),
        "label": _label(state, age_h),
        "since": since.isoformat() if since else None,
    }


def _band(state: str, age_h: float) -> str:
    """Clock colour: red (overdue) / amber (ageing) / green (fresh) / none (resolved/low)."""
    if state == WE_OWE:
        return "red" if age_h >= _RED_AFTER_H else "amber" if age_h >= _AMBER_AFTER_H else "green"
    if state == AWAITING:
        return "amber" if age_h >= _AWAITING_CHASE_H else "green"
    return "none"


def _humanize_age(age_h: float) -> str:
    if age_h < 1:
        return f"{int(age_h * 60)} min"
    if age_h < 48:
        return f"{int(round(age_h))} h"
    return f"{int(age_h // 24)} dias"


def _label(state: str, age_h: float) -> str:
    """PT-PT human label for the clock."""
    if state == WE_OWE:
        return f"devemos resposta há {_humanize_age(age_h)}"
    if state == AWAITING:
        return f"à espera há {_humanize_age(age_h)}"
    if state == HANDLED:
        return "tratado"
    return "interno"


def sort_key(clock: dict[str, Any], counterparty: str) -> tuple[int, int, float]:
    """Fila order: by state (we-owe first), then counterparty value, then age (oldest first).

    A transparent tuple (used for ORDER BY DESC) — easy to reason about and to test, unlike an opaque score."""
    return (_STATE_RANK.get(clock["state"], 0),
            1 if counterparty in HIGH_VALUE_COUNTERPARTIES else 0,
            clock["age_hours"])


def recency_key(s: ThreadSummary) -> float:
    """Seconds-since-epoch of the thread's LAST activity, any direction (used for ORDER BY DESC).

    ``0.0`` when the thread has no parseable date, so an undated thread sinks to the bottom instead of
    floating to the top of a most-recent-first queue."""
    return s.last_date.timestamp() if s.last_date else 0.0


def build_fila(interactions: Iterable[dict[str, Any]],
               thread_states: Optional[dict[str, dict[str, Any]]] = None,
               *, now: Optional[datetime] = None, include_resolved: bool = False,
               reclassified: Optional[dict[str, dict[str, str]]] = None,
               snoozes: Optional[dict[str, dict[str, str]]] = None,
               order: str = ORDER_RISK) -> list[dict[str, Any]]:
    """Top-level: fold → reclassification overlay → clock → sort. Returns Fila rows for the UI/JSON.

    ``thread_states``: ``{thread_root: {"owner": str, "handled": bool, "handled_ts": str}}`` (workspace).
    ``reclassified``: ``{message_id: {"counterparty"/"purpose"/...: value_human}}`` (the precious human
    corrections, ``Workspace.get_reclassifications``). When a thread's dominant verdict was corrected we
    use the human value, mark the row ``committed``, and — since the override happens BEFORE the clock —
    a correction can move a thread INTO or OUT of the active queue (e.g. OTHER→CLIENT, or CLIENT→OTHER).
    ``include_resolved``: keep HANDLED/INTERNAL rows (an "all" view); default drops them (shrink-to-zero).
    ``snoozes``: ``{thread_root: {"until_ts", "created_ts"}}`` (workspace v9, ADR-033 P3). A snoozed
    thread leaves the ACTIVE queue while it sleeps — but it wakes when ``until_ts`` passes OR when a
    NEW INBOUND arrives after ``created_ts``, whichever first: a thread hidden by the human can never
    be lost to the counterparty's move (non-negotiable #2). The resolved view keeps snoozed rows,
    stamped with ``snoozed_until``, so a deferral stays reviewable (ADR-028).
    ``order``: ``"risk"`` (default since ADR-033 — the response-risk tuple) or ``"recent"`` (newest
    thread activity first). Unknown values raise instead of silently falling back, so a typo can never
    quietly reorder the queue. Each row also carries ``order_keys`` = both keys, for a client-side
    re-sort."""
    if order not in ORDERS:
        raise ValueError(f"unknown Fila order {order!r} — expected one of {ORDERS}")
    now = now or datetime.now(timezone.utc)
    states = thread_states or {}
    recl = reclassified or {}
    rows: list[dict[str, Any]] = []
    for s in fold_threads(interactions):
        rc = recl.get(s.dominant_mid) or {}
        if not rc:
            # dominant_mid may have shifted to a newer message that has no reclassification yet —
            # search the rest of the thread so a correction on an older message isn't silently lost.
            for mid in s.all_message_ids:
                if mid != s.dominant_mid:
                    rc = recl.get(mid) or {}
                    if rc:
                        break
        committed = bool(rc)
        # Keep the ORIGINAL auto verdict so the Fila reclassify picker can send value_auto (the
        # training pair) and offer "↺ auto" reset, even after a human override has replaced the value.
        auto_cp, auto_purpose = s.counterparty, s.last_purpose
        if rc.get("counterparty"):
            s.counterparty = rc["counterparty"]
        if rc.get("purpose"):
            s.last_purpose = rc["purpose"]
        st = states.get(s.thread_root) or {}
        clock = thread_clock(s, now, handled=bool(st.get("handled")), handled_ts=st.get("handled_ts"))
        if not include_resolved and clock["state"] in (HANDLED, INTERNAL):
            continue
        # Adiar (v9): asleep only while BOTH wake conditions are unmet — see the docstring.
        sn = (snoozes or {}).get(s.thread_root)
        if sn:
            until = _parse_dt(sn.get("until_ts"))
            created = _parse_dt(sn.get("created_ts"))
            woke_time = until is None or until <= now
            woke_inbound = bool(s.last_inbound_date and created and s.last_inbound_date > created)
            if not (woke_time or woke_inbound) and not include_resolved:
                continue
        risk_k, recent_k = list(sort_key(clock, s.counterparty)), recency_key(s)
        rows.append({
            "thread_root": s.thread_root,
            "message_id": s.dominant_mid,   # the verdict id reclassify writes against (correct from the Fila)
            "subject": s.subject,
            "counterparty": s.counterparty,
            "purpose": s.last_purpose,
            "auto": {"counterparty": auto_cp, "purpose": auto_purpose},  # pre-override verdict (training pair + reset)
            "contact": s.participants[0] if s.participants else "",
            "n_messages": s.n_messages,
            "has_attachment": s.has_attachment,
            "attach_kinds": s.attach_kinds,   # typed 📎 on the row (ADR-034); [] on a pre-v4 crm.db
            "owner": st.get("owner") or "",              # legacy single (first owner) for old readers
            "owners": st.get("owners") or [],            # multi-owner set (the Fila chips)
            "clock": clock,
            # «Ritmo» (ADR-033 §8): the thread's own cadence vs its current silence.
            "momentum": momentum(s.dates, now),
            # Present only on snoozed rows (the resolved/«Adiadas» view shows when it wakes).
            **({"snoozed_until": sn.get("until_ts")} if sn else {}),
            "trust": {"confidence": round(s.confidence, 2), "decided_by": s.decided_by,
                      "reason": s.reason, "committed": committed},
            # Last activity in the thread (any direction) — what the "recent" order sorts on, and the
            # only date the UI can show without re-reading the clock's state-dependent `since`.
            "last_date": s.last_date.isoformat() if s.last_date else None,
            # BOTH sort keys, so the lens can flip order without a round-trip or a second implementation.
            "order_keys": {ORDER_RECENT: recent_k, ORDER_RISK: risk_k},
            "_sort": recent_k if order == ORDER_RECENT else risk_k,
        })
    rows.sort(key=lambda r: r["_sort"], reverse=True)
    for r in rows:
        del r["_sort"]
    return rows
