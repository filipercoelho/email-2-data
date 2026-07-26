"""The outbound transactional mailer (ADR-042) — what it sends, and what it refuses to.

Every test here substitutes :meth:`Mailer._send`, the module's single transport seam, so the suite
never opens a socket. That is the point of there being exactly one seam: a future second send path
that bypassed it would also bypass every assertion below, which is why ``send_password_reset`` and
``verify_connection`` are the only two methods that touch smtplib.

The invariant this module exists to keep is negative, so it is tested negatively: nothing here may
open IMAP, and nothing here may APPEND to a mailbox. ``test_the_mailer_never_touches_imap`` reads the
module source for the verbs ADR-002 forbids rather than trusting that no one adds one later.
"""

from __future__ import annotations

import smtplib
from pathlib import Path

import pytest

from email2data import mailer as M


def _mailer(**over):
    kwargs = dict(host="mail.example.pt", username="bot@example.pt", password="s3cret",
                  from_address="bot@example.pt", from_name="email-2-data")
    kwargs.update(over)
    return M.Mailer(**kwargs)


def _capture(m):
    """Replace the transport and return the list it records into."""
    sent = []
    m._send = sent.append          # the ONE seam
    return sent


# ── construction guards ──────────────────────────────────────────────────────

def test_a_from_address_that_breaks_dmarc_alignment_is_refused_at_construction():
    """lindoservico.pt publishes p=reject, so an unaligned From: is not delivered-to-spam, it is
    rejected outright. Caught at boot, where it is one log line, instead of at send time, where it
    is a person who never got their link and cannot tell why."""
    with pytest.raises(ValueError, match="DMARC"):
        _mailer(username="bot@lindoservico.pt", from_address="bot@gmail.com")


def test_an_empty_password_is_refused_with_the_env_var_named():
    """The failure mode this catches is a missing .env key, so the message must say which key."""
    with pytest.raises(ValueError, match="password_env"):
        _mailer(password="")


@pytest.mark.parametrize("bad", [{"host": ""}, {"username": ""}, {"username": "not-an-address"}])
def test_malformed_configuration_is_refused(bad):
    with pytest.raises(ValueError):
        _mailer(**bad)


# ── the one message ──────────────────────────────────────────────────────────

def test_the_reset_mail_carries_the_link_the_ttl_and_nothing_else():
    m = _mailer()
    sent = _capture(m)
    m.send_password_reset(to_address="alguem@example.pt", person_name="Luís",
                          reset_url="https://192.168.1.253:8042/recuperar/TOK", ttl_minutes=30)
    assert len(sent) == 1
    msg = sent[0]
    body = msg.get_content()
    assert "https://192.168.1.253:8042/recuperar/TOK" in body
    assert "30 minutos" in body
    assert "Luís" in body
    assert msg["To"] == "alguem@example.pt"
    assert "bot@example.pt" in msg["From"]
    # Transactional mail must not be auto-replied to or filed as bulk.
    assert msg["Auto-Submitted"] == "auto-generated"
    assert msg["Message-ID"] and msg["Date"]


def test_the_reset_mail_is_plain_text_only():
    """An HTML mail with a styled button is what phishing looks like. A bare URL is what a person
    can actually read and verify before clicking."""
    m = _mailer()
    sent = _capture(m)
    m.send_password_reset(to_address="a@example.pt", person_name="X", reset_url="https://h/r/T",
                          ttl_minutes=30)
    assert sent[0].get_content_type() == "text/plain"
    assert not sent[0].is_multipart()


def test_the_reset_mail_tells_a_non_requester_that_doing_nothing_is_safe():
    """Someone who did not ask must be told their password still works — otherwise the honest
    response to an unexpected reset mail is to panic and change something."""
    m = _mailer()
    sent = _capture(m)
    m.send_password_reset(to_address="a@example.pt", person_name="X", reset_url="https://h/r/T",
                          ttl_minutes=30)
    body = sent[0].get_content()
    assert "não foste tu" in body and "continua válida" in body


def test_a_transport_failure_becomes_MailError_and_leaks_no_credential(monkeypatch):
    """Patched at ``smtplib`` rather than at ``_send``, deliberately: ``_send`` IS the code that
    converts an SMTP exception into a MailError, so replacing it would test the fake instead."""
    class Boom:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def login(self, *a): raise smtplib.SMTPAuthenticationError(535, b"5.7.8 auth failed")
        def send_message(self, *a): raise AssertionError("must not be reached")

    monkeypatch.setattr(M.smtplib, "SMTP_SSL", Boom)
    m = _mailer()
    with pytest.raises(M.MailError) as exc:
        m.send_password_reset(to_address="a@example.pt", person_name="X",
                              reset_url="https://h/r/T", ttl_minutes=30)
    text = str(exc.value)
    assert "s3cret" not in text, "the credential reached the exception text"
    assert "SMTPAuthenticationError" in text, "the operator still needs the real cause"


