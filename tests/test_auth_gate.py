"""The default-deny auth gate (ADR-039) — what it blocks, what it lets through, what it must not break.

The property that matters most is the one the sibling app's own notes describe going wrong: gating by
per-route decorator means a new route ships open when someone forgets one. Here the gate is
middleware with a closed allowlist, so ``test_every_route_is_gated_by_default`` walks the real route
tree and fails when a new path is reachable signed-out.

The second-most-important is ``/healthz``: the image HEALTHCHECK probes it, and a 401 there marks the
container unhealthy — which also stops intake-bot, since it depends_on email2data being healthy.
"""

import re

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from conftest import TEST_ADMIN, TEST_PASSWORD, sign_in, sign_in_member
from email2data import webapp
from email2data.workspace import Workspace

SETTINGS = {"llm": {"provider": "vertex_gemini", "model": "gemini-2.5-flash"}}

# Public by design. Everything else must be unreachable signed-out.
#
# The /recuperar family (ADR-042) is public by necessity: the visitor cannot sign in, that is the
# problem being solved. They earn it by leaking nothing — `test_the_reset_request_is_not_an_account_
# oracle` pins that the response is byte-identical for a real name and an invented one, which is the
# property that makes a public POST here safe.
PUBLIC = {"/healthz", "/login", "/logout", "/setup", "/aceitar-convite", "/aceitar-convite/{token}",
          "/recuperar", "/recuperar/{token}", "/recuperar/definir"}

# Admin-only by design (ADR-040). Templates, matched against the route tree — so adding a route
# under /api/admin/ without listing it here fails `test_the_admin_path_set_matches_the_route_tree`.
ADMIN_ONLY = {"/admin", "/api/admin/accounts",
              # The legacy full report (ADR-045): every body, no scope seam, no `person` argument.
              # Admin-only rather than half-filtered — see webapp._ADMIN_EXACT.
              "/inbox",
              # ADR-041 «Pessoas» — the roster surface. Covered by the /api/admin/ prefix the moment
              # each route existed, which is the property ADR-040 §1 chose middleware for.
              "/api/admin/people", "/api/admin/people/{person_id}",
              "/api/admin/people/{person_id}/convite"}


def _app(tmp_path):
    ws = Workspace(tmp_path / "w.db").connect()
    app = webapp.create_app(SETTINGS, workspace=ws, jobspecs={}, reply_pb="pb",
                            prepared=([], [], {}))
    return app, ws


def _bootstrapped(tmp_path):
    """An app that already has one admin credential — i.e. past first-run setup."""
    app, ws = _app(tmp_path)
    person = ws.create_person("Bootstrap", can_login=True, is_admin=True)
    app.state.auth.set_password(person["person_id"], "bootstrap-pw-123")
    return app, ws


def _methods(route):
    """The methods worth probing. HEAD/OPTIONS are synthesised by Starlette, not app surface."""
    return sorted(m for m in route.methods if m not in ("HEAD", "OPTIONS"))


def _concrete(path: str) -> str:
    """"/api/projects/{pid}/draft" -> "/api/projects/probe/draft".

    Path params are substituted rather than skipped. Skipping them is what made the original version
    of this walk cover 18 of 76 routes while its docstring said "the real route tree" — and every
    id-bearing route, i.e. everything that touches one customer's data, was in the missing 58.
    ``{key:path}``-style converters are handled by the same regex.
    """
    return re.sub(r"\{[^}]+\}", "probe", path)


def _walk(app):
    """(route, method, concrete_path) for every non-public API route + method pair."""
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path in PUBLIC:
            continue
        for method in _methods(route):
            yield route, method, _concrete(route.path)


# ── default deny ─────────────────────────────────────────────────────────────

def test_every_route_is_gated_by_default(tmp_path):
    """Walk the real route tree: any non-public path reachable signed-out is a hole.

    This is the test the sibling app has only for its readonly flag, not for auth — and the reason
    a future route added without thinking about auth fails here rather than in production.

    Every method and every parameterised path, not just bare GETs. A signed-out POST that reaches its
    handler will answer 400/422 on the empty body rather than 401, so "not 303 and not 401" is the
    right test — a validation error IS a leak, because the handler ran.
    """
    app, _ws = _bootstrapped(tmp_path)
    client = TestClient(app, follow_redirects=False)
    leaked = []
    probed = 0
    for _route, method, path in _walk(app):
        probed += 1
        response = client.request(method, path)
        if response.status_code not in (303, 401):
            leaked.append((method, path, response.status_code))
    assert not leaked, f"reachable without signing in: {leaked}"
    # Guard the guard: a refactor that made _walk yield nothing would turn this into a test that
    # passes by checking nothing at all.
    assert probed > 60, f"the route walk only probed {probed} routes — it stopped covering the app"


def test_the_route_walk_actually_covers_parameterised_and_non_get_routes(tmp_path):
    """Pins the fix itself. The walk must include an id-bearing path and a non-GET method."""
    app, _ws = _bootstrapped(tmp_path)
    walked = [(m, p) for _r, m, p in _walk(app)]
    assert ("DELETE", "/api/projects/probe") in walked          # parameterised + non-GET
    assert ("POST", "/api/thread/handled") in walked            # non-GET
    assert ("GET", "/api/thread/probe") in walked               # {thread_root:path} converter


# ── authorization: admin-only paths (ADR-040) ────────────────────────────────

def test_admin_paths_are_admin_only_for_a_signed_in_non_admin(tmp_path):
    """The property, walked over the real route tree: authenticated ≠ authorized.

    Before ADR-040 every one of these answered 200 to any account that could log in — including
    POST /api/admin/accounts, which rewrites imap.accounts in settings.json.
    """
    app, ws = _bootstrapped(tmp_path)
    client = TestClient(app, follow_redirects=False)
    sign_in_member(client, ws)
    allowed = []
    for _route, method, path in _walk(app):
        if _concrete_is_admin(path):
            response = client.request(method, path)
            if response.status_code != 403:
                allowed.append((method, path, response.status_code))
    assert not allowed, f"a non-admin reached an admin-only route: {allowed}"


