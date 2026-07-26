"""Self-service password recovery, end to end (ADR-042).

The reason this exists: before it, the ONLY way back into a locked-out account was
``email2data auth reset`` at a terminal on the Docker host, or an admin issuing an invite. For the
sole administrator neither is reachable from inside the app — which is how the owner locked himself
out twice in one hour, and what ADR-041 §10 records.

The tests are grouped by the property they defend, and the first group is the one that matters most:
a public endpoint that says different things about different names turns the roster — people's real
names — into something anyone who can reach the port can enumerate. Every branch of POST /recuperar
must be indistinguishable from every other.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import sign_in
from email2data import webapp
from email2data.workspace import Workspace

SETTINGS = {"llm": {"provider": "vertex_gemini", "model": "gemini-2.5-flash"},
            "mail": {"enabled": False, "base_url": "https://192.168.1.253:8042",
                     "reset_max_per_hour": 5}}


class FakeMailer:
    """Records instead of sending. Mirrors the one method the webapp calls."""

    def __init__(self):
        self.sent = []

    def send_password_reset(self, **kwargs):
        self.sent.append(kwargs)

    def last_token(self):
        return self.sent[-1]["reset_url"].rsplit("/", 1)[-1]


class ExplodingMailer(FakeMailer):
    def send_password_reset(self, **kwargs):
        from email2data.mailer import MailError
        raise MailError("connection refused")


def _app(tmp_path, *, mailer=None, settings=None):
    ws = Workspace(tmp_path / "w.db").connect()
    app = webapp.create_app(settings or SETTINGS, workspace=ws, jobspecs={}, reply_pb="pb",
                            prepared=([], [], {}))
    app.state.mailer = FakeMailer() if mailer is None else mailer
    return app, ws


def _person(ws, app, name="Filipe", *, email="filipe@lindoservico.pt", password="original-pw-1234",
            can_login=True, is_admin=True):
    person = ws.create_person(name, can_login=can_login, is_admin=is_admin,
                              responsible="" if can_login else "Filipe")
    if password and can_login:
        app.state.auth.set_password(person["person_id"], password)
    if email:
        ws.set_person_email(person["person_id"], email)
    return person


# ── the property that makes a public endpoint safe ───────────────────────────

def test_the_reset_request_is_not_an_account_oracle(tmp_path):
    """A real name, an invented name, a name with no address on file, and a deactivated person must
    produce byte-identical responses. Anything that differs — status, body, even whitespace — is a
    roster-enumeration primitive on an unauthenticated endpoint."""
    app, ws = _app(tmp_path)
    _person(ws, app, "Filipe")
    _person(ws, app, "SemEmail", email="", password="pw-12345678", is_admin=False)
    gone = _person(ws, app, "Antigo", email="antigo@lindoservico.pt", password="pw-12345678",
                   is_admin=False)
    ws.set_person_active(gone["person_id"], False)
    client = TestClient(app, follow_redirects=False)

    responses = [client.post("/recuperar", data={"name": n})
                 for n in ("Filipe", "Ninguém Existe", "SemEmail", "Antigo")]
    assert len({r.status_code for r in responses}) == 1
    assert len({r.text for r in responses}) == 1, (
        "POST /recuperar answers differently for different names — the roster is enumerable")


def test_only_the_real_person_actually_causes_mail(tmp_path):
    """The flip side of the neutral response: identical output must not mean identical behaviour."""
    app, ws = _app(tmp_path)
    _person(ws, app, "Filipe")
    _person(ws, app, "SemEmail", email="", password="pw-12345678", is_admin=False)
    client = TestClient(app, follow_redirects=False)

    for name in ("Ninguém Existe", "SemEmail"):
        client.post("/recuperar", data={"name": name})
    assert app.state.mailer.sent == []
    client.post("/recuperar", data={"name": "Filipe"})
    assert len(app.state.mailer.sent) == 1


def test_a_send_failure_is_not_visible_to_the_requester(tmp_path):
    """An SMTP error must not become the oracle the neutral response exists to prevent."""
    app, ws = _app(tmp_path, mailer=ExplodingMailer())
    _person(ws, app, "Filipe")
    client = TestClient(app, follow_redirects=False)
    broken = client.post("/recuperar", data={"name": "Filipe"})
    unknown = client.post("/recuperar", data={"name": "Ninguém"})
    assert broken.status_code == unknown.status_code
    assert broken.text == unknown.text


def test_a_deactivated_person_cannot_recover_their_way_back_in(tmp_path):
    """Deactivation is how someone leaves (ADR-041). A stale address that still reached them would
    otherwise be a way back in after being removed."""
    app, ws = _app(tmp_path)
    _person(ws, app, "Filipe")
    gone = _person(ws, app, "Antigo", email="antigo@lindoservico.pt", password="pw-12345678",
                   is_admin=False)
    ws.set_person_active(gone["person_id"], False)
    TestClient(app).post("/recuperar", data={"name": "Antigo"})
    assert app.state.mailer.sent == []


def test_a_person_who_cannot_sign_in_is_never_mailed_a_reset(tmp_path):
    """Rita & co are assignable but have no login. A reset for someone with no password is a
    credential for an account that is not supposed to have one."""
    app, ws = _app(tmp_path)
    _person(ws, app, "Filipe")
    ws.create_person("Rita", can_login=False, responsible="Filipe")
    rita = ws.person("Rita")
    ws.set_person_email(rita["person_id"], "rita@lindoservico.pt")
    TestClient(app).post("/recuperar", data={"name": "Rita"})
    assert app.state.mailer.sent == []


# ── the happy path ───────────────────────────────────────────────────────────

def test_the_full_round_trip_signs_the_person_back_in(tmp_path):
    app, ws = _app(tmp_path)
    person = _person(ws, app, "Filipe")
    client = TestClient(app, follow_redirects=False)

    client.post("/recuperar", data={"name": "Filipe"})
    token = app.state.mailer.last_token()
    assert client.get(f"/recuperar/{token}").status_code == 200

    done = client.post("/recuperar/definir",
                       data={"token": token, "password": "brand-new-pw-99",
                             "confirm": "brand-new-pw-99"})
    assert done.status_code == 303 and done.headers["location"] == "/"
    assert "e2d_session" in done.headers.get("set-cookie", "")
    assert app.state.auth.check_password(person["person_id"], "brand-new-pw-99")
    assert not app.state.auth.check_password(person["person_id"], "original-pw-1234")


def test_the_mailed_link_uses_the_configured_base_url_not_the_request_host(tmp_path):
    """Reset-link poisoning: a link built from an attacker-controlled Host header mails the victim a
    genuine token pointing at the attacker's server. Configuration is the only trustworthy source."""
    app, ws = _app(tmp_path)
    _person(ws, app, "Filipe")
    TestClient(app).post("/recuperar", data={"name": "Filipe"},
                         headers={"Host": "evil.example.com"})
    url = app.state.mailer.sent[0]["reset_url"]
    assert url.startswith("https://192.168.1.253:8042/recuperar/")
    assert "evil" not in url


