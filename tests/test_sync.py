"""Incremental sync: the UID watermark (fetch) and the results.jsonl gate (triage).

No network — a fake IMAP connection models UIDVALIDITY + UID SEARCH/FETCH so we can prove:
  * first fetch bootstraps by date and persists a watermark;
  * the next fetch asks only for UID > watermark and returns nothing new;
  * a UIDVALIDITY change re-bootstraps;
  * triage classifies only message_ids absent from results.jsonl, and appends.
"""

import json

import pytest

import email2data.fetch as fetch
from email2data.sync import SyncStore


# ── fake IMAP ────────────────────────────────────────────────────────────────

def _eml(uid: int) -> bytes:
    return (f"Message-ID: <m{uid}@x.pt>\r\nFrom: a@x.pt\r\nSubject: s{uid}\r\n\r\nbody{uid}").encode()


class FakeIMAP:
    """Models just enough of imaplib for fetch.py: EXAMINE, UIDVALIDITY, UID SEARCH/FETCH, logout."""

    def __init__(self, uidvalidity: int, messages: dict[int, bytes]):
        self.uidvalidity = uidvalidity
        self.messages = messages           # uid -> raw bytes
        self.searches: list[tuple] = []     # recorded SEARCH criteria

    def select(self, mailbox, readonly=False):
        return ("OK", [b"1"])

    def response(self, name):
        if name == "UIDVALIDITY":
            return ("UIDVALIDITY", [str(self.uidvalidity).encode()])
        return (name, [None])

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            self.searches.append(args)
            crit = args[1] if len(args) > 1 else None
            all_uids = sorted(self.messages)
            if crit == "UID":
                lo = int(args[2].split(":")[0])
                hits = [u for u in all_uids if u >= lo]
                # IMAP "N:*" always echoes the highest message even when N exceeds every UID.
                if all_uids and all_uids[-1] not in hits:
                    hits.append(all_uids[-1])
                uids = sorted(set(hits))
            else:  # SINCE
                uids = all_uids
            return ("OK", [b" ".join(str(u).encode() for u in uids)])
        if cmd == "FETCH":
            uid = int(args[0])
            raw = self.messages.get(uid)
            if raw is None:
                return ("NO", [None])
            return ("OK", [(f"1 (UID {uid} BODY[])".encode(), raw)])
        return ("OK", [None])

    def logout(self):
        return ("BYE", [None])


def _settings(tmp_path):
    return {
        "__settings_path__": str(tmp_path / "config" / "settings.json"),
        "imap": {"host": "x", "accounts": [{"id": "acc", "username": "u", "password_env": "P",
                                            "mailboxes": ["INBOX"]}]},
        "fetch": {"since_days": 30, "max_messages": 200},
    }


# ── SyncStore ────────────────────────────────────────────────────────────────

def test_syncstore_cursor_roundtrip(tmp_path):
    s = SyncStore(tmp_path / "sync.db").connect()
    assert s.get_cursor("acc", "INBOX") is None
    s.set_cursor("acc", "INBOX", 100, 7)
    assert s.get_cursor("acc", "INBOX") == (100, 7)
    s.set_cursor("acc", "INBOX", 100, 12)        # upsert advances
    assert s.get_cursor("acc", "INBOX") == (100, 12)
    s.close()


# ── incremental fetch ────────────────────────────────────────────────────────