def test_the_admin_path_set_matches_the_route_tree(tmp_path):
    """Default-deny only holds if the set cannot silently fall behind the routes.

    A new /api/admin/* route is covered by the prefix and appears here automatically; a new admin
    surface on some other path fails this and has to be thought about.
    """
    app, _ws = _bootstrapped(tmp_path)
    found = {r.path for r in app.routes if isinstance(r, APIRoute) and _concrete_is_admin(r.path)}
    assert found == ADMIN_ONLY, (
        f"the admin surface moved: {found ^ ADMIN_ONLY}. Update ADMIN_ONLY here AND "
        f"webapp._ADMIN_EXACT/_ADMIN_PREFIX — deliberately, not reflexively.")


def test_no_page_offers_a_member_a_door_the_gate_refuses(tmp_path):
    """What the app SHOWS must agree with what it ALLOWS (non-negotiable #7).

    ADR-040 locked `/admin` and gated the nav's «Administração», but the ⌘K palettes are JS and could
    not see `person` at all, so two kept offering admin-only doors: `/admin` on Projetos and `/inbox`
    on the Fila. A member choosing either got a 403 — once per page, forever. This sweeps EVERY
    signed-in page instead of pinning the two that were found, so the next palette entry is covered
    by this test rather than by someone remembering.

    `/inbox` counts: it is in `_ADMIN_EXACT` because it renders the report over every message with no
    ADR-045 per-person filtering.

    WHAT THIS ASSERTS, precisely: the palettes are JS, so the entry ships as source either way and a
    grep cannot tell "offered" from "offered only to admins". So it pins the two halves that together
    make the gate hold — every door construction sits behind an `IS_ADMIN` guard, and `IS_ADMIN` is
    the right value for who is asking. The nav's own `<a href="/admin">` IS omitted server-side, and
    that half is still asserted directly."""
    app, ws = _bootstrapped(tmp_path)
    member = TestClient(app)
    sign_in_member(member, ws)
    pages = ["/", "/fila", "/para-ti", "/projetos", "/contrapartes", "/capturas", "/a-minha-conta"]
    for path in pages:
        html = member.get(path).text
        assert "const IS_ADMIN = false;" in html, f"{path} cannot gate anything"
        # The server-rendered nav link is omitted outright, not merely guarded.
        assert '<a class="gm" data-nav="admin" href="/admin">' not in html, path
        for door in ("/admin", "/inbox"):
            # Every navigation to a locked door must be guarded. Count the constructions and the
            # guarded ones: an unguarded entry makes these disagree.
            built = html.count(f"location.href='{door}'")
            guarded = html.count(f"if(IS_ADMIN) items.push({{kind:'ação',label:S.actInbox,"
                                 f"run:()=>{{location.href='{door}';}}}});")
            guarded += html.count(f"if(IS_ADMIN) base.push({{kind:'ação',label:'Admin',"
                                  f"run:()=>{{location.href='{door}';}}}});")
            assert built == guarded, (
                f"{path} builds {built} navigation(s) to {door} but only {guarded} are behind "
                f"IS_ADMIN — a member would be offered a door the gate answers with 403")


def test_an_admin_is_still_offered_the_admin_doors(tmp_path):
    """The other half — gating by `IS_ADMIN` must not cost an admin their own entries. Without this,
    the cheapest way to pass the test above is to delete the doors for everyone."""
    app, ws = _bootstrapped(tmp_path)
    admin = TestClient(app)
    sign_in(admin, ws)
    assert "location.href='/admin'" in admin.get("/projetos").text
    assert "location.href='/inbox'" in admin.get("/fila").text
    # And the embed the gating reads is a real JS boolean on both sides.
    assert "const IS_ADMIN = true;" in admin.get("/fila").text
    member = TestClient(app)
    sign_in_member(member, ws)
    assert "const IS_ADMIN = false;" in member.get("/fila").text


def test_an_admin_still_reaches_the_admin_surface(tmp_path):
    """The other half: the gate must not lock admins out of their own pages."""
    app, ws = _bootstrapped(tmp_path)
    client = TestClient(app, follow_redirects=False)
    sign_in(client, ws)                                          # TEST_ADMIN, is_admin=True
    assert client.get("/admin").status_code == 200
    assert client.get("/api/admin/accounts").status_code == 200


def test_a_non_admin_gets_403_not_a_login_redirect(tmp_path):
    """403, never 303 → /login. They ARE signed in; bouncing them to a login they would pass is a
    loop, and it misnames the problem."""
    app, ws = _bootstrapped(tmp_path)
    client = TestClient(app, follow_redirects=False)
    sign_in_member(client, ws)
    response = client.get("/admin")
    assert response.status_code == 403
    assert "location" not in response.headers
    assert "administradores" in response.text                    # says why, in the shared shell
    assert "Fila" in response.text                               # nav survives: not a dead end


def test_the_403_page_leaks_no_admin_data(tmp_path):
    """The refusal must not render the thing it refuses."""
    app, ws = _bootstrapped(tmp_path)
    client = TestClient(app, follow_redirects=False)
    sign_in_member(client, ws)
    body = client.get("/admin").text
    for leak in ("password_env", "imap", "username", "mailboxes"):
        assert leak not in body


def test_a_non_admin_cannot_rewrite_the_mail_accounts(tmp_path):
    """The concrete exploit ADR-040 closes, asserted on state and not only on status."""
    app, ws = _bootstrapped(tmp_path)
    client = TestClient(app, follow_redirects=False)
    sign_in_member(client, ws)
    response = client.post("/api/admin/accounts", json={"accounts": [
        {"id": "evil", "host": "mail.attacker.example", "port": 993,
         "username": "x@attacker.example", "password_env": "X", "mailboxes": ["INBOX"]}]})
    assert response.status_code == 403
    assert "administradores" in response.json()["error"]
    # 403 before the handler: the settings dict the app is running on is untouched.
    assert "imap" not in SETTINGS


def test_demoting_an_admin_takes_effect_on_their_next_request(tmp_path):
    """Identity is re-read every request (ADR-039), so authorization must be too — not cached in the
    session, or a demotion would wait for the cookie to expire."""
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    person = sign_in(client, ws)
    assert client.get("/admin").status_code == 200
    ws._conn.execute("UPDATE people SET is_admin = 0 WHERE person_id = ?", (person["person_id"],))
    ws._conn.commit()
    assert client.get("/admin").status_code == 403


