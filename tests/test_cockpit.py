"""Cockpit D1 — response clock + thread fold + precious thread_state.

Covers the critical FUNCTIONAL logic (who-owes-whom, reopen-on-new-inbound, sort order, fold) and the
TECHNICAL edges (date parsing, negative-age clamp, persistence across a re-run). Pure logic + an
in-memory/tmp Workspace; no network, no LLM.
"""

from datetime import datetime, timedelta, timezone

import pytest

from email2data.cockpit import (AWAITING, HANDLED, INTERNAL, WE_OWE, _age_hours, _parse_dt,
                                 build_fila, fold_threads, thread_clock)
from email2data.workspace import Workspace

NOW = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)


def ago(hours: float) -> str:
    return (NOW - timedelta(hours=hours)).isoformat()


def _row(root, mid, date, *, direction="inbound", counterparty="CLIENT",
         purpose="ESTIMATE_REQUEST_FROM_CLIENT", subject="Orçamento", has_attach=0,
         from_email="maria@acme.pt", confidence=0.91, decided_by="tier1:gemini", reason="pede orçamento"):
    return {"thread_root": root, "message_id": mid, "date": date, "direction": direction,
            "counterparty": counterparty, "purpose": purpose, "subject": subject,
            "has_attach": has_attach, "from_email": from_email,
            "confidence": confidence, "decided_by": decided_by, "reason": reason}


def _clock_for(rows, **state):
    [s] = fold_threads(rows)
    return s, thread_clock(s, NOW, **state)


# ── response state: who owes whom ────────────────────────────────────────────────────────────────

def test_inbound_last_is_we_owe():
    _, c = _clock_for([_row("t1", "m1", ago(6))])
    assert c["state"] == WE_OWE
    assert 5.9 < c["age_hours"] < 6.1
    assert c["band"] == "amber"
    assert c["label"] == "devemos resposta há 6 h"


def test_fresh_we_owe_is_green():
    _, c = _clock_for([_row("t1", "m1", ago(1))])
    assert c["band"] == "green" and c["label"] == "devemos resposta há 1 h"


def test_we_owe_turns_red_after_a_day():
    _, c = _clock_for([_row("t1", "m1", ago(30))])
    assert c["state"] == WE_OWE and c["band"] == "red"


def test_we_replied_is_awaiting():
    rows = [_row("t1", "m1", ago(10)),
            _row("t1", "m2", ago(2), direction="outbound", from_email="diogo@lindoservico.pt")]
    _, c = _clock_for(rows)
    assert c["state"] == AWAITING and c["label"].startswith("à espera")


def test_awaited_outbound_purpose_is_awaiting():
    # A colleague logged an order to a supplier internally; no reply observed yet → we're chasing them.
    rows = [_row("t1", "m1", ago(50), direction="internal", counterparty="SUPPLIER",
                 purpose="OUR_ORDER_TO_SUPPLIER", from_email="joao@lindoservico.pt")]
    _, c = _clock_for(rows)
    assert c["state"] == AWAITING


def test_own_rejection_outbound_is_awaiting():
    # We sent a definitive refusal → ball is in their court, but we owe nothing more.
    rows = [_row("t1", "m1", ago(10)),
            _row("t1", "m2", ago(2), direction="outbound", purpose="OWN_REJECTION",
                 from_email="orcamentos@lindoservico.pt")]
    _, c = _clock_for(rows)
    assert c["state"] == AWAITING


def test_client_rejection_after_own_rejection_auto_closes():
    # Full closure: we refused → client thanked us and closed → thread auto-HANDLED, off the Fila.
    rows = [_row("t1", "m1", ago(20)),
            _row("t1", "m2", ago(10), direction="outbound", purpose="OWN_REJECTION",
                 from_email="orcamentos@lindoservico.pt"),
            _row("t1", "m3", ago(2), purpose="CLIENT_REJECTION")]
    assert build_fila(rows, now=NOW) == []          # auto-resolved → out of the active queue
    [r] = build_fila(rows, now=NOW, include_resolved=True)
    assert r["clock"]["state"] == HANDLED


def test_client_rejection_standalone_auto_closes():
    # Client closes without a prior OWN_REJECTION (e.g. they changed mind after our quote).
    rows = [_row("t1", "m1", ago(20)),
            _row("t1", "m2", ago(2), purpose="CLIENT_REJECTION")]
    assert build_fila(rows, now=NOW) == []


def test_new_inbound_after_client_rejection_reopens():
    # A new real request after a closure should put the thread back in WE_OWE.
    rows = [_row("t1", "m1", ago(30)),
            _row("t1", "m2", ago(20), purpose="CLIENT_REJECTION"),
            _row("t1", "m3", ago(1), purpose="ESTIMATE_REQUEST_FROM_CLIENT")]
    [r] = build_fila(rows, now=NOW)
    assert r["clock"]["state"] == WE_OWE


