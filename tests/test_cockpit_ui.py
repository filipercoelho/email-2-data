"""C0 — cockpit_ui shared shell: page() assembler + structural smoke tests."""

from email2data.cockpit_ui import page


def _make(active="fila", extra_css="", counts=None):
    return page(
        "Test",
        active,
        "<div id='body'>body</div>",
        embeds={"rows": [1, 2], "team": ["Pedro"]},
        lens_js="function render(){} function paletteItems(q){return[];} function onKey(e){}",
        nav_counts=counts or {},
        extra_css=extra_css,
    )


def test_page_is_valid_html():
    html = _make()
    assert html.startswith("<!doctype html>")
    assert "</html>" in html


def test_title_is_embedded():
    html = _make()
    assert "<title>Test · email-2-data</title>" in html


def test_active_nav_item_has_on_class():
    html = _make(active="fila")
    # The Fila nav link should have class "nlink on"
    assert 'class="nlink on"' in html


def test_other_nav_items_do_not_have_on_class():
    html = _make(active="fila")
    # Contrapartes, Projetos, Para ti should not be active
    assert 'href="/contrapartes"' in html
    # Count occurrences of 'nlink on' — should be exactly 1
    assert html.count('nlink on') == 1


def test_all_four_nav_items_present():
    html = _make()
    for href in ["/", "/contrapartes", "/projetos", "/para-ti"]:
        assert f'href="{href}"' in html


def test_nav_count_badge_shown_when_nonzero():
    html = _make(counts={"para-ti": 3})
    assert "nbadge" in html
    assert ">3<" in html


def test_no_nav_badge_for_zero():
    html = _make(counts={"para-ti": 0})
    # CSS always defines .nbadge, but no <span> element should be emitted for count=0
    assert '<span class="nbadge">' not in html


def test_embeds_become_js_constants():
    html = _make()
    assert "const ROWS = " in html
    assert "const TEAM = " in html


def test_lens_js_is_included():
    html = _make()
    assert "function render(){}" in html
    assert "function paletteItems" in html
    assert "function onKey" in html


def test_shell_utilities_present():
    html = _make()
    for symbol in ["function toast", "function announce", "function doUndo",
                   "function openPalette", "function toggleDensity"]:
        assert symbol in html


def test_shell_event_wiring_present():
    html = _make()
    assert "_pq" in html           # palette input listener
    assert "_help" in html          # help overlay
    assert "keydown" in html        # keyboard handler


def test_structural_html_elements():
    html = _make()
    for el in ["id=\"_live\"", "id=\"_toast\"", "id=\"_palette\"",
               "id=\"_help\"", "id=\"_menu\"", "id=\"_pq\""]:
        assert el in html, f"missing {el}"


def test_extra_css_injected():
    html = _make(extra_css=".custom{color:red}")
    assert ".custom{color:red}" in html


def test_body_html_present():
    html = _make()
    assert "<div id='body'>body</div>" in html


def test_xss_safe_title():
    html = page("<script>", "fila", "", lens_js="function render(){} function paletteItems(q){return[];} function onKey(e){}")
    assert "<script>" not in html.split("<title>")[1].split("</title>")[0]