def test_a_non_admin_keeps_full_access_to_every_decision_lens(tmp_path):
    """ADR-040 is a narrow cut. The regression that would matter most is over-blocking: an ordinary
    user losing the app. Scope-based read filtering is a separate, later decision."""
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    sign_in_member(client, ws)
    for path in ("/", "/fila", "/projetos", "/para-ti", "/capturas", "/contrapartes",
                 "/api/inicio", "/api/fila", "/api/projects", "/api/para-ti", "/api/captures",
                 "/api/me"):
        assert client.get(path).status_code == 200, f"a non-admin lost access to {path}"


# ── the link agrees with the gate (ADR-041) ──────────────────────────────────
#
# ADR-040 closed the route and left the entry point open: a member saw «Administração», clicked it,
# and got a 403. These are the behavioural half of the shell tests in test_cockpit_ui.py — those
# prove `page()` can hide the entry, these prove every real lens actually passes the identity in.
# A builder that forgets `person=` fails HERE, not in a browser three weeks later.

LENS_PAGES = ("/", "/fila", "/projetos", "/para-ti", "/capturas", "/contrapartes")


def test_an_admin_is_offered_the_administration_entry_on_every_lens(tmp_path):
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    sign_in(client, ws)
    for path in LENS_PAGES:
        body = client.get(path).text
        assert 'data-nav="admin"' in body, f"an admin lost the Administração entry on {path}"


def test_a_member_is_never_offered_the_administration_entry(tmp_path):
    """The failure this pins is silent: the gate still answers 403, so nothing breaks — a member is
    just invited to walk into a wall, once per page, forever."""
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    sign_in_member(client, ws)
    for path in LENS_PAGES:
        body = client.get(path).text
        assert 'data-nav="admin"' not in body, f"{path} offered /admin to a non-admin"
        assert "Administração" not in body, f"{path} offered /admin to a non-admin"


def test_every_lens_says_whose_session_it_is(tmp_path):
    """Shared workshop machines: «who am I signed in as?» must be answerable without /api/me."""
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    person = sign_in_member(client, ws)
    for path in LENS_PAGES:
        body = client.get(path).text
        assert person["name"] in body, f"{path} does not name the signed-in person"
        assert "id='_acctbtn'" in body, f"{path} has no account control"


def test_the_403_page_names_who_is_refused(tmp_path):
    """«Não tens acesso» is only actionable if you know *who* «tu» is — on a machine where someone
    else may have left a session open, the name is the difference between asking for a promotion and
    signing in as yourself."""
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    person = sign_in_member(client, ws)
    r = client.get("/admin")
    assert r.status_code == 403
    assert person["name"] in r.text
    assert 'data-nav="admin"' not in r.text  # …and it does not re-offer the door it just closed


# ── «A minha conta» — the person's own surface (ADR-041) ─────────────────────
#
# Until now the ONLY way to change a password was `email2data auth invite` + a fresh token, i.e. an
# admin at a terminal. A person who suspected their password was compromised had no move they could
# make themselves, which is the kind of friction that gets solved by never changing the password.

ACCOUNT = "/a-minha-conta"
ACCOUNT_PW = "/a-minha-conta/palavra-passe"
NEW_PW = "a-new-password-9876"


def _resume(client, app, person):
    """Re-cookie a client whose session `set_password` just revoked (it always revokes all of them).

    Clears the jar first: a hand-set cookie carries no domain, so it sits alongside the one the app
    sets for `testserver` and can win the send — which reads as "the app signed me out" when the app
    did nothing of the sort.
    """
    client.cookies.clear()
    client.cookies.set("e2d_session", app.state.auth.start_session(person["person_id"]))


def test_the_account_page_says_who_you_are_and_what_you_can_do(tmp_path):
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    person = sign_in_member(client, ws)
    body = client.get(ACCOUNT).text
    assert person["name"] in body
    assert "Membro" in body                     # …the role, stated, not implied by an absent button
    assert 'action="/a-minha-conta/palavra-passe"' in body


def test_changing_your_password_requires_proving_you_know_the_old_one(tmp_path):
    """Without this, a session left open on an unlocked workshop machine is a permanent account
    takeover rather than a walk-up: change the password, and the owner is locked out of their own."""
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    person = sign_in_member(client, ws)
    r = client.post(ACCOUNT_PW, data={"current": "not-the-password", "new": NEW_PW,
                                      "confirm": NEW_PW})
    assert r.status_code == 400
    assert app.state.auth.check_password(person["person_id"], TEST_PASSWORD) is True


def test_a_new_password_is_length_checked_and_confirmed(tmp_path):
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    person = sign_in_member(client, ws)
    short = client.post(ACCOUNT_PW, data={"current": TEST_PASSWORD, "new": "abc", "confirm": "abc"})
    assert short.status_code == 400
    typo = client.post(ACCOUNT_PW, data={"current": TEST_PASSWORD, "new": NEW_PW,
                                         "confirm": NEW_PW + "x"})
    assert typo.status_code == 400
    assert app.state.auth.check_password(person["person_id"], TEST_PASSWORD) is True


def test_changing_your_password_keeps_you_signed_in_on_this_device(tmp_path):
    """`set_password` revokes every session in the same transaction (ADR-039) — including the one
    doing the changing. Without a fresh cookie the person is bounced to /login by their own success,
    which reads as "it failed" and invites them to retry with the old password."""
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    person = sign_in_member(client, ws)
    r = client.post(ACCOUNT_PW, data={"current": TEST_PASSWORD, "new": NEW_PW, "confirm": NEW_PW})
    assert r.status_code == 303
    assert client.get("/fila").status_code == 200, "changing the password signed me out of my own tab"
    assert app.state.auth.check_password(person["person_id"], NEW_PW) is True


def test_changing_your_password_signs_out_every_other_device(tmp_path):
    """The whole point of changing it under suspicion: whoever else holds a live session loses it."""
    app, ws = _app(tmp_path)
    here = TestClient(app, follow_redirects=False)
    person = sign_in_member(here, ws)
    elsewhere = TestClient(app, follow_redirects=False)
    elsewhere.cookies.set("e2d_session", app.state.auth.start_session(person["person_id"]))
    assert elsewhere.get("/fila").status_code == 200

    here.post(ACCOUNT_PW, data={"current": TEST_PASSWORD, "new": NEW_PW, "confirm": NEW_PW})
    assert elsewhere.get("/fila").status_code == 303, "the other session survived a password change"


