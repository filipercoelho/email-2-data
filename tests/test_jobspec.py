"""Phase A: deterministic JobSpec assembly + Gate-1 readiness (multi-item)."""

from email2data.jobspec import (ITEM_MUST, JOB_MUST, JobSpec, address, askables, build_jobspec,
                                confirm, readiness, score_drafts)

RESULT = {"message_id": "m1", "subject": "Pedido de orçamento", "counterparty": "CLIENT",
          "purpose": "ESTIMATE_REQUEST_FROM_CLIENT",
          "entities": {"product_or_service": "corte laser acrílico", "deadline": "2026-05-29", "money": None}}
ENV_NOATT = {"attachments": [], "subject": "x", "body_text": "b"}
ENV_ATT = {"attachments": [{"filename": "PO.pdf", "content_type": "application/pdf"}], "subject": "x", "body_text": "b"}


def _confirm_all_musts(s):
    for k in JOB_MUST:
        confirm(s, k, "valor")
    for i in range(len(s.items)):
        for k in ITEM_MUST:
            confirm(s, address(k, i), "valor")


def test_build_maps_existing_signals_with_provenance():
    s = build_jobspec(RESULT, ENV_NOATT)
    assert len(s.items) == 1                                       # one seeded line item
    assert s.items[0]["item"].value == "corte laser acrílico" and s.items[0]["item"].source == "llm"
    assert s.job_fields["deadline"].value == "2026-05-29"
    assert s.job_fields["client_identity"].value == "CLIENT" and s.job_fields["client_identity"].source == "offline"
    assert s.job_fields["design_ready"].value is None             # no attachment
    assert s.items[0]["material"].value is None                   # not drafted yet


def test_draft_line_items_become_separate_items():
    draft = {"line_items": [
        {"item": "placas", "material": "acrílico", "thickness": "3 mm", "quantity": "20"},
        {"item": "stickers", "material": "vinil", "colour_finish": "mate", "quantity": "100"},
    ], "material_supplied_by": "us", "delivery": None}
    s = build_jobspec(RESULT, ENV_NOATT, draft=draft)
    assert len(s.items) == 2
    assert s.items[0]["material"].value == "acrílico" and s.items[0]["material"].source == "llm"
    assert s.items[1]["item"].value == "stickers" and s.items[1]["colour_finish"].value == "mate"
    assert s.job_fields["material_supplied_by"].value == "us" and s.job_fields["material_supplied_by"].source == "llm"


def test_attachment_sets_design_ready_and_review_flag():
    s = build_jobspec(RESULT, ENV_ATT)
    assert s.has_attachment and s.job_fields["design_ready"].source == "offline"
    assert readiness(s)["attachment_to_review"] is True


def test_readiness_is_per_item_and_questions_are_deduped():
    draft = {"line_items": [{"item": "a", "material": "MDF"}, {"item": "b", "material": "PVC"}]}
    rd = readiness(build_jobspec(RESULT, ENV_NOATT, draft=draft))
    assert not rd["estimable"] and rd["n_items"] == 2
    assert "thickness#0" in rd["missing"] and "thickness#1" in rd["missing"]   # missing per item
    # the same base field missing on both items asks the question only once
    assert sum(1 for q in rd["questions"] if "espessura" in q.lower()) == 1


def test_confirm_is_authoritative_and_can_reach_estimable():
    s = build_jobspec(RESULT, ENV_NOATT)
    _confirm_all_musts(s)
    rd = readiness(s)
    assert rd["estimable"] is True and rd["coverage"] == 1.0
    assert s.items[0]["material"].source == "user" and s.items[0]["material"].confirmed


def test_multi_item_not_estimable_until_every_item_complete():
    draft = {"line_items": [{"item": "a"}, {"item": "b"}]}
    s = build_jobspec(RESULT, ENV_ATT, draft=draft)
    for k in JOB_MUST:
        confirm(s, k, "v")
    for k in ITEM_MUST:                                            # complete item 0 only
        confirm(s, address(k, 0), "v")
    assert readiness(s)["estimable"] is False                     # item 1 still missing must-haves
    for k in ITEM_MUST:
        confirm(s, address(k, 1), "v")
    assert readiness(s)["estimable"] is True


def test_from_dict_round_trips():
    s = build_jobspec(RESULT, ENV_ATT, draft={"line_items": [{"material": "acrílico"}]})
    s2 = JobSpec.from_dict(s.to_dict())
    assert s2.message_id == s.message_id and s2.has_attachment == s.has_attachment
    assert s2.items[0]["material"].value == "acrílico" and s2.items[0]["item"].value == s.items[0]["item"].value
    assert readiness(s2) == readiness(s)


def test_from_dict_migrates_legacy_flat_shape():
    legacy = {"message_id": "m1", "subject": "s", "has_attachment": False,
              "fields": {"item": {"value": "placa", "source": "llm", "confirmed": False},
                         "material": {"value": "MDF", "source": "llm", "confirmed": False},
                         "deadline": {"value": "2026-06-01", "source": "llm", "confirmed": False}}}
    s = JobSpec.from_dict(legacy)
    assert len(s.items) == 1 and s.items[0]["material"].value == "MDF"      # item-scope -> the one item
    assert s.job_fields["deadline"].value == "2026-06-01"                  # job-scope -> job_fields


def test_score_drafts_presence_agreement_base_field_level():
    specs = [{"message_id": "m1",
              "items": [{"material": {"value": "acrílico"}}, {"material": {"value": None}}],
              "job_fields": {"delivery": {"value": None}}}]
    labels = {"m1": {"material": "acrílico", "delivery": ""}}
    out = score_drafts(specs, labels)
    assert out["per_field_agreement"]["material"] == 1.0   # drafted on some item & labeled
    assert out["per_field_agreement"]["delivery"] == 1.0   # both blank
    assert out["n"] == 1


def test_askables_groups_gaps_by_tier_and_default_ticks_only_musts():
    """The composer's selectable prompts: must-gaps pre-ticked, should-gaps offered but off,
    the internal process note flagged + never default. Present fields drop out."""
    s = build_jobspec(RESULT, ENV_NOATT)               # one seeded item, most musts missing
    confirm(s, address("material", 0), "acrílico")     # fill one item-must → it leaves the list
    asks = {a["key"]: a for a in askables(s)}

    assert "material" not in asks                       # filled → not asked
    assert asks["thickness"]["tier"] == "must" and asks["thickness"]["default"] is True
    assert asks["colour_finish"]["tier"] == "should" and asks["colour_finish"]["default"] is False
    # the process field is internal — flagged, and never sent to the client by default
    assert asks["process"]["internal"] is True and asks["process"]["default"] is False
    # registry order preserved
    keys = [a["key"] for a in askables(s)]
    assert keys.index("dimensions") < keys.index("thickness") < keys.index("quantity")


def test_askables_item_gap_when_any_line_item_missing_it():
    draft = {"line_items": [
        {"item": "placas", "material": "acrílico", "thickness": "3mm", "quantity": "20"},
        {"item": "stickers", "material": "vinil", "quantity": "100"},   # no thickness on item 2
    ]}
    s = build_jobspec(RESULT, ENV_NOATT, draft=draft)
    keys = [a["key"] for a in askables(s)]
    assert "thickness" in keys      # asked once even though only item 2 lacks it (deduped)
