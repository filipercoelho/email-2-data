"""Per-person signatures (ADR-047) — the closing of a reply belongs to the person sending it.

The properties that carry weight:

  * **The vocabulary is closed and resolves only from the person's own row.** A signature is
    identity; an invented value is a lie told in someone else's name.
  * **A line whose placeholders are all empty is dropped.** Naive substitution produces ``Tel.:``
    followed by nothing, which a client reads as sloppiness.
  * **Strip-then-append, so nobody ever signs off twice.** The playbook is bind-mounted and can be
    edited back to writing its own closing; the strip is what keeps that from reaching a client.
  * **An empty signature changes nothing.** Removing a closing and replacing it with nothing is
    worse than leaving the closing alone.
"""

import pytest

from email2data import signature as sig

PERSON = {"name": "Filipe Coelho", "job_title": "Produção", "phone": "+351 912 345 678",
          "email": "filipe@lindoservico.pt", "signature": ""}


# ── the closed vocabulary ────────────────────────────────────────────────────

def test_every_placeholder_resolves_from_the_person_row():
    values = sig.values_for(PERSON)
    assert values == {"{nome}": "Filipe Coelho", "{cargo}": "Produção",
                      "{telefone}": "+351 912 345 678", "{email}": "filipe@lindoservico.pt"}


def test_a_missing_column_resolves_to_empty_rather_than_raising():
    # A row read from a DB that predates v12 has no signature/job_title/phone keys at all.
    assert sig.values_for({"name": "Rita"})["{cargo}"] == ""
    assert sig.values_for(None)["{nome}"] == ""


def test_an_unknown_token_is_named_so_the_editor_can_say_which_one():
    assert sig.unknown_tokens("{nome}\n{telemovel}\n{cargo}") == ["{telemovel}"]
    assert sig.unknown_tokens("{nome} {nome}") == []
    assert sig.unknown_tokens("{a} {b} {a}") == ["{a}", "{b}"]      # in order, deduped


def test_the_vocabulary_the_editor_advertises_is_the_one_the_renderer_fills():
    # The help text under the textarea names four tokens; if the module ever grows a fifth without
    # the UI following, someone types a token that silently vanishes. Pin the pair.
    from email2data import cockpit_ui
    html = cockpit_ui.account_page({"person_id": "P1", "name": "Filipe", "is_admin": False})
    for token in sig.PLACEHOLDERS:
        assert f"<code>{token}</code>" in html


# ── rule 2: an all-empty line disappears ─────────────────────────────────────

def test_a_line_whose_placeholders_are_all_empty_is_dropped():
    person = {**PERSON, "phone": "", "job_title": ""}
    out = sig.render("Com os melhores cumprimentos,\n{nome}\n{cargo}\nTel.: {telefone}\n{email}",
                     person)
    assert out == "Com os melhores cumprimentos,\nFilipe Coelho\nfilipe@lindoservico.pt"
    assert "Tel.:" not in out          # the label goes with the value, not without it


def test_a_line_mixing_a_filled_and_an_empty_placeholder_keeps_the_filled_one():
    # Documented behaviour, not an accident: "one field per line" is the advice precisely because
    # this line CANNOT disappear -- half of it is real.
    out = sig.render("{nome} · {cargo}", {**PERSON, "job_title": ""})
    assert out == "Filipe Coelho ·"


def test_static_lines_survive_with_no_placeholders_at_all():
    assert sig.render("Lindo Serviço\nlindoservico.pt", PERSON) == "Lindo Serviço\nlindoservico.pt"


def test_blank_runs_collapse_and_edges_are_trimmed():
    assert sig.render("\n\n{nome}\n\n\n{cargo}\n\n", PERSON) == "Filipe Coelho\n\nProdução"


def test_a_signature_of_only_empty_placeholders_renders_empty():
    assert sig.render("{nome}\n{cargo}", {"name": "", "job_title": ""}) == ""


# ── the person's own template wins over the install default ──────────────────

def test_a_person_with_no_signature_gets_the_install_default(tmp_path):
    (tmp_path / "signature_template.md").write_text("nota\n---\nAté já,\n{nome}\n", encoding="utf-8")
    assert sig.for_person(PERSON, tmp_path) == "Até já,\nFilipe Coelho"