def test_a_forced_password_change_blocks_the_app_until_it_is_done(tmp_path):
    """`must_change` existed in the schema and in AuthStore since ADR-039 and NOTHING read it — an
    admin could hand out a temporary password believing it was temporary. It now funnels."""
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    person = sign_in_member(client, ws)
    app.state.auth.set_password(person["person_id"], TEST_PASSWORD, must_change=True)
    _resume(client, app, person)

    bounced = client.get("/fila")
    assert bounced.status_code == 303 and bounced.headers["location"] == ACCOUNT
    assert client.get(ACCOUNT).status_code == 200, "the funnel does not lead anywhere"
    api = client.get("/api/fila")
    assert api.status_code == 403 and "palavra-passe" in api.json()["error"]


def test_the_forced_change_lifts_as_soon_as_the_password_changes(tmp_path):
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    person = sign_in_member(client, ws)
    app.state.auth.set_password(person["person_id"], TEST_PASSWORD, must_change=True)
    _resume(client, app, person)

    client.post(ACCOUNT_PW, data={"current": TEST_PASSWORD, "new": NEW_PW, "confirm": NEW_PW})
    assert app.state.auth.must_change_password(person["person_id"]) is False
    assert client.get("/fila").status_code == 200


def test_signing_out_stays_possible_under_a_forced_change(tmp_path):
    """A funnel that traps someone with no way out is a lockout. /logout is public, and must stay
    that way even when the gate is refusing everything else."""
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    person = sign_in_member(client, ws)
    app.state.auth.set_password(person["person_id"], TEST_PASSWORD, must_change=True)
    _resume(client, app, person)
    assert client.post("/logout").status_code == 303


def test_ending_the_other_sessions_leaves_this_one_alive(tmp_path):
    """«Terminar as outras sessões» — the answer to a laptop left signed in at a client's office,
    without having to change the password to get it."""
    app, ws = _app(tmp_path)
    here = TestClient(app, follow_redirects=False)
    person = sign_in_member(here, ws)
    elsewhere = TestClient(app, follow_redirects=False)
    elsewhere.cookies.set("e2d_session", app.state.auth.start_session(person["person_id"]))

    r = here.post("/a-minha-conta/sessoes")
    assert r.status_code == 303
    assert here.get("/fila").status_code == 200, "I logged myself out of the device I was using"
    assert elsewhere.get("/fila").status_code == 303


def test_the_account_page_carries_no_password_material(tmp_path):
    """It renders session rows and a form — never a hash, never a token."""
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    person = sign_in_member(client, ws)
    body = client.get(ACCOUNT).text
    stored = app.state.auth._conn.execute(
        "SELECT password FROM credentials WHERE person_id=?", (person["person_id"],)).fetchone()
    assert stored["password"] not in body
    assert TEST_PASSWORD not in body
    for token in client.cookies.jar:
        assert token.value not in body


# ── «Pessoas»: the admin-side people API (ADR-041) ───────────────────────────
#
# Everything after `create_person` used to require a sqlite3 prompt against workspace.db — the
# PRECIOUS store. Promoting someone, marking a leaver inactive, fixing a typo'd name: all of it was
# hand-written SQL against the one database with no rebuild path. That is the risk this closes.

PEOPLE = "/api/admin/people"


def test_the_people_list_reports_access_not_just_names(tmp_path):
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    sign_in(client, ws)
    ws.create_person("Rita", responsible=TEST_ADMIN)
    rows = {p["name"]: p for p in client.get(PEOPLE).json()["people"]}
    assert rows[TEST_ADMIN]["is_admin"] is True and rows[TEST_ADMIN]["has_credential"] is True
    assert rows["Rita"]["can_login"] is False and rows["Rita"]["has_credential"] is False
    assert rows["Rita"]["responsible"] == TEST_ADMIN


def test_a_member_cannot_read_or_change_the_roster(tmp_path):
    """Covered by the /api/admin/ prefix rather than by a decorator — the point of ADR-040 §1."""
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    sign_in_member(client, ws)
    assert client.get(PEOPLE).status_code == 403
    assert client.post(PEOPLE, json={"name": "Intruso", "access": "admin"}).status_code == 403


def test_an_admin_can_add_a_person(tmp_path):
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    sign_in(client, ws)
    r = client.post(PEOPLE, json={"name": "Diogo Santos", "access": "login"})
    assert r.status_code == 200
    assert ws.person("Diogo Santos")["can_login"] is True


def test_adding_an_assignable_person_needs_a_responsible_user(tmp_path):
    """The "never silently bin" rule applied to ownership: work assigned to someone who cannot sign
    in has to land in some signed-in person's view."""
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    sign_in(client, ws)
    r = client.post(PEOPLE, json={"name": "Rita", "access": "assign"})
    assert r.status_code == 400 and "responsável" in r.json()["error"]
    assert ws.person("Rita") is None
    ok = client.post(PEOPLE, json={"name": "Rita", "access": "assign", "responsible": TEST_ADMIN})
    assert ok.status_code == 200 and ws.person("Rita") is not None


def test_a_duplicate_name_is_refused_with_a_readable_reason(tmp_path):
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    sign_in(client, ws)
    r = client.post(PEOPLE, json={"name": TEST_ADMIN.lower(), "access": "login"})
    assert r.status_code == 400 and "existe" in r.json()["error"]


def test_promoting_and_deactivating_go_through_the_api(tmp_path):
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    sign_in(client, ws)
    other = ws.create_person("Diogo", can_login=True)
    assert client.post(f"{PEOPLE}/{other['person_id']}", json={"is_admin": True}).status_code == 200
    assert ws.person("Diogo")["is_admin"] is True
    assert client.post(f"{PEOPLE}/{other['person_id']}", json={"active": False}).status_code == 200
    assert ws.person("Diogo")["active"] is False


def test_the_api_refuses_to_leave_the_install_without_an_admin(tmp_path):
    """The store enforces it; this asserts the route surfaces the refusal instead of 500-ing."""
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    me = sign_in(client, ws)
    r = client.post(f"{PEOPLE}/{me['person_id']}", json={"is_admin": False})
    assert r.status_code == 400 and "administrador" in r.json()["error"]
    assert ws.person(TEST_ADMIN)["is_admin"] is True


