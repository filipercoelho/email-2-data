"""Cockpit D1 — response clock + thread fold + precious thread_state.

Covers the critical FUNCTIONAL logic (who-owes-whom, reopen-on-new-inbound, sort order, fold) and the
TECHNICAL edges (date parsing, negative-age clamp, persistence across a re-run). Pure logic + an
in-memory/tmp Workspace; no network, no LLM.
"""

from datetime import datetime, timedelta, timezone

import pytest

from email2data.cockpit import (AWAITING, G_BILL, G_CHASE, G_INFO, G_OWE, G_PAY, G_WAIT, HANDLED,
                                 INFO, INTERNAL, TO_PAY, WE_OWE, _age_hours, _parse_dt, build_fila,
                                 fold_threads, thread_clock)
from email2data.schema import derive_priority
from email2data.workspace import Workspace

NOW = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)


def ago(hours: float) -> str:
    return (NOW - timedelta(hours=hours)).isoformat()


def _row(root, mid, date, *, direction="inbound", counterparty="CLIENT",
         purpose="ESTIMATE_REQUEST_FROM_CLIENT", speech_act="UNKNOWN", subject="Orçamento",
         has_attach=0, attach_kinds="", from_email="maria@acme.pt", confidence=0.91,
         decided_by="tier1:gemini", reason="pede orçamento"):
    return {"thread_root": root, "message_id": mid, "date": date, "direction": direction,
            "counterparty": counterparty, "purpose": purpose, "speech_act": speech_act,
            "subject": subject, "has_attach": has_attach, "attach_kinds": attach_kinds,
            "from_email": from_email, "confidence": confidence, "decided_by": decided_by, "reason": reason}


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


def test_own_rejection_outbound_auto_closes():
    # ADR-036 Stage 0, Bug 1: we sent a definitive refusal → the thread closes from OUR side
    # (auto-HANDLED, off the Fila), not AWAITING → a false «A cobrar». Reopens if the client writes back.
    rows = [_row("t1", "m1", ago(10)),
            _row("t1", "m2", ago(2), direction="outbound", purpose="OWN_REJECTION",
                 from_email="orcamentos@lindoservico.pt")]
    _, c = _clock_for(rows)
    assert c["state"] == HANDLED
    assert build_fila(rows, now=NOW) == []                       # off the active queue
    [r] = build_fila(rows, now=NOW, include_resolved=True)
    assert r["clock"]["state"] == HANDLED


def test_new_inbound_after_own_rejection_reopens():
    # Non-negotiable #2: our close is never sticky — a new client message reopens the thread (WE_OWE).
    rows = [_row("t1", "m1", ago(30)),
            _row("t1", "m2", ago(20), direction="outbound", purpose="OWN_REJECTION",
                 from_email="orcamentos@lindoservico.pt"),
            _row("t1", "m3", ago(1), purpose="ESTIMATE_REQUEST_FROM_CLIENT")]
    [r] = build_fila(rows, now=NOW)
    assert r["clock"]["state"] == WE_OWE


# ── Fila obligation group (ADR-036 Stage 0): executed routing, not a source-string check ──────────

def test_overdue_outbound_invoice_is_billing_group():
    # A genuine unpaid invoice (OUTBOUND_INVOICE) past the 72h chase band → G_BILL «A cobrar».
    rows = [_row("t1", "m1", ago(80), direction="outbound", counterparty="CLIENT",
                 purpose="OUTBOUND_INVOICE", from_email="orcamentos@lindoservico.pt")]
    [r] = build_fila(rows, now=NOW)
    assert r["clock"]["state"] == AWAITING and r["clock"]["band"] == "amber"
    assert r["group"] == G_BILL


def test_stale_followup_is_chase_not_billing():
    # The mislabel we are fixing: a stalled proposal/follow-up is G_CHASE «A aguardar», NEVER billing.
    rows = [_row("t1", "m1", ago(80), direction="outbound", counterparty="CLIENT",
                 purpose="FOLLOW_UP", from_email="orcamentos@lindoservico.pt")]
    [r] = build_fila(rows, now=NOW)
    assert r["group"] == G_CHASE


def test_fresh_awaiting_is_wait_group():
    # We replied recently (<72h) → G_WAIT «À espera deles», muted.
    rows = [_row("t1", "m1", ago(10)),
            _row("t1", "m2", ago(2), direction="outbound", from_email="diogo@lindoservico.pt")]
    [r] = build_fila(rows, now=NOW)
    assert r["clock"]["state"] == AWAITING and r["group"] == G_WAIT


