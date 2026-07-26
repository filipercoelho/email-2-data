"""Mail-account attribution (ADR-038) — which of our inboxes a message reached.

The load-bearing properties, in the order they can hurt us:

  * **Nothing is silently binned.** A message we cannot attribute yields ``([], "")`` and folds to
    ``SCOPE_UNATTRIBUTED``, which admins see. A thread with one unattributed member still surfaces.
  * **Union, never intersection.** A message CC'd to two of our inboxes is visible to both readers;
    a thread's scope is the union over its messages. Attribution may only ever *widen* visibility.
  * **Evidence cannot be downgraded.** ``scopes.backfill`` runs beside a live fetch, so a derived
    header/participant guess must never overwrite the account we actually authenticated as.
  * **Only our addresses are scopes.** A client's address is a counterparty, never a grantable scope.
"""

import sqlite3

import pytest

import email2data.fetch as fetch
import email2data.scopes as scopes
from email2data.identity import canonical_id
from email2data.sync import SyncStore


# ── fake IMAP (mirrors tests/test_sync.py) ────────────────────────────────────

def _eml(uid: int, *, to: str = "orcamentos@lindoservico.pt") -> bytes:
    return (f"Message-ID: <m{uid}@x.pt>\r\nFrom: cliente@fora.pt\r\n"
            f"To: {to}\r\nSubject: s{uid}\r\n\r\nbody{uid}").encode()


class FakeIMAP:
    def __init__(self, uidvalidity: int, messages: dict[int, bytes]):
        self.uidvalidity = uidvalidity
        self.messages = messages

    def select(self, mailbox, readonly=False):
        return ("OK", [b"1"])

    def response(self, name):
        if name == "UIDVALIDITY":
            return ("UIDVALIDITY", [str(self.uidvalidity).encode()])
        return (name, [None])

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            return ("OK", [b" ".join(str(u).encode() for u in sorted(self.messages))])
        if cmd == "FETCH":
            uid = int(args[0])
            raw = self.messages.get(uid)
            if raw is None:
                return ("NO", [None])
            return ("OK", [(f"1 (UID {uid} BODY[])".encode(), raw)])
        return ("OK", [None])

    def logout(self):
        return ("BYE", [None])


def _settings(tmp_path, *, accounts=None):
    return {
        "__settings_path__": str(tmp_path / "config" / "settings.json"),
        "imap": {"host": "x", "accounts": accounts or [
            {"id": "acc", "username": "orcamentos@lindoservico.pt",
             "password_env": "P", "mailboxes": ["INBOX"]},
        ]},
        "fetch": {"since_days": 30, "max_messages": 200},
    }


def _store(tmp_path) -> SyncStore:
    return SyncStore(tmp_path / "sync.db").connect()


# ── derive(): the three evidence tiers ───────────────────────────────────────

def test_delivery_header_wins_over_participants():
    """Envelope-to is where the mail LANDED; To/Cc is only where the sender aimed it."""
    raw = (b"Envelope-to: pedro.ferreira@lindoservico.pt\r\n"
           b"To: orcamentos@lindoservico.pt\r\nFrom: c@fora.pt\r\n\r\nx")
    assert scopes.derive(raw) == (["pedro.ferreira@lindoservico.pt"], "header")


@pytest.mark.parametrize("header", ["Envelope-to", "Delivered-To", "X-Original-To", "X-Rcpt-To"])
def test_every_delivery_header_is_honoured(header):
    raw = f"{header}: luis.coelho@lindoservico.pt\r\nFrom: c@fora.pt\r\n\r\nx".encode()
    assert scopes.derive(raw) == (["luis.coelho@lindoservico.pt"], "header")


def test_participant_fallback_when_no_delivery_header():
    """102 of the 550 cached messages have no delivery header but do name one of our inboxes."""
    raw = b"To: cliente@fora.pt\r\nCc: margarida.reis@lindoservico.pt\r\nFrom: c@fora.pt\r\n\r\nx"
    assert scopes.derive(raw) == (["margarida.reis@lindoservico.pt"], "participant")


def test_our_outbound_is_attributed_to_the_sending_mailbox():
    """Sent folders are fetched too, so From must count — else our own replies are unattributed."""
    addresses, source = scopes.derive(b"From: luis.coelho@lindoservico.pt\r\nTo: c@fora.pt\r\n\r\nx")
    assert addresses == ["luis.coelho@lindoservico.pt"] and source == "participant"


