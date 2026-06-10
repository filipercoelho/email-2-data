"""Safety invariant (red-team B1): the fetch module must never use a mailbox-mutating or
\\Seen-setting IMAP command. This guards the one unrecoverable mistake."""

import re
from pathlib import Path

import email2data.fetch as fetch

SRC = Path(fetch.__file__).read_text()


def test_fetch_uses_body_peek():
    assert fetch._FETCH_ITEM == "(BODY.PEEK[])"


def test_mailbox_opened_readonly():
    assert "readonly=True" in SRC


def test_no_mutating_or_seen_setting_commands_in_executable_code():
    # Strip comments/docstrings so the _FORBIDDEN_IMAP literal and explanatory prose don't trip this.
    code = re.sub(r"#.*", "", SRC)
    code = re.sub(r'""".*?"""', "", code, flags=re.DOTALL)
    code = re.sub(r"_FORBIDDEN_IMAP\s*=\s*\([^)]*\)", "", code)
    for forbidden in ("STORE", "EXPUNGE", "APPEND", '"COPY"', "RFC822", "BODY[]"):
        assert forbidden not in code, f"forbidden IMAP usage found: {forbidden}"
    # The only FETCH item string allowed:
    assert "BODY.PEEK[]" in code


# ── Multi-mailbox helpers (no network) ──────────────────────────────────────

def test_account_mailboxes_uses_per_account_list():
    settings = {"imap": {"mailbox": "INBOX"}}
    account = {"id": "pedro", "mailboxes": ["INBOX", "Enviados"]}
    assert fetch._account_mailboxes(settings, account) == ["INBOX", "Enviados"]


def test_account_mailboxes_falls_back_to_imap_level():
    settings = {"imap": {"mailbox": "INBOX"}}
    account = {"id": "pedro"}
    assert fetch._account_mailboxes(settings, account) == ["INBOX"]


def test_source_header_format():
    h = fetch._source_header("Enviados")
    assert h == b"X-Email2Data-Source: Enviados\r\n"


def test_source_header_prepended_to_raw():
    raw = b"From: x@y.pt\r\nSubject: t\r\n\r\nbody"
    h = fetch._source_header("Enviados")
    combined = h + raw
    assert combined.startswith(b"X-Email2Data-Source: Enviados\r\n")
    assert b"From: x@y.pt" in combined


def test_incremental_search_uses_only_uid_range_no_forbidden_verbs():
    """The incremental watermark path adds a UID-range SEARCH — assert it is read-only (the SEARCH
    criterion is just ``UID``, never a STORE/COPY/etc.) so the red-team B1 invariant still holds."""
    code = re.sub(r"#.*", "", SRC)
    code = re.sub(r'""".*?"""', "", code, flags=re.DOTALL)
    # The only two SEARCH forms the module issues:
    assert '"SEARCH", None, "UID"' in code
    assert '"SEARCH", None, "SINCE"' in code
    # Watermark filtering happens client-side (defensive against the IMAP "N:*" highest-message echo).
    assert "u for u in uids if int(u) > last_uid" in code


# ── Sent-wins dedup: direction must not depend on fetch order ────────────────

from email import message_from_bytes  # noqa: E402

from email2data.identity import canonical_id_from_raw, safe_filename  # noqa: E402
from email2data.signals import header_signals  # noqa: E402

# From our own domain → "internal" without a Sent header, "outbound" when fetched from a Sent folder.
_RAW = (b"Message-ID: <dedup-001@lindoservico.pt>\r\nFrom: orcamentos@lindoservico.pt\r\n"
        b"To: orcamentos@lindoservico.pt\r\nSubject: ping\r\n\r\nbody\r\n")


def _replay(corpus: Path, order):
    """Replay _fetch_mailbox's dedup decision for a sequence of mailboxes, return the cached file."""
    for mailbox in order:
        raw = (fetch._source_header(mailbox) + _RAW) if mailbox.upper() != "INBOX" else _RAW
        dest = corpus / safe_filename(canonical_id_from_raw(raw))
        if dest.exists():
            if fetch.is_sent_folder(mailbox) and not fetch._existing_is_sent(dest):
                dest.write_bytes(raw)  # Sent copy overrides a non-Sent cached one
            continue
        dest.write_bytes(raw)
    return next(corpus.glob("*"))


def _direction(path: Path) -> str:
    return header_signals(message_from_bytes(path.read_bytes())).direction


def test_existing_is_sent_detects_sent_header(tmp_path):
    sent = tmp_path / "a.eml"
    sent.write_bytes(fetch._source_header("INBOX.Sent") + _RAW)
    plain = tmp_path / "b.eml"
    plain.write_bytes(_RAW)
    assert fetch._existing_is_sent(sent) is True
    assert fetch._existing_is_sent(plain) is False


def test_sent_copy_wins_inbox_first(tmp_path):
    """INBOX fetched before Sent (the order that used to lose the outbound signal)."""
    assert _direction(_replay(tmp_path, ["INBOX", "INBOX.Sent"])) == "outbound"


def test_sent_copy_wins_sent_first(tmp_path):
    """Sent fetched before INBOX — must not be downgraded back to internal."""
    assert _direction(_replay(tmp_path, ["INBOX.Sent", "INBOX"])) == "outbound"


def test_inbox_only_stays_internal(tmp_path):
    """No Sent copy at all → an own-domain message stays internal (no false outbound)."""
    assert _direction(_replay(tmp_path, ["INBOX"])) == "internal"