def test_inbound_last_is_owe_group():
    [r] = build_fila([_row("t1", "m1", ago(6))], now=NOW)
    assert r["group"] == G_OWE


# ── Stage 1 (ADR-036): inbound bill = «A pagar»/TO_PAY (Bug 2), FOLLOW_UP split ───────────────────

def _bill(mid, hours, root="t1"):
    return _row(root, mid, ago(hours), direction="inbound", counterparty="SUPPLIER",
                purpose="SUPPLIER_INVOICE", from_email="contabilidade@laminex.pt", subject="Fatura FT")


def test_inbound_supplier_invoice_is_to_pay():
    # Bug 2: a supplier bill is money-to-PAY, not a reply owed → TO_PAY, «por pagar», not WE_OWE.
    _, c = _clock_for([_bill("m1", 60)])
    assert c["state"] == TO_PAY
    assert c["label"].startswith("por pagar")
    assert c["band"] == "amber"                       # 60h ≥ 48h amber, < 168h red


def test_supplier_invoice_stays_in_active_queue_as_pay_group():
    [r] = build_fila([_bill("m1", 60)], now=NOW)      # default include_resolved=False
    assert r["clock"]["state"] == TO_PAY and r["group"] == G_PAY


def test_supplier_invoice_reopens_on_marca_tratado():
    # A paid bill is marked tratado → HANDLED; a NEW bill after that reopens as TO_PAY (never lost).
    _, c = _clock_for([_bill("m1", 50)], handled=True, handled_ts=ago(40))
    assert c["state"] == HANDLED
    reopened = [_bill("m1", 50), _bill("m2", 10)]
    _, c2 = _clock_for(reopened, handled=True, handled_ts=ago(40))
    assert c2["state"] == TO_PAY


def test_derive_priority_new_purposes():
    assert derive_priority("CLIENT", "OUTBOUND_QUOTE", 30, False) == "HIGH"     # client → HIGH (as FOLLOW_UP was)
    assert derive_priority("SUPPLIER", "OUTBOUND_QUOTE", 30, False) == "LOW"    # awaited-outbound → LOW
    assert derive_priority("SUPPLIER", "SUPPLIER_INVOICE", 30, False) == "MEDIUM"
    assert derive_priority("SUPPLIER", "SUPPLIER_INVOICE", 80, False) == "HIGH"  # time-pressured → HIGH


def test_outbound_quote_awaits_not_owe_or_billing():
    # The FOLLOW_UP split: a sent quote awaiting a decision → «A aguardar», never billing/owe/pay.
    rows = [_row("t1", "m1", ago(80), direction="outbound", counterparty="CLIENT",
                 purpose="OUTBOUND_QUOTE", from_email="orcamentos@lindoservico.pt")]
    [r] = build_fila(rows, now=NOW)
    assert r["clock"]["state"] == AWAITING and r["group"] == G_CHASE


# ── Stage 2 (ADR-036): speech_act → obligation fold, act-driven grouping ──────────────────────────

def test_inbound_ask_owes_reply():
    [r] = build_fila([_row("t1", "m1", ago(6), direction="inbound", speech_act="ASK")], now=NOW)
    assert r["clock"]["obligation"] == "OWE_REPLY" and r["clock"]["state"] == WE_OWE and r["group"] == G_OWE


def test_inbound_obligation_invoice_is_payment():
    # An inbound OBLIGATION on a bill → we owe a PAYMENT («A pagar»), not a reply (Bug 2, act-driven).
    rows = [_row("t1", "m1", ago(20), direction="inbound", counterparty="SUPPLIER",
                 purpose="SUPPLIER_INVOICE", speech_act="OBLIGATION")]
    [r] = build_fila(rows, now=NOW)
    assert r["clock"]["obligation"] == "OWE_PAYMENT" and r["clock"]["state"] == TO_PAY
    assert r["group"] == G_PAY and r["clock"]["label"].startswith("por pagar")


def test_outbound_invoice_obligation_is_collect():
    rows = [_row("t1", "m1", ago(20), direction="outbound", counterparty="CLIENT",
                 purpose="OUTBOUND_INVOICE", speech_act="OBLIGATION", from_email="orcamentos@lindoservico.pt")]
    [r] = build_fila(rows, now=NOW)
    assert r["clock"]["obligation"] == "COLLECT" and r["group"] == G_BILL


