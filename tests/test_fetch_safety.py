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
