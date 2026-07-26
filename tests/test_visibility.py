"""Per-person visibility — Phase D (ADR-045).

ADR-038 recorded which of our inboxes each message reached and said plainly that it held no policy:
``scopes.visible()`` was "the seam Phase D still owes a caller". Until this, it had **zero** callers,
so every signed-in person saw every thread while the account page said inbox scopes were editable.

**The trap these tests exist to avoid.** Every other module in this suite signs in as an admin, and
an admin bypasses ``person_scopes`` entirely (``scopes.visible(..., is_admin=True)`` returns True
unconditionally). A visibility filter is therefore invisible to ~1100 green tests. Worse, the obvious
fix — sign in a member and assert 200 — stays green while every lens renders empty, which is the
ADR-040 dishonest-refusal failure reborn. So every test below asserts on **content**: which threads
came back, and whether the numbers agree with them.

Fixtures build a small, explicit corpus rather than reusing the real one, so a scope change in
production data cannot silently turn an assertion into a tautology.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from conftest import sign_in, sign_in_member
from email2data import webapp
from email2data.workspace import Workspace

SETTINGS = {"llm": {"provider": "vertex_gemini", "model": "gemini-2.5-flash"},
            "imap": {"accounts": [{"username": "orcamentos@lindoservico.pt"},
                                  {"username": "luis.coelho@lindoservico.pt"}]}}

ORC = "orcamentos@lindoservico.pt"
LUIS = "luis.coelho@lindoservico.pt"
GHOST = "margarida.reis@lindoservico.pt"      # real mail, never a configured account


def _crm(path, rows):
    """A minimal crm.db: one interaction per (message_id, thread_root)."""
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE interactions (
        message_id TEXT PRIMARY KEY, thread_root TEXT, subject TEXT, from_email TEXT,
        to_emails TEXT, date TEXT, direction TEXT, counterparty TEXT, purpose TEXT,
        priority TEXT, has_attachments INTEGER DEFAULT 0)""")
    for mid, root, subject in rows:
        conn.execute(
            "INSERT INTO interactions (message_id, thread_root, subject, from_email, to_emails, "
            "date, direction, counterparty, purpose, priority) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (mid, root, subject, "cliente@exemplo.pt", "orcamentos@lindoservico.pt",
             "2026-07-20T10:00:00+00:00", "INBOUND", "CLIENT", "QUOTE_REQUEST", "HIGH"))
    conn.commit()
    conn.close()


