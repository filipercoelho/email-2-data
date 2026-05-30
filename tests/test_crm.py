"""CRM PoC: participant extraction + contact rollup (deterministic, no LLM)."""

import json

from email2data.crm import CrmStore, participants


def _env(mid, frm, to=None, cc=None, date="2026-05-20", subject="s", reply_to=None):
    return {
        "message_id": mid, "date": date, "subject": subject,
        "from": frm, "reply_to": reply_to or {},
        "to": to or [], "cc": cc or [], "references": [], "in_reply_to": None, "attachments": [],
    }


def _v(cp="CLIENT", purpose="PO_FROM_CLIENT", direction="inbound"):
    return {"counterparty": cp, "purpose": purpose, "direction": direction, "priority": "HIGH", "urgency": 70}


def test_participants_roles():
    env = _env("m1", {"email": "Joao@Cliente.PT", "name": "João"},
               to=[{"email": "pedro@lindoservico.pt", "name": "Pedro"}],
               cc=[{"email": "ana@cliente.pt", "name": "Ana"}],
               reply_to={"email": "vendas@cliente.pt", "name": "Vendas"})
    roles = {e: r for e, _, r in participants(env)}
    assert roles == {"joao@cliente.pt": "from", "pedro@lindoservico.pt": "to",
                     "ana@cliente.pt": "cc", "vendas@cliente.pt": "reply_to"}


def test_rollup_counts_recency_and_types(tmp_path):
    s = CrmStore(tmp_path / "crm.db").connect()
    # two emails from the same client contact, different dates/purposes
    s.record(_env("m1", {"email": "joao@cliente.pt", "name": "João"},
                  to=[{"email": "pedro@lindoservico.pt", "name": "Pedro"}], date="2026-05-10"),
             _v(purpose="ESTIMATE_REQUEST_FROM_CLIENT"))
    s.record(_env("m2", {"email": "joao@cliente.pt", "name": "João C."},
                  to=[{"email": "pedro@lindoservico.pt"}], date="2026-05-20"),
             _v(purpose="PO_FROM_CLIENT"))
    joao = {r["email"]: r for r in s.top_contacts(external_only=False)}["joao@cliente.pt"]
    assert joao["msg_count"] == 2 and joao["from_count"] == 2
    assert joao["display_name"] == "João C."           # latest non-empty name
    assert joao["last_from_date"] == "2026-05-20"        # recency of last contact
    assert joao["is_internal"] == 0
    assert json.loads(joao["purpose_counts"]) == {"ESTIMATE_REQUEST_FROM_CLIENT": 1, "PO_FROM_CLIENT": 1}
    s.close()


def test_internal_flag_and_external_filter(tmp_path):
    s = CrmStore(tmp_path / "crm.db").connect()
    s.record(_env("m1", {"email": "joao@cliente.pt"},
                  to=[{"email": "pedro@lindoservico.pt"}]), _v())
    by_email = {r["email"]: r for r in s.top_contacts(external_only=False)}
    assert by_email["pedro@lindoservico.pt"]["is_internal"] == 1
    assert by_email["joao@cliente.pt"]["is_internal"] == 0
    assert all(r["email"] != "pedro@lindoservico.pt" for r in s.top_contacts(external_only=True))
    s.close()


def test_counts_and_interaction_idempotent(tmp_path):
    s = CrmStore(tmp_path / "crm.db").connect()
    env, v = _env("m1", {"email": "a@x.pt"}, to=[{"email": "b@y.pt"}]), _v()
    s.record(env, v)
    s.record(env, v)  # same message_id -> interaction replaced, contacts NOT re-bumped
    assert s.counts()["interactions"] == 1
    assert {r["email"]: r["msg_count"] for r in s.top_contacts(external_only=False)} == {"a@x.pt": 1, "b@y.pt": 1}
    s.close()
