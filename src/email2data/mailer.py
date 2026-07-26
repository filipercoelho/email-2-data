"""Outbound transactional mail — the ONE thing this app sends (ADR-042).

Scope, stated narrowly because the boundary is the whole point: this module sends **authentication
mail to a person on the `people` roster**, from a dedicated mailbox, over SMTP. It is not a mail API.
There is no caller-supplied body, no attachment path, no arbitrary recipient, and no reply handling.

**This does not weaken ADR-002.** That guarantee binds the IMAP client and the mailboxes we triage --
``EXAMINE``, ``BODY.PEEK``, and a forbidden-verb list of ``STORE / EXPUNGE / DELETE / APPEND /
COPY``. Nothing here opens IMAP, and the sending account is deliberately absent from
``imap.accounts[]`` so it is never fetched. In particular this module never ``APPEND``s to a Sent
folder: the sent copy the SMTP server keeps is the server's business, and writing one ourselves
would be exactly the mailbox mutation ADR-002 forbids. The read-only guarantee is untouched, not
overturned -- see ADR-042 §Context.

Deliverability is not incidental here, it is a constraint. ``lindoservico.pt`` publishes
``v=spf1 ip4:185.12.116.228 ... -all`` and ``p=reject``: mail is delivered only when it leaves
through the domain's own server carrying a ``From:`` on the domain. Both are enforced at
construction (:meth:`Mailer.__init__`) rather than discovered as a silent non-delivery, because a
reset mail that is rejected looks identical, from inside the app, to one that was never requested.

Stdlib only (``smtplib`` + ``email.message``), matching the project's two-runtime-dependency budget
and :mod:`email2data.telegram`'s posture: outbound-only, no new port, no new package.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Implicit TLS. Port 465 wraps the whole session in TLS from the first byte; 587 would need
# ``starttls()`` and is reachable on this server too, but implicit TLS has no cleartext prologue to
# strip, so it is the default and the fallback is opt-in via settings.
DEFAULT_PORT = 465
DEFAULT_TIMEOUT_S = 20.0


class MailError(RuntimeError):
    """Sending failed. Carries no message body and no token -- only the transport-level reason.

    Raised for every failure mode (auth, connection, refused recipient) so a caller has exactly one
    thing to catch. The webapp catches it, logs it, and still shows the caller the same neutral
    "if that address is on file, a link is on its way" -- an SMTP error must not become the oracle
    the neutral response exists to prevent.
    """


def _mask(address: str) -> str:
    """``fi****@lindoservico.pt`` — enough to identify a misconfigured account in a log, not enough
    to disclose who was mailed. Applied to every address that reaches a log line."""
    local, _, domain = (address or "").partition("@")
    if not domain:
        return "…"
    head = local[:2] if len(local) > 2 else local[:1]
    return f"{head}{'*' * max(len(local) - len(head), 1)}@{domain}"


class Mailer:
    """SMTP submission for transactional auth mail. One public method, one private transport seam.

    The password is taken as an already-resolved value (the caller reads it through
    ``config.smtp_password``, the same ``*_env`` indirection every other secret uses) and is never
    stored anywhere it could be logged or reprd -- only a masked username is kept for diagnostics.
    """

    def __init__(self, *, host: str, username: str, password: str, port: int = DEFAULT_PORT,
                 from_address: str = "", from_name: str = "email-2-data",
                 use_ssl: bool = True, timeout: float = DEFAULT_TIMEOUT_S) -> None:
        if not host or not str(host).strip():
            raise ValueError("mail host is required")
        if not username or "@" not in username:
            raise ValueError("mail username must be a full address")
        if not password:
            raise ValueError("mail password is empty — check the env var named by mail.password_env")
        self._host = str(host).strip()
        self._port = int(port)
        self._username = username.strip()
        self._password = password
        self._from = (from_address or self._username).strip()
        self._from_name = from_name or "email-2-data"
        self._use_ssl = bool(use_ssl)
        self._timeout = float(timeout)
        # DMARC is p=reject on this domain, so a From: that does not align with the sending domain
        # is not "slightly wrong", it is undeliverable. Refuse at construction: a misconfiguration
        # caught at boot is a log line, the same one caught at send time is a person who never got
        # their reset link and has no way to tell why.
        sender_domain = self._from.rsplit("@", 1)[-1].lower()
        account_domain = self._username.rsplit("@", 1)[-1].lower()
        if sender_domain != account_domain:
            raise ValueError(
                f"mail.from_address domain ({sender_domain}) must match the sending account's "
                f"domain ({account_domain}) — this domain publishes DMARC p=reject, so an "
                f"unaligned From: is rejected outright rather than delivered to spam")
        self._masked = _mask(self._username)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"<Mailer {self._masked} via {self._host}:{self._port}>"

    # -- transport ------------------------------------------------------------------------------
    #
    # THE seam. Every send in this module funnels through this one method, so a test substitutes the
    # whole network by replacing it -- no monkeypatching of smtplib internals, no live connection in
    # the suite, and no risk that a future second send path quietly bypasses the fake.

    def _send(self, message: EmailMessage) -> None:
        context = ssl.create_default_context()
        try:
            if self._use_ssl:
                with smtplib.SMTP_SSL(self._host, self._port, context=context,
                                      timeout=self._timeout) as smtp:
                    smtp.login(self._username, self._password)
                    smtp.send_message(message)
            else:
                with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as smtp:
                    smtp.ehlo()
                    smtp.starttls(context=context)
                    smtp.ehlo()
                    smtp.login(self._username, self._password)
                    smtp.send_message(message)
        except (smtplib.SMTPException, OSError) as exc:
            # str(exc) can carry the server's rejection text but never our credential: smtplib does
            # not echo the password, and the message body is not included in a transport error.
            raise MailError(f"SMTP send failed via {self._host}:{self._port} "
                            f"as {self._masked}: {type(exc).__name__}: {exc}") from exc

    def _build(self, *, to_address: str, subject: str, body: str) -> EmailMessage:
        msg = EmailMessage()
        local, _, domain = self._from.partition("@")
        msg["From"] = Address(self._from_name, local, domain)
        msg["To"] = to_address
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=domain)
        # Transactional mail must never be auto-replied to, and must never be bulk-filed. These are
        # the headers mail systems actually honour for that.
        msg["Auto-Submitted"] = "auto-generated"
        msg["X-Auto-Response-Suppress"] = "All"
        # Plain text only, deliberately. An HTML mail with a styled button is what phishing looks
        # like; a bare URL the reader can see in full is what a person can actually verify.
        msg.set_content(body)
        return msg

    # -- the one message this app sends -----------------------------------------------------------

    def send_password_reset(self, *, to_address: str, person_name: str, reset_url: str,
                            ttl_minutes: int) -> None:
        """Mail one password-reset link. Raises :class:`MailError` if the transport failed.

        The URL is passed in fully-formed; this module never builds it, because the correct host
        depends on how the app is being served (loopback vs the LAN bind) and that is the webapp's
        knowledge, not the mailer's.
        """
        body = (
            f"Olá {person_name},\n\n"
            f"Foi pedida a reposição da palavra-passe da tua conta no email-2-data.\n\n"
            f"Abre este link para definires uma nova palavra-passe:\n\n"
            f"{reset_url}\n\n"
            f"O link é válido durante {ttl_minutes} minutos e só pode ser usado uma vez.\n"
            f"Ao defini-la, todas as sessões abertas terminam — terás de entrar de novo.\n\n"
            f"Se não foste tu que pediste, ignora esta mensagem: a palavra-passe atual\n"
            f"continua válida e nada muda. Se isto se repetir, avisa um administrador.\n\n"
            f"—\nesta mensagem é automática, não respondas\n"
        )
        message = self._build(
            to_address=to_address,
            subject="Repor a palavra-passe · email-2-data",
            body=body,
        )
        self._send(message)
        # The address is masked and the token never appears: this line must be safe in a log that
        # someone else can read, which is the standing "raw bodies/addresses never logged" rule.
        logger.info("password-reset mail sent to %s", _mask(to_address))

    def verify_connection(self) -> None:
        """Authenticate and disconnect without sending — what ``email2data auth mail-test`` runs.

        Exists so "is the mail path configured?" is answerable without mailing a real person. A
        successful login proves host, port, TLS and credential; it does not prove delivery, and the
        CLI says so rather than implying otherwise.
        """
        context = ssl.create_default_context()
        try:
            if self._use_ssl:
                with smtplib.SMTP_SSL(self._host, self._port, context=context,
                                      timeout=self._timeout) as smtp:
                    smtp.login(self._username, self._password)
            else:
                with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as smtp:
                    smtp.ehlo()
                    smtp.starttls(context=context)
                    smtp.ehlo()
                    smtp.login(self._username, self._password)
        except (smtplib.SMTPException, OSError) as exc:
            raise MailError(f"SMTP login failed via {self._host}:{self._port} "
                            f"as {self._masked}: {type(exc).__name__}: {exc}") from exc


def from_settings(settings: dict[str, Any]) -> Optional[Mailer]:
    """Build a :class:`Mailer` from ``settings['mail']``, or return None when mail is not configured.

    None is a first-class answer, not a failure: an install with no ``mail`` block is the state
    every install was in before ADR-042, and it must keep working -- ``/recuperar`` then tells the
    visitor to ask an administrator instead of pretending a link is on its way. A *misconfigured*
    block is different and raises, because silently degrading to "no recovery" is how a feature
    reports healthy while being dead.
    """
    from .config import mail_password

    cfg = (settings or {}).get("mail") or {}
    if not cfg.get("enabled"):
        return None
    return Mailer(
        host=cfg.get("host", ""),
        port=int(cfg.get("port", DEFAULT_PORT)),
        username=cfg.get("username", ""),
        password=mail_password(settings),
        from_address=cfg.get("from_address", ""),
        from_name=cfg.get("from_name", "email-2-data"),
        use_ssl=bool(cfg.get("use_ssl", True)),
        timeout=float(cfg.get("timeout_s", DEFAULT_TIMEOUT_S)),
    )