def test_resetting_revokes_every_other_session(tmp_path):
    """"I forgot my password" and "someone else is in my account" are indistinguishable from here,
    and the safe reading is the second."""
    app, ws = _app(tmp_path)
    person = _person(ws, app, "Filipe")
    stolen = app.state.auth.start_session(person["person_id"])
    assert app.state.auth.session_person(stolen) == person["person_id"]

    client = TestClient(app, follow_redirects=False)
    client.post("/recuperar", data={"name": "Filipe"})
    token = app.state.mailer.last_token()
    client.post("/recuperar/definir",
                data={"token": token, "password": "brand-new-pw-99", "confirm": "brand-new-pw-99"})
    assert app.state.auth.session_person(stolen) is None


# ── token hygiene ────────────────────────────────────────────────────────────

def test_a_reset_link_works_exactly_once(tmp_path):
    app, ws = _app(tmp_path)
    _person(ws, app, "Filipe")
    client = TestClient(app, follow_redirects=False)
    client.post("/recuperar", data={"name": "Filipe"})
    token = app.state.mailer.last_token()
    body = {"token": token, "password": "brand-new-pw-99", "confirm": "brand-new-pw-99"}
    assert client.post("/recuperar/definir", data=body).status_code == 303
    assert client.post("/recuperar/definir", data=dict(body, password="third-pw-000",
                                                       confirm="third-pw-000")).status_code == 404


