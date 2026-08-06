"""ADR-049 — the folder list is discovered from the server, not read off a snapshot.

The defect these pin: ``settings.json`` listed 78 folders for the ``orcamentos`` account while the
server had 82. A reply filed into one of the four it did not know about (``INBOX.orcamentado``)
between two polls was never opened by the fetcher, so no store downstream ever saw it — the message
did not arrive late or classified wrong, it did not exist. Discovery closes that by asking the
server what folders exist on every run.

No network: a fake connection replays real IMAP LIST/SEARCH/FETCH response shapes.
"""

import imaplib
import json
import re
from pathlib import Path

import pytest

import email2data.fetch as fetch

# Real LIST lines from the Lindo Serviço server (dovecot, "." delimiter).
_SERVER_LIST = [
    b'(\\HasNoChildren) "." INBOX',
    b'(\\HasChildren) "." INBOX.Trash',
    b'(\\HasNoChildren) "." INBOX.Sent',
    b'(\\HasNoChildren) "." INBOX.orcamentado',
    b'(\\HasNoChildren) "." INBOX.concluido',
    b'(\\HasNoChildren) "." INBOX.em-orcamentacao',
    b'(\\HasNoChildren) "." INBOX.spam',
    b'(\\HasNoChildren) "." "INBOX.Pedidos or&AOc-amento"',
    b'(\\Noselect \\HasChildren) "." INBOX.Archive',
]

_RAW = (b"Message-ID: <disc-001@lindoservico.pt>\r\nFrom: margarida.reis@lindoservico.pt\r\n"
        b"To: cliente@example.pt\r\nSubject: Proposta 2600476\r\nDate: Wed, 29 Jul 2026 20:02:05 "
        b"+0100\r\n\r\nbody\r\n")


class FakeIMAP:
    """Minimal read-only IMAP double. Records every command so the safety invariant is checkable."""

    def __init__(self, listing=None, messages=None):
        self.listing = list(_SERVER_LIST if listing is None else listing)
        self.messages = messages or {}          # {mailbox: {uid: raw}}
        self.commands: list[tuple] = []
        self.examined: list[str] = []
        self.selected = None

    def list(self, *a):
        self.commands.append(("LIST",) + a)
        return "OK", list(self.listing)

    def select(self, mailbox, readonly=False):
        self.commands.append(("SELECT", mailbox, readonly))
        assert readonly is True, "a mailbox must never be opened writable"
        name = mailbox.strip('"')
        self.selected = name
        self.examined.append(name)
        return "OK", [b"1"]

    def response(self, key):
        return "OK", [b"42"]  # a stable UIDVALIDITY

    def uid(self, command, *args):
        self.commands.append((command,) + args)
        if command == "SEARCH":
            uids = sorted(self.messages.get(self.selected, {}))
            return "OK", [b" ".join(str(u).encode() for u in uids)]
        if command == "FETCH":
            uid = int(args[0])
            raw = self.messages.get(self.selected, {}).get(uid)
            if raw is None:
                return "NO", []
            return "OK", [(b"1 (UID %d BODY[] {%d}" % (uid, len(raw)), raw), b")"]
        raise AssertionError(f"unexpected UID command {command}")

    def logout(self):
        self.commands.append(("LOGOUT",))
        return "BYE", []


def _settings(tmp_path: Path, account: dict) -> dict:
    settings_path = tmp_path / "settings.json"
    settings = {"imap": {"host": "mail.example.pt", "port": 993, "accounts": [account]},
                "fetch": {"since_days": 10, "max_messages": 500},
                "paths": {"corpus_dir": "corpus", "out_dir": "out"}}
    settings_path.write_text(json.dumps(settings), encoding="utf-8")
    settings["__settings_path__"] = str(settings_path)
    return settings


# ── discovery ────────────────────────────────────────────────────────────────

def test_discovery_finds_folders_settings_json_never_listed():
    """THE regression: the folder that swallowed the 2026-07-29 20:02 proposal."""
    found = fetch._discover_mailboxes(FakeIMAP(), fetch._DEFAULT_EXCLUDE)
    assert "INBOX.orcamentado" in found
    assert "INBOX.concluido" in found
    assert "INBOX.em-orcamentacao" in found