def test_client_addresses_are_never_scopes():
    """A counterparty is not a grantable inbox. No internal address => UNKNOWN, not 'cliente@'."""
    raw = b"Envelope-to: someone@fora.pt\r\nTo: other@elsewhere.pt\r\nFrom: c@fora.pt\r\n\r\nx"
    assert scopes.derive(raw) == ([], "")


def test_subdomain_of_our_domain_is_internal():
    raw = b"Envelope-to: a@mail.lindoservico.pt\r\nFrom: c@fora.pt\r\n\r\nx"
    assert scopes.derive(raw) == (["a@mail.lindoservico.pt"], "header")


def test_cc_to_two_of_our_inboxes_yields_both():
    """The shared-thread case: both readers must keep seeing it."""
    raw = (b"Envelope-to: orcamentos@lindoservico.pt\r\n"
           b"Delivered-To: pedro.ferreira@lindoservico.pt\r\nFrom: c@fora.pt\r\n\r\nx")
    addresses, source = scopes.derive(raw)
    assert set(addresses) == {"orcamentos@lindoservico.pt", "pedro.ferreira@lindoservico.pt"}
    assert source == "header"


@pytest.mark.parametrize("raw", [b"", b"\x00\xff\xfe not mime at all", b"Subject: only\r\n\r\nx"])
def test_malformed_or_empty_degrades_to_unknown_never_raises(raw):
    assert scopes.derive(raw) == ([], "")


# ── set_message_scopes(): evidence may only move up ──────────────────────────

def test_stronger_evidence_upgrades_weaker(tmp_path):
    s = _store(tmp_path)
    assert s.set_message_scopes("mid:a", ["orcamentos@lindoservico.pt"], "participant") == 1
    assert s.message_scopes("mid:a") == {"orcamentos@lindoservico.pt": "participant"}
    assert s.set_message_scopes("mid:a", ["orcamentos@lindoservico.pt"], "header") == 1
    assert s.message_scopes("mid:a") == {"orcamentos@lindoservico.pt": "header"}
    assert s.set_message_scopes("mid:a", ["orcamentos@lindoservico.pt"], "fetch") == 1
    assert s.message_scopes("mid:a") == {"orcamentos@lindoservico.pt": "fetch"}
    s.close()


def test_weaker_evidence_never_downgrades_a_fetch_row(tmp_path):
    """The whole reason backfill is safe to re-run beside a live fetch."""
    s = _store(tmp_path)
    s.set_message_scopes("mid:a", ["orcamentos@lindoservico.pt"], "fetch")
    assert s.set_message_scopes("mid:a", ["orcamentos@lindoservico.pt"], "participant") == 0
    assert s.set_message_scopes("mid:a", ["orcamentos@lindoservico.pt"], "header") == 0
    assert s.message_scopes("mid:a") == {"orcamentos@lindoservico.pt": "fetch"}
    s.close()


def test_recording_the_same_evidence_twice_writes_nothing(tmp_path):
    s = _store(tmp_path)
    assert s.set_message_scopes("mid:a", ["a@lindoservico.pt"], "header") == 1
    assert s.set_message_scopes("mid:a", ["a@lindoservico.pt"], "header") == 0
    s.close()


def test_addresses_are_normalised_and_deduped(tmp_path):
    s = _store(tmp_path)
    assert s.set_message_scopes(
        "mid:a", ["  Orcamentos@LindoServico.pt ", "orcamentos@lindoservico.pt", "", "  "], "header"
    ) == 1
    assert s.message_scopes("mid:a") == {"orcamentos@lindoservico.pt": "header"}
    s.close()


def test_unknown_source_is_refused(tmp_path):
    s = _store(tmp_path)
    with pytest.raises(ValueError, match="unknown message_scope source"):
        s.set_message_scopes("mid:a", ["a@lindoservico.pt"], "guess")
    s.close()


def test_counts_report_addresses_and_evidence(tmp_path):
    s = _store(tmp_path)
    s.set_message_scopes("mid:a", ["a@lindoservico.pt"], "fetch")
    s.set_message_scopes("mid:b", ["a@lindoservico.pt"], "header")
    s.set_message_scopes("mid:c", ["b@lindoservico.pt"], "participant")
    assert s.scope_address_counts() == {"a@lindoservico.pt": 2, "b@lindoservico.pt": 1}
    assert s.scope_source_counts() == {"fetch": 1, "header": 1, "participant": 1}
    s.close()


