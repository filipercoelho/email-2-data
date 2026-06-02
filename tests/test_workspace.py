"""The precious write layer: decisions persist, overlay the auto-spec, recompute readiness, survive re-runs."""

from email2data.jobspec import ITEM_MUST, JOB_MUST, address, build_jobspec
from email2data.workspace import Workspace

RESULT = {"message_id": "m1", "subject": "Pedido de orçamento", "counterparty": "CLIENT",
          "purpose": "ESTIMATE_REQUEST_FROM_CLIENT",
          "entities": {"product_or_service": "troféus", "deadline": None, "money": None}}
ENV = {"attachments": [{"filename": "spec.pdf", "content_type": "application/pdf"}], "subject": "x", "body_text": "b"}
SPEC = build_jobspec(RESULT, ENV).to_dict()   # auto-spec dict (read layer), one line item


def test_confirm_persists_and_overlays(tmp_path):
    ws = Workspace(tmp_path / "w.db").connect()
    ws.confirm("m1", address("material", 0), "acrílico")
    spec, rd = ws.merge(SPEC)
    assert spec.items[0]["material"].value == "acrílico"
    assert spec.items[0]["material"].source == "user" and spec.items[0]["material"].confirmed
    assert address("material", 0) not in rd["missing"]
    ws.close()


def test_confirming_all_must_haves_makes_estimable(tmp_path):
    ws = Workspace(tmp_path / "w.db").connect()
    for k in JOB_MUST:
        ws.confirm("m1", k, "ok")
    for k in ITEM_MUST:
        ws.confirm("m1", address(k, 0), "ok")
    _, rd = ws.merge(SPEC)
    assert rd["estimable"] is True and rd["coverage"] == 1.0
    ws.close()


def test_decisions_survive_reconnect_ie_a_triage_rerun(tmp_path):
    db = tmp_path / "w.db"
    Workspace(db).connect().confirm("m1", address("quantity", 0), "20 unidades")
    # a fresh Workspace on the same db = the pipeline re-ran; the human decision is still there
    ws2 = Workspace(db).connect()
    spec, _ = ws2.merge(SPEC)
    assert spec.items[0]["quantity"].value == "20 unidades" and spec.items[0]["quantity"].source == "user"
    ws2.close()


def test_add_item_then_confirm_second_item(tmp_path):
    ws = Workspace(tmp_path / "w.db").connect()
    ws.set_item_count("m1", 2)
    spec, _ = ws.merge(SPEC)
    assert len(spec.items) == 2                                  # padded with an empty item
    ws.confirm("m1", address("item", 1), "expositor")
    spec, _ = ws.merge(SPEC)
    assert spec.items[1]["item"].value == "expositor"
    ws.close()


def test_remove_item_renumbers_higher_rows(tmp_path):
    ws = Workspace(tmp_path / "w.db").connect()
    ws.set_item_count("m1", 3)
    ws.confirm("m1", address("item", 0), "A")
    ws.confirm("m1", address("item", 1), "B")
    ws.confirm("m1", address("item", 2), "C")
    ws.remove_item("m1", 1)                                      # drop the middle item
    spec, _ = ws.merge(SPEC)
    assert len(spec.items) == 2
    assert spec.items[0]["item"].value == "A" and spec.items[1]["item"].value == "C"  # C shifted down
    ws.close()


def test_clear_removes_a_decision(tmp_path):
    ws = Workspace(tmp_path / "w.db").connect()
    ws.confirm("m1", address("material", 0), "MDF")
    ws.clear("m1", address("material", 0))
    assert ws.decisions_for("m1") == {}
    ws.close()


def test_merge_does_not_mutate_other_jobs(tmp_path):
    ws = Workspace(tmp_path / "w.db").connect()
    ws.confirm("OTHER", address("material", 0), "vinil")
    spec, _ = ws.merge(SPEC)              # m1 has no decisions
    assert spec.items[0]["material"].value is None
    ws.close()
