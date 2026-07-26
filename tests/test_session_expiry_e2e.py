"""Real-browser acceptance for W2 — what the Fila says when the session dies under it.

This exists because the bug it guards is invisible to every other kind of test. The server was
correct throughout: it answered 401, exactly as ADR-039 specifies. The page JS then read the error
body, found no ``rows`` key, defaulted it to ``[]`` through ``d.rows||[]``, and rendered
**«✓ Tudo tratado · nada está a cair · 0 a responder»** — the app asserting an empty queue on the
strength of a request it had just been refused. A ``TestClient`` cannot see this (it never runs the
JS) and the static checks in ``test_cockpit_ui.py`` cannot either (they prove the seam is *shipped*,
not that it *works*). Only a real browser, with a really revoked session, shows what the person in
front of the screen would be told.

That failure mode is the zero-hallucination doctrine (VISION; CLAUDE.md non-negotiable #2) breaking
out of the classifier and into the UI: an UNKNOWN rendered as a confident FACT. Of all the false
things this app could say, "there is nothing left for you to do" is the one someone acts on by
walking away from work that is actually waiting.

Opt-in, like the other e2e module — skipped without the ``e2e`` extra and a Chrome/Chromium.
"""

import socket
import threading
import time

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import sync_playwright  # noqa: E402

from conftest import TEST_ADMIN, e2e_sign_in  # noqa: E402

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
    """One inbound CLIENT thread, so the Fila is demonstrably NON-empty before the session dies.

    Every assertion below depends on this. If the queue were empty anyway, «Tudo tratado» would be
    *true* and the test could not tell the honest render from the dishonest one — which is why
    :func:`test_the_queue_is_not_empty_to_begin_with` guards it explicitly.
    """
    crm = CrmStore(":memory:").connect()
    env = {"message_id": "t1", "date": "2026-06-03T09:00:00",
           "from": {"email": "maria@acme.pt", "name": "Maria"},
           "reply_to": {"email": "", "name": ""},
           "to": [{"email": "orcamentos@lindoservico.pt", "name": ""}], "cc": [],
           "references": [], "in_reply_to": None, "attachments": [],
           "subject": "Orçamento 50 placas"}
    verdict = {"direction": "inbound", "counterparty": "CLIENT",
               "purpose": "ESTIMATE_REQUEST_FROM_CLIENT", "priority": "HIGH", "urgency": 80,
               "entities": {}, "confidence": 0.9, "decided_by": "tier1:gemini-2.5-flash",
               "reason": "pede orçamento"}
    crm.record(env, verdict)
    return crm


@pytest.fixture(scope="module")
def live_app(tmp_path_factory):
    """Serve the working-tree app on a free loopback port; yield ``(base_url, app)``.

    Never binds 8042/8000 — a free port, so it cannot collide with the running container.
    """
    ws = Workspace(tmp_path_factory.mktemp("ws") / "w.db").connect()
    app = create_app({"team": ["Diogo"]}, workspace=ws, jobspecs={}, prepared=([], [], {}),
                     reply_pb="", crm_store=_seed_crm())
    e2e_sign_in(app)          # creates TEST_ADMIN + a credential; each test mints its own session
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
    yield f"http://127.0.0.1:{port}", app
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


def _open_fila(browser, base, app):
    """Open /fila on a session of this test's own. Returns ``(page, token)``.

    Per-test sessions, not the fixture's, because every test here *revokes* what it signed in with —
    sharing one token would make each test's outcome depend on file order, and a test that passes for
    the wrong reason is worse than no test.
    """
    person_id = app.state.workspace.person(TEST_ADMIN)["person_id"]
    token = app.state.auth.start_session(person_id)
    context = browser.new_context()
    context.add_cookies([{"name": "e2d_session", "value": token,
                          "domain": "127.0.0.1", "path": "/"}])
    page = context.new_page()
    page.goto(f"{base}/fila")
    page.wait_for_selector("#_list .row", timeout=5000)
    return page, token


def _revoke(app, token):
    person_id = app.state.auth.session_person(token)
    assert person_id, "the session was already dead — the test would prove nothing"
    app.state.auth.revoke_all_sessions(person_id)


def test_the_queue_is_not_empty_to_begin_with(live_app, browser):
    """Guard the guard: with a LIVE session the Fila shows the thread and no curtain."""
    base, app = live_app
    page, _token = _open_fila(browser, base, app)
    try:
        assert page.locator("#_gone").is_hidden()
        listing = page.locator("#_list").inner_text()
        assert "Tudo tratado" not in listing
        assert "Maria" in listing or "Orçamento" in listing
    finally:
        page.close()


def test_an_expired_session_raises_the_curtain_instead_of_claiming_an_empty_queue(live_app, browser):
    """THE regression. Revoke server-side, then make the page talk to the API."""
    base, app = live_app
    page, token = _open_fila(browser, base, app)
    try:
        _revoke(app, token)
        # Drive the lens's own refresh path — the same call the 30 s poll makes, without the wait.
        page.evaluate("() => refreshActiveRows()")
        # Settle on whichever outcome arrives — the curtain (fixed) or the empty-queue claim
        # (broken) — rather than waiting only for the curtain. A bare wait_for_selector on #_gone
        # reports the regression as a 5 s timeout; this way the assertion below fails by NAME, and
        # a failure that says what is wrong is the difference between a test and an alarm.
        page.wait_for_function(
            "() => !document.querySelector('#_gone').classList.contains('hidden')"
            " || document.body.innerText.includes('Tudo tratado')", timeout=5000)

        body = page.locator("body").inner_text()
        # The precise lie this whole file exists to prevent. Asserted FIRST, so it is what a
        # regression reports.
        assert "Tudo tratado" not in body, (
            "the Fila declared an empty queue off the back of a 401 — the UI asserting as fact "
            "something it had just been refused")
        assert page.locator("#_gone").is_visible(), "the session died and the page said nothing"
        assert "Sessão terminada" in body
        assert "pode já não ser verdade" in body, "the curtain must say the state behind it is stale"
    finally:
        page.close()


def test_the_curtain_sends_you_back_to_the_page_you_were_on(live_app, browser):
    """An honest error screen is not a dead end — the link must return here, not to '/'."""
    base, app = live_app
    page, token = _open_fila(browser, base, app)
    try:
        _revoke(app, token)
        page.evaluate("() => refreshActiveRows()")
        page.wait_for_selector("#_gone:not(.hidden)", timeout=5000)
        href = page.locator("#_gonebtn").get_attribute("href")
        assert href.startswith("/login?next=")
        assert "%2Ffila" in href, f"next= does not point back at /fila: {href}"
    finally:
        page.close()


def test_the_curtain_stops_the_background_poll(live_app, browser):
    """A tab left open behind the curtain must stop firing requests it knows will be refused."""
    base, app = live_app
    page, token = _open_fila(browser, base, app)
    try:
        assert page.evaluate("() => _timers.length") > 0, "the Fila registered no poll to stop"
        _revoke(app, token)
        page.evaluate("() => refreshActiveRows()")
        page.wait_for_selector("#_gone:not(.hidden)", timeout=5000)
        assert page.evaluate("() => _timers.length") == 0, "lens polls are still running"
    finally:
        page.close()
