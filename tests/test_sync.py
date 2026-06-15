"""Incremental sync: the UID watermark (fetch) and the results.jsonl gate (triage).

No network — a fake IMAP connection models UIDVALIDITY + UID SEARCH/FETCH so we can prove:
  * first fetch bootstraps by date and persists a watermark;
  * the next fetch asks only for UID > watermark and returns nothing new;
  * a UIDVALIDITY change re-bootstraps;
  * triage classifies only message_ids absent from results.jsonl, and appends.
"""

import json

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


# ── multi-account isolation (fetch_all) ──────────────────────────────────────

def _two_accounts(tmp_path, ids=("bad", "good")):
    s = _settings(tmp_path)
    s["imap"]["accounts"] = [
        {"id": i, "username": "u", "password_env": "P", "mailboxes": ["INBOX"]} for i in ids]
    return s


def test_fetch_all_isolates_a_failing_account(tmp_path, monkeypatch):
    """One bad/expired account must NOT starve the others: fetch_all audits the failure, keeps going,
    and the healthy account still syncs + advances its watermark (the red-team starvation fix)."""
    settings = _two_accounts(tmp_path)
    good = FakeIMAP(uidvalidity=42, messages={1: _eml(1), 2: _eml(2)})

    def fake_connect(s, account):
        if account["id"] == "bad":
            raise fetch.FetchError("IMAP error for account 'bad': [AUTHENTICATIONFAILED] auth failed")
        return good
    monkeypatch.setattr(fetch, "_connect", fake_connect)

    counts = fetch.fetch_all(settings)
    assert counts["bad"] == 0 and counts["good"] == 2          # good synced despite bad failing first

    # good advanced its watermark; the failure was audited (not silent), with no credential leaked.
    sync = SyncStore(tmp_path / "out" / "sync.db").connect()
    assert sync.get_cursor("good", "INBOX") == (42, 2)
    sync.close()
    audit_txt = (tmp_path / "out" / "audit.jsonl").read_text()
    assert "fetch_account_failed" in audit_txt and "bad" in audit_txt and "auth failed" in audit_txt


def test_fetch_all_raises_when_every_account_fails(tmp_path, monkeypatch):
    """A TOTAL outage (every account down) must surface loudly rather than report a misleading 0 — and
    only AFTER trying every account (the aggregated error names each, both are audited). This
    discriminates the new try-all-then-aggregate branch from the old fail-fast-on-the-first one."""
    import pytest
    settings = _two_accounts(tmp_path, ids=("a", "b"))
    monkeypatch.setattr(fetch, "_connect",
                        lambda s, a: (_ for _ in ()).throw(fetch.FetchError(f"boom {a['id']}")))
    with pytest.raises(fetch.FetchError) as ei:
        fetch.fetch_all(settings)
    # both accounts were attempted: the aggregated message names each (old fail-fast would name only 'a')
    assert "a" in str(ei.value) and "b" in str(ei.value)
    assert (tmp_path / "out" / "audit.jsonl").read_text().count("fetch_account_failed") == 2


def test_cli_reports_total_fetch_failure_cleanly(tmp_path, monkeypatch, capsys):
    """The CLI must surface a total fetch failure as a tidy 'Fetch error: …' line + rc 1, not a raw
    traceback (only ConfigError was caught before)."""
    from email2data import cli
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "settings.json").write_text(json.dumps({
        "imap": {"host": "x", "accounts": [
            {"id": "a", "username": "u", "password_env": "P", "mailboxes": ["INBOX"]}]},
        "fetch": {"since_days": 30, "max_messages": 200},
    }), encoding="utf-8")
    monkeypatch.setattr(fetch, "fetch_all",
                        lambda *a, **k: (_ for _ in ()).throw(fetch.FetchError("all accounts failed: a (boom)")))
    rc = cli.main(["--settings", str(cfg / "settings.json"), "fetch"])
    assert rc == 1
    assert "Fetch error" in capsys.readouterr().err


def test_triage_escalates_to_needs_review_when_tier1_fails(tmp_path, monkeypatch):
    """A Tier-1 failure (e.g. LLM/auth down) must NOT drop the message — it escalates to NEEDS_REVIEW
    so it stays visible in the human queue (VISION: uncertain escalates, never disappears)."""
    from email2data import cascade
    from email2data.config import paths
    from email2data.llm import LLMError

    settings = _settings(tmp_path)
    p = paths(settings, settings["__settings_path__"])
    (p["corpus_dir"] / "a.eml").write_bytes(_eml(1))

    monkeypatch.setattr(cascade.classifier, "load_playbook", lambda _p: "pb")
    monkeypatch.setattr(cascade.classifier, "make_client", lambda s: object())
    monkeypatch.setattr(cascade, "triage",
                        lambda *a, **k: (_ for _ in ()).throw(LLMError("vertex down")))

    counts = cascade.triage_corpus(settings, store=object())
    assert counts["failed"] == 1 and counts["new"] == 1        # written, not dropped
    rows = [json.loads(x) for x in (p["out_dir"] / "results.jsonl").read_text().splitlines() if x]
    assert len(rows) == 1
    r = rows[0]
    assert r["message_id"] == "mid:m1@x.pt"
    assert r["priority"] == "NEEDS_REVIEW" and r["decided_by"] == "tier1:error"

    # Idempotent: a second incremental run skips it (it's in results.jsonl), no duplicate row.
    counts2 = cascade.triage_corpus(settings, store=object())
    assert counts2["skipped"] == 1 and counts2["new"] == 0
    rows2 = [x for x in (p["out_dir"] / "results.jsonl").read_text().splitlines() if x]
    assert len(rows2) == 1


