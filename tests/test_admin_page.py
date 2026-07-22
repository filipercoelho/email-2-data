"""The Administração page (/admin) — the account inventory, the force-sync control, and the account
editor. TestClient can't run JS, so these assert the two things a static render CAN prove:

  1. the SECRET invariant — no password/token value ever reaches the HTML, whatever the API hands
     over (the projection is an allowlist, and `password_env` is a NAME that must look like one);
  2. the lens contract + pt-PT copy the webapp wires up (cockpit consts, render/paletteItems/onKey,
     the API endpoints, the fetch-only vs fetch+classify modes).

These call ``admin_page.build_html`` directly — the page is a pure function of its inputs, so no app
fixture is needed (same style as ``test_captures_page.test_build_html_neutralizes_untrusted_text``).
"""

from __future__ import annotations

from email2data import admin_page

SECRET = "sup3r-s3cr3t-imap-pw"

# A deliberately hostile account payload: real mailbox names (quotes, ampersands, apostrophes,
# modified-UTF7), plus every way an over-generous API could hand us a credential.
ACCOUNT = {
    "id": "orcamentos",
    "username": "orcamentos@lindoservico.pt",
    "host": "mail.lindoservico.pt",
    "port": 993,
    "mailboxes": [
        "INBOX",
        "INBOX.Trash.clientes.B&APw-rocratik",
        "INBOX.Trash.clientes.Violaine d'Harcourt - 1111",
        'INBOX."aspas"',
        "INBOX.<script>alert(1)</script>",
    ],
    "password_env": "EMAIL2DATA_ORCAMENTOS_PASSWORD",
    "credential_present": True,
    "last_sync": "2026-07-19T21:30:00+00:00",
    "cursors": [{"mailbox": "INBOX", "uidvalidity": 1234567, "last_uid": 8891,
                 "updated_ts": "2026-07-19T21:30:00+00:00"}],
    "errors": [{"ts": "2026-07-19T20:00:00+00:00", "mailbox": "INBOX.Archive",
                "error": "[AUTHENTICATIONFAILED] Authentication failed."}],
}


def test_no_secret_value_can_reach_the_rendered_page():
    # The page must survive an API that leaks: a resolved password on the account, a password in the
    # sync payload, and a secret pasted into password_env. The projection is an ALLOWLIST, so all
    # three are dropped — a grep of the HTML for the literal secret must find nothing.
    leaky = dict(ACCOUNT, password=SECRET, imap_password=SECRET, token=SECRET,
                 credential_present=True)
    html = admin_page.build_html(
        [leaky],
        {"running": False, "last": {"ts": "2026-07-19T21:30:00+00:00",
                                    "counts": {"fetched": 3, "password": SECRET},
                                    "error": ""}},
    )
    assert SECRET not in html
    # ...and the page still shows the two things it IS allowed to show: the env var NAME + the bool.
    assert "EMAIL2DATA_ORCAMENTOS_PASSWORD" in html
    assert '"credential_present": true' in html
    # no password input is rendered anywhere, by design
    assert 'type="password"' not in html


def test_password_env_that_is_not_an_identifier_is_suppressed():
    # A pasted secret does not look like an env var name. It must be suppressed, not echoed back,
    # and the UI must be told the configured name is invalid (so it can't be silently re-saved).
    html = admin_page.build_html([dict(ACCOUNT, password_env=SECRET)])
    assert SECRET not in html
    assert '"password_env": ""' in html
    assert '"password_env_invalid": true' in html


def test_sync_counts_keep_numbers_only():
    # Counts are the one channel where server-supplied values are rendered verbatim. Restricting
    # them to numbers means no string — and therefore no secret — can ride in through it.
    view = admin_page._sync_view({"running": True,
                                  "last_counts": {"fetched": 7, "leaked": SECRET, "ok": True}})
    assert view["running"] is True
    assert view["last"]["counts"] == {"fetched": 7}      # the str AND the bool are dropped