def test_a_person_with_their_own_signature_overrides_the_install_default(tmp_path):
    (tmp_path / "signature_template.md").write_text("nota\n---\nAté já,\n{nome}\n", encoding="utf-8")
    mine = {**PERSON, "signature": "Abraço,\n{nome} ({cargo})"}
    assert sig.for_person(mine, tmp_path) == "Abraço,\nFilipe Coelho (Produção)"


def test_a_missing_config_file_falls_back_to_the_builtin(tmp_path):
    out = sig.for_person(PERSON, tmp_path)
    assert out.startswith("Com os melhores cumprimentos,\nFilipe Coelho")
    assert sig.for_person(PERSON, None) == out          # no config dir at all -> same fallback


def test_a_config_template_with_an_unfillable_token_falls_back_instead_of_printing_it(tmp_path):
    # A botched edit must degrade to a working closing, never to a client email with {telemovel}.
    (tmp_path / "signature_template.md").write_text("n\n---\nAté já,\n{telemovel}\n", encoding="utf-8")
    assert "{telemovel}" not in sig.for_person(PERSON, tmp_path)
    assert sig.load_template(tmp_path) == sig.DEFAULT_TEMPLATE


def test_the_shipped_config_template_is_loadable_and_uses_only_known_tokens():
    # The file that actually ships -- a typo in it would hit every install that never customises.
    from pathlib import Path
    config = Path(__file__).resolve().parents[1] / "config"
    shipped = sig.load_template(config)
    assert sig.unknown_tokens(shipped) == []
    # It must have been READ, not silently fallen back to: the fence and the token guard both held.
    assert shipped == sig.DEFAULT_TEMPLATE, (
        "config/signature_template.md no longer renders as the built-in default — if that is "
        "deliberate, update this test; if not, the ---fence or a token guard just rejected the file "
        "and every install is quietly using the fallback.")
    assert sig.render(shipped, PERSON).splitlines()[0] == "Com os melhores cumprimentos,"


# ── strip-then-append ────────────────────────────────────────────────────────

@pytest.mark.parametrize("closing", [
    "Com os melhores cumprimentos,", "Melhores cumprimentos", "Cumprimentos,",
    "Atenciosamente,", "Best regards,", "Kind regards", "Regards,", "Sincerely,", "Cheers",
])
def test_a_trailing_sign_off_is_cut_before_the_real_one_is_appended(closing):
    body = f"Bom dia,\n\nRecebemos o pedido.\n\n{closing}\nLindo Serviço"
    assert sig.strip_closing(body) == "Bom dia,\n\nRecebemos o pedido."


def test_obrigado_is_body_text_and_survives():
    # The deterministic ask/follow-up templates END with "Obrigado." -- envelope._CLOSING matches it
    # and would delete it. This module's regex is narrower on purpose.
    body = "Bom dia,\n\nFaltava confirmar a quantidade.\n\nObrigado."
    assert sig.strip_closing(body) == body


def test_a_closing_far_from_the_end_is_prose_and_survives():
    body = ("Cumprimentos,\n" + "\n".join(f"linha {i}" for i in range(10)))
    assert sig.strip_closing(body) == body


def test_a_body_that_is_only_a_closing_is_never_emptied():
    assert sig.strip_closing("Cumprimentos,") == "Cumprimentos,"


def test_sign_replaces_the_model_written_closing_with_the_person_s(tmp_path):
    body = "Bom dia,\n\nRecebemos o pedido.\n\nCom os melhores cumprimentos,\nLindo Serviço"
    out = sig.sign(body, {**PERSON, "signature": "Abraço,\n{nome}"}, tmp_path)
    assert out == "Bom dia,\n\nRecebemos o pedido.\n\nAbraço,\nFilipe Coelho"
    assert out.count("cumprimentos") == 0


def test_sign_appends_when_there_was_no_closing(tmp_path):
    out = sig.sign("Bom dia,\n\nObrigado.", {**PERSON, "signature": "Abraço,\n{nome}"}, tmp_path)
    assert out == "Bom dia,\n\nObrigado.\n\nAbraço,\nFilipe Coelho"


