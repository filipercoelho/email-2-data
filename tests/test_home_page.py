"""Início — the landing page at «/» (ADR-044).

Two things are being protected here, and they fail in different ways:

* the **numbers** (`cockpit.home_summary`) — a wrong count on the landing page is worse than a wrong
  count anywhere else, because it is the one screen someone reads without reading anything else;
* the **restraint** — the page's whole reason to exist is that it shows less than the Fila. Nothing
  stops a future change from putting the queue back on it one useful widget at a time, so the
  "stays minimal" property is asserted, not assumed.
"""

from __future__ import annotations

import re

import pytest

from email2data import cockpit, home_page

pytest.importorskip("fastapi")


# ── fixtures ────────────────────────────────────────────────────────────────────────────────────

def _row(counterparty="CLIENT", state=cockpit.WE_OWE, band="red", age_h=48.0, root=None):
    """A Fila row, reduced to the fields the summary reads."""
    return {"thread_root": root or f"t-{counterparty}-{state}-{band}-{age_h}",
            "counterparty": counterparty,
            "clock": {"state": state, "band": band, "age_hours": age_h}}


ROWS = [
    _row("CLIENT", cockpit.WE_OWE, "red", 312.0),      # 13 dias — the worst one we owe
    _row("CLIENT", cockpit.WE_OWE, "amber", 6.0),
    _row("CLIENT", cockpit.WE_OWE, "green", 1.0),      # fresh — NOT demand
    _row("CLIENT", cockpit.AWAITING, "amber", 90.0),   # ours to chase
    _row("CLIENT", cockpit.AWAITING, "green", 5.0),    # their move, not overdue
    _row("SUPPLIER", cockpit.WE_OWE, "red", 100.0),
    _row("SUPPLIER", cockpit.AWAITING, "amber", 80.0),
    _row("LEAD", cockpit.INFO, "none", 200.0),
    # A very old row nobody is waiting on: it must NOT become «a mais antiga».
    _row("CLIENT", cockpit.INFO, "none", 5000.0),
]


# ── the numbers ─────────────────────────────────────────────────────────────────────────────────

def test_demand_counts_only_what_actually_demands_a_human():
    """WE_OWE at red/amber is demand; WE_OWE at green is not.

    The green exclusion is the load-bearing half. Counting a thread the moment it arrives would make
    the headline tick up for *receiving mail*, which turns «N esperam resposta» into a mail counter —
    the exact inventory-not-demand confusion ADR-034 removed from the nav badge."""
    assert cockpit.respond_demand(ROWS) == 3            # 2 CLIENT (red+amber) + 1 SUPPLIER red
    assert cockpit.chase_demand(ROWS) == 2              # AWAITING amber only, both fronts

    fresh = [_row("CLIENT", cockpit.WE_OWE, "green", 0.5)]
    assert cockpit.respond_demand(fresh) == 0


def test_the_python_and_js_definitions_of_demand_are_the_same_rule():
    """`respondCount`/`chaseCount` live in the Fila's JS; `respond_demand`/`chase_demand` live in
    Python and feed the nav badge and Início. Three surfaces, one queue, one viewport — if they drift
    the app contradicts itself on screen, which is how «56 a responder» and a «54» badge would ship.

    Asserted by reading the JS source, deliberately: a comment saying "keep these in sync" is not a
    mechanism. Change one and this fails until you change the other."""
    from email2data import fila_page

    js = fila_page._LENS_JS
    respond_js = re.search(r"function respondCount\(list\)\{([^}]*\}[^}]*)\}", js).group(1)
    chase_js = re.search(r"function chaseCount\(list\)\{([^}]*\}[^}]*)\}", js).group(1)

    # WE_OWE + (red|amber) — same states, same bands, same side of the comparison.
    assert "'WE_OWE'" in respond_js
    assert "c.band==='red'" in respond_js and "c.band==='amber'" in respond_js
    assert "'AWAITING'" in chase_js and "c.band==='amber'" in chase_js
    assert "'red'" not in chase_js                       # a chase is never red — that would be ours

    # …and the Python agrees, row for row, on a mixed set.
    assert cockpit.respond_demand(ROWS) == sum(
        1 for r in ROWS
        if r["clock"]["state"] == "WE_OWE" and r["clock"]["band"] in ("red", "amber"))
    assert cockpit.chase_demand(ROWS) == sum(
        1 for r in ROWS
        if r["clock"]["state"] == "AWAITING" and r["clock"]["band"] == "amber")