def test_outbound_ask_awaits_them_never_billing():
    rows = [_row("t1", "m1", ago(80), direction="outbound", counterparty="CLIENT",
                 purpose="OUTBOUND_QUOTE", speech_act="ASK", from_email="orcamentos@lindoservico.pt")]
    [r] = build_fila(rows, now=NOW)
    assert r["clock"]["obligation"] == "AWAIT_THEM" and r["group"] == G_CHASE   # stale → «A aguardar»


def test_close_auto_resolves_from_either_side():
    # Our decline (outbound CLOSE) and their thank-you (inbound CLOSE) both self-close (Bug 1 + "ack forever").
    for direction, frm in (("outbound", "x@lindoservico.pt"), ("inbound", "maria@acme.pt")):
        rows = [_row("t1", "m1", ago(10)),
                _row("t1", "m2", ago(2), direction=direction, speech_act="CLOSE", from_email=frm)]
        assert build_fila(rows, now=NOW) == []          # RESOLVED → off the active queue


def test_ack_auto_resolves():
    rows = [_row("t1", "m1", ago(10), direction="outbound", from_email="x@lindoservico.pt"),
            _row("t1", "m2", ago(2), direction="inbound", speech_act="ACK")]
    assert build_fila(rows, now=NOW) == []


def test_fyi_is_quiet_info_pile_not_dropped():
    [r] = build_fila([_row("t1", "m1", ago(6), direction="inbound", speech_act="FYI")], now=NOW)
    assert r["clock"]["obligation"] == "FYI" and r["clock"]["state"] == INFO
    assert r["group"] == G_INFO and r["clock"]["band"] == "none" and r["clock"]["label"] == "informação"


def test_last_decisive_act_wins_fyi_does_not_override_ask():
    # FYI/UNKNOWN never override a live move: an inbound ASK then an FYI still owes a reply.
    rows = [_row("t1", "m1", ago(10), direction="inbound", speech_act="ASK"),
            _row("t1", "m2", ago(2), direction="inbound", speech_act="FYI")]
    [r] = build_fila(rows, now=NOW)
    assert r["clock"]["obligation"] == "OWE_REPLY" and r["group"] == G_OWE


# ── ADR-051: a reply we can SEE discharges an owed reply ─────────────────────────────────────────
#
# The live defect, reported off the rendered dossier: a thread whose header read «devemos resposta há
# 2 dias» with our own reply visible in the timeline, sent the same afternoon. The rows below are the
# real shape of `mid:509ab3fb…@example.pt` — an inbound ASK, then our update-shaped answer that
# Gemini (correctly) called FYI. Because FYI is not decisive, the ASK stayed live and the clock kept
# counting from it. `_obligation_since(OWE_REPLY)` anchors to last_inbound_date, so no amount of
# replying ever moved the number.

def _answered_ask_rows():
    """Their ask, then our reply — the reply reads as FYI, which is exactly the live case."""
    return [_row("t1", "m1", ago(66), direction="inbound", speech_act="ASK", purpose="FOLLOW_UP"),
            _row("t1", "m2", ago(1), direction="outbound", speech_act="FYI", purpose="FOLLOW_UP",
                 from_email="orcamentos@lindoservico.pt")]


def test_our_reply_discharges_an_inbound_ask_even_when_it_reads_as_fyi():
    [r] = build_fila(_answered_ask_rows(), now=NOW)
    c = r["clock"]
    assert c["obligation"] == "AWAIT_THEM" and c["state"] == AWAITING     # the ball is theirs now
    assert c["age_hours"] < 2                                            # counts from OUR reply…
    assert not c["label"].startswith("devemos resposta")                 # …so the header cannot lie
    assert r["group"] == G_WAIT                                          # fresh → «À espera deles»


def test_the_act_driven_fold_agrees_with_the_legacy_fold_that_a_reply_moves_the_ball():
    """ADR-036 Stage 2 dropped `_legacy_obligation`'s `last_outbound >= last_inbound → AWAIT_THEM`
    guard and never replaced it, so a re-triaged crm.db was WORSE than a pre-re-triage one on the
    same thread. Pin the two folds together on this shape so they cannot diverge again."""
    from email2data.cockpit import _legacy_obligation, derive_obligation
    [s] = fold_threads(_answered_ask_rows())
    assert derive_obligation(s) == _legacy_obligation(s) == "AWAIT_THEM"