def test_an_admin_cannot_deactivate_themselves_by_accident(tmp_path):
    """Distinct from the last-admin rule: even with a second admin, signing yourself out of your own
    account from the roster screen is a misclick with no undo from inside the app."""
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    me = sign_in(client, ws)
    ws.create_person("Segundo", can_login=True, is_admin=True)
    r = client.post(f"{PEOPLE}/{me['person_id']}", json={"active": False})
    assert r.status_code == 400
    assert ws.person(TEST_ADMIN)["active"] is True


def test_granting_a_scope_that_is_not_one_of_our_inboxes_is_refused(tmp_path):
    """«a typo cannot silently grant nothing». `set_person_scopes` accepts any string, so a mistyped
    address would be stored, displayed back, and grant access to no mail at all — a permission that
    looks granted and is not."""
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    sign_in(client, ws)
    other = ws.create_person("Diogo", can_login=True)
    r = client.post(f"{PEOPLE}/{other['person_id']}", json={"scopes": ["orcamento@lindo.pt"]})
    assert r.status_code == 400 and "orcamento@lindo.pt" in r.json()["error"]
    assert ws.person_scopes(other["person_id"]) == []


def test_a_known_inbox_scope_is_granted(tmp_path):
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    sign_in(client, ws)
    known = client.get(PEOPLE).json()["known_scopes"]
    assert known, "the panel cannot validate a grant it has no vocabulary for"
    other = ws.create_person("Diogo", can_login=True)
    r = client.post(f"{PEOPLE}/{other['person_id']}", json={"scopes": [known[0]]})
    assert r.status_code == 200
    assert ws.person_scopes(other["person_id"]) == [known[0]]


def test_an_invite_link_is_minted_in_the_browser(tmp_path):
    """So the token stops travelling through shell history and a chat paste — `auth invite` printed a
    single-use credential to a terminal, which is where it then stayed."""
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    sign_in(client, ws)
    other = ws.create_person("Diogo", can_login=True)
    r = client.post(f"{PEOPLE}/{other['person_id']}/convite")
    assert r.status_code == 200
    url = r.json()["url"]
    assert url.startswith("/aceitar-convite/")
    assert app.state.auth.invite_person(url.rsplit("/", 1)[-1]) == other["person_id"]


def test_an_invite_for_someone_who_cannot_sign_in_is_refused(tmp_path):
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    sign_in(client, ws)
    rita = ws.create_person("Rita", responsible=TEST_ADMIN)
    assert client.post(f"{PEOPLE}/{rita['person_id']}/convite").status_code == 400


def test_removing_a_person_with_history_is_refused_and_says_why(tmp_path):
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    sign_in(client, ws)
    other = ws.create_person("Diogo", can_login=True)
    ws.set_thread_owner("T1", "Diogo")
    r = client.delete(f"{PEOPLE}/{other['person_id']}")
    assert r.status_code == 400 and "histórico" in r.json()["error"]
    assert ws.person("Diogo") is not None


def test_removing_a_fresh_person_also_clears_their_credentials(tmp_path):
    """workspace.db and auth.db are two files joined by person_id with no foreign key between them.
    Deleting one side alone is exactly the drift `auth list` warns about."""
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    sign_in(client, ws)
    other = ws.create_person("Tpyo", can_login=True)
    app.state.auth.set_password(other["person_id"], "some-password-123")
    assert client.delete(f"{PEOPLE}/{other['person_id']}").status_code == 200
    assert ws.person("Tpyo") is None
    assert app.state.auth.has_credential(other["person_id"]) is False


def _concrete_is_admin(path: str) -> bool:
    """Mirror of webapp._is_admin_path, applied to a route template or a concrete path.

    Deliberately a MIRROR and not an import: the whole value of
    ``test_the_admin_path_set_matches_the_route_tree`` is that two independently-maintained
    statements of the admin surface must agree. Importing the real predicate would make the test
    agree with the code by construction and prove nothing.
    """
    return path in ("/admin", "/inbox") or path.startswith("/api/admin/")


def test_html_routes_redirect_to_login(tmp_path):
    app, _ws = _bootstrapped(tmp_path)
    client = TestClient(app, follow_redirects=False)
    response = client.get("/fila")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_api_routes_401_rather_than_redirect(tmp_path):
    """An API caller needs a status it can act on, not a login page with a 200."""
    app, _ws = _bootstrapped(tmp_path)
    response = TestClient(app, follow_redirects=False).get("/api/fila")
    assert response.status_code == 401
    assert "autentica" in response.json()["error"]


def test_no_triage_data_reaches_a_signed_out_visitor(tmp_path):
    """Server-rendered means the gate is real: the data is never sent, not merely hidden."""
    app, _ws = _bootstrapped(tmp_path)
    body = TestClient(app, follow_redirects=True).get("/fila").text
    assert "Entrar" in body                            # we landed on the login page
    for leak in ("thread_root", "counterparty", "lindoservico.pt"):
        assert leak not in body


def test_healthz_stays_public(tmp_path):
    """A 401 here would mark the container unhealthy and stop intake-bot with it."""
    app, _ws = _bootstrapped(tmp_path)
    assert TestClient(app).get("/healthz").status_code == 200


# ── signing in ───────────────────────────────────────────────────────────────

def test_sign_in_then_reach_the_app(tmp_path):
    app, ws = _app(tmp_path)
    client = TestClient(app)
    sign_in(client, ws)
    assert client.get("/api/fila").status_code == 200


def test_wrong_password_is_refused(tmp_path):
    app, ws = _bootstrapped(tmp_path)
    client = TestClient(app, follow_redirects=False)
    response = client.post("/login", data={"name": "Bootstrap", "password": "wrong"})
    assert response.status_code == 401
    assert client.get("/api/fila").status_code == 401


def test_unknown_and_known_failures_look_identical(tmp_path):
    """No "who works here" oracle: the page must not distinguish the failure modes."""
    app, _ws = _bootstrapped(tmp_path)
    client = TestClient(app, follow_redirects=False)
    unknown = client.post("/login", data={"name": "Ninguém", "password": "x"})
    wrong = client.post("/login", data={"name": "Bootstrap", "password": "x"})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.text == wrong.text


def test_a_non_login_person_cannot_sign_in(tmp_path):
    """Rita is assignable but has no account — a password must never be enough on its own."""
    app, ws = _bootstrapped(tmp_path)
    rita = ws.create_person("Rita", responsible="Bootstrap")
    app.state.auth.set_password(rita["person_id"], "rita-password-123")
    response = TestClient(app, follow_redirects=False).post(
        "/login", data={"name": "Rita", "password": "rita-password-123"})
    assert response.status_code == 401


