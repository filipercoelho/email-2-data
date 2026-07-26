"""CLI port-binding policy: silent rebind is fine on localhost, but in container mode (--host 0.0.0.0)
a busy port must fail loudly — the published compose port (8042:8042) has no listener otherwise."""

import argparse
import json

from email2data import cli


def test_serve_port_free_passes_through(monkeypatch):
    monkeypatch.setattr(cli, "_free_port", lambda p: p)
    assert cli._resolve_serve_port(8042, "0.0.0.0") == (8042, None)


def test_serve_port_localhost_rebinds_with_note(monkeypatch):
    monkeypatch.setattr(cli, "_free_port", lambda p: 9999)        # 8042 busy → OS picks another
    port, note = cli._resolve_serve_port(8042, "127.0.0.1")
    assert port == 9999 and "using 9999" in note


def test_serve_port_container_refuses_to_rebind(monkeypatch):
    monkeypatch.setattr(cli, "_free_port", lambda p: 9999)
    port, note = cli._resolve_serve_port(8042, "0.0.0.0")
    assert port is None and "refusing to rebind" in note         # fail loud, not a dead published port


def test_cmd_serve_aborts_without_binding_in_container_mode(monkeypatch):
    """The fix that matters operationally: when _resolve_serve_port refuses (container mode, busy port)
    cmd_serve must return 1 and NEVER build the app or call uvicorn.run (a dead published port)."""
    import argparse

    import pytest
    pytest.importorskip("uvicorn")
    import uvicorn

    from email2data import webapp

    monkeypatch.setattr(cli, "_resolve_serve_port", lambda port, host: (None, "refusing to rebind"))
    served = {"hit": False}
    monkeypatch.setattr(webapp, "from_settings", lambda s: served.__setitem__("hit", True))
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: served.__setitem__("hit", True))

    rc = cli.cmd_serve(argparse.Namespace(port=8042, host="0.0.0.0", settings="config/settings.json"))
    assert rc == 1 and served["hit"] is False


# ── `email2data auth setup` — the brick guard (ADR-039/W10) ──────────────────
#
# Setting a password flips AuthStore.has_any_credentials() to True, which permanently 404s /setup —
# the ONLY unauthenticated way to mint the first admin. Do that to someone who cannot sign in and the
# install has nobody who can log in and no way to create anybody: unrecoverable short of deleting
# auth.db by hand. The webapp's /setup route has refused this since ADR-039; the CLI path did not,
# and the roster is full of assignable-only people whose names are exactly what you would type here.


def _auth_args(tmp_path, action, **kw):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"paths": {"out_dir": "out", "corpus_dir": "corpus",
                                              "captures_dir": "captures"}}), encoding="utf-8")
    return argparse.Namespace(action=action, settings=str(settings), name=kw.pop("name", "Filipe"),
                              **kw)


def _stores(tmp_path):
    from email2data.auth import AuthStore
    from email2data.workspace import Workspace
    out = tmp_path / "out"
    out.mkdir(parents=True, exist_ok=True)      # config.paths() makes this, but not before cmd_auth runs
    return Workspace(out / "workspace.db"), AuthStore(out / "auth.db")


def test_auth_setup_refuses_to_brick_the_install_on_a_non_login_person(tmp_path, monkeypatch,
                                                                      capsys):
    """Rita is assignable-only. Giving her the first password would close /setup for good.

    The state below is not hypothetical — it is precisely what a partial restore leaves behind:
    workspace.db back with its full roster, auth.db missing, so `has_any_credentials()` is False and
    the app is at first-run again. Whoever is recovering types a name from `auth list`. Pick the
    wrong one and the recovery is what bricks the install.
    """
    import getpass

    ws, auth = _stores(tmp_path)
    ws.connect()
    ws.create_person("Filipe", can_login=True, is_admin=True)   # roster survived...
    ws.create_person("Rita", responsible="Filipe")              # ...auth.db did not
    ws.close()

    monkeypatch.setattr(getpass, "getpass", lambda *_a, **_k: "a-good-password")
    rc = cli.cmd_auth(_auth_args(tmp_path, "setup", name="Rita"))

    assert rc == 2, "the CLI created a credential for someone who cannot sign in"
    assert "administrador" in capsys.readouterr().err.lower()
    auth.connect()
    assert auth.has_any_credentials() is False, "/setup is now closed and nobody can log in"
    auth.close()