def test_discovery_finds_sent_folders():
    """The 41-message hole: luis/filipe/Pedro pinned INBOX only, so every reply they sent was
    invisible unless it happened to CC a mailbox we did fetch."""
    found = fetch._discover_mailboxes(FakeIMAP(), fetch._DEFAULT_EXCLUDE)
    assert "INBOX.Sent" in found
    assert fetch.is_sent_folder("INBOX.Sent"), "discovery must feed the Sent-wins direction rule"


@pytest.mark.parametrize("name", ["spam", "INBOX.spam", "Junk", "INBOX.Junk E-mail", "INBOX.LIXO"])
def test_junk_folders_are_excluded_by_name(name):
    listing = [b'(\\HasNoChildren) "." ' + name.encode()]
    assert fetch._discover_mailboxes(FakeIMAP(listing), fetch._DEFAULT_EXCLUDE) == []


def test_junk_folder_excluded_by_special_use_attribute_whatever_it_is_called():
    listing = [b'(\\HasNoChildren \\Junk) "." INBOX.Indesejado']
    assert fetch._discover_mailboxes(FakeIMAP(listing), fetch._DEFAULT_EXCLUDE) == []


def test_trash_is_not_excluded():
    """Deliberate: deleted mail is still evidence a client wrote to us (non-negotiable #2), and
    orcamentos/INBOX.Trash has been fetched since day one. Only junk is dropped."""
    assert "INBOX.Trash" in fetch._discover_mailboxes(FakeIMAP(), fetch._DEFAULT_EXCLUDE)


def test_noselect_containers_are_skipped():
    """\\Noselect is a container with no messages — EXAMINE on it fails."""
    assert "INBOX.Archive" not in fetch._discover_mailboxes(FakeIMAP(), fetch._DEFAULT_EXCLUDE)


def test_quoted_names_with_spaces_survive_discovery():
    found = fetch._discover_mailboxes(FakeIMAP(), fetch._DEFAULT_EXCLUDE)
    assert "INBOX.Pedidos or&AOc-amento" in found, "quotes stripped, modified-UTF-7 left intact"


def test_literal_folder_names_are_parsed():
    """Some servers return the name as a literal, which imaplib hands back as a tuple."""
    listing = [(b'(\\HasNoChildren) "." {17}', b"INBOX.orcamentado"), b")"]
    assert fetch._discover_mailboxes(FakeIMAP(listing), fetch._DEFAULT_EXCLUDE) == ["INBOX.orcamentado"]


def test_exclusions_are_editable_without_a_code_change():
    settings = {"imap": {"exclude_mailboxes": ["Drafts"]}}
    patterns = fetch._exclude_patterns(settings)
    found = fetch._discover_mailboxes(FakeIMAP(), patterns)
    assert "INBOX.spam" in found, "an explicit list replaces the default, it does not extend it"
    assert fetch._exclude_patterns({"imap": {}}) == fetch._DEFAULT_EXCLUDE


def test_a_list_that_fails_falls_back_instead_of_narrowing_to_nothing():
    class Broken(FakeIMAP):
        def list(self, *a):
            raise imaplib.IMAP4.error("LIST failed")

    assert fetch._discover_mailboxes(Broken(), fetch._DEFAULT_EXCLUDE) is None
    settings = {"imap": {"mailbox": "INBOX"}}
    account = {"id": "x", "mailboxes": ["INBOX", "INBOX.Sent"]}
    assert fetch._account_mailboxes(settings, account, discovered=None) == ["INBOX", "INBOX.Sent"]


# ── union with the pinned list ───────────────────────────────────────────────

def test_pinned_folders_are_kept_even_when_they_look_like_junk():
    """The escape hatch: naming a folder explicitly outranks the junk filter."""
    settings = {"imap": {}}
    account = {"id": "x", "mailboxes": ["INBOX.spam"]}
    discovered = fetch._discover_mailboxes(FakeIMAP(), fetch._DEFAULT_EXCLUDE)
    assert "INBOX.spam" in fetch._account_mailboxes(settings, account, discovered=discovered)


