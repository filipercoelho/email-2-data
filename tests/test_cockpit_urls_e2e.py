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

from conftest import AuthedBrowser, e2e_headers, e2e_sign_in  # noqa: E402

_TOKEN: dict[str, str] = {}   # filled by live_app, read lazily by AuthedBrowser

def _get(url: str):
    """Authenticated GET against the live server (the ADR-039 gate applies to /api too)."""
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=e2e_headers(_TOKEN.get("v", ""))))


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
        headers=e2e_headers(_TOKEN.get("v", ""), {"Content-Type": "application/json"})))


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
    _TOKEN["v"] = e2e_sign_in(app)
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
    req = urllib.request.Request(
        f"{base}/api/projects", method="POST",
        data=json.dumps({"title": "Troféus Acme"}).encode(),
        headers=e2e_headers(_TOKEN.get("v", ""), {"Content-Type": "application/json"}))
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
        yield AuthedBrowser(b, lambda: _TOKEN.get("v", ""))
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
        page.goto(f"{base}/fila")
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
    — never the legacy /inbox app (the old behavior this test used to pin, now a fixed defect).

    The destination asserted is ``/fila``, and the assertion goes on to check the Fila actually
    MOUNTED the conversation. Both waits used to accept ``location.pathname=='/'``, which is why this
    test stayed green through ADR-044: after the Fila moved off the root those clicks were landing on
    Início, a page that reads no query parameter at all — the URL still carried ``?thread=`` and the
    user still lost the thread. Asserting the path without asserting what rendered is the proxy that
    hid the defect."""
    base, _pid = live_app
    page = browser.new_page()
    try:
        page.goto(f"{base}/contrapartes/acme.pt")
        page.wait_for_selector(".stats .stat", timeout=5000)            # insight strip rendered
        page.click("#_threads .lrow")                                   # open thread -> Fila
        page.wait_for_function("location.pathname=='/fila' && location.search.includes('thread=')",
                               timeout=5000)
        assert "thread=" in page.url
        # The row is really focused — the Fila consumed the param, it did not merely appear in the bar.
        page.wait_for_selector(".row.on", timeout=5000)
        page.goto(f"{base}/contrapartes/acme.pt")
        page.wait_for_selector("#_timeline .tclick", timeout=5000)
        page.click("#_timeline .tclick")                               # timeline row -> Fila thread
        page.wait_for_function("location.pathname=='/fila' && location.search.includes('thread=')",
                               timeout=5000)
        assert "thread=" in page.url and "/inbox" not in page.url
        page.wait_for_selector(".row.on", timeout=5000)
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
        stored = json.loads(
            _get(f"{base}/api/projects/{pid}").read())["job_fields"]["deadline"]["value"]
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
    """Loading /fila?counterparty=CLIENT applies the filter from the URL (the row survives); an unknown
    counterparty filters everything out — proving the query param actually drives the view.

    The path moved with ADR-044; the query contract did not. syncURL() rebuilds the address from
    location.pathname, so every Fila deep link works unchanged under the new prefix."""
    base, _pid = live_app
    page = browser.new_page()
    try:
        page.goto(f"{base}/fila?counterparty=CLIENT")
        page.wait_for_selector(".row", timeout=5000)
        assert page.locator(".row").count() == 1
        page.goto(f"{base}/fila?counterparty=NOPE")
        page.wait_for_function("document.querySelectorAll('.row').length === 0", timeout=5000)
        assert page.locator(".row").count() == 0
    finally:
        page.close()


def test_the_mail_handoff_builds_a_real_mailto_and_never_stacks_re(live_app, browser):
    """ADR-047: the `mailto:` hand-off, EXECUTED rather than grepped.

    `test_fila.py` asserts the regex literal is shipped; only a browser can run it. The stacking case
    is the one worth the browser: a reply to a reply must not become «Re: Re: Re: Orçamento», and the
    near-misses matter as much as the hits — "Reunião de segunda" starts with "Re" and IS a fresh
    subject, so it must still get a prefix.

    Deliberately does NOT click the button: `location.href='mailto:'` hands control to the OS mail
    client, which in CI is either absent or a dialog nothing can dismiss. The URL the click would
    navigate to is built from the same shipped functions, which is the assertion that has content —
    the click itself is one `location.href` assignment.
    """
    base, _pid = live_app
    page = browser.new_page()
    try:
        page.goto(f"{base}/fila")
        page.wait_for_selector(".row", timeout=5000)
        subjects = page.evaluate("""() => {
            const f = _replySubject;
            return {
                fresh:      f({subject: "Orçamento 50 placas"}),
                already:    f({subject: "Re: Orçamento"}),
                upper:      f({subject: "RE: Orçamento"}),
                nospace:    f({subject: "re:Orçamento"}),
                forwarded:  f({subject: "Fwd: Orçamento"}),
                pt_forward: f({subject: "Enc: Orçamento"}),
                near_miss:  f({subject: "Reunião de segunda"}),
                no_colon:   f({subject: "Resposta ao pedido"}),
                empty:      f({subject: ""}),
            };
        }""")
        assert subjects["fresh"] == "Re: Orçamento 50 placas"
        for key in ("already", "upper", "nospace", "forwarded", "pt_forward"):
            assert not subjects[key].lower().startswith("re: re"), (key, subjects[key])
            assert subjects[key].count(":") == 1, (key, subjects[key])
        # "Re"/"Res" without a colon is a real subject, not a prefix — it must still be prefixed.
        assert subjects["near_miss"] == "Re: Reunião de segunda"
        assert subjects["no_colon"] == "Re: Resposta ao pedido"
        assert subjects["empty"] == ""

        # …and the URL the button navigates to carries all three parts, escaped as a component.
        url = page.evaluate("""() => {
            const r = view()[0];
            r._draft = "Bom dia & obrigado #1";          // & and # would truncate a mis-escaped body
            return 'mailto:' + encodeURIComponent(r.contact || '')
                 + '?subject=' + encodeURIComponent(_replySubject(r))
                 + '&body=' + encodeURIComponent(r._draft);
        }""")
        assert url.startswith("mailto:maria%40acme.pt?subject=Re%3A%20")
        assert "%26" in url and "%23" in url, "& / # reached the URL unescaped — the body truncates"
        assert url.count("&body=") == 1
    finally:
        page.close()


# ── «Ficheiros»: the project's file list (ADR-052) ───────────────────────────────────────────────
#
# Its own live app, deliberately. `live_app` is module-scoped and several tests above count the Fila's
# rows; adding threads to that CRM would have quietly changed what those assertions mean. This one
# also needs a real corpus on disk, which `live_app` has none of.
#
# Cross-thread dedup is UNEXERCISED in production — all 13 live projects have exactly one thread — so
# this fixture is the only thing that runs it before a user does.

def _files_eml(mid: str, sender: str, date: str, parts: list[tuple[str, bytes]]) -> bytes:
    from email.message import EmailMessage
    m = EmailMessage()
    m["From"] = sender
    m["To"] = "orcamentos@lindoservico.pt"
    m["Subject"] = "Peça inox"
    m["Message-ID"] = f"<{mid}>"
    m["Date"] = date
    m.set_content("segue em anexo")
    for name, payload in parts:
        m.add_attachment(payload, maintype="application", subtype="pdf", filename=name)
    return m.as_bytes()


DRAWING = b"%PDF-1.4 desenho da peca com cotas"      # the SAME bytes ride both threads


@pytest.fixture(scope="module")
def files_app(tmp_path_factory):
    """A project spanning two conversations, one dangling root, and a capture — yields
    ``(base_url, project_id)``."""
    from email2data.captures import CaptureStore

    root = tmp_path_factory.mktemp("files")
    corpus, caps = root / "corpus", root / "captures"
    corpus.mkdir()
    (caps / "c-1-7").mkdir(parents=True)
    (caps / "c-1-7" / "foto-oficina.pdf").write_bytes(b"%PDF foto da oficina")

    index = {}
    for mid, sender, date, parts in (
            # The drawing arrives FIRST here, in June, from maria@ — so it is the carrier the tile
            # must name once the two threads are folded together.
            ("f1", "maria@acme.pt", "Mon, 01 Jun 2026 09:00:00 +0100",
             [("desenho.pdf", DRAWING)]),
            # …and is quoted back in July, in a DIFFERENT thread, by someone else.
            ("f2", "joao@acme.pt", "Mon, 20 Jul 2026 09:00:00 +0100",
             [("desenho.pdf", DRAWING), ("orcamento.pdf", b"%PDF-1.4 orcamento 1200 EUR")])):
        f = corpus / f"{mid}.eml"
        f.write_bytes(_files_eml(mid, sender, date, parts))
        index[mid] = f

    crm = CrmStore(":memory:").connect()
    for mid, sender, date in (("f1", "maria@acme.pt", "2026-06-01T09:00:00"),
                              ("f2", "joao@acme.pt", "2026-07-20T09:00:00")):
        crm.record({"message_id": mid, "from": {"email": sender, "name": ""},
                    "reply_to": {"email": "", "name": ""},
                    "to": [{"email": "orcamentos@lindoservico.pt", "name": ""}], "cc": [],
                    "references": [], "in_reply_to": None, "date": date,
                    "attachments": [{"filename": "desenho.pdf"}], "subject": "Peça inox"},
                   {"direction": "inbound", "counterparty": "CLIENT",
                    "purpose": "ESTIMATE_REQUEST_FROM_CLIENT", "priority": "HIGH", "urgency": 80,
                    "entities": {}, "confidence": 0.9, "decided_by": "tier1", "reason": "x"})

    ws = Workspace(root / "w.db").connect()
    cstore = CaptureStore(ws._conn)
    cstore.add(telegram_message_id=7, telegram_chat_id=1, raw_text="foto tirada na oficina",
               media_paths=["c-1-7/foto-oficina.pdf"], channel="telegram", asserted_by="Rita")
    app = create_app({"team": ["Diogo"]}, workspace=ws, jobspecs={}, prepared=([], [], {}),
                     reply_pb="", crm_store=crm, corpus_index=index,
                     capture_store=cstore, captures_dir=caps)
    token = e2e_sign_in(app)
    _TOKEN["v"] = token
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

    def _post(path: str, payload: dict):
        return json.loads(urllib.request.urlopen(urllib.request.Request(
            f"{base}{path}", method="POST", data=json.dumps(payload).encode(),
            headers=e2e_headers(token, {"Content-Type": "application/json"}))).read())

    pid = _post("/api/projects", {"title": "Peça inox Acme"})["project_id"]
    for ref in ("mid:f1", "mid:f2", "mid:esta-thread-nao-existe"):
        _post(f"/api/projects/{pid}/attach", {"ref": ref})     # …including the dangling one
    _post("/api/captures/c-1-7/apply", {"project_id": pid, "kind": "note"})
    yield base, pid
    server.should_exit = True
    thread.join(timeout=5)


def test_the_ficheiros_tab_lists_files_from_every_thread(files_app, browser):
    """The tab exists, and it MOUNTS real tiles — not merely an unhidden panel.

    Waiting for the panel to lose `.hidden` would pass against an empty box, which is the exact proxy
    failure this module was burned by (a URL asserted without asserting what rendered). So the wait is
    on a real `.attf .atti`, and the assertions are on what those tiles say: two distinct documents
    across two conversations, the drawing counted twice but LISTED once, and the capture's file
    present with its own provenance — proving the intake source reached the list at all."""
    base, pid = files_app
    page = browser.new_page()
    try:
        page.goto(f"{base}/projetos/{pid}")
        page.wait_for_selector('.ptab-btn[data-tab="ficheiros"]', timeout=5000)
        page.click('.ptab-btn[data-tab="ficheiros"]')
        page.wait_for_selector("#_files .attf .atti", timeout=8000)
        names = page.locator("#_files .attf .atti-n").all_inner_texts()
        assert sorted(names) == ["desenho.pdf", "foto-oficina.pdf", "orcamento.pdf"], names
        # cross-thread content dedup: the drawing rode BOTH threads and is one row, marked ×2
        drawing = page.locator("#_files .attw", has_text="desenho.pdf").first
        assert "×2" in drawing.inner_text(), drawing.inner_text()
        # the heading names the scope it folded, and the badge agrees with what is on screen
        # .attf-h is text-transform:uppercase, so compare case-insensitively (inner_text is the
        # RENDERED text, which is the right thing to assert on and the reason for the fold).
        assert "ficheiros do projeto" in page.inner_text("#_files .attf-h").lower()
        assert page.inner_text('.ptab-btn[data-tab="ficheiros"] .bdg').strip() == "3"
        # provenance: the CHRONOLOGICALLY first carrier, not whichever thread was attached first
        assert "maria@acme.pt" in drawing.inner_text(), drawing.inner_text()
        assert "2026-06-01" in drawing.inner_text()
        # …and the capture is honestly labelled as intake, with no conversation to jump to
        capture = page.locator("#_files .attw", has_text="foto-oficina.pdf").first
        assert "captura" in capture.inner_text() and "Rita" in capture.inner_text()
        assert capture.locator(".atti-jump").count() == 0
        # the funnel also still renders inside «Origem» — ADR-046 §4 is extended, not replaced
        page.click('.ptab-btn[data-tab="origem"]')
        page.wait_for_selector("#_origem .attf", timeout=5000)
        assert "ficheiros da conversa" in page.inner_text("#_origem .attf-h").lower()
    finally:
        page.close()


def test_one_dangling_thread_does_not_blank_the_other_threads_files(files_app, browser):
    """The `try` wrapped the whole root loop and `getJSON` throws on any non-2xx — and `/api/thread`
    404s both for a dangling root and for one this person was never granted. So a single unreachable
    thread cost EVERY other thread's messages and files, and the panel said only «falhou ao carregar
    contexto». This project carries exactly that: two good roots and one that resolves to nothing.

    The list must survive, and it must SAY it is incomplete — a shortened list presented as complete
    is the failure this whole funnel exists to prevent."""
    base, pid = files_app
    page = browser.new_page()
    try:
        page.goto(f"{base}/projetos/{pid}")
        page.click('.ptab-btn[data-tab="ficheiros"]')
        page.wait_for_selector("#_files .attf .atti", timeout=8000)
        assert page.locator("#_files .attf .atti").count() == 3, "a dead root cost the live ones"
        warn = page.inner_text("#_files .dwarn")
        assert "1 de 3" in warn and "incompleta" in warn, warn
        # …and «Origem» kept its messages too, instead of the whole panel collapsing to one red line
        page.click('.ptab-btn[data-tab="origem"]')
        page.wait_for_selector("#_origem .tmsg", timeout=5000)
        assert "falhou ao carregar contexto" not in page.inner_text("#_origem")
    finally:
        page.close()


def test_a_project_file_jumps_to_its_conversation_in_the_fila(files_app, browser):
    """«ver na fila →» answers "which email brought me this file?" — so it has to land on /fila AND
    mount the conversation. Asserting the URL alone is the proxy that let ADR-044 break every
    cross-page deep link while the tests stayed green."""
    base, pid = files_app
    page = browser.new_page()
    try:
        page.goto(f"{base}/projetos/{pid}")
        page.click('.ptab-btn[data-tab="ficheiros"]')
        page.wait_for_selector("#_files .attf .atti", timeout=8000)
        jump = page.locator("#_files .attw", has_text="orcamento.pdf").first.locator(".atti-jump")
        assert jump.count() == 1
        jump.click()
        page.wait_for_function("location.pathname=='/fila' && location.search.includes('thread=')",
                               timeout=8000)
        assert "thread=mid%3Af2" in page.url, page.url
        page.wait_for_selector(".row.on", timeout=8000)     # the Fila really mounted it
    finally:
        page.close()


# ── Phase 3 (fila-evidence plan §Phase 3) — the highlight actually PAINTS ──────────────────────
# The plan's own closing line: "A passing grep is not evidence that a highlight rendered." The kit
# tests in test_cockpit_ui.py execute the matching logic in node, but node has no CSS Custom
# Highlight API and no layout — only a real browser can answer whether `CSS.highlights` accepted the
# Ranges and whether the painted text is the text the ledger names.

_EVID_BODY = (
    "Bom dia,\r\n\r\nSegue o nosso NIF: 501442600 para a fatura.\r\n"
    "O IBAN e PT50 0002 0123 1234 5678 9015 4.\r\n"
    "Valor acordado: 1.250,00 EUR.\r\n\r\n"
    "Melhores cumprimentos\r\n\r\nANA MARQUES\r\nDiretora\r\n"
)


@pytest.fixture(scope="module")
def evidence_app(tmp_path_factory):
    """One CLIENT thread whose body carries a checksum-valid NIF, a spaced IBAN and an amount, with
    the matching entities on the verdict so «Registo do fio» has rows to click."""
    root = tmp_path_factory.mktemp("evid")
    corpus = root / "corpus"
    corpus.mkdir()
    eml = corpus / "e1.eml"
    eml.write_bytes(
        b"Subject: Fatura placas\r\nFrom: ana@acme.pt\r\nTo: orcamentos@lindoservico.pt\r\n"
        b"Message-ID: <e1@acme.pt>\r\nDate: Mon, 01 Jun 2026 09:00:00 +0100\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n" + _EVID_BODY.encode())

    crm = CrmStore(":memory:").connect()
    crm.record({"message_id": "e1", "from": {"email": "ana@acme.pt", "name": "Ana"},
                "reply_to": {"email": "", "name": ""},
                "to": [{"email": "orcamentos@lindoservico.pt", "name": ""}], "cc": [],
                "references": [], "in_reply_to": None, "date": "2026-06-01T09:00:00",
                "attachments": [], "subject": "Fatura placas"},
               {"direction": "inbound", "counterparty": "CLIENT", "purpose": "INVOICE_FROM_SUPPLIER",
                "priority": "HIGH", "urgency": 80, "confidence": 0.9, "decided_by": "tier1",
                "reason": "fatura", "entities": {
                    # exactly what extract.py stores: folded/space-stripped, NOT body substrings
                    "nif": "501442600", "iban": "PT50000201231234567890154",
                    "money": "1.250,00 EUR", "client_name": "ANA MARQUES",
                    "deadline": "2026-08-07"}})

    ws = Workspace(root / "w.db").connect()
    app = create_app({"team": ["Diogo"]}, workspace=ws, jobspecs={}, prepared=([], [], {}),
                     reply_pb="", crm_store=crm, corpus_index={"e1": eml})
    _TOKEN["v"] = e2e_sign_in(app)
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
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def _open_dossier(page, base):
    page.goto(f"{base}/fila", wait_until="networkidle")
    page.wait_for_selector("#_doss .dledger", timeout=10000)
    page.wait_for_function(
        "!document.querySelector('#_doss .dledger').textContent.includes('a carregar')",
        timeout=10000)


def _painted(page):
    """The text the browser is ACTUALLY painting, read back out of CSS.highlights."""
    return page.evaluate("""() => {
      const h = CSS.highlights && CSS.highlights.get('evid');
      if (!h) return [];
      return [...h].map(r => r.toString());
    }""")


def test_clicking_a_ledger_value_paints_its_evidence_in_the_body(evidence_app, browser):
    """The payoff of the whole phase, asserted on rendered output. The IBAN is the load-bearing
    case: the ledger holds `PT50000201231234567890154` and the body says «PT50 0002 0123 …», so a
    substring search finds nothing — only the pattern mirror + normalised compare can span it."""
    page = browser.new_page()
    _open_dossier(page, evidence_app)
    assert _painted(page) == [], "nothing is highlighted before anything is clicked"

    page.click('#_doss [data-lgk="nif"]')
    assert _painted(page) == ["501442600"], "the span must exclude the «NIF: » anchor word"

    page.click('#_doss [data-lgk="iban"]')
    assert _painted(page) == ["PT50 0002 0123 1234 5678 9015 4"]

    page.click('#_doss [data-lgk="money"]')
    assert _painted(page) == ["1.250,00 EUR"]
    page.close()


def test_the_picked_value_is_the_only_one_lit_and_a_second_click_clears_it(evidence_app, browser):
    """D2 is «one accent, interaction-driven» — two values lit at once would be the seven-colour
    palette the decision rejected, arrived at by accident."""
    page = browser.new_page()
    _open_dossier(page, evidence_app)
    page.click('#_doss [data-lgk="nif"]')
    assert page.eval_on_selector_all("#_doss .lg-r.picked", "els => els.length") == 1
    page.click('#_doss [data-lgk="money"]')
    assert page.eval_on_selector_all("#_doss .lg-r.picked", "els => els.length") == 1
    assert _painted(page) == ["1.250,00 EUR"]
    page.click('#_doss [data-lgk="money"]')                 # …clicking it again puts the light out
    assert _painted(page) == []
    assert page.eval_on_selector_all("#_doss .lg-r.picked", "els => els.length") == 0
    page.close()


def test_a_value_the_email_never_contained_says_so_instead_of_lighting_nothing(evidence_app, browser):
    """`deadline` is stored ISO-normalised (`2026-08-07`) and appears nowhere in the body — the
    single largest bucket in the measurement (40% of all values). The click must resolve to a
    stated absence, never to a silent no-op and never to a nearest match."""
    page = browser.new_page()
    _open_dossier(page, evidence_app)
    page.click('#_doss [data-lgk="deadline"]')
    assert _painted(page) == []
    cell = page.query_selector('#_doss [data-lgk="deadline"]')
    assert "noev" in (cell.get_attribute("class") or "")
    assert "sem evidência visível" in page.evaluate(
        "() => getComputedStyle(document.querySelector('#_doss [data-lgk=\\'deadline\\']'),"
        "'::after').content")
    page.close()


def test_evidence_inside_the_collapsed_signature_opens_the_block_it_is_in(evidence_app, browser):
    """«ANA MARQUES» lives in the closing block, which Phase 2 restored but renders COLLAPSED.
    Painting text inside a hidden div is indistinguishable from finding nothing, so the highlight
    opens the block — and the toggle's caret has to agree with what the reader now sees."""
    page = browser.new_page()
    _open_dossier(page, evidence_app)
    assert page.eval_on_selector(".tsig", "el => el.classList.contains('hidden')") is True
    page.click('#_doss [data-lgk="client_name"]')
    assert _painted(page) == ["ANA MARQUES"]
    assert page.eval_on_selector(".tsig", "el => el.classList.contains('hidden')") is False
    assert "▾" in page.eval_on_selector(".stoggle", "el => el.textContent")
    page.close()