# ── fetch: tier-1 attribution is recorded live ───────────────────────────────

def test_fetch_records_the_authenticated_account_as_tier_1(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(fetch, "_connect", lambda s, a: FakeIMAP(100, {1: _eml(1)}))
    monkeypatch.setattr(fetch, "account_password", lambda a: "pw")
    sync = _store(tmp_path)
    fetch.fetch_account(settings, settings["imap"]["accounts"][0], sync=sync)
    assert sync.message_scopes(canonical_id("<m1@x.pt>", b"")) == {
        "orcamentos@lindoservico.pt": "fetch"
    }
    sync.close()


def test_a_message_already_cached_by_another_account_still_gains_a_row(tmp_path, monkeypatch):
    """The shared-inbox regression: first-writer-wins on the FILE must not mean first-writer-wins
    on ATTRIBUTION, or a thread reaching two inboxes is visible to only one of their readers."""
    accounts = [
        {"id": "a", "username": "orcamentos@lindoservico.pt", "password_env": "P", "mailboxes": ["INBOX"]},
        {"id": "b", "username": "pedro.ferreira@lindoservico.pt", "password_env": "P", "mailboxes": ["INBOX"]},
    ]
    settings = _settings(tmp_path, accounts=accounts)
    monkeypatch.setattr(fetch, "_connect", lambda s, a: FakeIMAP(100, {1: _eml(1)}))
    monkeypatch.setattr(fetch, "account_password", lambda a: "pw")
    sync = _store(tmp_path)
    for account in accounts:
        fetch.fetch_account(settings, account, sync=sync)
    assert sync.message_scopes(canonical_id("<m1@x.pt>", b"")) == {
        "orcamentos@lindoservico.pt": "fetch",
        "pedro.ferreira@lindoservico.pt": "fetch",
    }
    sync.close()


def test_attribution_failure_never_breaks_a_fetch(tmp_path, monkeypatch):
    """Read-only fetch is the one unrecoverable-mistake surface; attribution is best-effort only."""
    class Exploding(SyncStore):
        def set_message_scopes(self, *a, **k):
            raise sqlite3.OperationalError("disk I/O error")

    settings = _settings(tmp_path)
    monkeypatch.setattr(fetch, "_connect", lambda s, a: FakeIMAP(100, {1: _eml(1)}))
    monkeypatch.setattr(fetch, "account_password", lambda a: "pw")
    sync = Exploding(tmp_path / "sync.db").connect()
    written = fetch.fetch_account(settings, settings["imap"]["accounts"][0], sync=sync)
    assert len(written) == 1                       # the mail still got cached
    audit = (tmp_path / "out" / "audit.jsonl").read_text()
    assert "scope_record_failed" in audit          # and the failure was reported, not swallowed
    sync.close()


# ── backfill over a real corpus directory ────────────────────────────────────

def test_backfill_classifies_by_tier_and_is_idempotent(tmp_path):
    settings = _settings(tmp_path)
    corpus = tmp_path / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    (corpus / "a.eml").write_bytes(
        b"Message-ID: <a@x.pt>\r\nEnvelope-to: orcamentos@lindoservico.pt\r\nFrom: c@fora.pt\r\n\r\nx")
    (corpus / "b.eml").write_bytes(
        b"Message-ID: <b@x.pt>\r\nCc: luis.coelho@lindoservico.pt\r\nFrom: c@fora.pt\r\n\r\nx")
    (corpus / "c.eml").write_bytes(
        b"Message-ID: <c@x.pt>\r\nTo: cliente@fora.pt\r\nFrom: outro@fora.pt\r\n\r\nx")
    sync = _store(tmp_path)

    first = scopes.backfill(settings, sync=sync)
    assert (first["messages"], first["header"], first["participant"], first["unattributed"]) == (3, 1, 1, 1)
    assert first["rows"] == 2
    assert first["by_address"] == {"orcamentos@lindoservico.pt": 1, "luis.coelho@lindoservico.pt": 1}

    second = scopes.backfill(settings, sync=sync)
    assert second["rows"] == 0, "re-running must write nothing"
    assert second["unattributed"] == 1
    sync.close()


def test_backfill_does_not_downgrade_live_fetch_rows(tmp_path):
    settings = _settings(tmp_path)
    corpus = tmp_path / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    (corpus / "a.eml").write_bytes(
        b"Message-ID: <a@x.pt>\r\nEnvelope-to: orcamentos@lindoservico.pt\r\nFrom: c@fora.pt\r\n\r\nx")
    sync = _store(tmp_path)
    sync.set_message_scopes(canonical_id("<a@x.pt>", b""), ["orcamentos@lindoservico.pt"], "fetch")
    scopes.backfill(settings, sync=sync)
    assert sync.message_scopes(canonical_id("<a@x.pt>", b"")) == {
        "orcamentos@lindoservico.pt": "fetch"
    }
    sync.close()


def test_backfill_on_an_empty_corpus_is_a_no_op(tmp_path):
    sync = _store(tmp_path)
    res = scopes.backfill(_settings(tmp_path), sync=sync)
    assert res["messages"] == 0 and res["rows"] == 0
    sync.close()


# ── thread aggregation: union, and the unattributed bucket ───────────────────

def _crm(tmp_path, rows: list[tuple[str, str]]):
    path = tmp_path / "crm.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE interactions (message_id TEXT, thread_root TEXT)")
    conn.executemany("INSERT INTO interactions VALUES (?, ?)", rows)
    conn.commit()
    conn.close()
    return path