def test_deactivating_a_person_ends_their_access_immediately(tmp_path):
    """Identity is re-read every request, so this must not wait for the session to expire."""
    app, ws = _app(tmp_path)
    client = TestClient(app)
    person = sign_in(client, ws)
    assert client.get("/api/fila").status_code == 200
    ws._conn.execute("UPDATE people SET active = 0 WHERE person_id = ?", (person["person_id"],))
    ws._conn.commit()
    assert client.get("/api/fila").status_code == 401


def test_logout_kills_the_session_server_side(tmp_path):
    """Not just the cookie: a copied token must stop working too."""
    app, ws = _app(tmp_path)
    client = TestClient(app)
    sign_in(client, ws)
    token = client.cookies.get("e2d_session")
    client.post("/logout", follow_redirects=False)

    replay = TestClient(app, follow_redirects=False)
    replay.cookies.set("e2d_session", token)
    assert replay.get("/api/fila").status_code == 401


def test_session_cookie_is_httponly_and_samesite_strict(tmp_path):
    app, ws = _bootstrapped(tmp_path)
    client = TestClient(app, follow_redirects=False)
    response = client.post("/login", data={"name": "Bootstrap", "password": "bootstrap-pw-123"})
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    # secure follows the live scheme, so it is absent on plain-HTTP loopback (where it would drop
    # the cookie) and present under opt-in TLS.
    assert "secure" not in cookie


def test_next_url_round_trips_a_deep_link(tmp_path):
    app, ws = _bootstrapped(tmp_path)
    client = TestClient(app, follow_redirects=False)
    assert "next=%2Fprojetos" in client.get("/projetos").headers["location"]
    response = client.post("/login", data={"name": "Bootstrap", "password": "bootstrap-pw-123",
                                           "next": "/projetos"})
    assert response.headers["location"] == "/projetos"


def test_next_url_carries_the_query_string_so_a_deep_link_survives_the_login(tmp_path):
    """A signed-out click on a thread deep link must come back to THAT thread, not to a bare queue.

    The gate sent only ``request.url.path``, so /fila?thread=<root> became next=%2Ffila and the
    person landed on an unfiltered Fila — which reads as "the link is broken", not "you were logged
    out". ``build_login_html`` already promised this round-trip in its docstring; the middleware was
    what could not deliver it. Note the asymmetry that hid this: the client-side expiry overlay
    (cockpit_ui) always preserved location.search, so it only ever failed on a COLD arrival."""
    app, _ws = _bootstrapped(tmp_path)
    client = TestClient(app, follow_redirects=False)

    location = client.get("/fila?thread=mid:t1&tab=CLIENT").headers["location"]
    assert location == "/login?next=%2Ffila%3Fthread%3Dmid%3At1%26tab%3DCLIENT"
    from urllib.parse import parse_qs, urlparse
    assert parse_qs(urlparse(location).query)["next"] == ["/fila?thread=mid:t1&tab=CLIENT"]

    # …and the form hands it back intact, so the deep link actually completes.
    assert 'value="/fila?thread=mid:t1&amp;tab=CLIENT"' in client.get(location).text
    response = client.post("/login", data={"name": "Bootstrap", "password": "bootstrap-pw-123",
                                           "next": "/fila?thread=mid:t1&tab=CLIENT"})
    assert response.headers["location"] == "/fila?thread=mid:t1&tab=CLIENT"


def test_a_query_string_cannot_smuggle_an_off_site_next(tmp_path):
    """Carrying the query widened what reaches _safe_next, so the open-redirect guard is re-pinned
    against the new shape: the value still has to be a same-site absolute path."""
    app, _ws = _bootstrapped(tmp_path)
    client = TestClient(app, follow_redirects=False)
    for hostile in ("/fila?next=//evil.example", "/fila?x=https://evil.example"):
        response = client.post("/login", data={"name": "Bootstrap",
                                               "password": "bootstrap-pw-123", "next": hostile})
        # Same-site path with a harmless query: honoured, and still pointed at our own origin.
        assert response.headers["location"].startswith("/fila?")
    for hostile in ("//evil.example/?a=/fila", "https://evil.example/?next=/fila"):
        response = client.post("/login", data={"name": "Bootstrap",
                                               "password": "bootstrap-pw-123", "next": hostile})
        assert response.headers["location"] == "/", f"open redirect via {hostile!r}"


def test_open_redirect_is_refused(tmp_path):
    """"//evil.example" is protocol-relative — browsers treat it as another origin, so a bare
    startswith("/") check would leak the login into an off-site redirect."""
    app, _ws = _bootstrapped(tmp_path)
    client = TestClient(app, follow_redirects=False)
    for hostile in ("//evil.example", "https://evil.example", "http://evil.example/x"):
        response = client.post("/login", data={"name": "Bootstrap",
                                               "password": "bootstrap-pw-123", "next": hostile})
        assert response.headers["location"] == "/", f"open redirect via {hostile!r}"


def test_api_me_reports_identity_and_scopes(tmp_path):
    app, ws = _app(tmp_path)
    client = TestClient(app)
    person = sign_in(client, ws)
    ws.set_person_scopes(person["person_id"], ["orcamentos@lindoservico.pt"])
    body = client.get("/api/me").json()
    assert body["name"] == TEST_ADMIN and body["is_admin"] is True
    assert body["scopes"] == ["orcamentos@lindoservico.pt"]


# ── first-run setup ──────────────────────────────────────────────────────────

def test_virgin_install_funnels_to_setup(tmp_path):
    app, _ws = _app(tmp_path)
    response = TestClient(app, follow_redirects=False).get("/fila")
    assert response.status_code == 303 and response.headers["location"] == "/setup"


def test_api_gets_a_status_it_can_act_on_before_setup(tmp_path):
    """An API client cannot follow a 303 to an HTML form; it needs 401, not a redirect."""
    app, _ws = _app(tmp_path)
    response = TestClient(app, follow_redirects=False).get("/api/fila")
    assert response.status_code == 401
    assert "/setup" in response.json()["error"]