def test_auth_setup_refuses_a_non_admin_who_can_log_in(tmp_path, monkeypatch, capsys):
    """can_login alone is not enough: a non-admin first account leaves nobody able to promote."""
    import getpass

    ws, auth = _stores(tmp_path)
    ws.connect()
    ws.create_person("Pedro", can_login=True, is_admin=False)
    ws.close()

    monkeypatch.setattr(getpass, "getpass", lambda *_a, **_k: "a-good-password")
    assert cli.cmd_auth(_auth_args(tmp_path, "setup", name="Pedro")) == 2
    auth.connect()
    assert auth.has_any_credentials() is False
    auth.close()


def test_auth_setup_still_creates_a_brand_new_admin(tmp_path, monkeypatch, capsys):
    """The guard must not break the path it protects — the ordinary virgin install."""
    import getpass

    monkeypatch.setattr(getpass, "getpass", lambda *_a, **_k: "a-good-password")
    assert cli.cmd_auth(_auth_args(tmp_path, "setup", name="Filipe")) == 0

    ws, auth = _stores(tmp_path)
    ws.connect()
    auth.connect()
    person = ws.person("Filipe")
    assert person["is_admin"] and person["can_login"]
    assert auth.check_password(person["person_id"], "a-good-password") is True
    auth.close()
    ws.close()


# ── `email2data auth reset` — the temporary password (ADR-041) ───────────────
#
# The recovery path for a forgotten password was `auth invite`, which mints a fresh onboarding token
# for someone already onboarded. `reset` is the honest verb, and it is the only producer of the
# `must_change` flag the webapp funnel consumes — without it that flag stays the dead column it has
# been since ADR-039.


def test_auth_reset_hands_out_a_password_that_must_be_changed(tmp_path, monkeypatch, capsys):
    import getpass

    ws, auth = _stores(tmp_path)
    ws.connect()
    person = ws.create_person("Diogo", can_login=True)
    ws.close()

    monkeypatch.setattr(getpass, "getpass", lambda *_a, **_k: "temporary-pw-123")
    assert cli.cmd_auth(_auth_args(tmp_path, "reset", name="Diogo")) == 0

    auth.connect()
    assert auth.check_password(person["person_id"], "temporary-pw-123") is True
    assert auth.must_change_password(person["person_id"]) is True, (
        "a temporary password that is not marked temporary is just a password an admin knows")
    auth.close()


def test_auth_reset_ends_the_sessions_it_replaces(tmp_path, monkeypatch):
    """Resetting under suspicion has to kick whoever is already in — otherwise the reset only adds a
    second way in beside the one you were worried about."""
    import getpass

    ws, auth = _stores(tmp_path)
    ws.connect()
    person = ws.create_person("Diogo", can_login=True)
    ws.close()
    auth.connect()
    token = auth.start_session(person["person_id"])
    auth.close()

    monkeypatch.setattr(getpass, "getpass", lambda *_a, **_k: "temporary-pw-123")
    assert cli.cmd_auth(_auth_args(tmp_path, "reset", name="Diogo")) == 0

    auth.connect()
    assert auth.session_person(token) is None
    auth.close()


def test_auth_reset_refuses_someone_who_cannot_sign_in(tmp_path, monkeypatch, capsys):
    """Same brick-guard family as `auth setup`: Rita is assignable-only, and a credential for her is
    a credential nobody can use — while `has_any_credentials()` treats it as a real account."""
    import getpass

    ws, auth = _stores(tmp_path)
    ws.connect()
    ws.create_person("Filipe", can_login=True, is_admin=True)
    ws.create_person("Rita", responsible="Filipe")
    ws.close()

    monkeypatch.setattr(getpass, "getpass", lambda *_a, **_k: "temporary-pw-123")
    assert cli.cmd_auth(_auth_args(tmp_path, "reset", name="Rita")) == 2
    auth.connect()
    assert auth.has_any_credentials() is False
    auth.close()