def test_an_empty_signature_leaves_the_draft_completely_untouched(tmp_path):
    # The dangerous case: strip fires, append renders nothing, and the mail goes out with no close.
    body = "Bom dia,\n\nRecebemos o pedido.\n\nCom os melhores cumprimentos,\nLindo Serviço"
    nobody = {"name": "", "job_title": "", "phone": "", "email": "", "signature": "{nome}"}
    assert sig.sign(body, nobody, tmp_path) == body


def test_an_unknown_person_falls_back_to_the_company_closing_naming_nobody(tmp_path):
    """`person=None` (an unguarded render path) must not produce a signature with someone's name.

    It renders the install default with every placeholder empty, so rule 2 drops each personal line
    and what is left is the company closing — byte-identical to the pre-ADR-047 output. That is the
    honest answer when we do not know who is asking, and it is why this case is safe rather than
    merely non-crashing.
    """
    body = "Bom dia,\n\nRecebemos o pedido.\n\nCom os melhores cumprimentos,\nLindo Serviço"
    assert sig.for_person(None, tmp_path) == "Com os melhores cumprimentos,\nLindo Serviço"
    assert sig.sign(body, None, tmp_path) == body


def test_sign_on_an_empty_body_returns_the_block_alone(tmp_path):
    assert sig.sign("", {**PERSON, "signature": "Abraço,\n{nome}"}, tmp_path) == "Abraço,\nFilipe Coelho"


def test_signing_is_idempotent_in_the_sense_that_matters(tmp_path):
    """Re-signing an already-signed draft must not stack two blocks.

    This is not academic: /api/reply caches the UNSIGNED body, but a caller that re-posted a signed
    draft (or a future retry path) would otherwise append a second copy.
    """
    person = {**PERSON, "signature": "Com os melhores cumprimentos,\n{nome}"}
    once = sig.sign("Bom dia,\n\nObrigado.", person, tmp_path)
    assert sig.sign(once, person, tmp_path) == once


# ── the pasted HTML signature (ADR-047 §10) ──────────────────────────────────
#
# Found by looking at the rendered page, not by a test: a real signature is COPIED out of Outlook or
# Gmail, so it arrives as an HTML table of logos, social icons and inline styles. Stored verbatim it
# put `<td style="padding:12px 0px 8px 12px;">` straight into the draft a client would read.

# Trimmed from the real block pasted into the live preview on 2026-07-26 — the structure that matters
# is all here: doctype/head, a layout table, an <img> logo inside an <a>, &nbsp;-padded phone numbers,
# a text link, and a row of social icons that are images inside links.
OUTLOOK = """<html lang="pt">
<head><meta http-equiv="Content-Type" content="text/html; charset=UTF-8"></head>
<body style="margin:0;padding:0;background:#ffffff;">
<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tbody>
<tr><td style="padding:12px 0px 8px 12px;">
  <div style="font-size:16px;font-weight:700;text-transform:uppercase;">FILIPE COELHO</div>
  <div style="font-size:11px;color:#000000;">Departamento T&eacute;cnico</div>
  <div style="font-size:11px;font-weight:700;"><a style="color:#000;text-decoration:none;">
    +351&nbsp;934&nbsp;367&nbsp;794</a></div>
</td></tr>
<!-- Logo -->
<tr><td style="padding:8px 0px 0px 12px;">
  <a href="https://www.lindoservico.pt" target="_blank"><img src="https://lindoservico.pt/logo.png"
     width="226" height="AUTO" alt="LINDO SERVI&Ccedil;O" style="display:block;"></a>
</td></tr>
<tr><td style="padding:12px 0px 8px 12px;">
  <div style="font-size:11px;">Rua da Centeira, 7</div>
  <div style="font-size:11px;">1800-056&nbsp;Lisboa</div>
  <div style="font-size:11px;"><a style="color:#000;">+351&nbsp;218&nbsp;394&nbsp;122</a></div>
  <div style="font-size:11px;"><a href="https://www.lindoservico.pt" target="_blank"
     style="color:#A6CE39;">www.lindoservico.pt</a></div>
</td></tr>
<!-- Social glyphs -->
<tr><td style="padding:0 0px 14px 12px;"><table><tbody><tr>
  <td><a href="https://www.facebook.com/lindoservico/" aria-label="Facebook"><img
     src="https://lindoservico.pt/facebook.png" width="22" height="22" alt="Facebook"></a></td>
  <td><a href="https://www.instagram.com/lindoservico/" aria-label="Instagram"><img
     src="https://lindoservico.pt/instagram.png" width="22" height="22" alt="Instagram"></a></td>
</tr></tbody></table></td></tr>
</tbody></table>
</body></html>"""