def test_setup_creates_the_first_admin_and_signs_them_in(tmp_path):
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    response = client.post("/setup", data={"name": "Filipe", "password": "a-good-password",
                                           "confirm": "a-good-password"})
    assert response.status_code == 303 and response.headers["location"] == "/"
    person = ws.person("Filipe")
    assert person["is_admin"] is True and person["can_login"] is True
    assert client.get("/api/fila").status_code == 200


def test_setup_disappears_once_a_credential_exists(tmp_path):
    """Otherwise it is a permanent unauthenticated admin-creation endpoint."""
    app, _ws = _bootstrapped(tmp_path)
    client = TestClient(app, follow_redirects=False)
    assert client.get("/setup").status_code == 404
    assert client.post("/setup", data={"name": "Intruso", "password": "12345678",
                                       "confirm": "12345678"}).status_code == 404


def test_setup_validates_its_input(tmp_path):
    app, _ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    for data, expect in (
        ({"name": "", "password": "a-good-password", "confirm": "a-good-password"}, "nome"),
        ({"name": "F", "password": "short", "confirm": "short"}, "8 caracteres"),
        ({"name": "F", "password": "a-good-password", "confirm": "different"}, "não coincidem"),
    ):
        response = client.post("/setup", data=data)
        assert response.status_code == 400 and expect in response.text


def test_setup_folds_the_configured_team_into_the_people_roster(tmp_path):
    """ADR-041/W8. On a real first boot there is no admin yet — that is what /setup is for — so the
    construction-time backfill is always a no-op. Without a second run here, `settings.team` would
    sit unmigrated and the owner picker would open EMPTY for the whole first session, until somebody
    happened to restart the container."""
    ws = Workspace(tmp_path / "w.db").connect()
    app = webapp.create_app({**SETTINGS, "team": ["Pedro", "Rita"]}, workspace=ws, jobspecs={},
                            reply_pb="pb", prepared=([], [], {}))
    client = TestClient(app, follow_redirects=False)
    assert ws.people() == []                                  # nothing to be accountable yet
    client.post("/setup", data={"name": "Filipe", "password": "a-good-password",
                                "confirm": "a-good-password"})
    assert {p["name"] for p in ws.people()} == {"Filipe", "Pedro", "Rita"}
    assert set(client.get("/api/roster").json()["roster"]) == {"Filipe", "Pedro", "Rita"}


def test_an_authenticated_user_is_never_bounced_to_setup(tmp_path):
    """Regression: the first-run funnel used to run before the session check."""
    app, ws = _app(tmp_path)
    client = TestClient(app)
    sign_in(client, ws)
    assert client.get("/api/fila").status_code == 200


# ── invites ──────────────────────────────────────────────────────────────────

def test_invite_lets_a_person_set_their_own_password_and_signs_them_in(tmp_path):
    app, ws = _bootstrapped(tmp_path)
    pedro = ws.create_person("Pedro", can_login=True)
    token = app.state.auth.create_invite(pedro["person_id"], created_by="Bootstrap")

    client = TestClient(app, follow_redirects=False)
    assert "Pedro" in client.get(f"/aceitar-convite/{token}").text
    response = client.post("/aceitar-convite", data={"token": token, "password": "pedro-password",
                                                     "confirm": "pedro-password"})
    assert response.status_code == 303
    assert client.get("/api/fila").status_code == 200


def test_a_used_invite_is_dead(tmp_path):
    app, ws = _bootstrapped(tmp_path)
    pedro = ws.create_person("Pedro", can_login=True)
    token = app.state.auth.create_invite(pedro["person_id"], created_by="Bootstrap")
    client = TestClient(app, follow_redirects=False)
    client.post("/aceitar-convite", data={"token": token, "password": "pedro-password",
                                          "confirm": "pedro-password"})
    second = client.post("/aceitar-convite", data={"token": token, "password": "other-password",
                                                   "confirm": "other-password"})
    assert second.status_code in (404, 409)
    assert app.state.auth.check_password(pedro["person_id"], "pedro-password") is True


def test_an_unknown_invite_says_so_rather_than_bouncing_to_login(tmp_path):
    app, _ws = _bootstrapped(tmp_path)
    response = TestClient(app, follow_redirects=False).get("/aceitar-convite/not-a-token")
    assert response.status_code == 404 and "inválido" in response.text


def test_invite_validates_its_input(tmp_path):
    app, ws = _bootstrapped(tmp_path)
    pedro = ws.create_person("Pedro", can_login=True)
    token = app.state.auth.create_invite(pedro["person_id"], created_by="Bootstrap")
    client = TestClient(app, follow_redirects=False)
    response = client.post("/aceitar-convite", data={"token": token, "password": "short",
                                                     "confirm": "short"})
    assert response.status_code == 400
    # the invite must survive a rejected attempt
    assert app.state.auth.invite_person(token) == pedro["person_id"]


def test_the_login_page_carries_no_app_data(tmp_path):
    app, _ws = _bootstrapped(tmp_path)
    body = TestClient(app).get("/login").text
    assert "Entrar" in body
    assert "fila" not in body.lower().replace("perfil", "")
    assert TEST_PASSWORD not in body


# ── «Pessoas»: the reset destination (ADR-042) ───────────────────────────────

def test_an_admin_can_set_and_clear_a_persons_reset_address(tmp_path):
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    sign_in(client, ws)
    person = ws.create_person("Diogo", can_login=True)
    url = f"{PEOPLE}/{person['person_id']}"

    assert client.post(url, json={"email": "Diogo@LindoServico.PT"}).status_code == 200
    assert ws.person("Diogo")["email"] == "diogo@lindoservico.pt"
    # Clearing is a real intent ("no address on file"), not a no-op.
    assert client.post(url, json={"email": ""}).status_code == 200
    assert ws.person("Diogo")["email"] == ""


def test_a_malformed_address_is_refused_with_a_readable_reason(tmp_path):
    """Rule (1) of the «Pessoas» section: a refusal is SHOWN, not flattened to «falhou»."""
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    sign_in(client, ws)
    person = ws.create_person("Diogo", can_login=True)
    r = client.post(f"{PEOPLE}/{person['person_id']}", json={"email": "not-an-address"})
    assert r.status_code == 400 and "inválido" in r.json()["error"]


