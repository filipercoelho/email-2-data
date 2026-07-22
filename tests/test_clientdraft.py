"""Deterministic client-email composer: template loading + body assembly, plus the optional AI polish
that sits on top of it (ADR-027) — including the coverage check that makes "on top of" mean something.
"""

import pytest

from email2data import clientdraft, llm


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


# ---------------------------------------------------------------------------
# ADR-027 — the optional AI polish that sits ON TOP of the deterministic draft
# ---------------------------------------------------------------------------

def test_missing_questions_is_the_guarantee_behind_the_polish_button():
    """ADR-013 lets an AI layer sit on top of the deterministic draft only if "on top" is CHECKED.
    A verbatim question survives; a reworded or dropped one is reported."""
    qs = ["Que espessura?", "Que quantidade?"]
    kept = "Bom dia,\n\n1. Que espessura?\n2. Que quantidade?\n\nObrigado."
    assert clientdraft.missing_questions(kept, qs) == []
    assert clientdraft.missing_questions("1. Que espessura?", qs) == ["Que quantidade?"]
    # a REWORD is a loss too — the user ticked that exact question
    assert clientdraft.missing_questions("Qual a espessura? E quantas?", qs) == qs


def test_missing_questions_tolerates_a_re_wrap_but_not_a_reword():
    """The model is told to keep questions verbatim but may legitimately re-wrap a long line, so the
    check is whitespace-insensitive — tight enough to catch a reword, loose enough to avoid a false
    alarm that would train the user to ignore the warning."""
    q = ["Fornecem o material ou tratamos da compra?"]
    assert clientdraft.missing_questions("1. Fornecem o material\n   ou tratamos da compra?", q) == []
    assert clientdraft.missing_questions("1. FORNECEM O MATERIAL OU TRATAMOS DA COMPRA?", q) == []
    assert clientdraft.missing_questions("1. Fornecem o material ou compramos nós?", q) == q


def test_build_polish_message_bounds_every_block_so_an_empty_one_reads_as_nothing_known():
    """Zero-hallucination applied to a prompt: an absent block must say "nothing", never be silently
    missing — an unlabelled gap is an invitation to fill it."""
    msg = clientdraft.build_polish_message("RASC", ["Que espessura?"])
    assert "RASCUNHO" in msg and "RASC" in msg
    assert "1. Que espessura?" in msg
    assert "(nada confirmado ainda)" in msg and "(sem histórico disponível)" in msg

    msg2 = clientdraft.build_polish_message(
        "RASC", ["A?"], facts=[("material", "inox")],
        thread=[{"from_email": "c@acme.pt", "date": "2026-03-01T09:00:00", "body": "olá"}])
    assert "material: inox" in msg2 and "c@acme.pt" in msg2 and "olá" in msg2


def test_build_polish_message_caps_the_thread_it_sends():
    """The composer needs tone, not the corpus: capping messages and per-message length is what stops
    this cheap call from re-billing the whole spec pass."""
    thread = [{"from_email": f"c{i}@acme.pt", "date": "2026-03-01", "body": "x" * 5000}
              for i in range(20)]
    msg = clientdraft.build_polish_message("RASC", ["A?"], thread=thread)
    assert "c19@acme.pt" in msg and "c0@acme.pt" not in msg      # newest kept, oldest dropped
    assert msg.count("x" * 1201) == 0                            # each excerpt is truncated


def test_polish_draft_passes_the_playbook_as_system_and_raises_on_empty():
    """A failure must be reported, never degraded into returning the unpolished text as a success —
    the user explicitly paid for this call."""
    seen = {}

    def fake_call(client, cfg, system, user, **kw):
        seen.update(system=system, user=user, text=kw.get("text"))
        return "  Bom dia,\n1. Que espessura?\nObrigado.  "

    orig = llm.call
    llm.call = fake_call
    try:
        out = clientdraft.polish_draft("RASC", ["Que espessura?"], "PLAYBOOK", object(), {})
        assert out == "Bom dia,\n1. Que espessura?\nObrigado."
        assert seen["system"] == "PLAYBOOK" and seen["text"] is True
        assert "RASC" in seen["user"]

        llm.call = lambda *a, **k: "   "
        with pytest.raises(llm.LLMError):
            clientdraft.polish_draft("RASC", ["A?"], "PB", object(), {})
    finally:
        llm.call = orig