def test_no_message_id_dedups_across_inbox_and_sent(tmp_path, monkeypatch):
    """Closed dedup edge: a Message-ID-LESS email in BOTH INBOX and a Sent folder maps to ONE corpus
    file — the id is hashed from the ORIGINAL bytes (before the X-Email2Data-Source header is
    injected), so the copies no longer diverge and the Sent copy wins (direction = outbound)."""
    from email import message_from_bytes

    from email2data.config import paths
    from email2data.signals import header_signals

    settings = _settings(tmp_path)
    settings["imap"]["accounts"][0]["mailboxes"] = ["INBOX", "INBOX.Sent"]
    no_mid = b"From: orcamentos@lindoservico.pt\r\nTo: c@client.pt\r\nSubject: ping\r\n\r\nbody\r\n"
    fake = FakeIMAP(uidvalidity=42, messages={1: no_mid})
    monkeypatch.setattr(fetch, "_connect", lambda s, a: fake)

    fetch.fetch_account(settings, settings["imap"]["accounts"][0])
    p = paths(settings, settings["__settings_path__"])
    files = list(p["corpus_dir"].glob("*.eml"))
    assert len(files) == 1                                          # one file, not two
    assert header_signals(message_from_bytes(files[0].read_bytes())).direction == "outbound"


def test_tier1_failure_preserves_outbound_direction(tmp_path, monkeypatch):
    """The escalated NEEDS_REVIEW row keeps the one fact still available offline — direction from header
    signals (here a Sent-folder copy → outbound) — so an escalated client reply isn't mis-binned."""
    from email2data import cascade
    from email2data.config import paths
    from email2data.llm import LLMError

    settings = _settings(tmp_path)
    p = paths(settings, settings["__settings_path__"])
    (p["corpus_dir"] / "s.eml").write_bytes(
        b"X-Email2Data-Source: INBOX.Sent\r\nMessage-ID: <s1@x.pt>\r\n"
        b"From: orcamentos@lindoservico.pt\r\nTo: c@client.pt\r\nSubject: re\r\n\r\nbody")
    monkeypatch.setattr(cascade.classifier, "load_playbook", lambda _p: "pb")
    monkeypatch.setattr(cascade.classifier, "make_client", lambda s: object())
    monkeypatch.setattr(cascade, "triage", lambda *a, **k: (_ for _ in ()).throw(LLMError("down")))

    cascade.triage_corpus(settings, store=object())
    rows = [json.loads(x) for x in (p["out_dir"] / "results.jsonl").read_text().splitlines() if x]
    assert rows[0]["priority"] == "NEEDS_REVIEW" and rows[0]["decided_by"] == "tier1:error"
    assert rows[0]["direction"] == "outbound"          # offline header signal preserved, not defaulted


def test_full_run_clears_a_tier1_error_escalation(tmp_path, monkeypatch):
    """Documented recovery: once the LLM is back, `triage --full` overwrites a tier1:error NEEDS_REVIEW
    row with the real classification (no stale escalation, no duplicate row)."""
    from email2data import cascade
    from email2data.config import paths
    from email2data.llm import LLMError
    from email2data.schema import EXTRACTOR_VERSION, Entities, TriageResult

    settings = _settings(tmp_path)
    p = paths(settings, settings["__settings_path__"])
    (p["corpus_dir"] / "a.eml").write_bytes(_eml(1))
    monkeypatch.setattr(cascade.classifier, "load_playbook", lambda _p: "pb")
    monkeypatch.setattr(cascade.classifier, "make_client", lambda s: object())

    monkeypatch.setattr(cascade, "triage", lambda *a, **k: (_ for _ in ()).throw(LLMError("down")))
    cascade.triage_corpus(settings, store=object())            # 1) LLM down → escalated

    def ok(raw, playbook, store, client, settings):
        from email2data.envelope import parse_eml
        return TriageResult(message_id=parse_eml(raw)["message_id"], counterparty="CLIENT", purpose="OTHER",
                            direction="inbound", priority="HIGH", urgency=80, confidence=0.9, reason="ok",
                            entities=Entities(), extractor_version=EXTRACTOR_VERSION, subject="s",
                            from_addr="a@x.pt", decided_by="tier1:test")
    monkeypatch.setattr(cascade, "triage", ok)
    cascade.triage_corpus(settings, store=object(), full=True)  # 2) LLM back → full reclassify overwrites

    rows = [json.loads(x) for x in (p["out_dir"] / "results.jsonl").read_text().splitlines() if x]
    assert len(rows) == 1
    assert rows[0]["decided_by"] == "tier1:test" and rows[0]["priority"] == "HIGH"