def test_a_reply_never_discharges_an_inbound_bill():
    """The discharge is for an owed REPLY only — a mail does not pay a supplier. «A pagar» must
    survive us answering "recebido, pagamos dia 10"."""
    rows = [_row("t1", "m1", ago(66), direction="inbound", counterparty="SUPPLIER",
                 purpose="SUPPLIER_INVOICE", speech_act="OBLIGATION"),
            _row("t1", "m2", ago(1), direction="outbound", counterparty="SUPPLIER",
                 purpose="FOLLOW_UP", speech_act="FYI", from_email="orcamentos@lindoservico.pt")]
    [r] = build_fila(rows, now=NOW)
    assert r["clock"]["obligation"] == "OWE_PAYMENT" and r["group"] == G_PAY
    assert r["clock"]["age_hours"] > 60                 # still counting from the bill, as it must


def test_an_internal_forward_is_not_an_answer_to_the_client():
    """Forwarding their question to a colleague is not replying to them — ADR-036's «an internal
    forward is still about a client» fold has to survive the discharge."""
    rows = [_row("t1", "m1", ago(66), direction="inbound", speech_act="ASK"),
            _row("t1", "m2", ago(1), direction="internal", speech_act="FYI",
                 from_email="diogo@lindoservico.pt")]
    [r] = build_fila(rows, now=NOW)
    assert r["clock"]["obligation"] == "OWE_REPLY" and r["group"] == G_OWE


def test_a_new_ask_after_our_reply_owes_again():
    """The discharge is scoped to outbound AFTER the decisive message — when they come back with a
    fresh ask, that ask is the decisive one and nothing precedes it from us."""
    rows = _answered_ask_rows() + [_row("t1", "m3", ago(0.5), direction="inbound", speech_act="ASK")]
    [r] = build_fila(rows, now=NOW)
    assert r["clock"]["obligation"] == "OWE_REPLY" and r["group"] == G_OWE


def test_an_inbound_fyi_still_never_overrides_their_live_ask():
    """The narrow strike, said as a test: only OUR outbound discharges. An inbound notification
    landing after their ask must still leave the ask live (the rule ADR-036 wrote it for)."""
    rows = [_row("t1", "m1", ago(10), direction="inbound", speech_act="ASK"),
            _row("t1", "m2", ago(2), direction="inbound", speech_act="FYI")]
    [r] = build_fila(rows, now=NOW)
    assert r["clock"]["obligation"] == "OWE_REPLY"


def test_the_clock_says_whether_the_debt_covers_the_segment_the_timeline_draws_it_across():
    """The dossier timeline paints its debt chip between «agora» and the NEWEST message. That is only
    the obligation's segment when the clock is anchored there — for an unpaid bill we have answered,
    it is not, and the chip used to print «sem resposta há 2 dias» above an hour-old mail. The clock
    now ships the fact, so the renderer does not have to guess."""
    [answered] = build_fila(_answered_ask_rows(), now=NOW)
    assert answered["clock"]["anchored_at_last"] is True                  # our reply IS the anchor
    assert answered["clock"]["gap_hours"] == answered["clock"]["age_hours"]

    bill = [_row("t1", "m1", ago(66), direction="inbound", counterparty="SUPPLIER",
                 purpose="SUPPLIER_INVOICE", speech_act="OBLIGATION"),
            _row("t1", "m2", ago(1), direction="outbound", counterparty="SUPPLIER",
                 purpose="FOLLOW_UP", speech_act="FYI", from_email="orcamentos@lindoservico.pt")]
    [b] = build_fila(bill, now=NOW)
    assert b["clock"]["anchored_at_last"] is False                        # the bill is, not our mail
    assert b["clock"]["gap_hours"] < 2 < b["clock"]["age_hours"]          # the two genuinely differ


def test_legacy_fallback_when_no_speech_act():
    # Pre-re-triage crm.db (UNKNOWN acts): the legacy fold reproduces Stage 0/1 routing exactly.
    inbound = build_fila([_row("t1", "m1", ago(6), speech_act="UNKNOWN")], now=NOW)[0]
    assert inbound["clock"]["obligation"] == "OWE_REPLY" and inbound["group"] == G_OWE
    bill = build_fila([_row("t2", "m2", ago(30), direction="inbound", counterparty="SUPPLIER",
                            purpose="SUPPLIER_INVOICE", speech_act="UNKNOWN")], now=NOW)[0]
    assert bill["clock"]["state"] == TO_PAY and bill["group"] == G_PAY


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