def test_a_pasted_outlook_signature_is_recognised_as_html():
    assert sig.looks_like_html(OUTLOOK) is True
    assert sig.looks_like_html("Com os melhores cumprimentos,\n{nome}\nLindo Serviço") is False


def test_an_address_in_angle_brackets_is_not_mistaken_for_markup():
    """The false positive that would matter: "<filipe@lindoservico.pt>" is a perfectly ordinary
    signature line, and converting it would strip the address out of the block entirely."""
    plain = "Filipe Coelho <filipe@lindoservico.pt>\n+351 912 345 678"
    assert sig.looks_like_html(plain) is False
    assert sig.normalize_signature(plain) == (plain, False)


def test_the_outlook_paste_flattens_to_the_text_a_client_would_read():
    text, was_html = sig.normalize_signature(OUTLOOK)
    assert was_html is True
    assert text == (
        "FILIPE COELHO\n"
        "Departamento Técnico\n"
        "+351 934 367 794\n"
        "Rua da Centeira, 7\n"
        "1800-056 Lisboa\n"
        "+351 218 394 122\n"
        "www.lindoservico.pt")
    # The three things that made the raw paste unusable are all gone.
    assert "<" not in text and "style=" not in text
    assert "AUTO" not in text and "Facebook" not in text and "Instagram" not in text, (
        "image alt text is signature noise — it reads like a bug in a client's inbox")
    assert " " not in text, "Outlook pads with NBSP; left in, it defeats the empty-line rule"


def test_entities_are_decoded_rather_than_shown():
    assert sig.html_to_text("<div>Departamento T&eacute;cnico &amp; Produ&ccedil;&atilde;o</div>") \
        == "Departamento Técnico & Produção"


def test_script_and_style_blocks_are_dropped_whole():
    """Gmail pastes carry a <style> block whose CSS would otherwise arrive as text — and whose
    braces would then be read as unknown placeholders and refuse the save."""
    markup = "<style>body{color:#000}.sig{margin:0}</style><div>Filipe</div><script>x()</script>"
    assert sig.html_to_text(markup) == "Filipe"
    assert sig.unknown_tokens(sig.normalize_signature(markup)[0]) == []


def test_inline_tags_do_not_break_a_line():
    """<b>/<span>/<a> are inline: breaking on them turns one phone line into three."""
    assert sig.html_to_text('<div>Tel.: <b>+351</b> <span>912</span> <a href="#">345 678</a></div>') \
        == "Tel.: +351 912 345 678"


def test_br_and_block_ends_become_line_breaks():
    assert sig.html_to_text("Filipe<br>Produção<br/>Lisboa") == "Filipe\nProdução\nLisboa"
    assert sig.html_to_text("<p>A</p><p>B</p>") == "A\nB"


def test_placeholders_survive_the_conversion():
    """A person can paste their Outlook block and then swap their name for {nome}, so a rename keeps
    updating it — which is the entire reason a TEMPLATE is stored rather than rendered text."""
    text, was_html = sig.normalize_signature("<div>{nome}</div><div>{cargo}</div>")
    assert (text, was_html) == ("{nome}\n{cargo}", True)
    assert sig.render(text, PERSON) == "Filipe Coelho\nProdução"


def test_an_html_block_with_no_text_at_all_converts_to_empty():
    """An image-only signature has nothing to keep. The STORE turns this into a refusal rather than
    silently reverting the person to the install default (see test_people)."""
    assert sig.html_to_text('<table><tr><td><img src="logo.png" alt="Logo"></td></tr></table>') == ""


def test_blank_lines_from_markup_nesting_are_dropped_not_merely_collapsed():
    """`</div></td></tr>` emits a break per nesting level, so collapsing to ONE blank line still
    leaves a gap between every field — spacing that is an artefact of the markup, never a design."""
    assert sig.html_to_text("<table><tr><td><div>A</div></td></tr>"
                            "<tr><td><div>B</div></td></tr></table>") == "A\nB"
