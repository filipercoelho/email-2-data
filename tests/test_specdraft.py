"""Phase B: spec-draft message build + coercion + Gemini path (no network)."""

import inspect
import json
from types import SimpleNamespace

import pytest

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


# ── tier: a per-call model override for the on-demand re-extract ─────────────────────────────────

class _RecordingGemini:
    """Captures the kwargs the SDK is called with, so we can see which model was actually used."""

    def __init__(self, text):
        self.kwargs = []
        self.models = SimpleNamespace(
            generate_content=lambda **kw: (self.kwargs.append(kw), SimpleNamespace(text=text))[1])


TIERED_SETTINGS = {"llm": {
    "provider": "vertex_gemini", "model": "gemini-2.5-flash", "max_retries": 2, "max_tokens": 1024,
    "context_cache": False,
    "tiers": {"heavy": {"model": "gemini-2.5-pro", "max_tokens": 8192, "thinking_budget": 4096}},
}}

_SPEC_JSON = json.dumps({"line_items": [{"material": "corten"}]})


def test_tier_is_keyword_only():
    """Positional callers (specbuild, the CLI, the tests above) must keep working unchanged."""
    params = inspect.signature(draft).parameters
    assert params["tier"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["tier"].default is None
    c = _RecordingGemini(_SPEC_JSON)
    with pytest.raises(TypeError):
        draft({"subject": "s", "attachments": [], "body_text": "b"}, "pb", c, TIERED_SETTINGS, "heavy")


def test_tier_applies_the_tier_model_without_repointing_the_shared_settings():
    c = _RecordingGemini(_SPEC_JSON)
    env = {"subject": "s", "attachments": [], "body_text": "b"}
    out = draft(env, "pb", c, TIERED_SETTINGS, tier="heavy")
    assert out["line_items"][0]["material"] == "corten"
    assert c.kwargs[0]["model"] == "gemini-2.5-pro"
    assert c.kwargs[0]["config"].max_output_tokens == 8192
    assert TIERED_SETTINGS["llm"]["model"] == "gemini-2.5-flash"   # shared settings untouched


def test_no_tier_keeps_the_configured_default_model():
    c = _RecordingGemini(_SPEC_JSON)
    env = {"subject": "s", "attachments": [], "body_text": "b"}
    draft(env, "pb", c, TIERED_SETTINGS)
    draft(env, "pb", c, TIERED_SETTINGS, tier="nonexistent")
    assert [kw["model"] for kw in c.kwargs] == ["gemini-2.5-flash", "gemini-2.5-flash"]