def test_a_connection_failure_also_becomes_MailError(monkeypatch):
    """OSError, not SMTPException — a refused connection must funnel to the same one exception the
    webapp catches, or it escapes as a 500 on a public endpoint."""
    def refuse(*a, **kw):
        raise OSError("connection refused")

    monkeypatch.setattr(M.smtplib, "SMTP_SSL", refuse)
    with pytest.raises(M.MailError):
        _mailer().send_password_reset(to_address="a@example.pt", person_name="X",
                                      reset_url="https://h/r/T", ttl_minutes=30)


def test_verify_connection_authenticates_without_sending(monkeypatch):
    """What `auth mail-test` runs. It must log in and stop — sending a probe message to prove the
    mail path works would mail a real person."""
    calls = []

    class Fake:
        def __init__(self, *a, **kw): calls.append("connect")
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def login(self, *a): calls.append("login")
        def send_message(self, *a): calls.append("send")

    monkeypatch.setattr(M.smtplib, "SMTP_SSL", Fake)
    _mailer().verify_connection()
    assert calls == ["connect", "login"], "verify_connection sent a message"


def test_the_logged_address_is_masked(caplog):
    """"Raw bodies/addresses never logged" (non-negotiable #5) reaching this module."""
    m = _mailer()
    _capture(m)
    with caplog.at_level("INFO"):
        m.send_password_reset(to_address="filipe.coelho@lindoservico.pt", person_name="F",
                              reset_url="https://h/r/TOKEN-VALUE", ttl_minutes=30)
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "filipe.coelho@lindoservico.pt" not in logged
    assert "TOKEN-VALUE" not in logged, "a reset token must never reach a log"
    assert "@lindoservico.pt" in logged, "the domain is kept so a misconfiguration is diagnosable"


def test_the_masked_repr_never_shows_the_password():
    m = _mailer()
    assert "s3cret" not in repr(m)
    assert "@example.pt" in repr(m)


# ── from_settings ────────────────────────────────────────────────────────────

def test_mail_that_is_absent_or_disabled_yields_None_not_an_error():
    """An install with no mail block is every install before ADR-042, and it must keep working:
    /recuperar then says recovery is unavailable instead of promising a link."""
    assert M.from_settings({}) is None
    assert M.from_settings({"mail": {"enabled": False}}) is None


def test_a_configured_but_broken_mail_block_raises_rather_than_silently_disabling(monkeypatch):
    """Degrading a misconfiguration to "no recovery" is how a dead feature reports healthy."""
    monkeypatch.delenv("E2D_TEST_MAIL_PW", raising=False)
    with pytest.raises(Exception):
        M.from_settings({"mail": {"enabled": True, "host": "h", "username": "a@b.pt",
                                  "password_env": "E2D_TEST_MAIL_PW"}})


def test_from_settings_reads_the_password_through_the_env_indirection(monkeypatch):
    monkeypatch.setenv("E2D_TEST_MAIL_PW", "from-the-env")
    m = M.from_settings({"mail": {"enabled": True, "host": "mail.example.pt",
                                  "username": "bot@example.pt", "password_env": "E2D_TEST_MAIL_PW"}})
    assert isinstance(m, M.Mailer)
    sent = _capture(m)
    m.send_password_reset(to_address="a@example.pt", person_name="X", reset_url="https://h/r/T",
                          ttl_minutes=30)
    assert len(sent) == 1


# ── the ADR-002 boundary ─────────────────────────────────────────────────────

def test_the_mailer_never_touches_imap():
    """ADR-002 binds the IMAP client and the mailboxes we triage. This module sits beside that
    guarantee rather than overturning it — and stays there only if nobody adds a verb.

    Read as source rather than asserted behaviourally on purpose: the failure being prevented is
    someone adding an APPEND-to-Sent later, which no behavioural test of today's code would catch.
    """
    source = Path(M.__file__).read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines()
                     if not line.lstrip().startswith("#"))
    # Strip the module docstring, which legitimately names the forbidden verbs to explain the rule.
    body = code.split('"""', 2)[-1]
    for verb in ("imaplib", "IMAP4", "APPEND", "STORE", "EXPUNGE"):
        assert verb not in body, f"{verb} appeared in mailer.py — that is the ADR-002 boundary"


def test_the_sending_account_is_never_a_fetched_inbox():
    """The outbound account must not appear in imap.accounts[], or the app would triage its own
    transactional mail — and a read-only guarantee would then be covering a mailbox we write to."""
    settings = {"imap": {"accounts": [{"username": "orcamentos@lindoservico.pt"}]},
                "mail": {"enabled": False, "username": "email-2-data@lindoservico.pt"}}
    fetched = {a["username"].lower() for a in settings["imap"]["accounts"]}
    assert settings["mail"]["username"].lower() not in fetched