def test_fetch_bootstraps_then_goes_incremental(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    fake = FakeIMAP(uidvalidity=42, messages={1: _eml(1), 2: _eml(2), 3: _eml(3)})
    monkeypatch.setattr(fetch, "_connect", lambda s, a: fake)
    sync = SyncStore(tmp_path / "sync.db").connect()

    # First run: no cursor -> bootstrap by SINCE, caches all 3, watermark -> 3.
    written = fetch.fetch_account(settings, settings["imap"]["accounts"][0], sync=sync)
    assert len(written) == 3
    assert fake.searches[0][1] == "SINCE"
    assert sync.get_cursor("acc", "INBOX") == (42, 3)

    # Second run, same epoch: searches UID 4:* -> server echoes highest (3), filtered out -> 0 new.
    fake.searches.clear()
    written = fetch.fetch_account(settings, settings["imap"]["accounts"][0], sync=sync)
    assert fake.searches[0][1] == "UID" and fake.searches[0][2] == "4:*"
    assert all(not p.name.endswith(".tmp") for p in written)   # only the (already-cached) echo, no new write
    # nothing new arrived; watermark unchanged
    assert sync.get_cursor("acc", "INBOX") == (42, 3)

    # A new message arrives -> incremental picks up only it, advances watermark to 4.
    fake.messages[4] = _eml(4)
    written = fetch.fetch_account(settings, settings["imap"]["accounts"][0], sync=sync)
    assert sync.get_cursor("acc", "INBOX") == (42, 4)
    sync.close()


def test_fetch_rebootstraps_on_uidvalidity_change(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    fake = FakeIMAP(uidvalidity=42, messages={1: _eml(1), 2: _eml(2)})
    monkeypatch.setattr(fetch, "_connect", lambda s, a: fake)
    sync = SyncStore(tmp_path / "sync.db").connect()
    fetch.fetch_account(settings, settings["imap"]["accounts"][0], sync=sync)
    assert sync.get_cursor("acc", "INBOX") == (42, 2)

    # Server renumbers (UIDVALIDITY changes): stored last_uid is meaningless -> bootstrap by SINCE.
    fake.uidvalidity = 99
    fake.searches.clear()
    fetch.fetch_account(settings, settings["imap"]["accounts"][0], sync=sync)
    assert fake.searches[0][1] == "SINCE"
    assert sync.get_cursor("acc", "INBOX") == (99, 2)
    sync.close()


def test_full_flag_forces_bootstrap_even_with_cursor(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    fake = FakeIMAP(uidvalidity=42, messages={1: _eml(1), 2: _eml(2)})
    monkeypatch.setattr(fetch, "_connect", lambda s, a: fake)
    sync = SyncStore(tmp_path / "sync.db").connect()
    fetch.fetch_account(settings, settings["imap"]["accounts"][0], sync=sync)
    fake.searches.clear()
    fetch.fetch_account(settings, settings["imap"]["accounts"][0], sync=sync, full=True)
    assert fake.searches[0][1] == "SINCE"      # full ignores the watermark
    sync.close()


# ── incremental triage ───────────────────────────────────────────────────────

def test_triage_corpus_skips_already_processed_and_appends(tmp_path, monkeypatch):
    from email2data import cascade
    from email2data.config import paths
    from email2data.schema import EXTRACTOR_VERSION, Entities, TriageResult

    settings = _settings(tmp_path)
    p = paths(settings, settings["__settings_path__"])

    # Two corpus emails; one already classified in results.jsonl.
    (p["corpus_dir"] / "a.eml").write_bytes(_eml(1))
    (p["corpus_dir"] / "b.eml").write_bytes(_eml(2))
    (p["out_dir"] / "results.jsonl").write_text(
        json.dumps({"message_id": "mid:m1@x.pt", "counterparty": "OTHER"}) + "\n", encoding="utf-8")

    calls = {"n": 0}

    def fake_triage(raw, playbook, store, client, settings):
        calls["n"] += 1
        from email2data.envelope import parse_eml
        mid = parse_eml(raw)["message_id"]
        return TriageResult(message_id=mid, counterparty="CLIENT", purpose="OTHER", direction="inbound",
                            priority="MEDIUM", urgency=10, confidence=0.9, reason="t", entities=Entities(),
                            extractor_version=EXTRACTOR_VERSION, subject="s", from_addr="a@x.pt",
                            decided_by="tier1:test")

    monkeypatch.setattr(cascade.classifier, "load_playbook", lambda p: "pb")
    monkeypatch.setattr(cascade.classifier, "make_client", lambda s: object())
    monkeypatch.setattr(cascade, "triage", fake_triage)

    counts = cascade.triage_corpus(settings, store=object())
    assert counts["new"] == 1 and counts["skipped"] == 1 and calls["n"] == 1
    lines = [json.loads(x) for x in (p["out_dir"] / "results.jsonl").read_text().splitlines() if x]
    assert {r["message_id"] for r in lines} == {"mid:m1@x.pt", "mid:m2@x.pt"}   # appended, not overwritten

    # full=True reclassifies both and overwrites.
    calls["n"] = 0
    counts = cascade.triage_corpus(settings, store=object(), full=True)
    assert counts["new"] == 2 and counts["skipped"] == 0 and calls["n"] == 2
