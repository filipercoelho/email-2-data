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


def _set_field(base: str, pid: str, field: str, value: str) -> None:
    """Set a spec field through the real API (the e2e workspace is a throwaway tmp db)."""
    urllib.request.urlopen(urllib.request.Request(
        f"{base}/api/projects/{pid}/field", method="POST",
        data=json.dumps({"field": field, "value": value}).encode(),
        headers={"Content-Type": "application/json"}))


def _reset_deadline(base: str, pid: str) -> None:
    """Clear `deadline` on the SHARED project. ``live_app`` is module-scoped, so a test that leaves a
    non-ISO deadline behind silently flips the next test's input to the type=text fallback — which is
    exactly how this helper came to exist. Any test that writes `deadline` must call this."""
    _set_field(base, pid, "deadline", "")


@pytest.fixture(scope="module")
def live_app(tmp_path_factory):
    """Serve the injected app on a free loopback port; yield ``(base_url, project_id)``."""
    ws = Workspace(tmp_path_factory.mktemp("ws") / "w.db").connect()
    app = create_app({"team": ["Diogo"]}, workspace=ws, jobspecs={}, prepared=([], [], {}),
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


def test_fila_focus_writes_thread_param_and_mounts_dossier(live_app, browser):
    """Since ADR-033 (Mesa) focusing IS opening: clicking a row writes ?thread=<root> and mounts
    the conversation in the dossier pane — there is no collapse gesture to clear it, because the
    dossier always shows the focused conversation (one queue, one focus, one URL)."""
    base, _pid = live_app
    page = browser.new_page()
    try:
        page.goto(f"{base}/")
        page.click(".row .rname")                       # anywhere on the row focuses + opens
        page.wait_for_function("location.search.includes('thread=')", timeout=5000)
        assert "thread=mid%3At1" in page.url
        # the dossier mounted the same conversation: verb bar + the thread renderer output
        page.wait_for_selector("#_doss .dverbs", timeout=5000)
        page.wait_for_selector("#_doss .texp", timeout=5000)
        # …and the thread LEDGER (P4a) arrived with the same fetch — past its loading state
        page.wait_for_selector("#_doss .dledger", timeout=5000)
        page.wait_for_function(
            "!document.querySelector('#_doss .dledger').textContent.includes('a carregar')",
            timeout=5000)
    finally:
        page.close()


def test_contrapartes_detail_navigates_to_related_data(live_app, browser):
    """The Contrapartes hub deep-links stay INSIDE the cockpit: the insight strip renders, an open
    thread jumps to the Fila (?thread=), and a timeline row ALSO opens its conversation in the Fila
    — never the legacy /inbox app (the old behavior this test used to pin, now a fixed defect)."""
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
        page.click("#_timeline .tclick")                               # timeline row -> Fila thread
        page.wait_for_function("location.pathname=='/' && location.search.includes('thread=')",
                               timeout=5000)
        assert "thread=" in page.url and "/inbox" not in page.url
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


def test_prazo_is_a_real_datetime_picker_without_losing_a_non_iso_value(live_app, browser):
    """`prazo` renders as a NATIVE date+time input, a picked value round-trips to the store, and a
    stored value that isn't ISO degrades to a text box that still SHOWS it — the failure this guards
    is a picker silently rendering "meados de agosto" as an empty field, which reads as 'the deadline
    is missing' when it isn't. Only a real browser can prove any of it."""
    base, pid = live_app
    page = browser.new_page()
    try:
        page.goto(f"{base}/projetos/{pid}")
        prazo = page.locator('.finput[data-addr="deadline"]')
        page.wait_for_selector('.finput[data-addr="deadline"]', timeout=5000)
        assert prazo.get_attribute("type") == "datetime-local"
        # Chrome parsed it as a real datetime control, not a text box that merely claims the type:
        # valueAsNumber is NaN on a text input and a number on a populated datetime-local.
        prazo.fill("2026-08-14T14:30")
        prazo.dispatch_event("change")
        page.wait_for_function(
            "document.querySelector('.finput[data-addr=\"deadline\"]').value === '2026-08-14T14:30'",
            timeout=5000)
        assert page.evaluate("!isNaN(document.querySelector("
                             "'.finput[data-addr=\"deadline\"]').valueAsNumber)")
        # the TIME actually persisted server-side — not just echoed in the DOM
        page.reload()
        page.wait_for_selector('.finput[data-addr="deadline"]', timeout=5000)
        assert prazo.input_value() == "2026-08-14T14:30"
        # a date-only value (extractor/LLM/pre-clock data) still gets a picker, widened to midnight
        _set_field(base, pid, "deadline", "2026-09-02")
        page.reload()
        page.wait_for_selector('.finput[data-addr="deadline"]', timeout=5000)
        assert prazo.get_attribute("type") == "datetime-local"
        assert prazo.input_value() == "2026-09-02T00:00"
        # ...and the widening is DISPLAY-only: an untouched field writes nothing back to the store
        stored = json.loads(urllib.request.urlopen(
            f"{base}/api/projects/{pid}").read())["job_fields"]["deadline"]["value"]
        assert stored == "2026-09-02", f"display widening leaked into the store: {stored!r}"
        # a free-text deadline (LLM/legacy) must stay visible rather than vanish into a picker
        _set_field(base, pid, "deadline", "meados de agosto")
        page.reload()
        page.wait_for_selector('.finput[data-addr="deadline"]', timeout=5000)
        assert prazo.get_attribute("type") == "text"
        assert prazo.input_value() == "meados de agosto"
    finally:
        _reset_deadline(base, pid)
        page.close()


def test_clicking_anywhere_on_a_date_field_opens_the_picker(live_app, browser):
    """Clicking the *text* area of a date field must open the picker, not just the ~14px calendar
    glyph — the regression is a box that looks clickable and does nothing. The native picker is
    browser chrome that automation cannot observe, so this stubs `showPicker` and asserts OUR wiring
    invokes it exactly once per click (the stub also stops a real picker blocking a headless run).
    It proves the call is made; it does not prove the OS widget painted."""
    base, pid = live_app
    page = browser.new_page()
    spy = ("window.__picks=[];HTMLInputElement.prototype.showPicker="
           "function(){window.__picks.push((this.dataset||{}).addr||this.type);};")
    page.add_init_script(spy)
    try:
        page.goto(f"{base}/projetos/{pid}")
        page.wait_for_selector('.finput[data-addr="deadline"]', timeout=5000)
        prazo = page.locator('.finput[data-addr="deadline"]')
        assert prazo.get_attribute("type") == "datetime-local"
        # click well inside the LEFT edge — nowhere near the calendar indicator on the right
        prazo.click(position={"x": 12, "y": 15})
        assert page.evaluate("window.__picks") == ["deadline"]
        # a second click opens it again (not a one-shot), and never double-fires per click
        prazo.click(position={"x": 60, "y": 15})
        assert page.evaluate("window.__picks") == ["deadline", "deadline"]
        # a plain text field must NOT try to open a picker
        page.evaluate("window.__picks=[]")
        page.locator('.finput[data-addr="material#0"]').click()
        assert page.evaluate("window.__picks") == []
        # ...nor the text fallback a non-ISO deadline degrades to (no picker can hold that value)
        _set_field(base, pid, "deadline", "meados de agosto")
        page.reload()
        page.wait_for_selector('.finput[data-addr="deadline"]', timeout=5000)
        assert prazo.get_attribute("type") == "text"
        page.evaluate("window.__picks=[]")
        prazo.click(position={"x": 12, "y": 15})
        assert page.evaluate("window.__picks") == []
    finally:
        _reset_deadline(base, pid)
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