def test_auth_reset_on_an_unknown_name_says_so(tmp_path, monkeypatch, capsys):
    import getpass

    monkeypatch.setattr(getpass, "getpass", lambda *_a, **_k: "temporary-pw-123")
    assert cli.cmd_auth(_auth_args(tmp_path, "reset", name="Ninguém")) == 2
    assert "não existe" in capsys.readouterr().err


def test_auth_setup_accepts_an_existing_admin_who_never_set_a_password(tmp_path, monkeypatch):
    """`auth add --admin` then `auth setup` is the documented two-step; it must keep working."""
    import getpass

    ws, _auth = _stores(tmp_path)
    ws.connect()
    ws.create_person("Filipe", can_login=True, is_admin=True)
    ws.close()

    monkeypatch.setattr(getpass, "getpass", lambda *_a, **_k: "a-good-password")
    assert cli.cmd_auth(_auth_args(tmp_path, "setup", name="Filipe")) == 0


# ── `auth email` and `auth mail-test` (ADR-042) ──────────────────────────────

def test_auth_email_sets_the_reset_destination(tmp_path, capsys):
    ws, _auth = _stores(tmp_path)
    ws.connect()
    ws.create_person("Filipe", can_login=True, is_admin=True)
    ws.close()

    rc = cli.cmd_auth(_auth_args(tmp_path, "email", name="Filipe",
                                 address="Filipe.Coelho@LindoServico.PT"))
    assert rc == 0
    assert "filipe.coelho@lindoservico.pt" in capsys.readouterr().out

    ws.connect()
    assert ws.person("Filipe")["email"] == "filipe.coelho@lindoservico.pt"
    ws.close()


def test_auth_email_refuses_a_malformed_address(tmp_path, capsys):
    ws, _auth = _stores(tmp_path)
    ws.connect()
    ws.create_person("Filipe", can_login=True, is_admin=True)
    ws.close()

    rc = cli.cmd_auth(_auth_args(tmp_path, "email", name="Filipe", address="not-an-address"))
    assert rc == 2
    assert "inválido" in capsys.readouterr().err


def test_auth_email_with_no_address_clears_it_and_says_what_that_costs(tmp_path, capsys):
    ws, _auth = _stores(tmp_path)
    ws.connect()
    person = ws.create_person("Filipe", can_login=True, is_admin=True)
    ws.set_person_email(person["person_id"], "filipe@lindoservico.pt")
    ws.close()

    assert cli.cmd_auth(_auth_args(tmp_path, "email", name="Filipe", address="")) == 0
    out = capsys.readouterr().out
    assert "sem email" in out and "Esqueceste-te" in out
    ws.connect()
    assert ws.person("Filipe")["email"] == ""
    ws.close()


def test_auth_email_on_an_unknown_name_says_so(tmp_path, capsys):
    ws, _auth = _stores(tmp_path)
    ws.connect()
    ws.close()
    assert cli.cmd_auth(_auth_args(tmp_path, "email", name="Ninguém", address="x@y.pt")) == 2
    assert "não existe" in capsys.readouterr().err


def test_auth_list_says_plainly_who_cannot_recover(tmp_path, capsys):
    """"Cannot recover" is invisible otherwise, and the way it is normally discovered is by being
    locked out."""
    ws, _auth = _stores(tmp_path)
    ws.connect()
    ws.create_person("Filipe", can_login=True, is_admin=True)
    person = ws.create_person("Pedro", can_login=True)
    ws.set_person_email(person["person_id"], "pedro@lindoservico.pt")
    ws.close()

    assert cli.cmd_auth(_auth_args(tmp_path, "list", name="")) == 0
    out = capsys.readouterr().out
    assert "SEM EMAIL" in out                      # Filipe
    assert "pedro@lindoservico.pt" in out          # Pedro


def test_auth_mail_test_reports_disabled_mail_without_pretending(tmp_path, capsys):
    """`mail-test` must not report success when there is nothing configured to succeed."""
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"paths": {"out_dir": "out", "corpus_dir": "corpus",
                                              "captures_dir": "captures"},
                                    "mail": {"enabled": False}}), encoding="utf-8")
    args = argparse.Namespace(action="mail-test", settings=str(settings), name="")
    assert cli.cmd_auth(args) == 1
    assert "desativado" in capsys.readouterr().out


