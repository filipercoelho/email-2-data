"""Real-browser acceptance for ADR-014 — deep-linkable cockpit URLs.

Heavier than the rest of the suite on purpose: it boots ``create_app()`` (working-tree code) on a
loopback port and drives a real Chrome via Playwright, so it can assert that *clicking* actually
changes ``location.href`` — the one thing the FastAPI ``TestClient`` can never check, because it never
runs the page JS. The text-presence tests in ``test_fila.py`` / ``test_webapp.py`` guard that the
wiring is *shipped*; this guards that the wiring *works*.

Opt-in. Skipped unless the ``e2e`` extra (Playwright) AND a Chrome/Chromium are installed, so the
default ``pytest -q`` stays clean without browser deps:

    pip install -e '.[e2e]'        # then `playwright install chromium`, or rely on system Chrome

Never binds 8042/8000 — it grabs a free loopback port so it can't collide with a running server.
"""

import json
import socket
import threading
import time
import urllib.request

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import expect, sync_playwright  # noqa: E402

uvicorn = pytest.importorskip("uvicorn")

from email2data.crm import CrmStore  # noqa: E402
from email2data.webapp import create_app  # noqa: E402
from email2data.workspace import Workspace  # noqa: E402


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _seed_crm() -> CrmStore:
    """One inbound CLIENT thread (root ``t1``) so the Fila has a row to expand."""
    crm = CrmStore(":memory:").connect()
    env = {"message_id": "t1", "date": "2026-06-03T09:00:00",
           "from": {"email": "maria@acme.pt", "name": "Maria"}, "reply_to": {"email": "", "name": ""},
           "to": [{"email": "orcamentos@lindoservico.pt", "name": ""}], "cc": [],
           "references": [], "in_reply_to": None, "attachments": [], "subject": "Orçamento 50 placas"}
    verdict = {"direction": "inbound", "counterparty": "CLIENT",
               "purpose": "ESTIMATE_REQUEST_FROM_CLIENT", "priority": "HIGH", "urgency": 80,
               "entities": {}, "confidence": 0.9, "decided_by": "tier1:gemini-2.5-flash",
               "reason": "pede orçamento"}
    crm.record(env, verdict)
    return crm


@pytest.fixture(scope="module")
def live_app(tmp_path_factory):
    """Serve the injected app on a free loopback port; yield ``(base_url, project_id)``."""
    ws = Workspace(tmp_path_factory.mktemp("ws") / "w.db").connect()
    app = create_app({"team": ["Pedro"]}, workspace=ws, jobspecs={}, prepared=([], [], {}),
                     reply_pb="", crm_store=_seed_crm())
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(250):
        if server.started:
            break
        time.sleep(0.02)
    if not server.started:
        pytest.skip("uvicorn did not start in time")
    base = f"http://127.0.0.1:{port}"
    # Create one project through the real API so the Projetos list has a row to click.
    req = urllib.request.Request(f"{base}/api/projects", method="POST",
                                 data=json.dumps({"title": "Troféus Acme"}).encode(),
                                 headers={"Content-Type": "application/json"})
    pid = json.loads(urllib.request.urlopen(req).read())["project_id"]
    yield base, pid
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = None
        for launch_kwargs in ({"channel": "chrome"}, {}):   # system Chrome, else bundled chromium
            try:
                b = p.chromium.launch(headless=True, **launch_kwargs)
                break
            except Exception:  # noqa: BLE001 — try the next launcher, skip if none work
                b = None
        if b is None:
            pytest.skip("no Chrome/Chromium available for Playwright")
        yield b
        b.close()


def test_projetos_click_drives_url_and_back_returns(live_app, browser):
    """Clicking a project row pushes /projetos/<pid> into the address bar; browser Back returns to
    /projetos (the list)."""
    base, pid = live_app
    page = browser.new_page()
    try:
        page.goto(f"{base}/projetos")
        page.click(".row")
        expect(page).to_have_url(f"{base}/projetos/{pid}", timeout=5000)
        page.go_back()
        expect(page).to_have_url(f"{base}/projetos", timeout=5000)
    finally:
        page.close()


def test_projetos_deep_link_opens_detail_and_unknown_404s(live_app, browser):
    """A direct load of /projetos/<pid> renders the detail workbench (not the list); an unknown id
    404s instead of serving an empty workbench."""
    base, pid = live_app
    page = browser.new_page()
    try:
        page.goto(f"{base}/projetos/{pid}")
        page.wait_for_selector("#_backbtn", timeout=5000)
        assert page.is_visible("#_backbtn")
        assert page.request.get(f"{base}/projetos/p-9999").status == 404
    finally:
        page.close()


