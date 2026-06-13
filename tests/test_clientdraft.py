"""Deterministic client-email composer: template loading + body assembly."""

from email2data import clientdraft


def test_build_draft_numbers_questions_into_the_skeleton():
    body = clientdraft.build_draft(["Que espessura?", "Que quantidade?"])
    assert "1. Que espessura?" in body and "2. Que quantidade?" in body
    # the default skeleton's prose survives, the placeholder is gone
    assert body.startswith("Bom dia,") and body.rstrip().endswith("Obrigado.")
    assert clientdraft.PLACEHOLDER not in body


def test_build_draft_with_no_questions_collapses_the_list():
    body = clientdraft.build_draft([])
    assert clientdraft.PLACEHOLDER not in body and "1." not in body
    assert "Bom dia," in body and "Obrigado." in body


def test_build_draft_honours_a_custom_template():
    tmpl = "Olá,\n{perguntas}\nCumprimentos."
    body = clientdraft.build_draft(["A?"], tmpl)
    assert body == "Olá,\n1. A?\nCumprimentos."


def test_load_template_reads_body_after_the_fence(tmp_path):
    f = tmp_path / "tmpl.md"
    f.write_text("# note\n\nignore me\n\n---\n\nCaro cliente,\n\n{perguntas}\n\nObrigado.\n",
                 encoding="utf-8")
    tmpl = clientdraft.load_template(f)
    assert tmpl.startswith("Caro cliente,") and "{perguntas}" in tmpl
    assert "ignore me" not in tmpl                     # the editor note above the fence is dropped


def test_load_template_falls_back_when_missing_or_tokenless(tmp_path):
    assert clientdraft.load_template(tmp_path / "nope.md") == clientdraft.DEFAULT_TEMPLATE
    bad = tmp_path / "bad.md"
    bad.write_text("---\nBom dia, sem token nenhum.\n", encoding="utf-8")  # lost {perguntas}
    assert clientdraft.load_template(bad) == clientdraft.DEFAULT_TEMPLATE


def test_load_template_with_no_fence_uses_whole_file(tmp_path):
    f = tmp_path / "plain.md"
    f.write_text("Bom dia,\n{perguntas}\nObrigado.", encoding="utf-8")
    assert clientdraft.load_template(f).startswith("Bom dia,")