def test_requesting_again_invalidates_the_previous_link(tmp_path):
    """Two live links for one person is two windows an attacker could be inside."""
    app, ws = _app(tmp_path)
    _person(ws, app, "Filipe")
    client = TestClient(app)
    client.post("/recuperar", data={"name": "Filipe"})
    first = app.state.mailer.last_token()
    client.post("/recuperar", data={"name": "Filipe"})
    second = app.state.mailer.last_token()
    assert first != second
    assert client.get(f"/recuperar/{first}").status_code == 404
    assert client.get(f"/recuperar/{second}").status_code == 200


def test_an_expired_link_is_refused(tmp_path):
    app, ws = _app(tmp_path)
    person = _person(ws, app, "Filipe")
    token = app.state.auth.create_reset(person["person_id"], ttl_minutes=-1)
    client = TestClient(app)
    assert client.get(f"/recuperar/{token}").status_code == 404
    assert client.post("/recuperar/definir",
                       data={"token": token, "password": "brand-new-pw-99",
                             "confirm": "brand-new-pw-99"}).status_code == 404


def test_a_garbage_token_is_refused(tmp_path):
    app, ws = _app(tmp_path)
    _person(ws, app, "Filipe")
    assert TestClient(app).get("/recuperar/not-a-real-token").status_code == 404


def test_the_token_is_never_stored_in_readable_form(tmp_path):
    """A stolen auth.db — or a stolen backup — must yield nothing redeemable."""
    app, ws = _app(tmp_path)
    _person(ws, app, "Filipe")
    TestClient(app).post("/recuperar", data={"name": "Filipe"})
    token = app.state.mailer.last_token()
    rows = list(app.state.auth._conn.execute("SELECT * FROM password_resets"))
    assert rows and all(token not in str(tuple(r)) for r in rows)


@pytest.mark.parametrize("password,confirm", [("short", "short"), ("aaaaaaaa1", "bbbbbbbb2")])
def test_a_rejected_password_does_not_burn_the_link(tmp_path, password, confirm):
    """A typo must not cost the person their one link — they would have to request another, and the
    first thing a locked-out person does is mistype."""
    app, ws = _app(tmp_path)
    _person(ws, app, "Filipe")
    client = TestClient(app, follow_redirects=False)
    client.post("/recuperar", data={"name": "Filipe"})
    token = app.state.mailer.last_token()
    assert client.post("/recuperar/definir",
                       data={"token": token, "password": password,
                             "confirm": confirm}).status_code == 400
    assert client.get(f"/recuperar/{token}").status_code == 200, "the link was consumed by a typo"


# ── throttling ───────────────────────────────────────────────────────────────

def test_requests_are_throttled_per_person(tmp_path):
    app, ws = _app(tmp_path)
    _person(ws, app, "Filipe")
    client = TestClient(app)
    for _ in range(9):
        client.post("/recuperar", data={"name": "Filipe"})
    assert len(app.state.mailer.sent) == 5, "the per-hour cap did not bound outbound mail"