def test_the_highlight_survives_the_fifteen_second_refresh(evidence_app, browser):
    """`refresh()` replaces every row object and re-renders the dossier on a timer, destroying the
    live Ranges. If the picked key had been parked on the row it would not be in the hand-copied
    carry list and the light would go out seconds after the click — intermittently, with no error.
    Driven here by calling the shipped render directly rather than waiting 15 s."""
    page = browser.new_page()
    _open_dossier(page, evidence_app)
    page.click('#_doss [data-lgk="nif"]')
    assert _painted(page) == ["501442600"]
    page.evaluate("renderDossier()")
    assert _painted(page) == ["501442600"], "the highlight did not survive a re-render"
    assert page.eval_on_selector_all("#_doss .lg-r.picked", "els => els.length") == 1
    page.close()


def test_lighting_a_value_does_not_throw_away_what_the_reader_opened(evidence_app, browser):
    """Found on real mail, not in a fixture. The first cut re-rendered the dossier on every ledger
    click — which replaces `#_doss.innerHTML`, so the «mensagem citada» block the reader had just
    opened snapped shut and the pane scrolled back to the top, on the very click whose job is to
    show them something. The highlight now paints in place."""
    page = browser.new_page()
    _open_dossier(page, evidence_app)
    page.click(".stoggle")                                   # the reader opens the signature
    assert page.eval_on_selector(".tsig", "el => el.classList.contains('hidden')") is False
    node_id = page.evaluate("() => { window.__t = document.querySelector('.tsig'); return 1; }")
    assert node_id == 1

    page.click('#_doss [data-lgk="money"]')                  # …and lights an unrelated value
    assert _painted(page) == ["1.250,00 EUR"]
    assert page.eval_on_selector(".tsig", "el => el.classList.contains('hidden')") is False, (
        "the ledger click closed a block the reader had opened")
    assert page.evaluate("() => window.__t === document.querySelector('.tsig')") is True, (
        "the dossier was re-rendered — the element the reader was looking at was replaced")
    page.close()
