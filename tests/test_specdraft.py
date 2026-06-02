"""Phase B: spec-draft message build + coercion + Gemini path (no network)."""

import json
from types import SimpleNamespace

from email2data.specdraft import build_spec_message, coerce_spec, draft


def test_coerce_keeps_known_nullifies_blank_and_clamps_supplied():
    out = coerce_spec({"line_items": [{"material": "  acrílico ", "dimensions": "", "quantity": "50 peças",
                                       "unknown": "x"}],
                       "material_supplied_by": "BOGUS", "delivery": "  "})
    assert len(out["line_items"]) == 1
    item = out["line_items"][0]
    assert item["material"] == "acrílico" and item["dimensions"] is None
    assert item["quantity"] == "50 peças"
    assert "unknown" not in item                   # unknown per-item keys discarded
    assert out["material_supplied_by"] is None     # not in the enum -> dropped
    assert out["delivery"] is None                 # blank -> None


def test_coerce_drops_empty_items_and_passes_supplied_enum():
    out = coerce_spec({"line_items": [{"item": "placa"}, {"material": None, "dimensions": ""}],
                       "material_supplied_by": "client"})
    assert len(out["line_items"]) == 1 and out["line_items"][0]["item"] == "placa"  # all-empty item dropped
    assert out["material_supplied_by"] == "client"


def test_build_message_includes_subject_attachments_body():
    env = {"subject": "Orçamento", "body_text": "corpo",
           "attachments": [{"filename": "PO.pdf", "content_type": "application/pdf"}]}
    m = build_spec_message(env)
    assert "Orçamento" in m and "PO.pdf" in m and "corpo" in m


class _FakeGemini:
    def __init__(self, text):
        self.models = SimpleNamespace(generate_content=lambda **kw: SimpleNamespace(text=text))


def test_draft_gemini_path_coerces():
    c = _FakeGemini(json.dumps({"line_items": [{"material": "MDF", "thickness": "3 mm", "junk": 1}],
                                "material_supplied_by": "client"}))
    settings = {"llm": {"provider": "vertex_gemini", "model": "gemini-2.5-flash", "max_retries": 2, "max_tokens": 256}}
    out = draft({"subject": "s", "attachments": [], "body_text": "b"}, "pb", c, settings)
    assert out["line_items"][0]["material"] == "MDF" and out["line_items"][0]["thickness"] == "3 mm"
    assert out["material_supplied_by"] == "client"
    assert "junk" not in out["line_items"][0]