def test_the_throttle_never_locks_anyone_out(tmp_path):
    """It refuses to MAIL, it does not refuse to authenticate. Someone who floods the form must not
    thereby disable the account — that would turn a mail-abuse guard into a denial-of-service."""
    app, ws = _app(tmp_path)
    person = _person(ws, app, "Filipe")
    client = TestClient(app, follow_redirects=False)
    for _ in range(9):
        client.post("/recuperar", data={"name": "Filipe"})
    assert app.state.auth.check_password(person["person_id"], "original-pw-1234")
    assert client.post("/login", data={"name": "Filipe", "password": "original-pw-1234"}
                       ).status_code == 303


# ── honest unavailability (ADR-040) ──────────────────────────────────────────

def test_with_no_mail_configured_the_page_says_so_instead_of_promising_a_link(tmp_path):
    app, ws = _app(tmp_path)
    app.state.mailer = None
    _person(ws, app, "Filipe")
    client = TestClient(app, follow_redirects=False)
    page = client.get("/recuperar")
    assert page.status_code == 503
    assert "indisponível" in page.text
    assert "administrador" in page.text, "it must name the route that still works"
    assert client.post("/recuperar", data={"name": "Filipe"}).status_code == 503


def test_a_missing_base_url_refuses_rather_than_mailing_a_dead_link(tmp_path):
    """A link with no host is worse than no mail: the person clicks it, nothing happens, and they
    have no reason to suspect configuration."""
    app, ws = _app(tmp_path, settings={"llm": SETTINGS["llm"], "mail": {"enabled": False}})
    _person(ws, app, "Filipe")
    client = TestClient(app, follow_redirects=False)
    assert client.get("/recuperar").status_code == 503
    assert client.post("/recuperar", data={"name": "Filipe"}).status_code == 503
    assert app.state.mailer.sent == []


def test_the_login_page_offers_the_route(tmp_path):
    """A recovery route nobody can find is not a recovery route."""
    app, _ws = _app(tmp_path)
    assert "/recuperar" in TestClient(app).get("/login").text


def test_a_signed_in_person_is_sent_home_rather_than_shown_the_form(tmp_path):
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    sign_in(client, ws)
    response = client.get("/recuperar")
    assert response.status_code == 303 and response.headers["location"] == "/"


# ── the set-password form ────────────────────────────────────────────────────

def test_the_reset_form_anchors_a_username_for_password_managers(tmp_path):
    """ADR-041 records the third defect found by the owner locking himself out: a change-password
    form with no autocomplete="username" anchor reads to a password manager as a *create*-password
    form, so it offers to save a second entry instead of updating the existing one."""
    app, ws = _app(tmp_path)
    _person(ws, app, "Filipe")
    client = TestClient(app)
    client.post("/recuperar", data={"name": "Filipe"})
    page = client.get(f"/recuperar/{app.state.mailer.last_token()}").text
    assert 'autocomplete="username"' in page
    assert 'autocomplete="new-password"' in page


def test_recovery_pages_are_not_cached(tmp_path):
    """A reset form left in a shared browser's back-cache is the token surviving its own use."""
    app, ws = _app(tmp_path)
    _person(ws, app, "Filipe")
    client = TestClient(app)
    client.post("/recuperar", data={"name": "Filipe"})
    page = client.get(f"/recuperar/{app.state.mailer.last_token()}")
    assert "no-store" in page.headers.get("cache-control", "")


# ── what this is NOT ─────────────────────────────────────────────────────────

def test_recovery_cannot_repair_an_install_with_no_people(tmp_path):
    """Stated as a test so it is not mistaken for a zero-admin fix (ADR-041 §10). It needs a person
    row with an address; a bricked install has no way to create one, and /setup is already closed."""
    app, ws = _app(tmp_path)
    person = ws.create_person("Alguém", can_login=True, is_admin=True)
    app.state.auth.set_password(person["person_id"], "pw-12345678")   # closes /setup
    client = TestClient(app, follow_redirects=False)
    assert client.get("/setup").status_code == 404
    client.post("/recuperar", data={"name": "Alguém"})
    assert app.state.mailer.sent == [], "no address on file — recovery has nowhere to send"
