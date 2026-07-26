"""Real-browser acceptance for ADR-023 — the Para ti queue refreshes itself.

The text-presence tests in ``test_fila.py`` guard that the refresh wiring is *shipped*; this guards
that it *works*: that a poll swaps the queue in place without a reload, that the nav badge follows,
and — the one that actually bites — that a dismissal keyed by content survives a refresh that
reorders the list. A ``TestClient`` can never check any of it, because it never runs the page JS.

Isolated from ``test_cockpit_urls_e2e``'s module-scoped app on purpose: these tests mutate project
state to make gates appear and disappear, which would perturb the shared fixture.

Opt-in, same as the other e2e module — skipped without the ``e2e`` extra and a Chrome/Chromium.
"""

import json
import socket
import threading
import time
import urllib.request

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import sync_playwright  # noqa: E402

uvicorn = pytest.importorskip("uvicorn")

from email2data.crm import CrmStore  # noqa: E402
from email2data.webapp import create_app  # noqa: E402

from conftest import AuthedBrowser, e2e_headers, e2e_sign_in  # noqa: E402

_TOKEN: dict[str, str] = {}   # filled by the live_app fixtures


def _get(url: str):
    """Authenticated GET against the live server (the ADR-039 gate applies to /api too)."""
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=e2e_headers(_TOKEN.get("v", ""))))
from email2data.workspace import Workspace  # noqa: E402


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _env(mid: str, frm: str, subject: str) -> dict:
    return {"message_id": mid, "date": "2026-06-03T09:00:00",
            "from": {"email": frm, "name": "Maria"}, "reply_to": {"email": "", "name": ""},
            "to": [{"email": "orcamentos@lindoservico.pt", "name": ""}], "cc": [],
            "references": [], "in_reply_to": None, "attachments": [], "subject": subject}


def _verdict() -> dict:
    return {"direction": "inbound", "counterparty": "CLIENT",
            "purpose": "ESTIMATE_REQUEST_FROM_CLIENT", "priority": "HIGH", "urgency": 80,
            "entities": {}, "confidence": 0.9, "decided_by": "tier1:gemini-2.5-flash",
            "reason": "pede orçamento"}


def _write_eml(path, mid: str, frm: str, subject: str, body: str, attachment: str | None = None):
    """A real .eml on disk, so ``/api/thread`` has a body (and optionally an attachment) to serve.

    Not optional detail: a message with neither body nor attachment is deduped away by the thread
    endpoint, so a corpus-less fixture renders an empty panel and would let a broken detail pass.
    """
    from email.message import EmailMessage
    m = EmailMessage()
    m["Message-ID"], m["From"], m["To"] = f"<{mid}>", frm, "orcamentos@lindoservico.pt"
    m["Subject"], m["Date"] = subject, "Wed, 03 Jun 2026 09:00:00 +0000"
    m.set_content(body)
    if attachment:
        m.add_attachment(b"%PDF-1.4 fake", maintype="application", subtype="pdf",
                         filename=attachment)
    path.write_bytes(m.as_bytes())
    return path


def _spec_with_three_items(mid: str) -> dict:
    """A spec whose 3 line items share the SAME unanswered must-haves — the shape that used to
    render 25 near-identical 'em falta' rows and bury the handful of known values."""
    from email2data import jobspec as js
    spec = js.JobSpec(message_id=mid, subject="Orçamento 50 placas", counterparty="CLIENT",
                      purpose="ESTIMATE_REQUEST_FROM_CLIENT")
    spec.job_fields = {"deadline": js.SpecField("2026-12-24", "llm", False)}
    spec.items = [{k: js.SpecField() for k in js.ITEM_KEYS} for _ in range(3)]
    spec.items[0]["item"] = js.SpecField("placa", "llm", False)
    spec.items[0]["material"] = js.SpecField("inox", "llm", False)
    return spec.to_dict()