def test_fold_unions_typed_attach_kinds_across_the_thread():
    """Typed 📎 (v4, ADR-034): the folded thread carries the UNION of its messages' attach_kinds, so
    a thread where one message brought a DWG and another a PDF shows both on the Fila row — sorted,
    deduped, and empty for a thread with no typed attachments."""
    rows = [_row("t1", "m1", ago(10), attach_kinds="cad,pdf"),
            _row("t1", "m2", ago(5), attach_kinds="pdf,img"),
            _row("t1", "m3", ago(2))]           # no attachments — contributes nothing
    [s] = fold_threads(rows)
    assert s.attach_kinds == ["cad", "img", "pdf"]              # union, sorted, deduped
    # and build_fila projects it onto the row for the JS layer
    [r] = build_fila(rows, now=NOW)
    assert r["attach_kinds"] == ["cad", "img", "pdf"]
    # a thread with no typed attachments carries an empty list, never a phantom
    plain = [_row("t2", "n1", ago(3))]
    assert fold_threads(plain)[0].attach_kinds == []


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


# ── ADR-033 P2 — momentum («Ritmo») + money parsing, deterministic ──────────

def test_momentum_single_message_fresh_vs_stalled():
    from email2data.cockpit import momentum
    assert momentum([_parse_dt(ago(2))], NOW) == "active"      # fresh single message
    assert momentum([_parse_dt(ago(100))], NOW) == "stalled"   # old single message (>72h)
    assert momentum([], NOW) == "stalled"                      # no dates: nothing moving


def test_momentum_cadence_bands():
    """gap ≤ max(48h, 1.5×cadence) = active; ≤ 3×cadence = slowing; else stalled (design §8)."""
    from email2data.cockpit import momentum
    cadence24 = [_parse_dt(ago(84)), _parse_dt(ago(60)), _parse_dt(ago(36))]  # 24h cadence
    assert momentum(cadence24 + [_parse_dt(ago(12))], NOW) == "active"    # gap 12 ≤ 48
    assert momentum([_parse_dt(ago(132)), _parse_dt(ago(108)),
                     _parse_dt(ago(84)), _parse_dt(ago(60))], NOW) == "slowing"   # gap 60 ∈ (48, 72]
    assert momentum([_parse_dt(ago(172)), _parse_dt(ago(148)),
                     _parse_dt(ago(124)), _parse_dt(ago(100))], NOW) == "stalled"  # gap 100 > 72


def test_build_fila_rows_carry_momentum():
    [r] = build_fila([_row("t1", "m1", ago(6))], now=NOW)
    assert r["momentum"] in ("active", "slowing", "stalled")


def test_money_value_parses_pt_formats():
    from email2data.cockpit import money_value
    assert money_value("€ 1.234,56") == 1234.56
    assert money_value("1.200") == 1200.0          # PT thousands, not 1.2
    assert money_value("1200,50 EUR") == 1200.5
    assert money_value("160€") == 160.0
    assert money_value("12,5") == 12.5
    assert money_value("sem valor") is None
    assert money_value("") is None and money_value(None) is None


# ── ADR-033 P3 — Adiar (snooze) wake rules: never silently bin a client ─────

def test_snoozed_thread_sleeps_until_its_time():
    rows = [_row("t1", "m1", ago(6))]
    sn = {"t1": {"until_ts": ago(-24), "created_ts": ago(2)}}      # wakes in 24h
    assert build_fila(rows, snoozes=sn, now=NOW) == []             # asleep → out of the active queue
    [r] = build_fila(rows, snoozes=sn, now=NOW, include_resolved=True)
    assert r["snoozed_until"] == ago(-24)                          # the ledger shows WHEN it wakes


def test_snooze_wakes_on_time():
    sn = {"t1": {"until_ts": ago(1), "created_ts": ago(48)}}       # wake time passed 1h ago
    [r] = build_fila([_row("t1", "m1", ago(6))], snoozes=sn, now=NOW)
    assert r["clock"]["state"] == WE_OWE                           # back in the queue


def test_snooze_wakes_on_new_inbound_never_silently_bins():
    """THE load-bearing rule (non-negotiable #2): a thread hidden by the human can never be lost to
    the counterparty's move — a new inbound after the snooze was created wakes it immediately,
    days before its timer."""
    rows = [_row("t1", "m1", ago(6)),
            _row("t1", "m2", ago(1))]                              # client wrote again 1h ago
    sn = {"t1": {"until_ts": ago(-72), "created_ts": ago(3)}}      # snoozed 3h ago, until +3 days
    [r] = build_fila(rows, snoozes=sn, now=NOW)
    assert r["clock"]["state"] == WE_OWE                           # awake, not waiting for the timer
