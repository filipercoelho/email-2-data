"""Tests for the DESCRIÇÃO composer (ADR-030).

The load-bearing property is zero-hallucination: a fact that is not confirmed in the JobSpec must not
appear in text that goes to a client with a price attached. It must appear as a VISIBLE gap instead —
a silently omitted thickness reads as a deliberate choice, which is the expensive failure.
"""

from __future__ import annotations

import pytest

from email2data import descdraft
from email2data.jobspec import JobSpec, SpecField


def _spec(**item_fields) -> JobSpec:
    """A one-item JobSpec whose item fields are confirmed unless a SpecField is passed explicitly."""
    item = {}
    for k, v in item_fields.items():
        item[k] = v if isinstance(v, SpecField) else SpecField(v, "user", True)
    return JobSpec(message_id="m1", subject="Letras Neauvia", items=[item])


FULL = dict(item="letras", material="EPS", thickness="100", dimensions="L 5850 x A 970 x P 485 mm",
            colour_finish="sem pintura")


# ── the deterministic block ──────────────────────────────────────────────────────────────────────

def test_full_spec_renders_the_average_prose_sentence():
    """The average corpus form: header + one flowing sentence, "Produção de <item> em <material>...".
    Not bullet lines (prose is 66% of the corpus)."""
    d = descdraft.build_description(_spec(**FULL))
    assert d.text == (
        "Letras Neauvia\n\n"
        "Produção de letras em EPS 100, c/ L 5850 x A 970 x P 485 mm, sem pintura."
    )
    assert d.gaps == []
    assert d.complete is True


def test_obs_line_is_dropped_when_empty_but_rendered_when_given():
    """The commercial caveat is genuinely optional, so an empty one must not leave a dangling
    "Obs.:" behind."""
    assert "Obs.:" not in descdraft.build_description(_spec(**FULL)).text
    d = descdraft.build_description(_spec(**FULL), observacoes="Não inclui transporte nem instalação")
    assert d.text.endswith("Obs.: Não inclui transporte nem instalação")


def test_thickness_fuses_onto_the_material():
    """House form is "em MDF de 12mm" — thickness rides on the material, not a slot of its own."""
    d = descdraft.build_description(_spec(**{**FULL, "material": "MDF", "thickness": "de 12mm"}))
    assert "em MDF de 12mm," in d.text
    assert "espessura" not in d.text.lower()


def test_title_is_passed_through_as_typed_not_uppercased():
    """The average is title/sentence case (33 vs 12 UPPER), so the header is passed through verbatim —
    forcing upper-case would be less representative, not more."""
    d = descdraft.build_description(_spec(**FULL), titulo="Placa de parque privado")
    assert d.text.splitlines()[0] == "Placa de parque privado"


# ── zero-hallucination: the reason this module exists ────────────────────────────────────────────

def test_missing_material_renders_a_visible_gap_not_an_omission():
    d = descdraft.build_description(_spec(**{k: v for k, v in FULL.items() if k != "material"}))
    assert "[[MATERIAL?]]" in d.text
    assert "MATERIAL" in d.gaps
    assert d.complete is False


def test_missing_thickness_marks_a_gap_on_the_material():
    d = descdraft.build_description(_spec(**{k: v for k, v in FULL.items() if k != "thickness"}))
    assert "em EPS [[ESPESSURA?]]," in d.text
    assert "ESPESSURA" in d.gaps


def test_unconfirmed_llm_value_is_withheld_and_reported():
    """An LLM-drafted material nobody ticked must NOT reach the client — it is a gap, and the caller is
    told a candidate exists so the UI can offer it for confirmation."""
    spec = _spec(**{**FULL, "material": SpecField("acrílico", "llm", False)})
    d = descdraft.build_description(spec)
    assert "acrílico" not in d.text
    assert "[[MATERIAL?]]" in d.text
    assert "MATERIAL" in d.gaps
    assert "MATERIAL" in d.unconfirmed


def test_require_confirmed_false_lets_the_preview_show_the_candidate():
    spec = _spec(**{**FULL, "material": SpecField("acrílico", "llm", False)})
    d = descdraft.build_description(spec, require_confirmed=False)
    assert "em acrílico 100," in d.text
    assert "MATERIAL" not in d.gaps


def test_opener_falls_back_to_the_style_default_never_to_a_specific_process():
    """"Produção de" is the modal opener (15/59) and is true of everything the shop makes, so it is
    style. A specific process would be a factual claim and must never be guessed."""
    d = descdraft.build_description(_spec(**FULL))
    assert d.text.splitlines()[2].startswith("Produção de letras em EPS")
    assert "PROCESSO" not in d.gaps
    assert "[[PROCESSO?]]" not in d.text


def test_caller_may_override_the_opener():
    d = descdraft.build_description(_spec(**FULL), processo="Produção e Fornecimento de")
    assert d.text.splitlines()[2].startswith("Produção e Fornecimento de letras em EPS")


def test_internal_process_field_never_leaks_into_client_facing_prose():
    """Regression: the registry ``process`` field is an INTERNAL manufacturing note. Wiring it to the
    opener produced "Impressão Direta HQ - 1 Face placa" — a mangled line in a document with a price
    on it. The internal note must not reach the client."""
    spec = _spec(**{**FULL, "item": "placa", "process": "Impressão Direta HQ - 1 Face"})
    d = descdraft.build_description(spec)
    assert "Impressão Direta HQ" not in d.text
    assert d.text.splitlines()[2].startswith("Produção de placa em EPS")


