"""Deterministic extraction: high precision via format anchors + NIF checksum."""

from email2data.extract import extract_values, render_candidates


def test_amount_requires_currency_anchor():
    v = extract_values("", "O valor é 1.234,56 € mais portes de 50 euros.")
    assert "1.234,56 €" in v["amounts"]
    assert any("50" in a for a in v["amounts"])
    # bare numbers (dimensions, quantities) must NOT be picked up as money
    assert extract_values("", "cortar 50 peças em acrílico 3mm")["amounts"] == []


def test_nif_anchored_and_checksum_valid():
    v = extract_values("", "Contribuinte nº 501442600, com os melhores cumprimentos")
    assert v["nif"] == "501442600"


def test_nif_rejected_without_anchor_or_bad_checksum():
    assert extract_values("", "encomenda 501442600 unidades")["nif"] is None   # no anchor
    assert extract_values("", "NIF 123456788")["nif"] is None                  # checksum fails


def test_iban_normalized():
    v = extract_values("", "Pagamento para PT50 0002 0123 1234 5678 9015 4 obrigado")
    assert v["iban"] == "PT50000201231234567890154"


def test_explicit_date_yes_relative_no():
    v = extract_values("", "Entrega até 31/05/2026, ou então até sexta.")
    assert "31/05/2026" in v["dates"]
    assert all("sexta" not in d for d in v["dates"])


def test_doc_number_candidate():
    v = extract_values("Fatura FT 2026/123", "referente à encomenda 4500987")
    joined = " ".join(v["doc_numbers"])
    assert "2026/123" in joined and "4500987" in joined


def test_render_candidates_empty_and_populated():
    assert render_candidates(extract_values("olá", "tudo bem")) == ""
    out = render_candidates(extract_values("", "NIF 501442600 valor 10 €"))
    assert "nif=501442600" in out and "amounts_found=" in out