def test_load_polish_playbook_falls_back_without_becoming_permissive(tmp_path):
    """A missing config must not quietly turn into a prompt with no rules."""
    fallback = clientdraft.load_polish_playbook(tmp_path / "nope.md")
    assert fallback == clientdraft.DEFAULT_POLISH_PLAYBOOK
    assert "palavra por palavra" in fallback and "nunca inventes" in fallback

    f = tmp_path / "pb.md"
    f.write_text("  sê breve  ", encoding="utf-8")
    assert clientdraft.load_polish_playbook(f) == "sê breve"
    f.write_text("   ", encoding="utf-8")
    assert clientdraft.load_polish_playbook(f) == clientdraft.DEFAULT_POLISH_PLAYBOOK


# ---------------------------------------------------------------------------
# ADR-031 — the purpose selector + the verbatim-fact guard
# ---------------------------------------------------------------------------

def test_purposes_registry_has_eight_and_ask_is_the_unchanged_default():
    ids = [p.id for p in clientdraft.PURPOSES]
    assert ids == ["ask", "reject", "quote", "follow_up", "approval", "payment", "deadline", "ready"]
    assert clientdraft.DEFAULT_PURPOSE == "ask"
    # ask/follow_up reuse the historical questions token; every purpose declares an input kind
    assert clientdraft.PURPOSES_BY_ID["ask"].token == clientdraft.PLACEHOLDER
    assert clientdraft.PURPOSES_BY_ID["follow_up"].token == clientdraft.PLACEHOLDER
    assert {p.input_kind for p in clientdraft.PURPOSES} == {"questions", "reason", "text"}


def test_build_purpose_draft_ask_is_byte_identical_to_build_draft():
    qs = ["Que espessura?", "Que quantidade?"]
    assert clientdraft.build_purpose_draft("ask", clientdraft.DEFAULT_TEMPLATE, questions=qs) \
        == clientdraft.build_draft(qs, clientdraft.DEFAULT_TEMPLATE)


def test_build_purpose_draft_reason_appends_the_free_note():
    tmpl = "X\n{motivo}\nY"
    both = clientdraft.build_purpose_draft("reject", tmpl, reason="Sem capacidade", reason_note="obrigado")
    assert both == "X\nSem capacidade\n\nobrigado\nY"
    # an empty note is dropped, not left as a dangling blank line
    assert clientdraft.build_purpose_draft("reject", tmpl, reason="Sem capacidade", reason_note="") \
        == "X\nSem capacidade\nY"


def test_build_purpose_draft_text_splices_content_verbatim():
    tmpl = "X\n{conteudo}\nY"
    assert clientdraft.build_purpose_draft("quote", tmpl, content="Total 160€, prazo 10 dias") \
        == "X\nTotal 160€, prazo 10 dias\nY"


def test_extract_values_pulls_money_units_and_dates_in_order_deduped():
    got = clientdraft.extract_values(
        "160€, 160 €, €160, 160 euros, 1.250,00€, 50%, 2mm, 2 m, 10 dias, 20 un, "
        "30/09, 30/09/2026, 2026-09-30. Ao dispor.")
    assert got == ["160€", "160 €", "€160", "160 euros", "1.250,00€", "50%",
                   "2mm", "2 m", "10 dias", "20 un", "30/09", "30/09/2026", "2026-09-30"]
    # de-dup: the same token written twice appears once
    assert clientdraft.extract_values("160€ e outra vez 160€") == ["160€"]


def test_extract_values_avoids_list_markers_thousands_and_phones():
    # list markers ("1.", "2."), a bare total with no symbol/unit, and a phone run must NOT match —
    # only tokens carrying a currency/unit/percent/date shape are guarded (documented boundary).
    assert clientdraft.extract_values("1. primeira\n2. segunda") == []
    assert clientdraft.extract_values("Total: 1250 sem simbolo") == []
    assert clientdraft.extract_values("liga 912 345 678") == []
    assert clientdraft.extract_values("160 metros de cabo") == []   # 'metros' is not a guarded unit


def test_missing_values_blocks_an_altered_or_reformatted_number():
    # kept verbatim → nothing missing
    assert clientdraft.missing_values("fica em 160€ dentro de 10 dias", ["160€", "10 dias"]) == []
    # a changed number is reported (a wrong commitment to the client)
    assert clientdraft.missing_values("agora 170€ em 10 dias", ["160€", "10 dias"]) == ["160€"]
    # a mere reformat is treated as an alteration, on purpose (rounding must not slip through)
    assert clientdraft.missing_values("custa 160,00 € hoje", ["160€"]) == ["160€"]


