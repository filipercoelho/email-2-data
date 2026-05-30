"""Phase B: spec-draft message build + coercion + Gemini path (no network)."""

import json
from types import SimpleNamespace

from email2data.specdraft import build_spec_message, coerce_spec, draft


def test_coerce_keeps_known_nullifies_blank_and_clamps_supplied():
    out = coerce_spec({"material": "  acrílico ", "dimensions": "", "quantity": "50 peças",
                       "material_supplied_by": "BOGUS", "unknown": "x"})
    assert out["material"] == "acrílico" and out["dimensions"] is None
    assert out["quantity"] == "50 peças"
    assert out["material_supplied_by"] is None    # not in the enum -> dropped
    assert "unknown" not in out                    # unknown keys discarded


def test_coerce_supplied_enum_passes_through():
    assert coerce_spec({"material_supplied_by": "client"})["material_supplied_by"] == "client"


def test_build_message_includes_subject_attachments_body():
    env = {"subject": "Orçamento", "body_text": "corpo",
           "attachments": [{"filename": "PO.pdf", "content_type": "application/pdf"}]}
    m = build_spec_message(env)
    assert "Orçamento" in m and "PO.pdf" in m and "corpo" in m


class _FakeGemini:
    def __init__(self, text):
        self.models = SimpleNamespace(generate_content=lambda **kw: SimpleNamespace(text=text))


def test_draft_gemini_path_coerces():
    c = _FakeGemini(json.dumps({"material": "MDF", "thickness": "3 mm",
                                "material_supplied_by": "client", "junk": 1}))
    settings = {"llm": {"provider": "vertex_gemini", "model": "gemini-2.5-flash", "max_retries": 2, "max_tokens": 256}}
    out = draft({"subject": "s", "attachments": [], "body_text": "b"}, "pb", c, settings)
    assert out["material"] == "MDF" and out["thickness"] == "3 mm" and out["material_supplied_by"] == "client"
    assert "junk" not in out
