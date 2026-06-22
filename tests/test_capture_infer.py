"""LLM project inference + capture field extraction (capture_infer.py — Increment 2, ADR-019 §4-5).

Pins the never-trust-the-model contract: extracted values are coerced (known keys only, trimmed, mapped
to a project field ADDRESS), confidences clamped, a hallucinated project id dropped, and EVERY LLM
failure degrades to empty so the capture survives and nothing is ever auto-applied (R9). The LLM is
mocked — these are pure-logic tests, no network.
"""

from __future__ import annotations

import pytest

from email2data import capture_infer as ci, llm


def _mock(monkeypatch, value):
    """Make llm.call (as seen by capture_infer) return ``value``, or raise if it's an Exception."""
    def fake(*a, **k):
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(ci.llm, "call", fake)


def test_extract_coerces_known_keys_and_addresses_item_fields(monkeypatch):
    _mock(monkeypatch, {"material": "inox 304", "deadline": "2026-07-01", "thickness": "  ",
                        "bogus": "x", "confidence": 0.9})
    r = ci.extract_fields("inox, prazo 1 jul", client=object(), cfg={})
    # item-scope 'material' -> 'material#0'; job-scope 'deadline' -> 'deadline'; blank + unknown dropped
    assert r["fields"] == {"material#0": "inox 304", "deadline": "2026-07-01"}
    assert r["confidence"] == 0.9


def test_extract_clamps_confidence_and_is_zero_with_no_fields(monkeypatch):
    _mock(monkeypatch, {"confidence": 5})        # nothing extracted -> confidence forced to 0.0
    assert ci.extract_fields("olá", object(), {}) == {"fields": {}, "confidence": 0.0}


def test_extract_degrades_on_llm_error(monkeypatch):
    _mock(monkeypatch, llm.LLMError("vertex down"))
    assert ci.extract_fields("material inox", object(), {}) == {"fields": {}, "confidence": 0.0}


def test_extract_no_text_or_no_client_does_not_call_the_model(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(ci.llm, "call", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    assert ci.extract_fields("", object(), {})["fields"] == {}          # no text
    assert ci.extract_fields("x", None, {})["fields"] == {}             # no client
    assert called["n"] == 0


def test_infer_drops_hallucinated_ids_and_ranks_by_confidence(monkeypatch):
    _mock(monkeypatch, {"candidates": [{"project_id": "p-2", "confidence": 0.6},
                                       {"project_id": "p-99", "confidence": 0.99},   # not active -> dropped
                                       {"project_id": "p-1", "confidence": 0.8}],
                        "none_match": False})
    active = [{"project_id": "p-1", "title": "A"}, {"project_id": "p-2", "title": "B"}]
    r = ci.infer_project("x", active, object(), {})
    assert [c["project_id"] for c in r["candidates"]] == ["p-1", "p-2"]   # p-99 gone, sorted desc
    assert r["none_match"] is False


def test_best_inferred_only_pre_selects_high_confidence(monkeypatch):
    _mock(monkeypatch, {"candidates": [{"project_id": "p-1", "confidence": 0.6}], "none_match": False})
    active = [{"project_id": "p-1", "title": "A"}]
    assert ci.best_inferred(ci.infer_project("x", active, object(), {})) is None   # 0.6 < 0.75 -> manual
    _mock(monkeypatch, {"candidates": [{"project_id": "p-1", "confidence": 0.9}], "none_match": False})
    assert ci.best_inferred(ci.infer_project("x", active, object(), {})) == "p-1"  # 0.9 >= 0.75


def test_infer_degrades_on_llm_error(monkeypatch):
    _mock(monkeypatch, llm.LLMError("down"))
    active = [{"project_id": "p-1", "title": "A"}]
    assert ci.infer_project("x", active, object(), {}) == {"candidates": [], "none_match": True}


@pytest.mark.parametrize("key,addr", [
    ("deadline", "deadline"), ("material", "material#0"), ("dimensions", "dimensions#0"),
    ("budget", "budget"), ("process", None), ("nonsense", None)])
def test_field_address_maps_scope(key, addr):
    assert ci.field_address(key) == addr