def test_internal_forward_of_client_mail_still_we_owe():
    # A colleague forwarded a client request internally, but no external reply was sent — still our move.
    rows = [_row("t1", "m1", ago(10), counterparty="CLIENT"),
            _row("t1", "m2", ago(2), direction="internal", counterparty="CLIENT",
                 from_email="ana@lindoservico.pt")]
    s, c = _clock_for(rows)
    assert c["state"] == WE_OWE
    assert c["age_hours"] == round(_age_hours(s.last_inbound_date, NOW), 2)  # from the client inbound (10h)


def test_internal_only_thread_is_internal():
    rows = [_row("t1", "m1", ago(3), direction="internal", counterparty="INTERNAL",
                 purpose="INTERNAL_OPS", from_email="ana@lindoservico.pt")]
    [s] = fold_threads(rows)
    assert thread_clock(s, NOW)["state"] == INTERNAL
    assert build_fila(rows, now=NOW) == []          # internal chatter is not in the active queue


# ── handled / reopen ─────────────────────────────────────────────────────────────────────────────

def test_handled_resolves_and_drops_from_active_queue():
    rows = [_row("t1", "m1", ago(6))]
    states = {"t1": {"handled": True, "handled_ts": ago(1)}}     # handled AFTER the inbound
    assert build_fila(rows, states, now=NOW) == []               # gone from the active queue
    [r] = build_fila(rows, states, now=NOW, include_resolved=True)
    assert r["clock"]["state"] == HANDLED and r["clock"]["band"] == "none"


def test_new_inbound_after_handled_reopens():
    rows = [_row("t1", "m1", ago(6)),
            _row("t1", "m2", ago(1))]                            # client wrote again, 1h ago
    states = {"t1": {"handled": True, "handled_ts": ago(5)}}     # handled BEFORE the new inbound
    [r] = build_fila(rows, states, now=NOW)
    assert r["clock"]["state"] == WE_OWE
    assert 0.9 < r["clock"]["age_hours"] < 1.1                   # age from the NEW inbound, not the old one


# ── fold ───────────────────────────────────────────────────────────────────────────────────────

def test_fold_groups_messages_into_one_thread():
    rows = [_row("t1", "m1", ago(10)),
            _row("t1", "m2", ago(5), has_attach=1),
            _row("t1", "m3", ago(2))]
    [s] = fold_threads(rows)
    assert s.n_messages == 3 and s.has_attachment is True
    assert s.last_date == _parse_dt(ago(2))                      # latest message wins


def test_dominant_counterparty_prefers_external_over_internal():
    rows = [_row("t1", "m1", ago(10), counterparty="CLIENT"),
            _row("t1", "m2", ago(2), direction="internal", counterparty="INTERNAL",
                 purpose="INTERNAL_OPS", from_email="ana@lindoservico.pt")]
    [s] = fold_threads(rows)
    assert s.counterparty == "CLIENT"                            # not masked by the later internal note


# ── sort order ───────────────────────────────────────────────────────────────────────────────────

def test_fila_sort_we_owe_client_first_then_awaiting():
    rows = (
        [_row("owe_client_old", "a", ago(30))] +                                 # WE_OWE CLIENT 30h
        [_row("owe_client_new", "b", ago(2))] +                                  # WE_OWE CLIENT 2h
        [_row("owe_supplier", "c", ago(40), counterparty="SUPPLIER",
              purpose="SUPPLIER_REPLY_OR_CONFIRMATION", from_email="x@laminate-example.com")] +  # WE_OWE SUPPLIER
        [_row("await", "d", ago(50)),
         _row("await", "e", ago(5), direction="outbound", from_email="diogo@lindoservico.pt")]  # AWAITING
    )
    order = [r["thread_root"] for r in build_fila(rows, now=NOW, order="risk")]
    assert order == ["owe_client_old", "owe_client_new", "owe_supplier", "await"]


def test_fila_default_order_is_response_risk():
    """ADR-033: the DEFAULT queue order is the response-risk tuple — a queue whose top item is not
    the highest-stakes item fails 'the next move is never a question' on every load. The oldest
    WE_OWE debt surfaces first; recency is one explicit argument away, never deleted."""
    rows = [_row("old", "a", ago(30)),
            _row("newest", "b", ago(2)),
            _row("middle", "c", ago(9))]
    assert [r["thread_root"] for r in build_fila(rows, now=NOW)] == ["old", "middle", "newest"]


def test_recent_order_uses_last_activity_not_thread_start():
    """A long-running thread that got a message 1h ago beats a thread that started later but went
    quiet — the queue tracks the conversation's LAST move, not its birth."""
    rows = [_row("old_thread_fresh_msg", "a", ago(50)),
            _row("old_thread_fresh_msg", "b", ago(1)),
            _row("started_later", "c", ago(20))]
    order = [r["thread_root"] for r in build_fila(rows, now=NOW, order="recent")]
    assert order == ["old_thread_fresh_msg", "started_later"]