def test_two_people_cannot_share_one_reset_address(tmp_path):
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    sign_in(client, ws)
    first = ws.create_person("Diogo", can_login=True)
    second = ws.create_person("Marta", can_login=True)
    client.post(f"{PEOPLE}/{first['person_id']}", json={"email": "partilhado@lindoservico.pt"})
    r = client.post(f"{PEOPLE}/{second['person_id']}", json={"email": "partilhado@lindoservico.pt"})
    assert r.status_code == 400 and "Diogo" in r.json()["error"]


def test_the_roster_payload_carries_the_address_but_no_secret(tmp_path):
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    sign_in(client, ws)
    person = ws.create_person("Diogo", can_login=True)
    ws.set_person_email(person["person_id"], "diogo@lindoservico.pt")
    rows = {p["name"]: p for p in client.get(PEOPLE).json()["people"]}
    assert rows["Diogo"]["email"] == "diogo@lindoservico.pt"
    blob = client.get(PEOPLE).text.lower()
    for leak in ("scrypt", "token_hash", "password\":"):
        assert leak not in blob


def test_a_member_cannot_set_anyones_address(tmp_path):
    """The address decides where a credential-bearing link goes, so writing it is an admin act."""
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    sign_in_member(client, ws)
    person = ws.create_person("Diogo", can_login=True)
    assert client.post(f"{PEOPLE}/{person['person_id']}",
                       json={"email": "atacante@exemplo.pt"}).status_code == 403


# ── ADR-047 — the signature editor is a person's own surface ──────────────────

ACCOUNT_SIG = "/a-minha-conta/assinatura"


def test_the_signature_route_is_closed_to_a_stranger(tmp_path):
    """Protected by the middleware's closed allowlist, not by remembering a decorator on a new
    route — the non-negotiable this whole module exists to pin."""
    app, _ws = _bootstrapped(tmp_path)          # past first-run setup, so /login is the real door
    anon = TestClient(app, follow_redirects=False)
    r = anon.post(ACCOUNT_SIG, data={"signature": "Abraço,\n{nome}"})
    assert r.status_code == 303 and "/login" in r.headers["location"]


def test_a_member_can_set_their_own_signature(tmp_path):
    """No id in the URL and none in the form, so "can I edit someone else's signature?" is not a
    question this surface can be asked."""
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    person = sign_in_member(client, ws)
    r = client.post(ACCOUNT_SIG, data={"signature": "Abraço,\n{nome} · {cargo}",
                                       "job_title": "Produção", "phone": "912345678"})
    assert r.status_code == 303 and r.headers["location"].endswith("?ok=sig")
    row = ws.person_by_id(person["person_id"])
    assert row["signature"] == "Abraço,\n{nome} · {cargo}"
    assert row["job_title"] == "Produção" and row["phone"] == "912345678"
    assert "Assinatura guardada." in client.get(ACCOUNT + "?ok=sig").text


def test_a_signature_with_an_unfillable_token_is_refused_and_echoed_back(tmp_path):
    """400 with the offending token named, and the textarea still holding what they typed — an error
    beside a box that has reverted to the old value makes the person retype the whole thing."""
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    person = sign_in_member(client, ws)
    r = client.post(ACCOUNT_SIG, data={"signature": "Abraço,\n{telemovel}",
                                       "job_title": "Produção"})
    assert r.status_code == 400
    assert "{telemovel}" in r.text
    assert "Abraço," in r.text and "Produção" in r.text
    row = ws.person_by_id(person["person_id"])
    assert row["signature"] == "" and row["job_title"] == "", "a rejected save wrote anyway"


def test_the_signature_route_cannot_change_the_reset_address(tmp_path):
    """ADR-042 boundary: `{email}` renders people.email, but a stolen session that could rewrite it
    becomes a permanent takeover instead of a walk-up. An admin sets it, not this form."""
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    person = sign_in_member(client, ws)
    ws.set_person_email(person["person_id"], "membro@lindoservico.pt")
    client.post(ACCOUNT_SIG, data={"signature": "Abraço,\n{nome}",
                                   "email": "atacante@example.com"})
    assert ws.person_by_id(person["person_id"])["email"] == "membro@lindoservico.pt"


def test_the_signature_editor_is_reachable_during_the_forced_password_change(tmp_path):
    """The funnel exempts the WHOLE of «A minha conta» (one rule, in the gate). Narrowing it here
    would put a second, differently-shaped rule beside it — which is how funnels grow holes."""
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    person = sign_in_member(client, ws)
    app.state.auth.set_password(person["person_id"], TEST_PASSWORD, must_change=True)
    _resume(client, app, person)
    assert client.get("/fila").status_code == 303          # the funnel IS holding everything else
    assert client.post(ACCOUNT_SIG, data={"signature": "Abraço,\n{nome}"}).status_code == 303
    assert ws.person_by_id(person["person_id"])["signature"] == "Abraço,\n{nome}"


def test_pasting_an_outlook_signature_is_converted_and_said_out_loud(tmp_path):
    """ADR-047 §10, found by looking at the rendered page rather than by a test: a real signature is
    pasted out of Outlook as HTML, and stored verbatim it put `<td style="…">` in a client's inbox.

    The banner is load-bearing — the textarea now holds something different from what the person
    pasted, and a save that stays quiet about that reads as the app having mangled their signature."""
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    person = sign_in_member(client, ws)
    r = client.post(ACCOUNT_SIG, data={"signature":
                                       '<div style="font-weight:700">FILIPE COELHO</div>'
                                       '<div>Departamento T&eacute;cnico</div>'
                                       '<div><img src="logo.png" alt="LINDO SERVI&Ccedil;O"></div>'})
    assert r.status_code == 303 and r.headers["location"].endswith("?ok=sightml")
    assert ws.person_by_id(person["person_id"])["signature"] == \
        "FILIPE COELHO\nDepartamento Técnico"
    page = client.get(ACCOUNT + "?ok=sightml").text
    assert "HTML" in page and "convert" in page.lower()


def test_an_image_only_paste_is_refused_at_the_route_too(tmp_path):
    """It flattens to nothing; storing '' would silently revert them to the install default."""
    app, ws = _app(tmp_path)
    client = TestClient(app, follow_redirects=False)
    person = sign_in_member(client, ws)
    r = client.post(ACCOUNT_SIG, data={"signature": '<table><tr><td><img src="s.png"></td></tr></table>'})
    assert r.status_code == 400 and "só imagens" in r.text
    assert ws.person_by_id(person["person_id"])["signature"] == ""