def test_oldest_is_the_oldest_we_owe_not_the_oldest_row():
    """«a mais antiga está parada há N» is a promise that acting on it makes N go away.

    The 5000h INFO row is older than everything, and nobody is waiting on it. Reporting that would
    put a permanent, terrifying, un-actionable number on the landing page."""
    assert cockpit.oldest_owed_hours(ROWS) == 312.0
    assert cockpit.humanize_age(312.0) == "13 dias"

    # Nothing owed → no claim at all, rather than a zero that reads like "0 days ago".
    quiet = [_row("CLIENT", cockpit.AWAITING, "green", 9000.0)]
    assert cockpit.oldest_owed_hours(quiet) is None


def test_each_front_counts_only_its_own_counterparty():
    """ADR-034's rule, carried onto the landing page: a card's numbers describe the card. A Clientes
    card showing the global demand is the 58-vs-32 confusion the Fila already fixed once."""
    s = cockpit.home_summary(ROWS)

    assert s["CLIENT"]["respond"] == 2 and s["CLIENT"]["chase"] == 1
    assert s["SUPPLIER"]["respond"] == 1 and s["SUPPLIER"]["chase"] == 1
    assert s["LEAD"]["respond"] == 0 and s["LEAD"]["chase"] == 0
    assert s["LEAD"]["total"] == 1

    # «Hoje» is the whole active queue, and the fronts sum into it (no row counted twice, none lost).
    assert s["all"]["total"] == len(ROWS)
    assert s["all"]["respond"] == s["CLIENT"]["respond"] + s["SUPPLIER"]["respond"] + s["LEAD"]["respond"]

    # Each front's «mais antiga» is its own, not the global one.
    assert s["CLIENT"]["oldest_label"] == "13 dias"
    assert s["SUPPLIER"]["oldest_h"] == 100.0
    assert s["LEAD"]["oldest_label"] == ""


def test_summary_of_an_empty_queue_is_zeroes_not_a_crash():
    """The all-clear morning. Every block must still exist so the page can render «Está tudo
    tratado» instead of throwing on a missing key — a landing page that white-screens when there is
    no work is broken exactly when it should be most reassuring."""
    s = cockpit.home_summary([])
    for key in ("all", "CLIENT", "SUPPLIER", "LEAD"):
        assert s[key] == {"total": 0, "respond": 0, "chase": 0, "oldest_h": None, "oldest_label": ""}


def test_rows_with_missing_or_malformed_clocks_are_survivable():
    """Rows arrive from build_fila, but the summary must not be the thing that explodes if one is
    short a clock — the landing page failing takes the whole app's front door with it."""
    weird = [{"counterparty": "CLIENT"},                       # no clock at all
             {"counterparty": "CLIENT", "clock": {}},          # empty clock
             {"clock": {"state": cockpit.WE_OWE, "band": "red"}},   # no counterparty, no age
             {"counterparty": None, "clock": None}]
    s = cockpit.home_summary(weird)
    assert s["all"]["total"] == 4
    assert s["all"]["respond"] == 1          # the WE_OWE/red row counts…
    assert s["all"]["oldest_h"] == 0.0       # …and its missing age reads as 0, not as a crash


# ── the page ────────────────────────────────────────────────────────────────────────────────────

def _html(rows=ROWS, **kw):
    kw.setdefault("nav_counts", {"fila": 3, "para-ti": 17, "capturas": 1})
    kw.setdefault("person", {"name": "Filipe", "is_admin": True})
    return home_page.build_home_html(cockpit.home_summary(rows), **kw)


