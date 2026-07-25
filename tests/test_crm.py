"""CRM PoC: participant extraction + contact rollup (deterministic, no LLM)."""

import json

from email2data.crm import CrmStore, attach_kinds, participants


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
               to=[{"email": "diogo@lindoservico.pt", "name": "Diogo"}],
               cc=[{"email": "ana@cliente.pt", "name": "Ana"}],
               reply_to={"email": "vendas@cliente.pt", "name": "Vendas"})
    roles = {e: r for e, _, r in participants(env)}
    assert roles == {"joao@cliente.pt": "from", "diogo@lindoservico.pt": "to",
                     "ana@cliente.pt": "cc", "vendas@cliente.pt": "reply_to"}


def test_rollup_counts_recency_and_types(tmp_path):
    s = CrmStore(tmp_path / "crm.db").connect()
    # two emails from the same client contact, different dates/purposes
    s.record(_env("m1", {"email": "joao@cliente.pt", "name": "João"},
                  to=[{"email": "diogo@lindoservico.pt", "name": "Diogo"}], date="2026-05-10"),
             _v(purpose="ESTIMATE_REQUEST_FROM_CLIENT"))
    s.record(_env("m2", {"email": "joao@cliente.pt", "name": "João C."},
                  to=[{"email": "diogo@lindoservico.pt"}], date="2026-05-20"),
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
                  to=[{"email": "diogo@lindoservico.pt"}]), _v())
    by_email = {r["email"]: r for r in s.top_contacts(external_only=False)}
    assert by_email["diogo@lindoservico.pt"]["is_internal"] == 1
    assert by_email["joao@cliente.pt"]["is_internal"] == 0
    assert all(r["email"] != "diogo@lindoservico.pt" for r in s.top_contacts(external_only=True))
    s.close()


def test_counts_and_interaction_idempotent(tmp_path):
    s = CrmStore(tmp_path / "crm.db").connect()
    env, v = _env("m1", {"email": "a@x.pt"}, to=[{"email": "b@y.pt"}]), _v()
    s.record(env, v)
    s.record(env, v)  # same message_id -> interaction replaced, contacts NOT re-bumped
    assert s.counts()["interactions"] == 1
    assert {r["email"]: r["msg_count"] for r in s.top_contacts(external_only=False)} == {"a@x.pt": 1, "b@y.pt": 1}
    s.close()


def test_attach_kinds_derivation_ranked_and_deduped():
    """Typed 📎 (v4, ADR-034): distinct categories, ranked cad>vetor>pdf>folha>img>doc>zip, from the
    envelope's real attachment shape ({"filename",...}). For a fabrication shop the CAD/vector/PDF
    distinction is «can I quote without opening the file?»."""
    # ranked, not filename order: a PDF then a DWG then a JPG comes back cad,pdf,img
    assert attach_kinds([{"filename": "quote.pdf"}, {"filename": "part.DWG"}, {"filename": "photo.jpg"}]) \
        == "cad,pdf,img"
    # deduped: two images collapse to one «img»
    assert attach_kinds([{"filename": "a.png"}, {"filename": "b.jpeg"}]) == "img"
    # the envelope key is «filename», not «name» — this is the bug the wrong key would have hidden
    assert attach_kinds([{"filename": "d.step"}]) == "cad"
    # unknown / extensionless / empty → no phantom category
    assert attach_kinds([{"filename": "noext"}, {"filename": "weird.qqq"}]) == ""
    assert attach_kinds([]) == ""
    # bare strings and the legacy «name» key still work (robust to caller shape)
    assert attach_kinds(["drawing.dxf", {"name": "sheet.xlsx"}]) == "cad,folha"


def test_record_stores_attach_kinds_and_all_interactions_surfaces_it(tmp_path):
    """record() computes attach_kinds at write time (it is data, not a view) and all_interactions()
    (SELECT *) carries it, so cockpit.fold_threads can union it onto the Fila row."""
    s = CrmStore(tmp_path / "crm.db").connect()
    env = _env("m1", {"email": "joao@cliente.pt"}, to=[{"email": "diogo@lindoservico.pt"}])
    env["attachments"] = [{"filename": "peca.dwg", "content_type": "image/vnd.dwg", "size_bytes": 100},
                          {"filename": "orcamento.pdf", "content_type": "application/pdf", "size_bytes": 50}]
    s.record(env, _v())
    # a message with no attachments stores NULL, never an empty phantom string
    s.record(_env("m2", {"email": "ana@cliente.pt"}, to=[{"email": "diogo@lindoservico.pt"}]), _v())
    by_mid = {it["message_id"]: it for it in s.all_interactions()}
    assert by_mid["m1"]["attach_kinds"] == "cad,pdf"
    assert by_mid["m1"]["has_attach"] == 1
    assert by_mid["m2"]["attach_kinds"] is None


def test_record_persists_speech_act_for_the_obligation_fold(tmp_path):
    """ADR-036: record() writes the speech_act column and all_interactions() (SELECT *) surfaces it,
    so cockpit.derive_obligation can fold it. A verdict without the key stores "" → legacy fold."""
    s = CrmStore(tmp_path / "crm.db").connect()
    s.record(_env("m1", {"email": "laminex@x.pt"}, to=[{"email": "geral@lindoservico.pt"}]),
             {**_v(cp="SUPPLIER", purpose="SUPPLIER_INVOICE"), "speech_act": "OBLIGATION"})
    s.record(_env("m2", {"email": "ana@cliente.pt"}, to=[{"email": "diogo@lindoservico.pt"}]), _v())
    by_mid = {it["message_id"]: it for it in s.all_interactions()}
    assert by_mid["m1"]["speech_act"] == "OBLIGATION"
    assert by_mid["m2"]["speech_act"] == ""      # absent in the verdict → empty (fold reads it as UNKNOWN)
    assert by_mid["m2"]["has_attach"] == 0
    s.close()