def test_fila_expand_writes_thread_param_and_collapse_clears(live_app, browser):
    """Expanding a Fila thread writes ?thread=<root> to the URL; collapsing it clears the param."""
    base, _pid = live_app
    page = browser.new_page()
    try:
        page.goto(f"{base}/")
        page.click(".row .subj")   # the subject line is unambiguously the thread toggle
        page.wait_for_function("location.search.includes('thread=')", timeout=5000)
        assert "thread=mid%3At1" in page.url
        page.click(".row .subj")   # the subject line is unambiguously the thread toggle
        page.wait_for_function("!location.search.includes('thread=')", timeout=5000)
        assert "thread=" not in page.url
    finally:
        page.close()


def test_contrapartes_detail_navigates_to_related_data(live_app, browser):
    """The Contrapartes hub deep-links to where the data lives: the insight strip renders, an open
    thread jumps to the Fila (?thread=), and a timeline row jumps to the inbox report."""
    base, _pid = live_app
    page = browser.new_page()
    try:
        page.goto(f"{base}/contrapartes/acme.pt")
        page.wait_for_selector(".stats .stat", timeout=5000)            # insight strip rendered
        page.click("#_threads .lrow")                                   # open thread -> Fila
        page.wait_for_function("location.pathname=='/' && location.search.includes('thread=')",
                               timeout=5000)
        assert "thread=" in page.url
        page.goto(f"{base}/contrapartes/acme.pt")
        page.wait_for_selector("#_timeline .tclick", timeout=5000)
        page.click("#_timeline .tclick")                               # timeline row -> inbox report
        page.wait_for_function("location.pathname=='/inbox'", timeout=5000)
        assert "/inbox" in page.url
    finally:
        page.close()


def test_registar_capture_deep_links_and_writes_timeline(live_app, browser):
    """ADR-015 capture: /projetos/<pid>?registar=nota deep-links straight into the Registar tab, and
    capturing a note (deterministic, no LLM) appends it to the timeline — proving the off-email
    knowledge path works end-to-end in a real browser, and that ?registar view-state survives load."""
    base, pid = live_app
    page = browser.new_page()
    try:
        page.goto(f"{base}/projetos/{pid}?registar=nota")
        page.wait_for_selector("#_captext", timeout=5000)
        assert page.is_visible("#_captext")                                   # capture surface shown
        assert "on" in (page.get_attribute('.ptab-btn[data-tab="registar"]', "class") or "")
        page.fill("#_captext", "Cliente confirmou inox 304 por telefone")
        page.click("#_capsave")
        page.wait_for_selector("#_timeline .tl-row", timeout=5000)            # save -> timeline tab
        assert "inox 304" in page.inner_text("#_timeline")
        page.wait_for_function("!location.search.includes('registar=')", timeout=5000)
    finally:
        page.close()


def test_projeto_ux_tabs_tiers_and_gapjump(live_app, browser):
    """UX pass: the email composer lives in its OWN 'Email ao cliente' tab (not the spec scroll),
    optional gaps are NOT styled as red blockers (only must-gaps are), and the section gap-count
    jumps focus to the first missing required field — the page's 'what's next' affordance."""
    base, pid = live_app
    page = browser.new_page()
    try:
        page.goto(f"{base}/projetos/{pid}")
        page.wait_for_selector(".ptabs", timeout=5000)
        # composer moved OUT of Especificação and into the Email tab (one tab = one task)
        assert page.locator('.ppanel[data-panel="espec"] #_ask').count() == 0
        assert page.locator('.ppanel[data-panel="email"] #_ask').count() == 1
        page.click('.ptab-btn[data-tab="email"]')
        page.wait_for_selector('.ppanel[data-panel="email"]:not(.hidden) #_ask', timeout=5000)
        # tier-aware color semantics: required-missing rows flagged, optional gaps stay calm
        page.click('.ptab-btn[data-tab="espec"]')
        assert page.locator(".frow.miss-must").count() >= 1
        assert page.locator(".frow.miss-opt").count() >= 1
        # gap-count jumps focus to the first missing REQUIRED field
        page.click("#_gapjump")
        page.wait_for_function(
            "document.activeElement && document.activeElement.classList.contains('finput')", timeout=5000)
        addr = page.evaluate("document.activeElement.dataset.addr")
        assert page.locator(f'.frow.miss-must .finput[data-addr="{addr}"]').count() == 1
    finally:
        page.close()


def test_fila_counterparty_filter_is_deep_linkable(live_app, browser):
    """Loading /?counterparty=CLIENT applies the filter from the URL (the row survives); an unknown
    counterparty filters everything out — proving the query param actually drives the view."""
    base, _pid = live_app
    page = browser.new_page()
    try:
        page.goto(f"{base}/?counterparty=CLIENT")
        page.wait_for_selector(".row", timeout=5000)
        assert page.locator(".row").count() == 1
        page.goto(f"{base}/?counterparty=NOPE")
        page.wait_for_function("document.querySelectorAll('.row').length === 0", timeout=5000)
        assert page.locator(".row").count() == 0
    finally:
        page.close()