def test_the_page_is_the_four_buttons_and_carries_no_queue():
    """The restraint, asserted. Início exists because the cockpit shows too much on arrival; a change
    that reintroduces the rows, the vistas rail, the dossier or the filter bar has undone it, however
    reasonable each addition looked on its own."""
    html = _html()

    assert 'id="_hcards"' in html
    for front in ("CLIENT", "SUPPLIER", "LEAD"):                         # Clientes/Fornecedores/Leads
        assert f"href:'/fila?tab={front}'" in html
    assert "href:'/para-ti'" in html                                     # …+ Para ti = four cards

    for banned in ('class="mesa"', 'id="_vrail"', 'id="_doss"', 'id="_fbar"',
                   'id="_list"', 'id="_selbar"'):
        assert banned not in html, f"the Fila's {banned} came back to the landing page"


def test_the_page_renders_its_answer_without_a_fetch():
    """Every number is embedded server-side. A landing page that has to call an API before it can say
    anything shows a spinner as its first impression, which is the failure this page exists to
    prevent — the point is that arriving costs one glance."""
    html = _html()
    assert "const SUMMARY =" in html
    assert '"respond": 3' in html.replace("'", '"') or '"respond":3' in html.replace(" ", "")
    # The only fetch the LENS makes is the post-sync repaint, never the first paint. (getJSON is also
    # *defined* in the shared shell — count calls with a literal URL, which only the lens has.)
    assert html.count("getJSON('") == 1
    assert "getJSON('/api/inicio')" in html

    # …and it is in onSynced(), not in render(): the first paint must touch the network zero times.
    render_body = home_page._LENS_JS.split("function render(){", 1)[1].split("\n}", 1)[0]
    for network in ("getJSON", "fetch(", "post(", "await "):
        assert network not in render_body, f"render() reached the network via {network!r}"


def test_the_headline_and_the_cards_come_from_one_summary():
    """The «Hoje» headline and each card read the same object, so they cannot disagree. Pinned
    structurally: two independent embeds would be two things to update and one to forget."""
    html = _html()
    assert "const PARA_TI = 17" in html and "const CAPTURAS = 1" in html
    assert html.count("const SUMMARY =") == 1
    assert "summary.all" in html and "summary[f.k]" in html


def test_para_ti_and_capturas_agree_with_their_nav_badges():
    """The card and the badge are the same number from the same dict — shown twice in one viewport,
    so a mismatch is visible and embarrassing."""
    html = _html(nav_counts={"fila": 3, "para-ti": 4, "capturas": 9})
    assert "const PARA_TI = 4" in html and "const CAPTURAS = 9" in html
    assert '<span class="nbadge">4</span>' in html and '<span class="nbadge">9</span>' in html


def test_no_lens_is_active_and_the_logo_is():
    """Início is not one of the queues, so no nav link may claim it — but the header still has to say
    where you are (see test_the_logo_is_the_way_back_to_inicio in test_cockpit_ui.py)."""
    html = _html()
    assert 'class="nlink on"' not in html
    assert "class='logo on' href='/'" in html


def test_the_page_forwards_the_signed_in_person():
    """ADR-041's default-deny shell contract: a builder that drops `person` costs an admin their
    «Administração» link. Início is a new builder and inherits the same obligation."""
    assert "Administração" in _html(person={"name": "Filipe", "is_admin": True})
    assert "Administração" not in _html(person={"name": "Zé", "is_admin": False})
    assert "Administração" not in _html(person=None)


def test_the_page_is_calm_when_nothing_is_owed():
    """The design guardrail: colour means «you are needed». A quiet morning must render quiet, or the
    signal is always on and therefore carries nothing.

    Asserted on the rendered payload rather than the JS: with an empty queue the summary itself must
    contain no demand for the template to colour."""
    html = _html(rows=[_row("CLIENT", cockpit.AWAITING, "green", 2.0)])
    assert '"respond": 0' in html.replace("'", '"') or '"respond":0' in html.replace(" ", "")
    assert "Está tudo tratado." in html                  # the zero-state headline exists
    assert "em dia" in html and "sem leads novos" in html


def test_secondary_destinations_stay_reachable():
    """Stripping the page down must not strip away the way out. Capturas / Projetos / Contrapartes /
    the full Fila are all one click away, or the landing page is a dead end that people learn to
    skip past."""
    html = _html()
    for href in ("/capturas", "/projetos", "/contrapartes", "/fila"):
        assert f"href:'{href}'" in html or f'href="{href}"' in html