def test_load_purpose_template_reads_override_and_falls_back(tmp_path):
    # a per-purpose override file after the fence is used …
    (tmp_path / "client_email_quote_template.md").write_text(
        "nota\n---\nProposta:\n{conteudo}\nFim", encoding="utf-8")
    assert clientdraft.load_purpose_template("quote", tmp_path) == "Proposta:\n{conteudo}\nFim"
    # … a missing file falls back to the built-in default for that purpose …
    assert clientdraft.load_purpose_template("reject", tmp_path) == clientdraft.DEFAULT_TEMPLATES["reject"]
    # … and a body that lost its token falls back too (a botched edit never ships token-less)
    (tmp_path / "client_email_ready_template.md").write_text("nota\n---\nsem token", encoding="utf-8")
    assert clientdraft.load_purpose_template("ready", tmp_path) == clientdraft.DEFAULT_TEMPLATES["ready"]
    # no config dir at all → built-in default
    assert clientdraft.load_purpose_template("quote", None) == clientdraft.DEFAULT_TEMPLATES["quote"]


def test_load_reasons_reads_the_list_and_falls_back(tmp_path):
    f = tmp_path / "reasons.md"
    f.write_text("editor note\n---\n- Motivo A\nMotivo B\n\n* Motivo C\n", encoding="utf-8")
    assert clientdraft.load_reasons(f) == ["Motivo A", "Motivo B", "Motivo C"]
    # missing file → the built-in defaults (all eight)
    assert clientdraft.load_reasons(tmp_path / "nope.md") == clientdraft.DEFAULT_REJECT_REASONS
    assert len(clientdraft.DEFAULT_REJECT_REASONS) == 8
    # a file with only a note and no reasons → defaults, never an empty menu
    f.write_text("só uma nota\n---\n\n", encoding="utf-8")
    assert clientdraft.load_reasons(f) == clientdraft.DEFAULT_REJECT_REASONS


def test_build_polish_message_labels_the_values_block_and_keeps_questions():
    # money/text purpose: the must-keep block is VALORES, and every token is listed
    msg = clientdraft.build_polish_message("RASC", keep_values=["160€", "10 dias"])
    assert "VALORES A MANTER" in msg and "160€" in msg and "10 dias" in msg
    assert "PERGUNTAS" not in msg
    assert "(nada confirmado ainda)" in msg and "(sem histórico disponível)" in msg
    # question purpose still emits PERGUNTAS (backward compatible)
    q = clientdraft.build_polish_message("RASC", ["Que espessura?"])
    assert "PERGUNTAS" in q and "1. Que espessura?" in q and "VALORES A MANTER" not in q


def test_polish_draft_forwards_keep_values_to_the_prompt():
    seen = {}

    def fake_call(client, cfg, system, user, **kw):
        seen["user"] = user
        return "Bom dia,\nfica em 160€.\nObrigado."

    orig = llm.call
    llm.call = fake_call
    try:
        clientdraft.polish_draft("RASC", [], "PB", object(), {}, keep_values=["160€"])
        assert "VALORES A MANTER" in seen["user"] and "160€" in seen["user"]
    finally:
        llm.call = orig


# ---------------------------------------------------------------------------
# ADR-032 — the composer output language
# ---------------------------------------------------------------------------

def test_languages_registry_has_the_four_with_pt_default():
    assert [c for c, _l in clientdraft.LANGUAGES] == ["pt", "en", "fr", "es"]
    assert clientdraft.DEFAULT_LANGUAGE == "pt"
    assert clientdraft.LANGUAGES_BY_ID["en"] == "English"


def test_build_polish_message_pt_unchanged_but_non_pt_adds_a_translate_directive():
    # PT (the default) adds nothing → byte-identical to omitting language
    assert clientdraft.build_polish_message("RASC", ["Q?"], language="pt") \
        == clientdraft.build_polish_message("RASC", ["Q?"])
    # EN prepends the IDIOMA DE SAÍDA directive naming the target and still lists the VALORES
    en = clientdraft.build_polish_message("RASC", keep_values=["160€"], language="en")
    assert "IDIOMA DE SAÍDA" in en and "inglês" in en
    assert "VALORES A MANTER" in en and "160€" in en
    # the directive tells the model NOT to translate/reformat a number
    assert "nunca traduzas" in en.lower()
    assert "francês" in clientdraft.build_polish_message("RASC", ["Q?"], language="fr")


def test_polish_draft_forwards_the_language_to_the_prompt():
    seen = {}

    def fake_call(client, cfg, system, user, **kw):
        seen["user"] = user
        return "Bonjour,\n160€"

    orig = llm.call
    llm.call = fake_call
    try:
        clientdraft.polish_draft("RASC", [], "PB", object(), {}, keep_values=["160€"], language="fr")
        assert "IDIOMA DE SAÍDA" in seen["user"] and "francês" in seen["user"]
    finally:
        llm.call = orig
