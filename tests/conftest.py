"""Shared test helpers — chiefly: how a test client gets past the ADR-039 auth gate.

The gate is default-deny and there is **no test bypass**, deliberately. An env var that switched auth
off would mean the 216 pre-existing route assertions proved nothing about it; making them log in for
real turns every one of them into an implicit "the gate lets a legitimate user through" assertion,
and any regression that breaks sign-in fails the whole suite loudly.

Use :func:`sign_in` right after building a ``TestClient``.
"""

from __future__ import annotations

from typing import Any

TEST_ADMIN = "Teste Admin"
TEST_MEMBER = "Teste Membro"
TEST_PASSWORD = "test-password-1234"


def sign_in(client: Any, workspace: Any = None, *, name: str = TEST_ADMIN,
            password: str = TEST_PASSWORD, is_admin: bool = True) -> dict[str, Any]:
    """Create ``name`` as a login-capable person, set a password, and sign ``client`` in.

    Returns the person row. The session cookie lands in the client's jar, so every later request on
    that client is authenticated. Idempotent — calling twice re-uses the person and re-logs in.

    ``is_admin`` applies only when the person is CREATED here. Pass a distinct ``name`` when you want
    a non-admin, or the default ``TEST_ADMIN`` row is simply re-used and the flag silently does
    nothing — which is what :func:`sign_in_member` exists to prevent.
    """
    workspace = workspace if workspace is not None else client.app.state.workspace
    person = workspace.person(name)
    if person is None:
        person = workspace.create_person(name, can_login=True, is_admin=is_admin)
    assert bool(person["is_admin"]) is bool(is_admin), (
        f"{name!r} already exists with is_admin={person['is_admin']} but the test asked for "
        f"is_admin={is_admin} — an authorization test that silently gets the wrong role proves the "
        f"opposite of what it claims. Use a distinct name.")
    auth = client.app.state.auth
    auth.set_password(person["person_id"], password)
    response = client.post("/login", data={"name": name, "password": password},
                           follow_redirects=False)
    assert response.status_code == 303, (
        f"test sign-in failed ({response.status_code}) — the auth gate or the login route changed")
    return person


def sign_in_member(client: Any, workspace: Any = None, *, name: str = TEST_MEMBER,
                   scopes: list[str] | None = None, **kwargs: Any) -> dict[str, Any]:
    """Sign in an ordinary, NON-admin person (ADR-040), optionally with inbox grants (ADR-045).

    The counterpart the suite lacked: every existing test signed in as an admin, so nothing
    distinguished "authenticated" from "allowed" and the whole authorization layer could have been
    absent without a single failure. Its own default name, because reusing ``TEST_ADMIN`` would hand
    back the admin row and quietly turn a permission test into a no-op.

    ``scopes`` exists because the same trap reappeared one layer down for **visibility**. A member
    with no grants sees nothing, so a test that signs one in and asserts ``200`` stays green while
    every lens renders empty — which is exactly the ADR-040 dishonest-refusal failure reborn. Pass
    the grants the test means to exercise, and assert on CONTENT, never on the status code alone.
    ``None`` means "grant nothing", which is a legitimate case to test — but test it deliberately.
    """
    workspace = workspace if workspace is not None else client.app.state.workspace
    person = sign_in(client, workspace, name=name, is_admin=False, **kwargs)
    if scopes is not None:
        workspace.set_person_scopes(person["person_id"], list(scopes))
        person = workspace.person_by_id(person["person_id"])
    return person


def signed_in_client(client: Any, workspace: Any = None, **kwargs: Any) -> Any:
    """``sign_in`` but returns the client, for one-line use at a construction site."""
    sign_in(client, workspace, **kwargs)
    return client


# ── live-server (e2e) auth ───────────────────────────────────────────────────
#
# The browser e2e tests drive a real uvicorn server, so they need a real session. The token is minted
# directly off the app's AuthStore rather than by POSTing /login: it is one call, needs no HTTP
# round-trip during fixture setup, and exercises the same session rows the gate reads.

E2E_COOKIE = "e2d_session"


def e2e_sign_in(app: Any, *, name: str = TEST_ADMIN) -> str:
    """Create an admin on the live app and return a raw session token (the cookie value)."""
    workspace = app.state.workspace
    person = workspace.person(name) or workspace.create_person(
        name, can_login=True, is_admin=True)
    # Set a password too: a session without a credential is a state real login can never produce,
    # and leaving it out made has_any_credentials() False and the whole app funnel to /setup.
    app.state.auth.set_password(person["person_id"], TEST_PASSWORD)
    return app.state.auth.start_session(person["person_id"])


def e2e_headers(token: str, extra: dict | None = None) -> dict:
    """Request headers carrying the session cookie, for the raw urllib calls in the e2e fixtures."""
    headers = dict(extra or {})
    if token:
        headers["Cookie"] = f"{E2E_COOKIE}={token}"
    return headers


class AuthedBrowser:
    """Wraps a Playwright ``Browser`` so every ``new_page()`` carries the session cookie.

    Keeps the existing ``browser.new_page()`` call sites unchanged while making them authenticated —
    without this the gate would bounce each page to /login and every assertion would fail on the
    login markup rather than on what it meant to test. The token is read lazily because the browser
    fixture is built before the server fixture has minted one.
    """

    def __init__(self, browser: Any, token_getter: Any) -> None:
        self._browser = browser
        self._token_getter = token_getter

    def new_page(self, **kwargs: Any) -> Any:
        context = self._browser.new_context(**kwargs)
        token = self._token_getter()
        if token:
            context.add_cookies([{"name": E2E_COOKIE, "value": token,
                                  "domain": "127.0.0.1", "path": "/"}])
        return context.new_page()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._browser, name)
