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