def _sync(path, scopes):
    """A minimal sync.db message_scope table: {message_id: [address, ...]}."""
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE message_scope (
        message_id TEXT NOT NULL, address TEXT NOT NULL, source TEXT NOT NULL,
        updated_ts TEXT, PRIMARY KEY (message_id, address))""")
    for mid, addrs in scopes.items():
        for a in addrs:
            conn.execute("INSERT INTO message_scope VALUES (?,?,?,?)",
                         (mid, a, "header", "2026-07-20T10:00:00+00:00"))
    conn.commit()
    conn.close()


@pytest.fixture
def app_ws(tmp_path):
    """Three threads: one reaching only orcamentos@, one only luis@, one only an inbox that is NOT a
    configured account (the case that would have vanished — see test_a_scope_that_is_not_a_configured
    _account_is_still_grantable)."""
    out = tmp_path / "out"
    out.mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "corpus").mkdir()
    _crm(out / "crm.db", [("m-orc", "t-orc", "Orçamento A"),
                          ("m-luis", "t-luis", "Obra B"),
                          ("m-ghost", "t-ghost", "Recrutamento C")])
    _sync(out / "sync.db", {"m-orc": [ORC], "m-luis": [LUIS], "m-ghost": [GHOST]})
    settings = {**SETTINGS,
                "paths": {"corpus_dir": str(tmp_path / "corpus"), "out_dir": str(out)},
                "__settings_path__": str(tmp_path / "config" / "settings.json")}
    ws = Workspace(out / "w.db").connect()
    app = webapp.create_app(settings, workspace=ws, jobspecs={}, reply_pb="pb",
                            prepared=([], [], {}))
    return app, ws


def _roots(client, *, resolved=False):
    url = "/api/fila?include=resolved" if resolved else "/api/fila"
    return {r["thread_root"] for r in client.get(url).json()["rows"]}


# ── the filter itself ────────────────────────────────────────────────────────

def test_an_admin_still_sees_every_thread(app_ws):
    """The other half of the property. A visibility layer that quietly narrowed the admin's view
    would hide work from the one person accountable for all of it."""
    app, ws = app_ws
    client = TestClient(app, follow_redirects=False)
    sign_in(client, ws)
    assert _roots(client, resolved=True) == {"t-orc", "t-luis", "t-ghost"}


def test_a_member_sees_only_the_threads_their_grants_reach(app_ws):
    app, ws = app_ws
    client = TestClient(app, follow_redirects=False)
    sign_in_member(client, ws, scopes=[ORC])
    assert _roots(client, resolved=True) == {"t-orc"}


def test_grants_are_additive(app_ws):
    app, ws = app_ws
    client = TestClient(app, follow_redirects=False)
    sign_in_member(client, ws, scopes=[ORC, LUIS])
    assert _roots(client, resolved=True) == {"t-orc", "t-luis"}


def test_a_member_with_no_grants_sees_nothing(app_ws):
    """Fail closed. Not an error, not everything — nothing."""
    app, ws = app_ws
    client = TestClient(app, follow_redirects=False)
    sign_in_member(client, ws, scopes=[])
    assert _roots(client, resolved=True) == set()


def test_a_revoked_grant_bites_on_the_next_request(app_ws):
    """Nothing scope-shaped may be cached across requests — the same property ADR-040 required of
    demotion. A grant removed while someone is looking at the queue must take effect immediately."""
    app, ws = app_ws
    client = TestClient(app, follow_redirects=False)
    person = sign_in_member(client, ws, scopes=[ORC, LUIS])
    assert _roots(client, resolved=True) == {"t-orc", "t-luis"}
    ws.set_person_scopes(person["person_id"], [ORC])
    assert _roots(client, resolved=True) == {"t-orc"}


# ── the blocker that had to be cleared first ─────────────────────────────────

def test_a_scope_that_is_not_a_configured_account_is_still_grantable(app_ws):
    """The measured near-miss (ADR-045). `_known_scopes` offered only `imap.accounts[].username`,
    while `message_scope` on the real corpus held 10 addresses against 4 configured — mail reaches
    an inbox by Cc, by forward, by alias. 22 of 374 real threads (5.9%) carried none of the 4, and
    only ONE carried `sem-atribuicao`, so the admin bucket did not catch them either. Validating
    grants against the configured list would have made those threads ungrantable: invisible to every
    non-admin, with no way to fix it from the UI. That is "never silently bin a client" reached
    through a permission vocabulary instead of through a classifier."""
    app, ws = app_ws
    admin = TestClient(app, follow_redirects=False)
    sign_in(admin, ws)
    assert GHOST in admin.get("/api/admin/people").json()["known_scopes"]

    member = TestClient(app, follow_redirects=False)
    person = sign_in_member(member, ws, scopes=[GHOST])
    assert ws.person_scopes(person["person_id"]) == [GHOST], "the grant was rejected"
    assert _roots(member, resolved=True) == {"t-ghost"}


def test_a_typo_is_still_refused(app_ws):
    """Widening the vocabulary must not turn it off: an unvalidated typo reads as a permission and
    matches no mail at all (ADR-041)."""
    app, ws = app_ws
    client = TestClient(app, follow_redirects=False)
    sign_in(client, ws)
    person = ws.create_person("Novo", can_login=True)
    r = client.post(f"/api/admin/people/{person['person_id']}",
                    json={"scopes": ["nao-existe@lindoservico.pt"]})
    assert r.status_code == 400
    assert ws.person_scopes(person["person_id"]) == []


# ── the escapes: hiding a row must also close its routes ─────────────────────

def test_the_thread_route_refuses_a_thread_the_reader_cannot_see(app_ws):
    """The natural pivot: Fila rows are clicked, and this is what the click fetches. Hiding the row
    while leaving this open would protect the index and publish the contents."""
    app, ws = app_ws
    client = TestClient(app, follow_redirects=False)
    sign_in_member(client, ws, scopes=[ORC])
    assert client.get("/api/thread/t-orc").status_code == 200
    assert client.get("/api/thread/t-luis").status_code == 404


def test_the_refusal_is_404_not_403(app_ws):
    """403 would confirm the thread exists, which is most of what an unauthorised caller wanted."""
    app, ws = app_ws
    client = TestClient(app, follow_redirects=False)
    sign_in_member(client, ws, scopes=[ORC])
    assert client.get("/api/thread/t-luis").status_code == 404
    assert client.get("/api/thread/nao-existe-de-todo").status_code == 404


def test_the_relations_route_refuses_and_filters(app_ws):
    app, ws = app_ws
    client = TestClient(app, follow_redirects=False)
    sign_in_member(client, ws, scopes=[ORC])
    assert client.get("/api/relations/m-luis").status_code == 404
    allowed = client.get("/api/relations/m-orc")
    if allowed.status_code == 200:
        leaked = [x.get("thread_root") for bucket in allowed.json().values() for x in bucket
                  if x.get("thread_root") not in {"t-orc"}]
        assert leaked == [], f"related buckets leaked other inboxes' threads: {leaked}"


def test_the_attachment_route_refuses_bytes_from_an_invisible_thread(app_ws):
    """The only route that hands over raw bytes straight off the corpus — no crm join, no thread
    fold, nothing else in the way."""
    app, ws = app_ws
    client = TestClient(app, follow_redirects=False)
    sign_in_member(client, ws, scopes=[ORC])
    assert client.get("/api/attachment/m-luis/0").status_code == 404


def test_the_legacy_inbox_report_is_admin_only(app_ws):
    """It renders every body from a startup-bound closure and takes no `person` at all. Admin-only is
    honest and total; a half-filtered report would look filtered and not be."""
    app, ws = app_ws
    member = TestClient(app, follow_redirects=False)
    sign_in_member(member, ws, scopes=[ORC])
    assert member.get("/inbox").status_code == 403
    admin = TestClient(app, follow_redirects=False)
    sign_in(admin, ws)
    assert admin.get("/inbox").status_code == 200


# ── the counters must agree with the rows ────────────────────────────────────

def test_the_nav_badge_counts_only_what_this_person_can_see(app_ws):
    """A badge is a claim about work waiting for YOU. Counted from unfiltered rows it says
    «7 a responder» over a queue showing three — and the nav is the number people trust, so the
    disagreement resolves in favour of the lie."""
    app, ws = app_ws
    admin = TestClient(app, follow_redirects=False)
    sign_in(admin, ws)
    admin_fila = admin.get("/api/fila").json()

    member = TestClient(app, follow_redirects=False)
    sign_in_member(member, ws, scopes=[ORC])
    member_fila = member.get("/api/fila").json()

    assert member_fila["nav_counts"].get("fila", 0) <= admin_fila["nav_counts"].get("fila", 0)
    assert len(member_fila["rows"]) < len(admin_fila["rows"])


def test_a_member_with_no_grants_gets_no_badges_at_all(app_ws):
    app, ws = app_ws
    client = TestClient(app, follow_redirects=False)
    sign_in_member(client, ws, scopes=[])
    assert client.get("/api/fila").json()["nav_counts"] == {}


# ── the empty queue must say WHY it is empty ─────────────────────────────────

def test_an_empty_queue_caused_by_missing_grants_says_so(app_ws):
    """«Tudo tratado» over a queue nobody granted is ADR-040's dishonest refusal one layer down: the
    app asserting, on no evidence, that there is nothing to do — and the person acts on it by walking
    away from waiting work. Verified in a real browser too (ADR-045 Consequences); this pins the
    embed that drives it, because TestClient cannot run the JS."""
    app, ws = app_ws
    client = TestClient(app, follow_redirects=False)
    sign_in_member(client, ws, scopes=[])
    assert "const NO_SCOPES = true;" in client.get("/fila").text


def test_a_genuinely_clear_queue_still_says_tudo_tratado(app_ws):
    """The flag must not fire for someone who has grants and has simply finished — that person has
    earned «Tudo tratado» and telling them otherwise is its own lie."""
    app, ws = app_ws
    client = TestClient(app, follow_redirects=False)
    sign_in_member(client, ws, scopes=[ORC])
    assert "const NO_SCOPES = false;" in client.get("/fila").text
    admin = TestClient(app, follow_redirects=False)
    sign_in(admin, ws)
    assert "const NO_SCOPES = false;" in admin.get("/fila").text


# ── projects: derived visibility ─────────────────────────────────────────────

def test_a_project_is_visible_when_one_of_its_threads_is(app_ws):
    app, ws = app_ws
    admin = TestClient(app, follow_redirects=False)
    sign_in(admin, ws)
    pid = admin.post("/api/projects", json={"title": "Obra do Luís"}).json()["project_id"]
    admin.post(f"/api/projects/{pid}/attach", json={"ref": "t-luis"})

    member = TestClient(app, follow_redirects=False)
    sign_in_member(member, ws, scopes=[LUIS])
    assert pid in {p["project_id"] for p in member.get("/api/projects").json()}
    assert member.get(f"/api/projects/{pid}").status_code == 200


def test_a_project_whose_threads_are_all_invisible_is_refused(app_ws):
    """And refused on EVERY id-bearing route, because the rule lives in the middleware rather than in
    23 separate handlers — the 24th route inherits it."""
    app, ws = app_ws
    admin = TestClient(app, follow_redirects=False)
    sign_in(admin, ws)
    pid = admin.post("/api/projects", json={"title": "Obra do Luís"}).json()["project_id"]
    admin.post(f"/api/projects/{pid}/attach", json={"ref": "t-luis"})

    member = TestClient(app, follow_redirects=False)
    sign_in_member(member, ws, scopes=[ORC])
    assert pid not in {p["project_id"] for p in member.get("/api/projects").json()}
    for path in (f"/api/projects/{pid}",
                 f"/api/projects/{pid}/timeline",
                 f"/api/projects/{pid}/participants",
                 f"/api/projects/{pid}/description"):
        assert member.get(path).status_code == 404, f"{path} was reachable"


def test_a_project_with_no_threads_is_admin_only(app_ws):
    """Fail closed, and honest: nothing is attached, so there is no evidence anyone should see it.
    Visible-to-all-until-first-thread would make every new project briefly public."""
    app, ws = app_ws
    admin = TestClient(app, follow_redirects=False)
    sign_in(admin, ws)
    pid = admin.post("/api/projects", json={"title": "Ainda sem threads"}).json()["project_id"]
    assert admin.get(f"/api/projects/{pid}").status_code == 200

    member = TestClient(app, follow_redirects=False)
    sign_in_member(member, ws, scopes=[ORC, LUIS])
    assert member.get(f"/api/projects/{pid}").status_code == 404


# ── the shape of the seam ────────────────────────────────────────────────────

def test_fila_rows_has_no_default_person(app_ws):
    """Default-deny expressed in the signature. A default of any kind — even a safe None — lets a
    new call site omit it silently; with none, a forgotten call site is a TypeError the suite
    raises at once."""
    import inspect

    app, _ws = app_ws
    # The closure lives inside create_app, so reach it through the module source instead.
    source = inspect.getsource(webapp.create_app)
    assert "def _fila_rows(*, person: dict[str, Any] | None,\n" in source, (
        "_fila_rows gained a default for `person` — a forgotten call site is now silent")