def test_recent_order_still_available_and_really_differs():
    """`Mais recentes` survives ADR-033 as the explicit non-default — same rows, two orders,
    genuinely different answers (the flip stays meaningful, not cosmetic)."""
    rows = [_row("owe_client_old", "a", ago(30)),
            _row("owe_client_new", "b", ago(2)),
            _row("await", "d", ago(50)),
            _row("await", "e", ago(5), direction="outbound", from_email="diogo@lindoservico.pt")]
    recent = [r["thread_root"] for r in build_fila(rows, now=NOW, order="recent")]
    risk = [r["thread_root"] for r in build_fila(rows, now=NOW)]
    assert recent == ["owe_client_new", "await", "owe_client_old"]   # by last activity
    assert risk == ["owe_client_old", "owe_client_new", "await"]     # by who owes, and for how long


def test_rows_carry_both_order_keys_so_the_ui_can_flip_locally():
    """Both keys ride on every row: the lens toggles order client-side without a round-trip AND
    without re-implementing the risk tuple in JS (where it would drift from this definition)."""
    [r] = build_fila([_row("t1", "m1", ago(6))], now=NOW)
    assert set(r["order_keys"]) == {"recent", "risk"}
    assert r["order_keys"]["risk"] == [3, 1, r["clock"]["age_hours"]]   # WE_OWE + high-value CLIENT
    assert r["order_keys"]["recent"] == _parse_dt(ago(6)).timestamp()
    assert r["last_date"] == _parse_dt(ago(6)).isoformat()


def test_undated_thread_sinks_to_the_bottom_of_the_recent_order():
    """A thread with no parseable date must not float to the TOP of a newest-first queue (it would
    look like the most urgent thing in the shop). It sorts as epoch 0."""
    rows = [_row("dated", "a", ago(30)), _row("undated", "b", "not a date")]
    assert [r["thread_root"] for r in build_fila(rows, now=NOW, order="recent")] == ["dated", "undated"]


def test_recent_order_is_deterministic_on_identical_timestamps():
    """The Fila is rebuilt on every request; two threads with the same last activity must not shuffle
    between builds or rows would move under the cursor mid-decision."""
    rows = [_row("t1", "a", ago(3)), _row("t2", "b", ago(3), subject="Outro")]
    first = [r["thread_root"] for r in build_fila(rows, now=NOW, order="recent")]
    assert first == [r["thread_root"] for r in build_fila(rows, now=NOW, order="recent")]


def test_unknown_order_raises_instead_of_silently_falling_back():
    """A typo'd order must fail loudly — silently serving a different queue than asked for is exactly
    the kind of quiet wrongness this codebase refuses."""
    with pytest.raises(ValueError):
        build_fila([_row("t1", "m1", ago(2))], now=NOW, order="urgencia")


def test_owner_is_surfaced_and_sem_dono_is_blank():
    rows = [_row("t1", "m1", ago(3)), _row("t2", "m2", ago(4), subject="Outro")]
    by_root = {r["thread_root"]: r for r in build_fila(rows, {"t1": {"owner": "diogo"}}, now=NOW)}
    assert by_root["t1"]["owner"] == "diogo"
    assert by_root["t2"]["owner"] == ""                          # sem dono


# ── trust & reclassification overlay (B5) ─────────────────────────────────────────────────────────

def test_trust_block_carries_dominant_verdict():
    [r] = build_fila([_row("t1", "m1", ago(3), confidence=0.88,
                            decided_by="tier1:gemini", reason="pede orçamento")], now=NOW)
    t = r["trust"]
    assert t["confidence"] == 0.88 and t["decided_by"] == "tier1:gemini"
    assert t["reason"] == "pede orçamento" and t["committed"] is False


def test_reclassification_overrides_and_enters_queue():
    # AI said OTHER (excluded from the queue); the human corrected it to CLIENT → now WE_OWE + committed.
    out = build_fila([_row("t1", "m1", ago(3), counterparty="OTHER", purpose="OTHER")],
                     now=NOW, reclassified={"m1": {"counterparty": "CLIENT"}})
    assert len(out) == 1
    assert out[0]["counterparty"] == "CLIENT" and out[0]["clock"]["state"] == "WE_OWE"
    assert out[0]["trust"]["committed"] is True


def test_reclassification_to_other_leaves_queue():
    out = build_fila([_row("t1", "m1", ago(3), counterparty="CLIENT")],
                     now=NOW, reclassified={"m1": {"counterparty": "OTHER"}})
    assert out == []                               # corrected to a non-counterparty → out of the queue