@pytest.fixture
def live_app(tmp_path):
    """Serve an app with THREE open estimate threads → three ``propor_projeto`` gates.

    Three, not two, on purpose: proving a dismissal is content-keyed needs the queue to *reorder*
    under it (resolve an early gate, dismiss a late one). With two items the indices happen to line
    up and an index-keyed bug would pass.

    Backed by a real corpus + a jobspec so the expanded panel has genuine bodies, an attachment and
    an extraction summary to render.
    """
    crm = CrmStore(":memory:").connect()
    crm.record(_env("t1", "maria@acme.pt", "Orçamento 50 placas"), _verdict())
    crm.record(_env("t2", "joao@beta.pt", "Orçamento portão"), _verdict())
    crm.record(_env("t3", "ana@gama.pt", "Orçamento estrutura"), _verdict())
    corpus = {
        "t1": _write_eml(tmp_path / "t1.eml", "t1", "maria@acme.pt", "Orçamento 50 placas",
                         "Bom dia, precisamos de 50 placas em inox.", "desenho.pdf"),
        "t2": _write_eml(tmp_path / "t2.eml", "t2", "joao@beta.pt", "Orçamento portão",
                         "Bom dia, queremos orçamento para um portão."),
        "t3": _write_eml(tmp_path / "t3.eml", "t3", "ana@gama.pt", "Orçamento estrutura",
                         "Precisamos de uma estrutura metálica."),
    }
    ws = Workspace(tmp_path / "w.db").connect()
    app = create_app({"team": ["Pedro"]}, workspace=ws,
                     jobspecs={"t1": _spec_with_three_items("t1")},
                     prepared=([], [], {}), reply_pb="", crm_store=crm, corpus_index=corpus)
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


def _open(browser, base: str):
    """Open /para-ti and collect JS errors, so a broken refresh can't pass silently.

    Uncaught exceptions (``pageerror``) plus console errors — minus sub-resource 404s, which are just
    the browser asking for a favicon this app doesn't serve, not a fault in the page.
    """
    page = browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: (
        errors.append(m.text)
        if m.type == "error" and "Failed to load resource" not in m.text else None))
    page.goto(f"{base}/para-ti")
    page.wait_for_selector(".gate")
    return page, errors


def _resolve(base: str, thread_root: str, title: str) -> None:
    """Retire a ``propor_projeto`` gate the way the user would — attach its thread to a project."""
    urllib.request.urlopen(urllib.request.Request(
        f"{base}/api/projects", method="POST",
        data=json.dumps({"title": title, "from_message": thread_root}).encode(),
        headers=e2e_headers(_TOKEN.get("v", ""), {"Content-Type": "application/json"})))


def _subjects(page) -> list[str]:
    return page.locator(".gate").all_inner_texts()


def test_refresh_swaps_the_queue_in_place_without_a_reload(live_app, browser):
    """A gate resolved server-side disappears from an open tab on the next poll — and the page never
    navigated to do it (a reload mid-decision would lose scroll position and the focused card)."""
    page, errors = _open(browser, live_app)
    try:
        assert page.locator(".gate").count() == 3
        page.evaluate("window.__nav = false;"
                      "window.addEventListener('beforeunload', () => { window.__nav = true; })")

        _resolve(live_app, "mid:t1", "Troféus Acme")

        page.evaluate("refresh({quiet:true})")
        page.wait_for_function("document.querySelectorAll('.gate').length === 2", timeout=5000)
        assert not any("50 placas" in s for s in _subjects(page))   # the resolved one, gone
        assert page.evaluate("window.__nav") is False, "page reloaded instead of swapping in place"
        assert not errors, f"JS errors during refresh: {errors}"
    finally:
        page.close()


def test_dismissal_survives_a_refresh_that_reorders_the_queue(live_app, browser):
    """The regression this feature could most easily introduce: ``dismissed`` used to hold list
    INDICES. Dismiss the LAST gate, then resolve an EARLIER one server-side so the queue shifts
    under it — an index-keyed dismissal now points at the wrong decision, resurrecting the card the
    user dismissed. Content keys are what make this safe.
    """
    page, errors = _open(browser, live_app)
    try:
        assert page.locator(".gate").count() == 3
        # Dismiss the gate for t3 by its subject (never by position — gate order isn't contractual).
        gama = page.locator(".gate").filter(has_text="estrutura")
        gama.locator('[data-act="dismiss"]').click()
        page.wait_for_function("document.querySelectorAll('.gate').length === 2")

        _resolve(live_app, "mid:t1", "Troféus Acme")     # shifts every later index down by one

        page.evaluate("refresh({quiet:true})")
        page.wait_for_function("document.querySelectorAll('.gate').length === 1", timeout=5000)
        page.wait_for_timeout(300)                        # let a wrong re-render settle if it happens
        remaining = _subjects(page)
        assert len(remaining) == 1
        assert "portão" in remaining[0], f"refresh swapped in the wrong item: {remaining}"
        assert not any("estrutura" in s for s in remaining), "dismissed gate came back after refresh"
        assert not errors, f"JS errors during refresh: {errors}"
    finally:
        page.close()