def test_empty_spec_is_all_gaps_and_still_returns_text():
    """A draft with holes beats no draft — the holes are marked in-band."""
    d = descdraft.build_description(JobSpec(message_id="m", subject=""))
    assert set(d.gaps) >= {"O QUÊ", "MATERIAL", "DIMENSÕES", "ACABAMENTO", "TÍTULO"}
    assert d.text.strip()
    assert d.complete is False


# ── multi-item ───────────────────────────────────────────────────────────────────────────────────

def test_multi_item_renders_one_block_per_item_with_a_single_header():
    """A single lead routinely lists several distinct pieces; the corpus carries exactly one header."""
    spec = _spec(**FULL)
    spec.items.append({k: SpecField(v, "user", True) for k, v in
                       {**FULL, "item": "base", "material": "MDF"}.items()})
    d = descdraft.build_description(spec)
    # Regression: a sentinel-header hack used to leak "[[TÍTULO?]]" between the blocks. Asserting the
    # header COUNT passed while the output was visibly broken, so assert the whole text.
    assert d.text == (
        "Letras Neauvia\n\n"
        "Produção de letras em EPS 100, c/ L 5850 x A 970 x P 485 mm, sem pintura.\n\n"
        "Produção de base em MDF 100, c/ L 5850 x A 970 x P 485 mm, sem pintura."
    )
    assert "[[" not in d.text
    assert d.gaps == []


def test_item_index_renders_that_item_alone():
    spec = _spec(**FULL)
    spec.items.append({k: SpecField(v, "user", True) for k, v in
                       {**FULL, "item": "base"}.items()})
    d = descdraft.build_description(spec, item_index=1)
    assert "Produção de base" in d.text
    assert "Produção de letras" not in d.text


# ── template loading ─────────────────────────────────────────────────────────────────────────────

def test_load_template_reads_the_fenced_block_from_the_real_playbook():
    tmpl = descdraft.load_template("config/description_playbook.md")
    assert "{titulo}" in tmpl and "{material}" in tmpl
    assert "{processo} {item} em {material}" in tmpl   # the average prose skeleton
    assert "House style" not in tmpl        # the editor note above the fence is not the template
    assert "Controlled vocabulary" not in tmpl  # nor the reference lists below it


def test_the_real_playbook_renders_the_same_block_as_the_builtin_default():
    """A drift between the shipped playbook and DEFAULT_TEMPLATE would mean the fallback silently
    changes the house style whenever the config is unreadable."""
    tmpl = descdraft.load_template("config/description_playbook.md")
    spec = _spec(**FULL)
    assert descdraft.build_description(spec, tmpl).text == descdraft.build_description(spec).text


@pytest.mark.parametrize("bad", ["", "no tokens here at all", "---\nstill no tokens\n---"])
def test_botched_playbook_falls_back_instead_of_shipping_an_empty_descritivo(tmp_path, bad):
    p = tmp_path / "pb.md"
    p.write_text(bad, encoding="utf-8")
    assert descdraft.load_template(p) == descdraft.DEFAULT_TEMPLATE


def test_missing_playbook_falls_back():
    assert descdraft.load_template("/nonexistent/nope.md") == descdraft.DEFAULT_TEMPLATE


# ── the polish guarantee (ADR-027 shape) ─────────────────────────────────────────────────────────

def test_missing_facts_flags_an_altered_measurement():
    facts = [("MATERIAL", "EPS 100"), ("DIMENSÕES", "L 5850 x A 970 x P 485 mm")]
    ok = "Produção de letras em EPS 100, L 5850 x A 970 x P 485 mm."
    assert descdraft.missing_facts(ok, facts) == []
    bad = "Produção de letras em EPS 100, L 5850 x A 970 x P 480 mm."
    assert descdraft.missing_facts(bad, facts) == ["L 5850 x A 970 x P 485 mm"]


def test_missing_facts_tolerates_a_rewrap_but_not_a_reword():
    facts = [("DIMENSÕES", "L 200 x A 300 mm")]
    assert descdraft.missing_facts("medidas:\nL 200   x A 300\nmm", facts) == []
    assert descdraft.missing_facts("medidas: 200x300mm", facts) == ["L 200 x A 300 mm"]


def test_dropped_gaps_counts_markers_the_polish_tidied_away():
    original = "Produção de placa em EPS [[ESPESSURA?]], c/ 500 x 300 mm, [[ACABAMENTO?]]."
    assert descdraft.dropped_gaps(original, original) == 0
    assert descdraft.dropped_gaps("Produção de placa em EPS, c/ 500 x 300 mm, [[ACABAMENTO?]].", original) == 1
    assert descdraft.dropped_gaps("Produção de placa em EPS, c/ 500 x 300 mm, polido.", original) == 2


def test_build_polish_message_labels_an_empty_fact_block_instead_of_leaving_it_absent():
    """An unlabelled gap in a prompt is an invitation to fill it."""
    msg = descdraft.build_polish_message("x", [])
    assert "(nada confirmado ainda)" in msg


def test_polish_raises_rather_than_silently_returning_the_unpolished_draft(monkeypatch):
    monkeypatch.setattr(descdraft.llm, "call", lambda *a, **k: "   ")
    with pytest.raises(descdraft.llm.LLMError):
        descdraft.polish_description("draft", [], "pb", object(), {})


def test_polish_returns_the_model_text_on_success(monkeypatch):
    monkeypatch.setattr(descdraft.llm, "call", lambda *a, **k: "  melhor texto  ")
    assert descdraft.polish_description("draft", [], "pb", object(), {}) == "melhor texto"


# ── idempotency (definition of done) ─────────────────────────────────────────────────────────────

def test_rendering_twice_yields_the_same_text():
    spec = _spec(**FULL)
    assert descdraft.build_description(spec).text == descdraft.build_description(spec).text