def test_reclassification_survives_dominant_mid_shift():
    """After a sync, a new message in the same thread may become dominant_mid.  The correction stored
    against the old dominant_mid must still be applied — not silently dropped.

    Scenario: m1 AI-classified as SUPPLIER (wrong). Human corrects to CLIENT. m2 arrives later in the
    same thread; AI also says SUPPLIER → m2 becomes the new dominant_mid.  Without the fallback search
    recl.get("m2") returns {} and the thread reverts to SUPPLIER.  With the fix it finds the correction
    on m1 via all_message_ids and applies it."""
    rows = [_row("t1", "m1", ago(10), counterparty="SUPPLIER"),
            _row("t1", "m2", ago(2),  counterparty="SUPPLIER")]
    out = build_fila(rows, now=NOW, reclassified={"m1": {"counterparty": "CLIENT"}})
    assert len(out) == 1
    assert out[0]["counterparty"] == "CLIENT"
    assert out[0]["trust"]["committed"] is True


# ── technical edges ──────────────────────────────────────────────────────────────────────────────

def test_build_fila_handles_empty():
    assert build_fila([], now=NOW) == []


def test_parse_dt_iso_naive_rfc2822_and_garbage():
    assert _parse_dt("2026-06-03T10:00:00+00:00").hour == 10
    assert _parse_dt("2026-06-03T10:00:00").tzinfo is timezone.utc      # naive → assume UTC
    assert _parse_dt("Mon, 02 Jun 2026 10:00:00 +0100") is not None     # RFC2822 fallback
    assert _parse_dt("not a date") is None
    assert _parse_dt(None) is None
    assert _parse_dt("") is None


def test_future_date_clamps_age_to_zero():
    _, c = _clock_for([_row("t1", "m1", (NOW + timedelta(hours=5)).isoformat())])
    assert c["age_hours"] == 0.0 and c["band"] == "green"


# ── precious thread_state (workspace) ──────────────────────────────────────────────────────────────

def test_thread_state_persists_across_reconnect(tmp_path):
    db = tmp_path / "w.db"
    ws = Workspace(db).connect()
    ws.set_thread_owner("t1", "diogo")
    ws.set_thread_handled("t1", True)
    ws.close()
    ws2 = Workspace(db).connect()                                # == the pipeline re-ran
    st = ws2.thread_states()["t1"]
    assert st["owner"] == "diogo" and st["handled"] is True and st["handled_ts"]
    ws2.close()


def test_unhandle_is_the_undo_path(tmp_path):
    ws = Workspace(tmp_path / "w.db").connect()
    ws.set_thread_handled("t1", True)
    ws.set_thread_handled("t1", False)
    st = ws.thread_states()["t1"]
    assert st["handled"] is False and st["handled_ts"] is None
    ws.close()


def test_owner_and_handled_are_independent_columns(tmp_path):
    ws = Workspace(tmp_path / "w.db").connect()
    ws.set_thread_handled("t1", True)                            # row born via the handled path
    assert ws.thread_states()["t1"]["owner"] == ""               # owner still unset
    ws.set_thread_owner("t1", "ana")                             # set owner; handled must survive
    st = ws.thread_states()["t1"]
    assert st["owner"] == "ana" and st["handled"] is True
    ws.close()


def test_workspace_states_feed_build_fila(tmp_path):
    ws = Workspace(tmp_path / "w.db").connect()
    ws.set_thread_handled("t1", True, ts=ago(1))                 # handled after the inbound
    rows = [_row("t1", "m1", ago(6))]
    assert build_fila(rows, ws.thread_states(), now=NOW) == []   # resolved → not in the active queue
    ws.close()


def test_build_fila_row_carries_message_id_and_auto():
    """Phase A2: each Fila row carries the dominant message_id (so reclassify can write against it)
    and the ORIGINAL auto verdict (for value_auto + the '↺ auto' reset)."""
    [r] = build_fila([_row("t1", "m1", ago(2))], now=NOW)
    assert r["message_id"] == "m1"
    assert r["auto"] == {"counterparty": "CLIENT", "purpose": "ESTIMATE_REQUEST_FROM_CLIENT"}


def test_reclassified_row_keeps_original_auto():
    """A human correction overlays the displayed value but `auto` keeps the original — so the training
    pair (value_auto) and the reset target survive the override."""
    recl = {"m1": {"purpose": "FOLLOW_UP", "counterparty": "SUPPLIER"}}
    [r] = build_fila([_row("t1", "m1", ago(2))], now=NOW, reclassified=recl)
    assert r["purpose"] == "FOLLOW_UP" and r["counterparty"] == "SUPPLIER"     # overlaid
    assert r["auto"] == {"counterparty": "CLIENT", "purpose": "ESTIMATE_REQUEST_FROM_CLIENT"}
    assert r["trust"]["committed"] is True