def test_refresh_updates_the_nav_badge_and_zero_state(live_app, browser):
    """Badges and the empty state are part of 'up to date' — an emptied queue must actually read as
    empty, not keep a stale count in the nav."""
    page, errors = _open(browser, live_app)
    try:
        assert "3" in page.locator('.nlink[data-nav="para-ti"]').inner_text()
        for root, title in (("mid:t1", "Acme"), ("mid:t2", "Beta"), ("mid:t3", "Gama")):
            _resolve(live_app, root, title)

        page.evaluate("refresh({quiet:true})")
        page.wait_for_function("document.querySelectorAll('.gate').length === 0", timeout=5000)
        page.wait_for_selector("#_zero:not(.hidden)")
        badge = page.locator('.nlink[data-nav="para-ti"] .nbadge')
        assert badge.count() == 0, "nav badge kept a stale count after the queue emptied"
        assert not errors, f"JS errors during refresh: {errors}"
    finally:
        page.close()


def test_page_shows_how_fresh_the_mail_is(live_app, browser):
    """The freshness stamp is the honest signal: if ingestion stalls, the user can see it rather than
    trusting a queue that stopped moving."""
    page, errors = _open(browser, live_app)
    try:
        page.evaluate("refresh({quiet:true})")
        page.wait_for_timeout(300)
        # No sync has run in this injected app, so the stamp stays empty rather than inventing a time.
        assert page.locator("#_fresh").count() == 1
        assert page.evaluate("typeof paintFreshness") == "function"
        assert page.evaluate("_agoLabel(new Date(Date.now() - 5*60000).toISOString())") == "há 5 min"
        assert page.evaluate("_agoLabel(null)") == ""
        assert not errors, f"JS errors: {errors}"
    finally:
        page.close()


# ── ADR-024: the gate expands in place into the evidence ─────────────────────

def test_clicking_a_gate_expands_it_into_the_email_thread(live_app, browser):
    """The complaint this fixes: the card looked clickable (cursor:pointer) and did nothing, so
    judging a decision meant leaving the page."""
    page, errors = _open(browser, live_app)
    try:
        assert page.locator(".gdetail").count() == 0          # nothing open to start
        page.locator(".gate").first.click()
        page.wait_for_selector(".gate.open .gdetail", timeout=5000)
        assert page.locator(".gate.open .tmsg").count() >= 1, "no messages rendered in the detail"
        assert page.locator(".gate.open").get_attribute("aria-expanded") == "true"
        assert page.locator(".gate.open").count() == 1        # accordion: only one at a time

        page.locator(".gate.open .ghead").click()             # click again collapses
        page.wait_for_function("document.querySelectorAll('.gate.open').length === 0", timeout=5000)
        assert not errors, f"JS errors: {errors}"
    finally:
        page.close()


def test_only_one_gate_is_open_at_a_time(live_app, browser):
    page, errors = _open(browser, live_app)
    try:
        page.locator(".gate").nth(0).click()
        page.wait_for_selector(".gate.open .gdetail")
        page.locator(".gate").nth(2).click()
        page.wait_for_timeout(400)
        assert page.locator(".gate.open").count() == 1
        assert not errors, f"JS errors: {errors}"
    finally:
        page.close()


def test_the_open_gate_is_addressable_and_restored_from_the_url(live_app, browser):
    """ADR-014: the open decision rides in the URL, so it survives a reload and can be handed to a
    colleague. Keyed by content, so it still resolves after the queue reorders."""
    page, errors = _open(browser, live_app)
    try:
        page.locator(".gate").first.click()
        page.wait_for_selector(".gate.open .gdetail")
        assert "item=" in page.url
        opened = page.locator(".gate.open").inner_text()

        page.goto(page.url)                                   # reload the deep link
        page.wait_for_selector(".gate.open .gdetail", timeout=5000)
        assert page.locator(".gate.open").inner_text().split("\n")[1] == opened.split("\n")[1]
        assert not errors, f"JS errors: {errors}"
    finally:
        page.close()


