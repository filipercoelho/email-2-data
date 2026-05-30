"""Phase A: deterministic JobSpec assembly + Gate-1 readiness."""

from email2data.jobspec import MUST, build_jobspec, confirm, readiness, score_drafts

RESULT = {"message_id": "m1", "subject": "Pedido de orçamento", "counterparty": "CLIENT",
          "purpose": "ESTIMATE_REQUEST_FROM_CLIENT",
          "entities": {"product_or_service": "corte laser acrílico", "deadline": "2026-05-29", "money": None}}
ENV_NOATT = {"attachments": [], "subject": "x", "body_text": "b"}
ENV_ATT = {"attachments": [{"filename": "PO.pdf", "content_type": "application/pdf"}], "subject": "x", "body_text": "b"}


def test_build_maps_existing_signals_with_provenance():
    s = build_jobspec(RESULT, ENV_NOATT)
    assert s.fields["item"].value == "corte laser acrílico" and s.fields["item"].source == "llm"
    assert s.fields["deadline"].value == "2026-05-29"
    assert s.fields["client_identity"].value == "CLIENT" and s.fields["client_identity"].source == "offline"
    assert s.fields["design_ready"].value is None      # no attachment
    assert s.fields["material"].value is None          # not drafted yet


def test_attachment_sets_design_ready_and_review_flag():
    s = build_jobspec(RESULT, ENV_ATT)
    assert s.has_attachment and s.fields["design_ready"].source == "offline"
    assert readiness(s)["attachment_to_review"] is True   # must-haves missing + attachment present


def test_draft_fills_semantic_fields_as_llm():
    s = build_jobspec(RESULT, ENV_NOATT, draft={"material": "acrílico", "quantity": "50 peças", "dimensions": None})
    assert s.fields["material"].value == "acrílico" and s.fields["material"].source == "llm"
    assert s.fields["quantity"].value == "50 peças"
    assert s.fields["dimensions"].value is None           # null draft doesn't fill


def test_readiness_missing_and_questions():
    rd = readiness(build_jobspec(RESULT, ENV_NOATT))
    assert not rd["estimable"]
    assert "material" in rd["missing"] and "thickness" in rd["missing"]
    assert any("material" in q.lower() for q in rd["questions"])


def test_confirm_is_authoritative_and_can_reach_estimable():
    s = build_jobspec(RESULT, ENV_NOATT)
    for k in MUST:
        confirm(s, k, "valor")
    rd = readiness(s)
    assert rd["estimable"] is True and rd["coverage"] == 1.0
    assert s.fields["material"].source == "user" and s.fields["material"].confirmed


def test_score_drafts_presence_agreement():
    specs = [{"message_id": "m1", "fields": {"material": {"value": "acrílico"}, "thickness": {"value": None}}}]
    labels = {"m1": {"material": "acrílico", "thickness": ""}}
    out = score_drafts(specs, labels)
    assert out["per_field_agreement"]["material"] == 1.0   # both filled
    assert out["per_field_agreement"]["thickness"] == 1.0  # both blank
    assert out["n"] == 1