def test_union_has_no_duplicates_and_inbox_comes_first():
    settings = {"imap": {}}
    account = {"id": "x", "mailboxes": ["INBOX", "INBOX.Sent"]}
    got = fetch._account_mailboxes(settings, account,
                                   discovered=fetch._discover_mailboxes(FakeIMAP(),
                                                                        fetch._DEFAULT_EXCLUDE))
    assert got[0] == "INBOX"
    assert len(got) == len(set(got))
    assert got.count("INBOX.Sent") == 1


# ── end to end through fetch_account ─────────────────────────────────────────

def test_fetch_account_opens_a_discovered_folder_that_was_never_configured(tmp_path, monkeypatch):
    """The whole point, asserted where it matters: the message in the unconfigured folder lands in
    corpus/. Against the pre-ADR-049 fetcher this folder is never selected and corpus/ stays empty."""
    account = {"id": "orcamentos", "username": "orcamentos@lindoservico.pt",
               "password_env": "P", "mailboxes": ["INBOX"]}
    settings = _settings(tmp_path, account)
    conn = FakeIMAP(messages={"INBOX.orcamentado": {167: _RAW}})
    monkeypatch.setattr(fetch, "_connect", lambda s, a: conn)
    monkeypatch.setenv("P", "pw")

    written = fetch.fetch_account(settings, account)

    assert "INBOX.orcamentado" in conn.examined
    assert "INBOX.spam" not in conn.examined
    cached = list((tmp_path / "corpus").glob("*.eml"))
    assert len(cached) == 1
    assert b"Proposta 2600476" in cached[0].read_bytes()
    assert written and written[0] == cached[0]


def test_discovery_never_opens_a_mailbox_writable(tmp_path, monkeypatch):
    """Read-only invariant (ADR-002) across the wider folder set: every SELECT is readonly and no
    mutating verb is ever issued, however many folders discovery adds."""
    account = {"id": "a", "username": "a@lindoservico.pt", "password_env": "P", "mailboxes": ["INBOX"]}
    settings = _settings(tmp_path, account)
    conn = FakeIMAP(messages={"INBOX.Sent": {5: _RAW}})
    monkeypatch.setattr(fetch, "_connect", lambda s, a: conn)
    monkeypatch.setenv("P", "pw")

    fetch.fetch_account(settings, account)

    assert all(c[2] is True for c in conn.commands if c[0] == "SELECT")
    issued = {c[0].upper() for c in conn.commands}
    assert not issued & {"STORE", "EXPUNGE", "DELETE", "APPEND", "COPY"}
    assert ("LIST",) in conn.commands


def test_discovery_is_audited_so_which_folders_we_looked_in_is_answerable(tmp_path, monkeypatch):
    """When a message goes missing, the first question is which folders were even opened. It used
    to be unrecorded."""
    account = {"id": "a", "username": "a@lindoservico.pt", "password_env": "P", "mailboxes": ["INBOX"]}
    settings = _settings(tmp_path, account)
    monkeypatch.setattr(fetch, "_connect", lambda s, a: FakeIMAP())
    monkeypatch.setenv("P", "pw")

    fetch.fetch_account(settings, account)

    events = [json.loads(line) for line in
              (tmp_path / "out" / "audit.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    picked = [e for e in events if e["event"] == "mailboxes_selected"]
    assert picked, "the selected folder set must be auditable"
    assert picked[0]["meta"]["discovered"] == 7  # 9 listed - \Noselect - spam
    assert picked[0]["meta"]["fetching"] == 7    # INBOX was pinned AND discovered: no duplicate


def test_list_is_a_read_only_verb_in_the_source():
    """Belt to the FakeIMAP suspenders: discovery must not have introduced a mutating command."""
    src = Path(fetch.__file__).read_text()
    code = re.sub(r"#.*", "", src)
    code = re.sub(r'""".*?"""', "", code, flags=re.DOTALL)
    code = re.sub(r"_FORBIDDEN_IMAP\s*=\s*\([^)]*\)", "", code)
    assert "conn.list()" in code
    for forbidden in ("STORE", "EXPUNGE", "APPEND", '"COPY"'):
        assert forbidden not in code