def test_expanded_gate_survives_the_refresh_poll(live_app, browser):
    """Reading a thread must not be interrupted by the 30 s poll (ADR-023) closing the panel."""
    page, errors = _open(browser, live_app)
    try:
        page.locator(".gate").first.click()
        page.wait_for_selector(".gate.open .gdetail")
        opened = page.locator(".gate.open").inner_text()

        _resolve(live_app, "mid:t3", "Gama")                  # a DIFFERENT gate resolves
        page.evaluate("refresh({quiet:true})")
        page.wait_for_function("document.querySelectorAll('.gate').length === 2", timeout=5000)
        assert page.locator(".gate.open").count() == 1, "the poll closed the panel being read"
        assert page.locator(".gate.open").inner_text().split("\n")[1] == opened.split("\n")[1]
        assert not errors, f"JS errors: {errors}"
    finally:
        page.close()


def test_the_open_gate_closes_only_when_its_decision_leaves_the_queue(live_app, browser):
    page, errors = _open(browser, live_app)
    try:
        gama = page.locator(".gate").filter(has_text="estrutura")
        gama.click()
        page.wait_for_selector(".gate.open .gdetail")

        _resolve(live_app, "mid:t3", "Gama")                  # resolve the OPEN one
        page.evaluate("refresh({quiet:true})")
        page.wait_for_function("document.querySelectorAll('.gate').length === 2", timeout=5000)
        assert page.locator(".gate.open").count() == 0
        assert not errors, f"JS errors: {errors}"
    finally:
        page.close()


def test_esc_closes_the_open_gate(live_app, browser):
    page, errors = _open(browser, live_app)
    try:
        page.locator(".gate").first.click()
        page.wait_for_selector(".gate.open .gdetail")
        page.keyboard.press("Escape")
        page.wait_for_function("document.querySelectorAll('.gate.open').length === 0", timeout=5000)
        assert "item=" not in page.url
        assert not errors, f"JS errors: {errors}"
    finally:
        page.close()


def test_action_buttons_do_not_toggle_the_card(live_app, browser):
    """Every action lives inside the clickable card, so a stray toggle would fire on every click."""
    page, errors = _open(browser, live_app)
    try:
        before = page.locator(".gate").count()
        page.locator('.gate [data-act="dismiss"]').first.click()
        page.wait_for_function(f"document.querySelectorAll('.gate').length === {before - 1}")
        assert page.locator(".gate.open").count() == 0, "dismiss also expanded a card"
        assert not errors, f"JS errors: {errors}"
    finally:
        page.close()


def test_reclassifying_from_the_card_persists(live_app, browser):
    """The decision is judged and corrected in one place — no trip to the Fila and back."""
    page, errors = _open(browser, live_app)
    try:
        page.locator('.gate [data-act="reclassCp"]').first.click()
        page.wait_for_selector("#_menu:not(.hidden)")
        page.locator('#_menu .mi[data-val="SUPPLIER"]').click()
        page.wait_for_timeout(600)
        saved = json.loads(_get(f"{live_app}/api/reclassifications").read())
        assert any(any(c["field"] == "counterparty" and c["human"] == "SUPPLIER" for c in v)
                   for v in saved.values()), f"correction not persisted: {saved}"
        assert not errors, f"JS errors: {errors}"
    finally:
        page.close()


def test_marking_handled_from_the_card_removes_it_from_the_fila(live_app, browser):
    page, errors = _open(browser, live_app)
    try:
        before = page.locator(".gate").count()
        page.locator('.gate [data-act="handled"]').first.click()
        page.wait_for_function(f"document.querySelectorAll('.gate').length === {before - 1}",
                               timeout=5000)
        rows = json.loads(_get(f"{live_app}/api/fila").read())["rows"]
        assert len(rows) == before - 1, "thread was not marked handled server-side"
        assert not errors, f"JS errors: {errors}"
    finally:
        page.close()


def test_attachments_are_openable_from_the_expanded_thread(live_app, browser):
    """Often the attachment IS the decision (a drawing, a spec) — it has to be reachable here."""
    page, errors = _open(browser, live_app)
    try:
        page.locator(".gate").filter(has_text="50 placas").click()
        page.wait_for_selector(".gate.open .tmsg", timeout=5000)
        att = page.locator(".gate.open .tatt")
        assert att.count() >= 1, "attachment not rendered in the detail"
        href = att.first.get_attribute("href")
        assert href.startswith("/api/attachment/")
        got = _get(live_app + href)
        assert got.status == 200 and got.read(), "attachment link does not actually serve bytes"
        assert not errors, f"JS errors: {errors}"
    finally:
        page.close()