def test_flat_sync_status_shape_is_normalised():
    # /api/sync/status speaks {running, last_counts, last_error}; /api/admin/accounts speaks
    # {running, last:{...}}. Both must normalise to the one shape the lens renders.
    view = admin_page._sync_view({"running": False, "last_counts": {"fetched": 2},
                                  "last_error": "IMAP timeout", "last_ts": "2026-07-19T10:00:00"})
    assert view["last"]["counts"] == {"fetched": 2}
    assert view["last"]["error"] == "IMAP timeout"
    assert view["last"]["ts"] == "2026-07-19T10:00:00"


def test_hostile_mailbox_names_survive_the_embed_intact():
    # Real mailbox names carry & " ' and modified-UTF7. They ride in as JSON string DATA (inert
    # HTML) and the lens wraps them in esc() before touching innerHTML.
    html = admin_page.build_html([ACCOUNT])
    assert r"INBOX.Trash.clientes.B&APw-rocratik" in html
    assert r"Violaine d'Harcourt - 1111" in html
    # the "</" escape stops a </script> payload breaking out of the embed
    assert "<script>alert(1)</script>" not in html
    assert r"<\/script>" in html
    # every rendered mailbox goes through esc()
    assert "esc(m)" in html


def test_page_ships_the_lens_contract_and_the_admin_apis():
    html = admin_page.build_html([ACCOUNT], {"running": False, "last": {}})
    for marker in ("const ACCOUNTS =", "const SYNC =", "const COUNT_LABELS =",
                   "function render(", "function paletteItems(", "function onKey("):
        assert marker in html, marker
    for marker in ("/api/admin/accounts", "/api/sync", "/api/sync/status",
                   "do_fetch", "do_triage", "account_id"):
        assert marker in html, marker


def test_pt_pt_copy_and_the_two_sync_modes():
    html = admin_page.build_html([ACCOUNT])
    for s in ("Administração", "Contas de email", "Sincronizar agora", "Última sync",
              "Só buscar", "Buscar + classificar", "Editar contas", "Guardar contas",
              "Adicionar conta", "Remover conta", "Caixas de correio", "credencial presente"):
        assert s in html, s
    # the fetch-only mode must be the DEFAULT selected mode (no LLM spend unless asked)
    assert "_mode = 'fetch'" in html
    assert "do_triage: (_mode === 'full')" in html


def test_sync_button_is_disabled_while_a_sync_runs():
    html = admin_page.build_html([ACCOUNT], {"running": True})
    assert "const dis = running ? ' disabled' : '';" in html
    assert "if(sync.running || _busy) return;" in html          # and the handler double-checks
    assert '"running": true' in html                            # the running state is shipped


def test_no_destructive_or_imap_mutating_control_is_offered():
    # Non-negotiables #1 and #6: read-only IMAP, and workspace.db is never reset from the UI.
    html = admin_page.build_html([ACCOUNT]).lower()
    for forbidden in ("esvaziar", "marcar como lida", "apagar mensagens", "reconstruir",
                      "workspace.db", "expunge", "/api/reset"):
        assert forbidden not in html, forbidden


def test_cursors_and_errors_render_per_account():
    html = admin_page.build_html([ACCOUNT])
    assert "function cursorsHTML" in html and "function errorsHTML" in html
    assert "last_uid" in html and "uidvalidity" in html and "8891" in html
    assert "Authentication failed." in html          # the recent fetch error is surfaced
    assert "Cursores de leitura" in html


def test_malformed_input_does_not_explode():
    # Empty/None/garbage: a missing account list, a non-dict account, a bare-string error, a bad
    # port — the page must still render rather than 500 the whole /admin route.
    html = admin_page.build_html([None, {"id": "x", "port": "not-a-port", "errors": ["boom"]}], None)
    assert "const ACCOUNTS =" in html
    assert '"port": null' in html
    assert "boom" in html
    assert admin_page.build_html([], {}) .count("const ACCOUNTS = []") == 1