def test_thread_scope_is_the_union_of_its_messages(tmp_path):
    sync = _store(tmp_path)
    sync.set_message_scopes("mid:a", ["orcamentos@lindoservico.pt"], "fetch")
    sync.set_message_scopes("mid:b", ["luis.coelho@lindoservico.pt"], "header")
    crm = _crm(tmp_path, [("mid:a", "root1"), ("mid:b", "root1")])
    assert scopes.thread_scopes(sync, crm) == {
        "root1": {"orcamentos@lindoservico.pt", "luis.coelho@lindoservico.pt"}
    }
    sync.close()


def test_one_unattributed_member_keeps_the_thread_admin_visible(tmp_path):
    """Union can only widen: the attributed reader keeps it AND an admin can still find it."""
    sync = _store(tmp_path)
    sync.set_message_scopes("mid:a", ["orcamentos@lindoservico.pt"], "fetch")
    crm = _crm(tmp_path, [("mid:a", "root1"), ("mid:unknown", "root1")])
    assert scopes.thread_scopes(sync, crm) == {
        "root1": {"orcamentos@lindoservico.pt", scopes.SCOPE_UNATTRIBUTED}
    }
    sync.close()


def test_thread_with_no_root_falls_back_to_its_message_id(tmp_path):
    sync = _store(tmp_path)
    sync.set_message_scopes("mid:a", ["orcamentos@lindoservico.pt"], "fetch")
    crm = _crm(tmp_path, [("mid:a", None)])
    assert scopes.thread_scopes(sync, crm) == {"mid:a": {"orcamentos@lindoservico.pt"}}
    sync.close()


def test_missing_crm_db_yields_empty_not_an_exception(tmp_path):
    sync = _store(tmp_path)
    assert scopes.thread_scopes(sync, tmp_path / "absent.db") == {}
    sync.close()


# ── visible(): the policy seam Phase D will call ─────────────────────────────

def test_admin_sees_everything_including_the_unattributed_bucket():
    assert scopes.visible({scopes.SCOPE_UNATTRIBUTED}, [], is_admin=True) is True
    assert scopes.visible(set(), [], is_admin=True) is True


def test_grant_intersection_decides_for_everyone_else():
    thread = {"orcamentos@lindoservico.pt"}
    assert scopes.visible(thread, ["orcamentos@lindoservico.pt"]) is True
    assert scopes.visible(thread, ["luis.coelho@lindoservico.pt"]) is False


def test_unattributed_is_grantable_to_a_named_delegate():
    assert scopes.visible({scopes.SCOPE_UNATTRIBUTED}, [scopes.SCOPE_UNATTRIBUTED]) is True


def test_empty_scope_fails_closed_for_non_admins():
    """A thread naming a message we never cached must not become visible-to-all."""
    assert scopes.visible(set(), ["orcamentos@lindoservico.pt"]) is False
    assert scopes.visible(set(), [scopes.SCOPE_UNATTRIBUTED]) is True
