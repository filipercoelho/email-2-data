"""CLI port-binding policy: silent rebind is fine on localhost, but in container mode (--host 0.0.0.0)
a busy port must fail loudly — the published compose port (8042:8042) has no listener otherwise."""

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
