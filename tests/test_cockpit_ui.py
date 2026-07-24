"""C0 — cockpit_ui shared shell: page() assembler + structural smoke tests."""

from email2data.cockpit_ui import page


def _make(active="fila", extra_css="", counts=None):
    return page(
        "Test",
        active,
        "<div id='body'>body</div>",
        embeds={"rows": [1, 2], "team": ["Diogo"]},
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


def test_all_nav_items_present():
    """Every lens is reachable from the shared shell. /capturas and /admin joined later — a lens with
    no nav entry is a page only its own URL can reach, which is how /admin was unreachable at first."""
    html = _make()
    for href in ["/", "/contrapartes", "/projetos", "/para-ti", "/capturas", "/admin"]:
        assert f'href="{href}"' in html


def test_nav_has_the_admin_item_and_marks_exactly_one_link_active():
    """The Administração lens gets the same shell as the decision lenses, and ``active="admin"``
    actually matches a nav key — before it existed the page rendered with NOTHING highlighted, which
    reads as "you are nowhere"."""
    html = _make(active="admin")
    assert 'data-nav="admin" href="/admin">' in html and 'class="nlbl">Admin<' in html
    assert html.count('class="nlink on"') == 1
    assert 'class="nlink on" data-nav="admin"' in html
    # …and it is last in the strip: config comes after the queues, never between them
    assert html.index('href="/admin"') > html.index('href="/capturas"')


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


def test_css_has_no_stale_percent_escapes():
    """A leftover printf-style '%%' ships as INVALID CSS (the browser drops the whole declaration).

    This exact bug shipped: `.toast{left:50%%;transform:translateX(-50%%)}` and `#_pq{width:100%%}`
    — the toast (the primary action feedback) rendered un-centred and the ⌘K palette input lost its
    width on every page. page() builds the stylesheet with .replace(), NOT %-formatting, so a literal
    '%%' can never be right anywhere in the shell HTML/CSS."""
    html = _make()
    assert "%%" not in html
    assert "left:50%;transform:translateX(-50%)" in html   # the toast is really centred
    assert "#_pq{width:100%;" in html                      # the palette input really fills the card


def test_only_critical_red_clock_pulses():
    """Motion is reserved for the critical tier: `.clock.red .d` must NOT animate unconditionally —
    only `.clock.red.crit .d` may. 29 permanently pulsing dots made red carry zero signal."""
    html = _make()
    assert ".clock.red.crit .d{animation" in html
    assert ".clock.red .d{animation" not in html


def test_failure_strings_distinguish_reverted_from_failed():
    """S.falhou ('nothing happened') and S.revertido ('your optimistic change was rolled back') are
    different promises — both must exist so lenses can tell the truth about what a failure did."""
    html = _make()
    assert "falhou:'falhou'" in html
    assert "revertido:'falhou — revertido'" in html


def test_mesa_palette_tokens_are_canonical():
    """ADR-033: the shell ships the design-proposal palette — steel-blue accent on cool graphite,
    and the CVD-validated counterparty trio (cliente teal · fornecedor blue · lead amber; purple
    LEAD was rejected at ΔE 2.9 protan against supplier blue). Pinned so a future restyle cannot
    silently regress the validated identity colors."""
    html = _make()
    for token in ("--ac:#2C5E80", "--cli:#0A8F72", "--forn:#3B5FC0", "--lead:#A16207",
                  "--red:#B3392E", "--amber:#96660F", "--green:#2E7D4F", "--bg:#F1F3F6"):
        assert token in html, token
    assert ".cp.LEAD{background:var(--lead-bg);color:var(--lead)}" in html
    assert ".cp.CLIENT{background:var(--cli-bg);color:var(--cli)}" in html
    assert ".cp.SUPPLIER{background:var(--forn-bg);color:var(--forn)}" in html


def test_nav_lens_links_carry_icons_and_a_monogram():
    """ADR-034 P5b: every lens link gets a stroke glyph (scan by shape), the label wraps in .nlbl,
    and the wordmark gains a monogram — the nav becomes iconic, not plain text."""
    html = _make()
    for key in ("fila", "contrapartes", "projetos", "para-ti", "capturas", "admin"):
        assert f'data-nav="{key}"' in html
    assert html.count("<svg viewBox") >= 6            # one glyph per lens
    assert ".nlink svg{" in html and 'class="nlbl"' in html
    assert "class='mark'" in html                     # the e2d monogram


def test_dark_theme_tokens_toggle_and_no_hardcoded_surfaces():
    """ADR-035: the shell ships a full dark theme — the validated dark palette under
    [data-theme="dark"], the new tint sub-tokens (surface2/int-bg/int-line/purple-bg), a pre-paint
    no-flash script that follows the saved choice or the OS, and a nav toggle. Component CSS must no
    longer hardcode a light surface, or dark mode would show white patches."""
    html = _make()
    assert ':root[data-theme="dark"]' in html
    for tok in ("--bg:#10151B", "--card:#171E26", "--tx:#E6EBF0", "--ac:#7FB0D0",
                "--cli:#219980", "--forn:#6E85DE", "--lead:#BA8628"):
        assert tok in html, tok
    for tok in ("--surface2:#F7F9FB", "--int-bg:", "--int-line:", "--purple-bg:"):
        assert tok in html, tok
    assert "e2d-theme" in html and "prefers-color-scheme:dark" in html      # no-flash + OS fallback
    assert "id='_themebtn'" in html and "setAttribute('data-theme'" in html  # the toggle
    css = html.split("<style>")[1].split("</style>")[0]
    for hard in ("background:#fff", "background:#f8f9fb", "background:#f0fdfa", "background:#efeafb"):
        assert hard not in css, hard                                         # tokenized, not raw
