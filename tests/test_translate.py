"""Translate-to-English reading aid (ADR-032): the LLM call is faithful (temperature 0), fails loudly
on empty, and a missing playbook falls back to the built-in rules rather than a permissive prompt.
"""

import pytest

from email2data import llm, translate


def test_translate_to_english_calls_the_llm_faithfully_and_strips():
    seen = {}

    def fake_call(client, cfg, system, user, **kw):
        seen.update(system=system, user=user, text=kw.get("text"), temperature=kw.get("temperature"))
        return "  Good morning, the price is 160€ by 30/09.  "

    orig = llm.call
    llm.call = fake_call
    try:
        out = translate.translate_to_english("Bom dia, o preço é 160€ até 30/09.", "PB", object(), {})
        assert out == "Good morning, the price is 160€ by 30/09."   # stripped
        assert seen["system"] == "PB" and seen["text"] is True      # playbook as system, plain text
        assert seen["temperature"] == 0.0                           # faithful, not creative
        assert "160€" in seen["user"]                               # the body is what we translate
    finally:
        llm.call = orig


def test_translate_to_english_raises_on_empty_instead_of_echoing():
    orig = llm.call
    llm.call = lambda *a, **k: "   "
    try:
        with pytest.raises(llm.LLMError):
            translate.translate_to_english("x", "PB", object(), {})
    finally:
        llm.call = orig


def test_load_playbook_falls_back_without_becoming_permissive(tmp_path):
    fb = translate.load_playbook(tmp_path / "nope.md")
    assert fb == translate.DEFAULT_TRANSLATION_PLAYBOOK
    assert "faithfully" in fb and "EXACTLY" in fb                   # the hard rules survive the fallback

    f = tmp_path / "pb.md"
    f.write_text("  translate nicely  ", encoding="utf-8")
    assert translate.load_playbook(f) == "translate nicely"
    f.write_text("   ", encoding="utf-8")
    assert translate.load_playbook(f) == translate.DEFAULT_TRANSLATION_PLAYBOOK
