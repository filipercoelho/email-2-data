"""Deterministic capture→project resolver (capture_resolve.py — ADR-019 §4 / R2 seed, Increment 1).

Pins the offline pre-filter contract: rank active projects by how strongly a capture names them, only
SUGGEST a project when the top match is unambiguous (else hand off to the human / the LLM — ADR-001),
and parse the editable capture_playbook aliases + gazetteer best-effort (a bad file degrades, never
crashes the worker). No LLM, no network.
"""

from __future__ import annotations

from email2data import capture_resolve as cr

P = [
    {"project_id": "p-1", "title": "Estante Sousa", "client_name": "João Sousa",
     "client_email": "joao@sousa.pt", "stage": "LEAD"},
    {"project_id": "p-2", "title": "Placas acrílico Acme", "client_name": "Maria",
     "client_email": "maria@acme.pt", "stage": "LEAD"},
]


def test_rank_scores_client_and_product_tokens():
    r = cr.rank_projects("o Sousa quer mais duas estantes", P)
    assert r[0]["project_id"] == "p-1" and r[0]["score"] >= 1
    assert "sousa" in r[0]["matched"]


def test_rank_is_accent_insensitive():
    # "acrilico" (no accent) must still match the project "acrílico"
    r = cr.rank_projects("precisam de placas em acrilico", P)
    assert r[0]["project_id"] == "p-2" and "acrilico" in r[0]["matched"]


def test_best_project_none_when_no_signal():
    assert cr.best_project("olá tudo bem por aí", P) is None


def test_best_project_none_on_tie():
    # both projects match "placas" equally -> ambiguous -> never guess (defer to human / LLM)
    projs = [{"project_id": "a", "title": "Placas Norte", "client_email": "", "stage": "LEAD"},
             {"project_id": "b", "title": "Placas Sul", "client_email": "", "stage": "LEAD"}]
    assert cr.best_project("preciso de placas", projs) is None


def test_aliases_expand_the_capture_text():
    # a short form the staffer says expands to the canonical term before matching
    aliases = {"vdh": "Sousa"}
    assert cr.best_project("a VDH ligou hoje", P, aliases=aliases) == "p-1"


def test_gazetteer_note_adds_needles_for_the_clients_domain():
    projs = [{"project_id": "p-1", "title": "Obra Norte", "client_email": "x@corticoenetos.com",
              "stage": "LEAD"}]
    gaz = {"corticoenetos.com": "supplies cork rolls AND pays us to cut to measure"}
    # a capture mentioning "cork" matches via the gazetteer note for the client's domain
    assert cr.best_project("the cork order is ready", projs, gazetteer=gaz) == "p-1"


def test_load_aliases_parses_list_items_only(tmp_path):
    pb = tmp_path / "capture_playbook.md"
    pb.write_text(
        "# Capture Playbook\n## Aliases\n- VDH -> Violaine d'Harcourt\n"
        "this prose line has a -> arrow but is not a list item\n"
        "- acrilico = acrílico\n## Next section\n- ignored -> x\n", encoding="utf-8")
    aliases = cr.load_aliases(pb)
    assert aliases == {"vdh": "Violaine d'Harcourt", "acrilico": "acrílico"}  # prose + other sections out


def test_load_aliases_missing_file_is_empty():
    assert cr.load_aliases("/no/such/capture_playbook.md") == {}


def test_load_gazetteer_skips_comments_and_header(tmp_path):
    gz = tmp_path / "g.csv"
    gz.write_text("domain,counterparty,note\n# a comment line\nacme.pt,CLIENT,placas acrilico\n",
                  encoding="utf-8")
    assert cr.load_gazetteer(gz) == {"acme.pt": "placas acrilico"}


def test_rank_preserves_input_order_on_a_zero_signal_capture():
    # no token matches anything -> every score 0 -> the input (newest-first) order is preserved
    r = cr.rank_projects("xyzzy", P)
    assert [x["project_id"] for x in r] == ["p-1", "p-2"] and all(x["score"] == 0 for x in r)
