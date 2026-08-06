"""C0 — cockpit_ui shared shell: page() assembler + structural smoke tests."""

import pathlib
import re

from email2data import cockpit_ui
from email2data.cockpit_ui import page


ADMIN = {"person_id": "PER-A", "name": "Filipe Coelho", "is_admin": True}
MEMBER = {"person_id": "PER-M", "name": "Diogo Santos", "is_admin": False}


def _make(active="fila", extra_css="", counts=None, person=None):
    return page(
        "Test",
        active,
        "<div id='body'>body</div>",
        embeds={"rows": [1, 2], "team": ["Diogo"]},
        lens_js="function render(){} function paletteItems(q){return[];} function onKey(e){}",
        nav_counts=counts or {},
        extra_css=extra_css,
        person=person,
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
    no nav entry is a page only its own URL can reach, which is how /admin was unreachable at first.

    Built as an ADMIN deliberately: since W3 the /admin entry is rendered only for a person carrying
    the flag (ADR-041), so the honest form of "every item is present" has to name whose shell it is.
    The other half — that a member does NOT get it — is
    `test_a_non_admin_is_never_shown_the_administration_entry`; do not merge the two by relaxing
    either one."""
    html = _make(person=ADMIN)
    for href in ["/fila", "/contrapartes", "/projetos", "/para-ti", "/capturas", "/admin"]:
        assert f'href="{href}"' in html
    # Início is not a lens and has no strip entry (ADR-044) — the logo is its door, and it has to
    # exist on every page or the landing screen becomes a place you can only reach by typing a URL.
    assert "href='/'" in html


def test_the_logo_is_the_way_back_to_inicio():
    """ADR-044: «/» is a real destination, so the shell needs a permanent affordance for it. The logo
    is that affordance, and on Início it carries the active state — every other page marks its lens,
    and a header with nothing marked reads as «you are nowhere», which is the exact complaint the
    landing page was built to answer."""
    on_inicio = _make(active="inicio")
    assert "class='logo on' href='/'" in on_inicio
    assert 'class="nlink on"' not in on_inicio          # no LENS is active on Início

    on_fila = _make(active="fila")
    assert "class='logo' href='/'" in on_fila           # present, not active
    assert 'class="nlink on"' in on_fila                # the Fila link is the active one there


def test_admin_lives_in_the_gear_not_the_lens_strip():
    """ADR-034 P5d: Admin is configuration, not a decision lens — it moved OUT of the main nav strip
    into the gear menu (with densidade + tema). It is still reachable (`href="/admin"`), and on the
    /admin page the gear is marked active so you are never «nowhere». No lens link is active there."""
    html = _make(active="admin", person=ADMIN)
    assert 'data-nav="admin" href="/admin">' in html            # present — in the gear menu
    assert 'class="gm" data-nav="admin"' in html                # …as a gear menu item, not a lens link
    assert 'class="nlink on"' not in html                       # no LENS link is active on /admin
    assert "id='_gearbtn'" in html and "class='hbtn ic on'" in html   # the gear is the active affordance
    # Admin is no longer between the queues — it's after everything, in the gear
    assert html.index('data-nav="admin"') > html.index('data-nav="capturas"')


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
    for key in ("fila", "contrapartes", "projetos", "para-ti", "capturas"):   # admin is in the gear
        assert f'data-nav="{key}"' in html
    assert html.count("<svg viewBox") >= 6            # one glyph per lens (+ gear/sync)
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


def test_the_change_password_form_is_legible_to_a_password_manager():
    """Reported from the field, and it cost a real lockout twice: NordPass read «Palavra-passe atual»
    as a *new*-password field (offering to generate one) and did not recognise the actual new-password
    boxes — so a generated string nobody ever saw became the account password.

    Three password inputs with **no username field** is an ambiguous form: the username is the anchor a
    manager uses to decide "this changes the password of account X" rather than "this creates one".
    `autocomplete` alone did not save it, and it never will on its own — the anchor has to be there,
    and it has to be RENDERED, because managers routinely skip `display:none`."""
    html = cockpit_ui.account_page({"person_id": "P", "name": "Filipe", "is_admin": True})
    form = html[html.index('action="/a-minha-conta/palavra-passe"'):]
    form = form[:form.index("</form>")]
    assert 'autocomplete="username"' in form, "no account anchor — the form reads as 'create a password'"
    assert 'value="Filipe"' in form and "readonly" in form
    assert "hidden" not in form, "a hidden anchor is one a password manager is entitled to ignore"
    # …and each box still says what it is, by id as well as by autocomplete.
    for ident, kind in (("_pw_cur", "current-password"), ("_pw_new", "new-password"),
                        ("_pw_cnf", "new-password")):
        assert f'id="{ident}"' in form and f'for="{ident}"' in form, ident
    assert form.count('autocomplete="new-password"') == 2
    assert form.count('autocomplete="current-password"') == 1


def test_the_password_boxes_can_be_revealed_before_submitting():
    """The safety net under the one above. Whatever a manager filled in, the person must be able to
    SEE it before committing — an unreadable field is how a wrong autofill becomes the password of
    record with nobody the wiser. Verify before you claim, applied to the user's own action."""
    html = cockpit_ui.account_page({"person_id": "P", "name": "Filipe", "is_admin": True})
    assert 'id="_pwshow"' in html and "Mostrar" in html
    js = html[html.index("_pwshow"):]
    assert "'text'" in js and "'password'" in js, "the toggle never switches the input type"


def test_the_account_pages_primary_button_reads_in_both_themes():
    """`--ac` is a DARK blue in light mode and a PALE blue in dark mode, so a filled accent button
    cannot pin its text to one colour: white-on-#2C5E80 reads, white-on-#7FB0D0 does not. Both
    surfaces here are new, and this was caught by looking at the dark render, not by a test."""
    css = cockpit_ui.account_page({"person_id": "P", "name": "N", "is_admin": True})
    rule = css[css.index(".abtn{"):css.index(".abtn.ghost")]
    assert "background:var(--ac)" in rule
    assert "#fff" not in rule, "the label is pinned to white — unreadable on the dark palette's --ac"
    assert "var(--" in rule.split("color:")[1][:12], "the label colour must follow the theme"


def test_p5d_gear_menu_and_freshness_sync_pill():
    """ADR-034 P5d: the nav ends in one gear (Admin + densidade + tema fold into it) and one
    freshness-as-sync PILL — «Sincronizar» merged with «correio há N min» (a dot: green fresh /
    amber stale / spinning while syncing) that you click to sync. Fewer top-level buttons, more
    signal."""
    html = _make(person=ADMIN)      # the Administração item is admin-only since W3 (ADR-041)
    # the gear + its three items
    assert "id='_gearbtn'" in html and "id='_gearmenu'" in html
    assert "id='_denbtn'" in html and "id='_themebtn'" in html          # densidade + tema now inside
    assert "Administração" in html                                       # admin link inside the gear
    # the sync pill
    assert "syncpill" in html and "id='_synclbl'" in html and "id='_sdot'" in html
    assert "function setSynced(" in html and ".syncpill.syncing .sdot{" in html
    # no more standalone densidade button text in the top strip (it's a gear menu item now)
    assert "<button class='hbtn' id='_denbtn'>densidade</button>" not in html


# ── W2: the network seam ─────────────────────────────────────────────────────
#
# An expired session used to render «✓ Tudo tratado · 0 a responder». The 401 body {"error": …} has
# no .rows, `d.rows||[]` made that an empty list, and the Fila stated as fact that there was nothing
# left to do. Of every wrong thing the UI could say, "nothing needs you" is the one a person acts on
# by closing the laptop.

# The lens modules that render through page(). report.py is deliberately absent — the legacy /inbox
# report is not built on this shell (see W11).
LENS_MODULES = ["fila_page", "para_ti_page", "projetos_page", "captures_page",
                "contrapartes_page", "admin_page"]


def _src(module):
    return (pathlib.Path(__file__).resolve().parents[1]
            / "src" / "email2data" / f"{module}.py").read_text(encoding="utf-8")


def test_the_shell_exposes_one_network_seam():
    html = _make()
    for fn in ("function fetchJSON(", "function getJSON(", "async function post("):
        assert fn in html, f"the shared shell lost {fn}"


def test_a_401_raises_the_curtain_rather_than_returning_a_body():
    html = _make()
    seam = html[html.index("async function fetchJSON("):]
    seam = seam[:seam.index("function getJSON(")]
    assert "r.status===401" in seam and "sessionEnded()" in seam
    assert "if(!r.ok) throw" in seam, "a non-2xx must throw, never hand a body back to `||[]`"


def test_a_refusal_carries_its_reason_without_ever_returning_a_body():
    """The gap the «Pessoas» panel found in the ADR-040 seam. A 400 from a form is not «the server
    would not talk to you» — it is a considered answer («Rita já existe», «essa caixa não é desta
    instalação»), and throwing it away left the panel able to say only «falhou».

    The rule that mattered is intact: a non-2xx still THROWS, so no renderer can mistake an error for
    data. The reason rides on the error object, which only a catch block can read."""
    html = _make()
    seam = html[html.index("class HttpError"):html.index("function getJSON(")]
    assert "if(!r.ok) throw" in seam, "a non-2xx must throw, never hand a body back to `||[]`"
    assert "e.detail" in seam, "the refusal's reason is dropped — the panel can only say «falhou»"
    # …and the reason is attached to the ERROR, never returned in its place.
    assert "return" not in seam.split("if(!r.ok) throw")[1].split("\n")[0]
    assert "function failMsg(" in html


def test_every_verb_goes_through_the_one_seam():
    """DELETE included. A raw `fetch(url,{method:'DELETE'})` at one call site is how the single-door
    rule dies — it starts as the exception that "doesn't return data anyway"."""
    html = _make()
    assert "function del(" in html
    body = html[html.index("function del("):]
    assert "fetchJSON(" in body[:200]


def test_the_session_ended_curtain_ships_on_every_page():
    html = _make()
    assert 'id="_gone"' in html
    assert "Sessão terminada" in html
    assert 'role="alertdialog"' in html          # not a toast: it must not fade after 2.6s
    assert "pode já não ser verdade" in html     # names what is behind it as possibly stale


def test_the_curtain_sits_above_every_other_overlay():
    """It covers the palette, the help card and any menu — z-index 120 vs their 60/70/80."""
    html = _make()
    gone = re.search(r"\.overlay\.gone\{([^}]*)\}", html)
    assert gone and "z-index:120" in gone.group(1)


def test_a_lens_crash_is_reported_not_swallowed():
    """A render() that throws used to log to a console nobody has open, leaving the nav above an
    empty page — indistinguishable from an empty queue."""
    html = _make()
    assert "catch(_e){bootFailed(_e);}" in html
    assert "function bootFailed(" in html
    assert "não é uma lista vazia" in html.lower()


def test_polls_are_registered_so_the_shell_can_stop_them():
    html = _make()
    assert "function everyMs(" in html and "function stopPolling(" in html
    assert "stopPolling();" in html[html.index("function sessionEnded("):]


def test_no_lens_page_calls_fetch_directly():
    """The seam only holds if there is exactly one door.

    This is the regression guard that matters more than any single call-site fix: a new lens written
    the old way — `(await fetch(url)).json()` — reintroduces the whole class of bug, and would
    otherwise be caught by nobody until an expired session lied to someone.
    """
    offenders = []
    for module in LENS_MODULES:
        for i, line in enumerate(_src(module).splitlines(), 1):
            if re.search(r"(?<![\w.])fetch\s*\(", line) and "fetchJSON" not in line:
                offenders.append(f"{module}.py:{i}: {line.strip()[:90]}")
    assert not offenders, (
        "raw fetch() in a lens page — use getJSON()/post() so a 401 raises the curtain:\n  "
        + "\n  ".join(offenders))


def test_no_lens_page_softens_an_error_body_into_an_empty_list():
    """The exact shape that produced the false «Tudo tratado»: reading `.json()` off an unchecked
    response and then defaulting the missing key."""
    offenders = []
    for module in LENS_MODULES:
        for i, line in enumerate(_src(module).splitlines(), 1):
            if "await fetch(" in line and ").json()" in line:
                offenders.append(f"{module}.py:{i}")
    assert not offenders, f"unchecked response read as JSON: {offenders}"


def test_the_three_failure_strings_stay_distinct():
    """`revertido` claims a rollback happened. `falhou` claims nothing happened. `undoFalhou` admits
    the screen and the server disagree. Collapsing any pair makes the UI assert something false."""
    html = _make()
    for key in ("revertido:", "falhou:", "undoFalhou:"):
        assert key in html
    assert "recarrega a página" in html          # undoFalhou tells you how to resolve the divergence


# ── W3: identity in the shell (ADR-041) ──────────────────────────────────────
#
# ADR-040 gated the /admin ROUTE and left the /admin LINK visible to everyone: a member's only
# feedback was a 403 page they had to walk into. And the shell never said whose session it was —
# on a shared workshop machine, "who am I signed in as?" had no answer short of /api/me.


def test_the_shell_names_who_is_signed_in():
    html = _make(person=ADMIN)
    assert "id='_acctbtn'" in html and "id='_acctmenu'" in html
    assert "Filipe Coelho" in html


def test_the_account_control_sits_between_the_sync_pill_and_the_gear():
    """Right-hand cluster, in order: sync (status) · account (identity) · gear (config). Identity is
    not a lens and not a setting — putting it in the gear would bury the one control that answers
    «whose session is this?» behind a menu."""
    html = _make(person=ADMIN)
    assert html.index("id='_syncbtn'") < html.index("id='_acctbtn'") < html.index("id='_gearbtn'")


def test_signing_out_posts_rather_than_links():
    """`/logout` is POST-only — it revokes the session ROW, not just the cookie (ADR-039). An
    <a href='/logout'> would 405 and leave the person signed in while looking like it worked."""
    html = _make(person=MEMBER)
    assert "action='/logout'" in html and "method='post'" in html
    assert "Terminar sessão" in html
    assert "href='/logout'" not in html and 'href="/logout"' not in html


def test_the_account_menu_states_the_role():
    """Whether you are an admin is the difference between «the button is missing» and «the app is
    broken». The menu says which you are, so a missing Administração entry is self-explaining."""
    assert "Administrador" in _make(person=ADMIN)
    assert "Membro" in _make(person=MEMBER)


def test_a_non_admin_is_never_shown_the_administration_entry():
    """The other half of `test_all_nav_items_present`. ADR-040 made /admin answer 403; W3 stops
    offering it. Do not fix a failure here by deleting the assertion — if the entry is meant to be
    visible to members again, that is an ADR, not a test edit."""
    html = _make(person=MEMBER)
    assert 'data-nav="admin"' not in html
    assert 'href="/admin"' not in html
    assert "Administração" not in html
    # …while everything a member IS entitled to stays put.
    assert "id='_gearbtn'" in html and "id='_denbtn'" in html and "id='_themebtn'" in html


def test_a_shell_built_without_identity_is_default_deny():
    """`person=None` is "nobody said who this is" — an unknown, not an admin. The shell resolves it
    the same way the gate does: no admin entry, and no account menu claiming a session."""
    html = _make()
    assert 'data-nav="admin"' not in html and "Administração" not in html
    assert "id='_acctbtn'" not in html


def test_the_signed_in_name_is_escaped():
    """The name is person-controlled text (`auth add`, `/setup`) rendered into the header of every
    page. Unescaped it is stored XSS with the widest possible blast radius."""
    html = _make(person={"person_id": "PER-X", "name": "<img src=x onerror=alert(1)>",
                         "is_admin": False})
    assert "<img src=x onerror=alert(1)>" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_the_account_menu_leads_to_the_account_page():
    """«A minha conta» has to hang off the identity control, not the gear: changing your own password
    is not a setting, and a person who cannot find it asks an admin to reset it instead."""
    html = _make(person=MEMBER)
    assert "/a-minha-conta" in html and "A minha conta" in html


def test_the_account_menu_closes_on_an_outside_click():
    """Mirrors the gear's own behaviour — a menu that only closes by re-clicking its button reads as
    stuck, and two open menus can overlap."""
    html = _make(person=ADMIN)
    assert "acctwrap" in html                    # the close-on-outside-click handler keys on this
    events = html[html.index("_acctbtn"):]
    assert "closest('.acctwrap')" in events


def test_every_lens_forwards_the_signed_in_person_to_the_shell():
    """The seam guard. `page(person=…)` only works if every builder threads it through; a lens that
    forgets renders an admin a shell with no Administração entry and no account menu — a silently
    degraded page, which is worse than a crash because nobody reports it.

    Fix by adding `person` to the builder signature and `person=person` to its `page()` call — not by
    dropping the module from LENS_MODULES."""
    offenders = [m for m in LENS_MODULES if "person=person" not in _src(m)]
    assert not offenders, (
        "lens builders that never forward `person` to cockpit_ui.page(): " + ", ".join(offenders))


def test_only_genuinely_optimistic_call_sites_still_say_revertido():
    """Fila and Para ti mutate local state before the POST, so a rollback is real there. Projetos,
    Capturas and Admin assign only from the server response — nothing to revert, ever."""
    for module in ("projetos_page", "captures_page", "admin_page"):
        assert "S.revertido" not in _src(module), (
            f"{module} reports a rollback it never performs")
    for module in ("fila_page", "para_ti_page"):
        assert "S.revertido" in _src(module)


# ── ADR-047 — «A minha assinatura» on the person's own surface ────────────────

def _account(*, signature="", signature_preview="", **person):
    """The account page. ``signature``/``signature_preview`` are PAGE arguments, not person columns —
    the page is handed the raw template and its rendered form separately (ADR-047), and folding them
    into the person dict here would silently test neither."""
    return cockpit_ui.account_page(
        {"person_id": "P", "name": "Filipe Coelho", "is_admin": False, **person},
        signature=signature, signature_preview=signature_preview)


def test_the_account_page_offers_a_signature_editor():
    html = _account(job_title="Produção", phone="+351 912 345 678")
    assert "A minha assinatura" in html
    form = html[html.index('action="/a-minha-conta/assinatura"'):]
    form = form[:form.index("</form>")]
    assert 'name="signature"' in form and "<textarea" in form
    assert 'name="job_title"' in form and 'value="Produção"' in form
    assert 'name="phone"' in form and 'value="+351 912 345 678"' in form
    # Every token the renderer can fill is advertised; one it cannot is not.
    assert "<code>{nome}</code>" in form and "<code>{cargo}</code>" in form
    assert "{empresa}" not in form


def test_the_editor_shows_the_signature_RENDERED_not_just_the_template():
    """The empty-line rule means you cannot tell what the block looks like by reading the template —
    a person with no phone number has no phone line, and only the preview says so."""
    html = _account(signature="Abraço,\n{nome}\nTel.: {telefone}",
                    signature_preview="Abraço,\nFilipe Coelho")
    assert "Como fica" in html
    prev = html[html.index('class="sigprev"'):]
    assert "Abraço,\nFilipe Coelho" in prev
    assert "{telefone}" not in prev, "the preview is showing the template, not the rendered block"


def test_the_editor_says_so_when_there_is_no_closing_at_all():
    """A blank preview box would read as a rendering bug. Naming the state is the ADR-040 rule
    (a surface must agree with what the app will actually do) applied to a signature."""
    html = _account(signature="{nome}", signature_preview="")
    assert "Sem fecho" in html
    assert "<pre>" not in html


def test_the_signature_editor_carries_the_never_sends_promise():
    """This is the one field on the page whose output a client reads. The person has to know the app
    is not about to mail it for them."""
    assert "nunca envia" in _account()


def test_the_password_form_is_unchanged_by_the_signature_card():
    """The signature card sits ABOVE the password card and shares the .aform class — the account
    anchor NordPass needs must still be inside the password form, and only there."""
    html = _account()
    pw = html[html.index('action="/a-minha-conta/palavra-passe"'):]
    pw = pw[:pw.index("</form>")]
    assert 'autocomplete="username"' in pw
    sig = html[html.index('action="/a-minha-conta/assinatura"'):]
    sig = sig[:sig.index("</form>")]
    assert 'autocomplete="username"' not in sig, (
        "a second username anchor turns the signature form into a login form for a password manager")
    assert 'type="password"' not in sig


def test_the_editor_says_an_outlook_paste_is_welcome_before_you_paste_it():
    """Said BEFORE the paste, not only in the banner after it: the person about to paste an Outlook
    block is exactly the person who needs to know it will come back as plain text."""
    html = _account()
    assert "Outlook/Gmail" in html
    assert "texto simples" in html


# ── Phase 2 (fila-evidence plan §Phase 2) — the signature renders COLLAPSED ────────────────────
# `clean_email_body` deleted the closing salutation and everything after it. That block is where a
# sender's name, role and NIF live, and deleting it is the same class of act as silently binning a
# message: the reader is never told anything was removed. It now arrives as its own field and
# renders behind a toggle, exactly like «mensagem citada».

def _msg_kit_js() -> str:
    """The SHIPPED msgSplitQuote + msgHTML, sliced out of the shared kit so node can run them.

    Deliberately NOT the `_ATT_GLYPH`→`msgThreadHTML(` window that tests/test_attachments.py
    executes — these functions sit before it, and widening that window would drag unrelated source
    into an unrelated test's node payload."""
    kit = cockpit_ui._SHELL_UTILS
    return kit[kit.index("function msgSplitQuote("):kit.index("const _ATT_GLYPH=")]


def _run_msg(js_body: str):
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        import pytest
        pytest.skip("node not available — the shipped JS cannot be executed")
    kit = cockpit_ui._SHELL_UTILS
    esc = kit[kit.index("const esc="):].split("\n")[0]
    src = (esc + "\nfunction msgDirTag(d){return {k:'inbound',c:'#000',i:'',t:'in'};}\n"
           + _msg_kit_js() + "\n" + js_body)
    r = subprocess.run([node, "-e", src], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_the_signature_renders_collapsed_behind_its_own_toggle():
    """EXECUTES msgHTML. The signature must be present in the DOM (so it is one click away and
    searchable) and hidden by default (so it does not push the conversation off-screen)."""
    html = _run_msg(
        "console.log(JSON.stringify(msgHTML({message_id:'m1',body:'Bom dia.\\n\\nCumprimentos\\nSOFIA DIAS',"
        "body_clean:'Bom dia.',body_sig:'Cumprimentos\\nSOFIA DIAS',direction:'inbound'})));")
    assert "SOFIA DIAS" in html                       # present — never deleted
    assert "tsig hidden" in html                      # …and collapsed by default
    assert "stoggle" in html                          # …behind a visible affordance
    assert "assinatura" in html                       # …that says what it is, in PT


def test_a_message_with_no_signature_grows_no_toggle():
    """Absence is absence — the app's standing rule. No empty «assinatura» button on the ~40% of
    messages that never had a closing block."""
    html = _run_msg(
        "console.log(JSON.stringify(msgHTML({message_id:'m1',body:'Bom dia.',"
        "body_clean:'Bom dia.',body_sig:'',direction:'inbound'})));")
    assert "stoggle" not in html and "tsig" not in html


def test_every_toggle_still_sits_immediately_before_the_element_it_reveals():
    """The quote and raw toggles find their target with `nextElementSibling` (msgWireQuoteToggles).
    Inserting the signature block between them would silently repoint a toggle at the wrong div —
    no error, just a button that reveals someone else's content. Executed, then checked positionally."""
    html = _run_msg(
        "console.log(JSON.stringify(msgHTML({message_id:'m1',"
        "body:'Bom dia.\\n\\nDe: x@y.pt\\nPara: z@w.pt\\ncitado',"
        "body_clean:'Bom dia.\\n\\nDe: x@y.pt\\nPara: z@w.pt\\ncitado',"
        "body_sig:'Cumprimentos\\nSOFIA DIAS',direction:'inbound'})));")
    for toggle, target in (("stoggle", "tsig"), ("qtoggle", "tquote")):
        i = html.index(toggle)
        rest = html[i:]
        nxt = rest.index("<div class=")
        assert target in rest[nxt:nxt + 40], (
            f"the element right after .{toggle} is not its .{target} — nextElementSibling breaks")


def test_the_signature_toggle_is_wired_and_reveals_only_its_own_block():
    """A shipped toggle nobody wired is a dead button. The handler must key off `.stoggle` and,
    like the other two, stop the click from reaching the row-level dossier handler."""
    kit = cockpit_ui._SHELL_UTILS
    wiring = kit[kit.index("function msgWireQuoteToggles("):]
    wiring = wiring[:wiring.index("\nlet ")]
    assert ".stoggle" in wiring and "tsig" in wiring
    assert wiring.count("e.stopPropagation()") >= 3     # one per toggle, incl. the new one


def test_ver_original_still_appears_on_a_message_whose_signature_was_kept():
    """`hasNoise = rawBody.length > cleanBody.length + 60` decides whether «ver original» exists.
    Folding the signature INTO body_clean would shrink that gap and delete the escape hatch on
    exactly the messages this phase touches — which is why the signature ships as its own field and
    body_clean is byte-identical to before. This is the guard on that decision."""
    long_sig = "Cumprimentos\\n" + "\\n".join(f"LINHA {i} DA ASSINATURA" for i in range(12))
    html = _run_msg(
        "console.log(JSON.stringify(msgHTML({message_id:'m1',"
        "body:'Bom dia.\\n\\n" + long_sig + "\\n+351 912 345 678\\nhttps://acme.pt',"
        "body_clean:'Bom dia.',body_sig:'" + long_sig + "',direction:'inbound'})));")
    assert "rawtoggle" in html, "the raw-body escape hatch vanished once a signature was kept"


def test_a_signature_only_message_still_renders_its_content():
    """`noVisible` falls back to the raw body when cleaning empties a message. A message that is
    only a signature must still show something — before this phase it fell back to raw, and that
    path must keep working rather than being replaced by a lone collapsed toggle."""
    html = _run_msg(
        "console.log(JSON.stringify(msgHTML({message_id:'m1',"
        "body:'Cumprimentos\\nSOFIA DIAS\\nDiretora',body_clean:'',"
        "body_sig:'Cumprimentos\\nSOFIA DIAS\\nDiretora',direction:'inbound'})));")
    assert "SOFIA DIAS" in html
    assert "tbody" in html, "the raw fallback stopped rendering a body"


# ── Phase 3 (fila-evidence plan §Phase 3) — deterministic evidence spans ───────────────────────
# Click a ledger row, its evidence lights up in the message body. Zero LLM: the format-locked
# fields re-derive their own spans from `extract.py`'s patterns, mirrored client-side over the text
# that is ACTUALLY on screen. Never server-side — `extract_values` folds (NFKD + strip combining +
# casefold) before matching, so its outputs are not substrings of the body and any offset computed
# there drifts silently on exactly the Portuguese mail this app handles.

def _hl_kit_js() -> str:
    """The SHIPPED evidence helpers. Sliced from AFTER msgWireQuoteToggles deliberately: the window
    `_ATT_GLYPH`→`msgThreadHTML(` is executed verbatim by tests/test_attachments.py, and anything
    dropped in there is swallowed into an unrelated test's node payload."""
    kit = cockpit_ui._SHELL_UTILS
    return kit[kit.index("const _EV_AMOUNT="):kit.index("function evHighlight(")]


def _run_hl(js_body: str):
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        import pytest
        pytest.skip("node not available — the shipped JS cannot be executed")
    r = subprocess.run([shutil.which("node"), "-e", _hl_kit_js() + "\n" + js_body],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_the_client_side_patterns_agree_with_extract_py():
    """EXECUTED against the same shapes `extract.py` accepts, because a JS port of a Python regex is
    exactly the kind of change that looks right and behaves differently (`\\b`, `re.I`, the PT
    thousands separator). If these drift, the dossier highlights text the pipeline never extracted."""
    out = _run_hl(
        "const t='Total 1.250,00 EUR e ainda 50 peças e 3mm e 2026 e €80';"
        "console.log(JSON.stringify({"
        " amounts:evMatches(t,'money').map(m=>t.slice(m.s,m.e)),"
        " nifs:evMatches('NIF: 501442600 e 123456789','nif').map(m=>m.text),"
        " ibans:evMatches('IBAN PT50 0002 0123 1234 5678 9015 4 fim','iban').map(m=>m.text)}));")
    assert out["amounts"] == ["1.250,00 EUR", "€80"], out["amounts"]
    assert "50 peças" not in " ".join(out["amounts"])   # the currency anchor is what keeps precision
    assert out["nifs"] == ["501442600"], "the mod-11 check must reject 123456789"
    assert out["ibans"] == ["PT50 0002 0123 1234 5678 9015 4"]


def test_the_nif_span_covers_the_number_and_not_its_anchor_word():
    """`_NIF` has a CAPTURE GROUP (`(?:nif|nipc|contribuinte)\\D{0,12}(\\d{9})`); `_AMOUNT`/`_IBAN`
    do not. A `matchAll` port that highlights `m[0]` paints «NIF: » too — visibly wrong, and it
    silently proves the offset was never re-derived from the group."""
    out = _run_hl("const t='Empresa, NIF: 501442600, obrigado';"
                  "const m=evMatches(t,'nif')[0];"
                  "console.log(JSON.stringify({text:t.slice(m.s,m.e),s:m.s,e:m.e}));")
    assert out["text"] == "501442600"


def test_a_formatted_iban_still_matches_the_folded_stored_value():
    """THE reason this is not a substring search. `extract_values` stores the IBAN space-stripped and
    upper-cased (`PT50000201231234567890154`); the body says «PT50 0002 0123 …». A naive
    `body.includes(value)` finds nothing — measured False by execution — so the match is by
    NORMALISED form, and the span is the raw on-screen text."""
    out = _run_hl(
        "const t='Pagamento para PT50 0002 0123 1234 5678 9015 4 ate sexta';"
        "console.log(JSON.stringify({"
        " naive:t.includes('PT50000201231234567890154'),"
        " found:evLocate(t,'iban','PT50000201231234567890154').map(m=>t.slice(m.s,m.e))}));")
    assert out["naive"] is False, "if this ever becomes True the test has stopped proving anything"
    assert out["found"] == ["PT50 0002 0123 1234 5678 9015 4"]


def test_a_non_format_locked_value_falls_back_to_an_accent_folded_search():
    """«Produto / serviço» and «Nome» have no format. They fall back to a fold-tolerant literal
    search — the mechanism §4 rejects as a PRIMARY strategy (37% hit rate) but which is correct as a
    user-initiated secondary: the person clicked the row, and a miss simply highlights nothing."""
    out = _run_hl(
        "const t='Pedido de letras em INOX escovado para a fachada';"
        "console.log(JSON.stringify({"
        " hit:evLocate(t,'product_or_service','letras em inox escovado').map(m=>t.slice(m.s,m.e)),"
        " miss:evLocate(t,'product_or_service','carimbos para ceramica')}));")
    assert out["hit"] == ["letras em INOX escovado"], "case/accent folding must not change the span"
    assert out["miss"] == [], "a miss is silent — never a wrong highlight"


def test_a_value_that_is_not_in_the_text_highlights_nothing():
    """40% of extracted values are never in the email text in any form (§3.2). The honest answer is
    zero spans — no fuzzy nearest-match, ever. A wrong highlight is worse than none."""
    out = _run_hl("console.log(JSON.stringify(evLocate('Bom dia, até sexta.','deadline','2026-08-07')));")
    assert out == []


def test_the_highlight_uses_the_custom_highlight_api_and_never_mark():
    """Splicing `<mark>` into the escaped body breaks two things at once: `esc()` does not escape
    `'`, and indexing escaped text drifts 4 chars per `&`; and a wrapper element repoints the
    `nextElementSibling` toggles. The Custom Highlight API paints over live Ranges and mutates no DOM."""
    kit = cockpit_ui._SHELL_UTILS
    fn = kit[kit.index("function evHighlight("):]
    fn = fn[:fn.index("\nfunction ", 10)] if "\nfunction " in fn[10:] else fn
    assert "<mark" not in fn and "innerHTML" not in fn
    assert "CSS.highlights" in fn and "new Range(" in fn
    assert "typeof Highlight" in fn or "!CSS.highlights" in fn, "must feature-detect, not assume"


def test_the_hidden_raw_body_is_never_highlighted():
    """There are TWO .tbody per message when the raw body is noisier — the visible one and the one
    inside the hidden `.rawbody` («ver original»). Highlighting the hidden copy paints nothing the
    user can see and, worse, makes the count of matches lie."""
    kit = cockpit_ui._SHELL_UTILS
    fn = kit[kit.index("function evTextNodes("):kit.index("function evHighlight(")]
    assert "rawbody" in fn, "the hidden raw copy must be excluded explicitly"


# ── Phase 4: the LOCATED sentence (ADR-054) ──────────────────────────────────────────────────────

def test_a_located_sentence_is_found_across_the_hard_wrap_that_rendered_it():
    """The locate pass stores the email's own text, newline and all; the DOM holds that text after
    clean_email_body dropped lines, msgSplitQuote TRIMMED both halves and three slices cut it. A run
    of whitespace is the one difference that survives all of that, so it is the one tolerated."""
    out = _run_hl(
        "const t='Precisamos de construção de cenografia \"Órfãos da \\nLua\" para a peça.';"
        "console.log(JSON.stringify("
        " evLocateQuote(t,'construção de cenografia \"Órfãos da Lua\"').map(m=>t.slice(m.s,m.e))));")
    assert out == ['construção de cenografia "Órfãos da \nLua"']


def test_the_quote_span_survives_folding_that_is_not_length_preserving():
    """evLocate's fallback assumes folding is 1:1 per character and says so in-line. That holds for
    NFC Portuguese but NOT in general — NFKD expands a ligature, and a lone combining mark folds
    away to nothing. The quote path maps every normalised character back to its SOURCE index instead
    of doing arithmetic on the folded string, so a ligature cannot shift the span."""
    out = _run_hl(
        "const t='O orçamento inclui o acabamento ﬁnal da peça.';"
        "console.log(JSON.stringify({"
        " span:evLocateQuote(t,'o acabamento final da peça').map(m=>t.slice(m.s,m.e)),"
        " naive:t.indexOf('o acabamento final')}));")
    assert out["naive"] == -1, "if this ever becomes -1≠ the test has stopped proving anything"
    assert out["span"] == ["o acabamento ﬁnal da peça"]


def test_a_sentence_that_is_not_on_screen_paints_nothing():
    """A stored quote can still be unreachable — the region it came from may have been cut by the
    render. Silence there is correct; «sem evidência visível» is the honest answer."""
    out = _run_hl("console.log(JSON.stringify(evLocateQuote('Bom dia.','uma frase que não está aqui')));")
    assert out == []


def test_an_empty_quote_never_matches_everything():
    """The dangerous degenerate case: an empty needle found at every offset would paint the entire
    dossier and read as a spectacular success."""
    out = _run_hl("console.log(JSON.stringify({"
                  " empty:evLocateQuote('Bom dia, tudo bem?',''),"
                  " spaces:evLocateQuote('Bom dia, tudo bem?','   '),"
                  " nul:evLocateQuote('Bom dia, tudo bem?',null)}));")
    assert out == {"empty": [], "spaces": [], "nul": []}


def test_the_located_sentence_is_a_fallback_and_never_replaces_an_exact_value_span():
    """The ORDER is the design. The deterministic search already paints 44% of ledger rows exactly,
    and on those the model's quote is an echo of the value 89% of the time — preferring it would
    swap a precise span for a whole sentence, and would move what the browser e2e tests read back
    out of CSS.highlights. Phase 4 exists for the rows where the value is in the email in NO form."""
    kit = cockpit_ui._SHELL_UTILS
    fn = kit[kit.index("function evHighlight("):]
    fn = fn[:fn.index("\nfunction ", 10)]
    assert fn.index("evLocate(t,key,value)") < fn.index("evLocateQuote(t,quote)")
    assert "!ranges.length&&quote" in fn, "the quote path must be conditional on the value finding none"


def test_a_message_card_carries_its_id_so_a_narrative_step_can_reach_it():
    """data-tmid, not data-mid: the translate button already owns data-mid and is found by lookups
    that walk up the tree, so giving the wrapper the same attribute would make every click inside a
    message resolve to it."""
    kit = cockpit_ui._SHELL_UTILS
    fn = kit[kit.index("function msgHTML("):kit.index("function msgWireQuoteToggles(")]
    assert 'data-tmid="' in fn
    assert 'class="tmsg' in fn


def test_the_highlight_token_exists_in_both_themes():
    """No transient accent existed before this phase — the palette is bands, counterparties and the
    steel accent, all of which already MEAN something. Highlight gets its own token so it cannot be
    read as «atrasado» or «cliente», and it must be defined in light AND dark or one theme paints
    the evidence in the browser default."""
    html = _make()
    light = html.split(":root{")[1].split("}")[0]
    dark = html.split(':root[data-theme="dark"]{')[1].split("}")[0]
    assert "--hl-bg:" in light and "--hl-tx:" in light
    assert "--hl-bg:" in dark and "--hl-tx:" in dark
    assert "::highlight(evid)" in html