def test_spec_panel_folds_per_field_not_per_line_item(live_app, browser):
    """Regression: the panel used to emit one row per (field × line item), so a 3-piece job with the
    same unanswered must-haves produced ~25 near-identical 'em falta' rows that buried the handful of
    known values. One row per field; the unanswered must-haves collapse to chips; partial coverage is
    stated honestly rather than rounded to 'known' or 'missing'."""
    page, errors = _open(browser, live_app)
    try:
        page.locator(".gate").filter(has_text="50 placas").click()
        page.wait_for_selector(".gate.open .spec", timeout=5000)
        rows = page.locator(".gate.open .specrow")
        assert rows.count() == 3, f"expected one row per KNOWN field, got {rows.count()}"
        assert page.locator(".gate.open .mchip").count() == 6      # unanswered must-haves, folded
        panel = page.locator(".gate.open .spec").inner_text()
        assert "1 de 3" in panel, "partial coverage must be stated, not rounded away"
        assert "inox" in panel and "2026-12-24" in panel
        assert panel.count("em falta") <= 1, "per-item 'em falta' rows are back"
        assert not errors, f"JS errors: {errors}"
    finally:
        page.close()


def test_spec_panel_never_claims_estimable_while_must_haves_are_missing(live_app, browser):
    """Zero-hallucination at the UI layer: an incomplete spec must read as incomplete."""
    page, errors = _open(browser, live_app)
    try:
        page.locator(".gate").filter(has_text="50 placas").click()
        page.wait_for_selector(".gate.open .spec", timeout=5000)
        assert page.locator(".gate.open .estim.no").count() == 1
        assert page.locator(".gate.open .estim.yes").count() == 0
        # IA-sourced values are marked as inference, never as fact.
        assert page.locator(".gate.open .prov.ia").count() >= 1
        assert not errors, f"JS errors: {errors}"
    finally:
        page.close()


def test_gate_without_a_thread_says_so_instead_of_spinning(live_app, browser):
    """The identity gate has no conversation; the panel must say that, not hang on 'a carregar…'."""
    page, errors = _open(browser, live_app)
    try:
        html = page.content()
        assert "não tem conversa associada" in html      # the branch is shipped
        assert not errors, f"JS errors: {errors}"
    finally:
        page.close()


# -- grouping + filtering (needs more than one gate kind) ---------------------

@pytest.fixture
def live_app_mixed(tmp_path):
    """Two kinds at once: two project proposals plus one low-confidence verdict to review."""
    crm = CrmStore(":memory:").connect()
    crm.record(_env("t1", "maria@acme.pt", "Orçamento 50 placas"), _verdict())
    crm.record(_env("t2", "joao@beta.pt", "Orçamento portão"), _verdict())
    shaky = {**_verdict(), "purpose": "FOLLOW_UP", "confidence": 0.30}
    crm.record(_env("t3", "ana@gama.pt", "Seguimento incerto"), shaky)
    ws = Workspace(tmp_path / "w.db").connect()
    app = create_app({"team": ["Pedro"]}, workspace=ws, jobspecs={}, prepared=([], [], {}),
                     reply_pb="", crm_store=crm)
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


def test_queue_groups_by_kind_and_filters(live_app_mixed, browser):
    """Eight identical 'PROPOR PROJETO' badges carried no information. Headings with counts do, and
    the filter makes a long queue navigable."""
    page, errors = _open(browser, live_app_mixed)
    try:
        assert page.locator(".ghdr").count() == 2, "gate kinds are not grouped"
        assert page.locator(".chip").count() == 3          # Todos + one per kind
        total = page.locator(".gate").count()

        page.locator('.chip[data-kind="rever_classificacao"]').click()
        page.wait_for_function("document.querySelectorAll('.ghdr').length === 1", timeout=5000)
        filtered = page.locator(".gate").count()
        assert 0 < filtered < total
        assert "tipo=rever_classificacao" in page.url     # the filter is addressable too

        page.locator('.chip[data-kind=""]').click()
        page.wait_for_function(f"document.querySelectorAll('.gate').length === {total}", timeout=5000)
        assert "tipo=" not in page.url
        assert not errors, f"JS errors: {errors}"
    finally:
        page.close()


def test_single_kind_queue_shows_no_filter_chrome(live_app, browser):
    """A filter that can only ever select everything is noise — don't render it."""
    page, errors = _open(browser, live_app)
    try:
        assert page.locator(".gate").count() == 3
        assert page.locator(".chip").count() == 0
        assert page.locator(".ghdr").count() == 1
        assert not errors, f"JS errors: {errors}"
    finally:
        page.close()