def test_auth_mail_test_proves_the_credential_and_says_it_does_not_prove_delivery(tmp_path,
                                                                                 monkeypatch,
                                                                                 capsys):
    """The standing rule against reporting a proxy as the real thing: a successful SMTP login is
    evidence of the credential and the transport, and of nothing else."""
    from email2data import mailer as mailermod

    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps(
        {"paths": {"out_dir": "out", "corpus_dir": "corpus", "captures_dir": "captures"},
         "mail": {"enabled": True, "host": "mail.example.pt", "username": "bot@example.pt",
                  "password_env": "E2D_CLI_MAIL_PW", "base_url": "https://192.168.1.253:8042"}}),
        encoding="utf-8")
    monkeypatch.setenv("E2D_CLI_MAIL_PW", "pw")
    monkeypatch.setattr(mailermod.Mailer, "verify_connection", lambda self: None)

    args = argparse.Namespace(action="mail-test", settings=str(settings), name="")
    assert cli.cmd_auth(args) == 0
    out = capsys.readouterr().out
    assert "OK" in out
    assert "NÃO prova entrega" in out
    assert "https://192.168.1.253:8042" in out


def test_auth_mail_test_warns_when_the_base_url_is_empty(tmp_path, monkeypatch, capsys):
    """A link with no host is worse than no mail: it opens nothing and looks like nothing happened."""
    from email2data import mailer as mailermod

    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps(
        {"paths": {"out_dir": "out", "corpus_dir": "corpus", "captures_dir": "captures"},
         "mail": {"enabled": True, "host": "mail.example.pt", "username": "bot@example.pt",
                  "password_env": "E2D_CLI_MAIL_PW"}}), encoding="utf-8")
    monkeypatch.setenv("E2D_CLI_MAIL_PW", "pw")
    monkeypatch.setattr(mailermod.Mailer, "verify_connection", lambda self: None)

    assert cli.cmd_auth(argparse.Namespace(action="mail-test", settings=str(settings), name="")) == 0
    assert "base_url está vazio" in capsys.readouterr().out


def test_the_lan_ip_probe_sends_no_traffic_and_never_raises(monkeypatch):
    """It connects a UDP socket to TEST-NET-1 purely to read the routing decision. On a host with no
    route it must return '' rather than take down `auth mail-test`."""
    import socket

    assert isinstance(cli._local_lan_ip(), str)

    class Dead:
        def __init__(self, *a, **kw): pass
        def connect(self, *a): raise OSError("no route to host")
        def getsockname(self): raise AssertionError("must not be reached")
        def close(self): pass

    monkeypatch.setattr(socket, "socket", Dead)
    assert cli._local_lan_ip() == ""


def test_the_lan_ip_probe_declines_to_answer_inside_a_container(monkeypatch):
    """In Docker the probe resolves to the bridge address (172.x), which `mail.base_url` should never
    name — so the drift comparison fired on every run and reported drift that did not exist. A
    warning that always fires teaches the reader to skip the line, including the one time it is
    real. On the container it declines to answer rather than answering wrongly."""
    monkeypatch.setattr(cli, "_in_container", lambda: True)
    assert cli._local_lan_ip() == ""


# ── `email2data gazetteer` — the management surface the priors never had ──────
#
# config/gazetteer.csv is gitignored (it names real clients), so it is the one store input with no
# second copy. When it went missing on the live host, out/knowledge.db kept serving 15 frozen rows
# and nothing said so. `status` makes that state visible and non-zero; `export` is the way back.


def _gaz_args(tmp_path, action, force=False):
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "out").mkdir(exist_ok=True)
    sp = tmp_path / "config" / "settings.json"
    sp.write_text(json.dumps({"paths": {"out_dir": "out", "corpus_dir": "corpus",
                                        "captures_dir": "captures"}}), encoding="utf-8")
    return argparse.Namespace(action=action, settings=str(sp), force=force)


def _seed(tmp_path, body="acme-example.pt,CLIENT,a client\nlaminex-example.pt,SUPPLIER,a supplier\n"):
    from email2data import cascade
    args = _gaz_args(tmp_path, "status")
    settings = cli._load_settings(args)
    csv_path = cascade.gazetteer_csv(settings)
    csv_path.write_text("domain,counterparty,note\n" + body, encoding="utf-8")
    store = cascade.build_store(settings)     # seeds knowledge.db from the CSV
    store.close()
    return csv_path


def test_gazetteer_status_reports_a_frozen_table_and_exits_non_zero(tmp_path, capsys):
    """The whole point: an unmanaged gazetteer must be *detectable*, including from a script."""
    csv_path = _seed(tmp_path)
    csv_path.unlink()
    rc = cli.cmd_gazetteer(_gaz_args(tmp_path, "status"))
    out = capsys.readouterr().out
    assert rc == 1
    assert "MISSING" in out and "FROZEN" in out
    assert "gazetteer export" in out


def test_gazetteer_status_is_green_when_the_csv_is_there(tmp_path, capsys):
    _seed(tmp_path)
    rc = cli.cmd_gazetteer(_gaz_args(tmp_path, "status"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "present" in out and "FROZEN" not in out


def test_gazetteer_status_never_prints_the_keys(tmp_path, capsys):
    """The keys are real client/supplier domains. A status line lands in scrollback, in CI logs and
    in screenshots — it gets counts per counterparty, never the names (non-negotiable #5)."""
    _seed(tmp_path)
    cli.cmd_gazetteer(_gaz_args(tmp_path, "status"))
    out = capsys.readouterr().out
    assert "acme-example.pt" not in out and "laminex-example.pt" not in out
    assert "1  CLIENT" in out and "1  SUPPLIER" in out


def test_gazetteer_export_recovers_a_deleted_csv(tmp_path, capsys):
    """The recovery path, end to end: delete the source of truth, export it back, and the file that
    lands is the one the seeder reads — same rows, no retyping of real client names."""
    csv_path = _seed(tmp_path)
    original = csv_path.read_text(encoding="utf-8")
    csv_path.unlink()

    assert cli.cmd_gazetteer(_gaz_args(tmp_path, "export")) == 0
    assert "wrote 2 row(s)" in capsys.readouterr().out
    assert csv_path.exists()
    for line in original.splitlines()[1:]:
        assert line in csv_path.read_text(encoding="utf-8")
    assert cli.cmd_gazetteer(_gaz_args(tmp_path, "status")) == 0    # and the drift is gone


def test_gazetteer_export_refuses_to_clobber_an_existing_csv(tmp_path, capsys):
    """The CSV is the source of truth and may hold hand edits not yet seeded. Overwriting it with a
    dump of the table would silently discard them, so it takes --force."""
    csv_path = _seed(tmp_path)
    csv_path.write_text(csv_path.read_text(encoding="utf-8") + "novo-example.pt,CLIENT,not seeded yet\n",
                        encoding="utf-8")
    before = csv_path.read_text(encoding="utf-8")

    rc = cli.cmd_gazetteer(_gaz_args(tmp_path, "export"))
    assert rc == 1
    assert "refusing to overwrite" in capsys.readouterr().err
    assert csv_path.read_text(encoding="utf-8") == before          # the hand edit survived

    assert cli.cmd_gazetteer(_gaz_args(tmp_path, "export", force=True)) == 0
    assert "novo-example.pt" not in csv_path.read_text(encoding="utf-8")   # --force does discard it


def test_gazetteer_export_refuses_to_write_an_empty_file(tmp_path, capsys):
    """An empty table exports to a CSV with no rows — which, on the next triage, would REPLACE the
    table with nothing. Refusing beats writing a file whose only effect is to erase."""
    rc = cli.cmd_gazetteer(_gaz_args(tmp_path, "export"))
    assert rc == 1
    assert "empty" in capsys.readouterr().err


def test_gazetteer_is_wired_into_the_parser(tmp_path):
    """A command nobody can reach is not a management surface."""
    import pytest
    with pytest.raises(SystemExit):
        cli.main(["gazetteer"])            # subcommand required
