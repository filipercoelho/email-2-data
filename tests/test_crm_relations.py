"""Tests for CrmStore relation queries: thread(), by_contact(), by_entity(), related().

All tests use an in-memory SQLite database (via tmp_path) so they are fast, fully isolated,
and require no real email corpus.  The helpers build minimal env/verdict dicts that match the
shapes produced by parse_eml() and TriageResult.to_dict().
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from email2data.crm import CrmStore, _INDEXABLE_ENTITY_KEYS, _norm_entity


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

def make_env(
    message_id: str,
    *,
    from_email: str = "sender@example.com",
    from_name: str = "Sender",
    to: list[str] | None = None,
    cc: list[str] | None = None,
    subject: str = "Test",
    date: str = "2026-01-01T10:00:00",
    in_reply_to: str | None = None,
    references: list[str] | None = None,
    attachments: list[dict] | None = None,
) -> dict[str, Any]:
    """Minimal parsed-envelope dict."""
    return {
        "message_id": message_id,
        "from": {"email": from_email, "name": from_name},
        "to": [{"email": e, "name": ""} for e in (to or [])],
        "cc": [{"email": e, "name": ""} for e in (cc or [])],
        "subject": subject,
        "date": date,
        "in_reply_to": in_reply_to,
        "references": references or [],
        "attachments": attachments or [],
        "body_text": "body",
    }


def make_verdict(
    *,
    counterparty: str = "CLIENT",
    purpose: str = "PO_FROM_CLIENT",
    priority: str = "HIGH",
    urgency: int = 80,
    direction: str = "inbound",
    entities: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "counterparty": counterparty,
        "purpose": purpose,
        "priority": priority,
        "urgency": urgency,
        "direction": direction,
        "entities": entities or {},
    }


@pytest.fixture()
def store(tmp_path: Path) -> CrmStore:
    s = CrmStore(tmp_path / "crm.db").connect()
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Schema & basic record()
# ---------------------------------------------------------------------------

def test_record_creates_interaction(store: CrmStore) -> None:
    store.record(make_env("m1"), make_verdict())
    rows = store._conn.execute("SELECT * FROM interactions WHERE message_id='m1'").fetchall()
    assert len(rows) == 1
    assert rows[0]["counterparty"] == "CLIENT"


def test_record_stores_entities_json(store: CrmStore) -> None:
    ents = {"client_name": "Acme", "nif": "123456789"}
    store.record(make_env("m1"), make_verdict(entities=ents))
    row = store._conn.execute("SELECT entities FROM interactions WHERE message_id='m1'").fetchone()
    stored = json.loads(row["entities"])
    assert stored["client_name"] == "Acme"
    assert stored["nif"] == "123456789"


def test_record_null_entities_stores_null(store: CrmStore) -> None:
    store.record(make_env("m1"), make_verdict(entities=None))
    row = store._conn.execute("SELECT entities FROM interactions WHERE message_id='m1'").fetchone()
    assert row["entities"] is None


def test_record_populates_participants(store: CrmStore) -> None:
    env = make_env("m1", to=["alice@example.com", "bob@example.com"], cc=["carol@example.com"])
    store.record(env, make_verdict())
    rows = store._conn.execute("SELECT email, role FROM participants WHERE message_id='m1'").fetchall()
    by_role = {r["role"]: r["email"] for r in rows}
    assert by_role["from"] == "sender@example.com"
    assert by_role["to"] in {"alice@example.com", "bob@example.com"}
    # all three roles recorded
    roles = {r["role"] for r in rows}
    assert {"from", "to", "cc"}.issubset(roles)


def test_record_populates_entity_refs(store: CrmStore) -> None:
    ents = {"client_name": "Acme Lda", "nif": "987654321", "action_requested": "send quote"}
    store.record(make_env("m1"), make_verdict(entities=ents))
    rows = store._conn.execute("SELECT entity_key, entity_value FROM entity_refs WHERE message_id='m1'").fetchall()
    indexed = {r["entity_key"]: r["entity_value"] for r in rows}
    # indexable keys are present, normalised to lowercase
    assert indexed["client_name"] == "acme lda"
    assert indexed["nif"] == "987654321"
    # non-indexable key (action_requested) is NOT indexed
    assert "action_requested" not in indexed


def test_record_idempotent_on_repeat(store: CrmStore) -> None:
    """Calling record() twice with the same message_id must not double-count contacts."""
    store.record(make_env("m1"), make_verdict())
    store.record(make_env("m1"), make_verdict())
    c = store._conn.execute("SELECT msg_count FROM contacts WHERE email='sender@example.com'").fetchone()
    assert c["msg_count"] == 1  # not 2


def test_counts_includes_new_tables(store: CrmStore) -> None:
    store.record(make_env("m1", to=["a@b.com"]), make_verdict(entities={"nif": "111222333"}))
    c = store.counts()
    assert c["interactions"] == 1
    assert c["participants"] >= 2   # from + to
    assert c["entity_refs"] >= 1


# ---------------------------------------------------------------------------
# thread()
# ---------------------------------------------------------------------------

def test_thread_returns_all_messages_in_thread(store: CrmStore) -> None:
    root_id = "<root@example.com>"
    store.record(make_env(root_id, subject="Original", date="2026-01-01T10:00:00"), make_verdict())
    store.record(make_env("<r1@example.com>", subject="Re: Original", date="2026-01-02T10:00:00",
                          in_reply_to=root_id, references=[root_id]), make_verdict())
    store.record(make_env("<r2@example.com>", subject="Re: Original", date="2026-01-03T10:00:00",
                          references=[root_id, "<r1@example.com>"]), make_verdict())

    result = store.thread(root_id)
    assert len(result) == 3
    # ordered oldest-first
    assert result[0]["message_id"] == root_id
    assert result[-1]["date"] > result[0]["date"]


def test_thread_root_message_only(store: CrmStore) -> None:
    """A standalone message (no replies) returns just itself."""
    store.record(make_env("<solo@example.com>"), make_verdict())
    result = store.thread("<solo@example.com>")
    assert len(result) == 1
    assert result[0]["message_id"] == "<solo@example.com>"


def test_thread_unknown_root_returns_empty(store: CrmStore) -> None:
    assert store.thread("<does-not-exist@x.com>") == []


# ---------------------------------------------------------------------------
# by_contact()
# ---------------------------------------------------------------------------

def test_by_contact_finds_sender(store: CrmStore) -> None:
    store.record(make_env("<m1>", from_email="alice@corp.com", date="2026-01-01T09:00:00"), make_verdict())
    store.record(make_env("<m2>", from_email="alice@corp.com", date="2026-01-02T09:00:00"), make_verdict())
    store.record(make_env("<m3>", from_email="bob@other.com"), make_verdict())

    result = store.by_contact("alice@corp.com")
    ids = {r["message_id"] for r in result}
    assert ids == {"<m1>", "<m2>"}


def test_by_contact_finds_recipient(store: CrmStore) -> None:
    """A contact appearing as To: (not From:) must still be found."""
    store.record(make_env("<m1>", from_email="lindo@lindoservico.pt", to=["client@client.com"]), make_verdict())
    store.record(make_env("<m2>", from_email="client@client.com"), make_verdict())

    result = store.by_contact("client@client.com")
    ids = {r["message_id"] for r in result}
    assert "<m1>" in ids and "<m2>" in ids


def test_by_contact_case_insensitive(store: CrmStore) -> None:
    store.record(make_env("<m1>", from_email="Alice@Corp.COM"), make_verdict())
    result = store.by_contact("alice@corp.com")
    assert len(result) == 1


def test_by_contact_unknown_returns_empty(store: CrmStore) -> None:
    assert store.by_contact("nobody@nowhere.com") == []


# ---------------------------------------------------------------------------
# by_entity()
# ---------------------------------------------------------------------------

def test_by_entity_finds_matching_nif(store: CrmStore) -> None:
    ents = {"nif": "123456789"}
    store.record(make_env("<m1>"), make_verdict(entities=ents))
    store.record(make_env("<m2>"), make_verdict(entities={"nif": "999999999"}))

    result = store.by_entity("nif", "123456789")
    assert len(result) == 1
    assert result[0]["message_id"] == "<m1>"


def test_by_entity_is_case_insensitive(store: CrmStore) -> None:
    store.record(make_env("<m1>"), make_verdict(entities={"client_name": "Acme LDA"}))
    result = store.by_entity("client_name", "ACME LDA")
    assert len(result) == 1


def test_by_entity_multiple_messages_same_entity(store: CrmStore) -> None:
    ents = {"client_name": "Acme"}
    store.record(make_env("<m1>", date="2026-01-01T10:00:00"), make_verdict(entities=ents))
    store.record(make_env("<m2>", date="2026-01-03T10:00:00"), make_verdict(entities=ents))
    store.record(make_env("<m3>"), make_verdict(entities={"client_name": "Other"}))

    result = store.by_entity("client_name", "acme")
    ids = {r["message_id"] for r in result}
    assert ids == {"<m1>", "<m2>"}
    # ordered most-recent-first
    assert result[0]["date"] > result[1]["date"]


def test_by_entity_unindexed_key_returns_empty(store: CrmStore) -> None:
    store.record(make_env("<m1>"), make_verdict(entities={"action_requested": "send quote"}))
    # action_requested is not in _INDEXABLE_ENTITY_KEYS
    assert "action_requested" not in _INDEXABLE_ENTITY_KEYS
    result = store.by_entity("action_requested", "send quote")
    assert result == []


def test_by_entity_empty_value_returns_empty(store: CrmStore) -> None:
    assert store.by_entity("nif", "") == []
    assert store.by_entity("nif", "   ") == []


# ---------------------------------------------------------------------------
# related()
# ---------------------------------------------------------------------------

def test_related_unknown_message_id(store: CrmStore) -> None:
    result = store.related("<does-not-exist>")
    assert result == {"thread": [], "by_contact": [], "by_entity": []}


def test_related_excludes_seed_from_all_lists(store: CrmStore) -> None:
    """The queried message must not appear in its own relation lists."""
    ents = {"nif": "111222333", "client_name": "Acme"}
    env = make_env("<root>", from_email="a@a.com")
    store.record(env, make_verdict(entities=ents))

    result = store.related("<root>")
    all_mids = (
        [r["message_id"] for r in result["thread"]]
        + [r["message_id"] for r in result["by_contact"]]
        + [r["message_id"] for r in result["by_entity"]]
    )
    assert "<root>" not in all_mids


def test_related_thread_siblings(store: CrmStore) -> None:
    root = "<root@x.com>"
    store.record(make_env(root, subject="Enquiry", date="2026-01-01T10:00:00"), make_verdict())
    store.record(make_env("<r1>", in_reply_to=root, references=[root],
                          subject="Re: Enquiry", date="2026-01-02T09:00:00"), make_verdict())

    result = store.related(root)
    assert len(result["thread"]) == 1
    assert result["thread"][0]["message_id"] == "<r1>"


def test_related_by_contact_cross_thread(store: CrmStore) -> None:
    """A second thread from the same client should appear in by_contact."""
    store.record(make_env("<m1>", from_email="client@co.pt", date="2026-01-01T10:00:00"), make_verdict())
    store.record(make_env("<m2>", from_email="client@co.pt", date="2026-02-01T10:00:00"), make_verdict())

    result = store.related("<m1>")
    ids = {r["message_id"] for r in result["by_contact"]}
    assert "<m2>" in ids
    assert "<m1>" not in ids


def test_related_by_entity_cross_thread(store: CrmStore) -> None:
    """Two emails from different contacts sharing a NIF must be linked via entity."""
    ents = {"nif": "555444333"}
    store.record(make_env("<m1>", from_email="a@a.com", date="2026-01-01T10:00:00"),
                 make_verdict(entities=ents))
    store.record(make_env("<m2>", from_email="b@b.com", date="2026-02-01T10:00:00"),
                 make_verdict(entities=ents))

    result = store.related("<m1>")
    ids = {r["message_id"] for r in result["by_entity"]}
    assert "<m2>" in ids
    assert result["by_entity"][0]["_matched_entity"] == "nif"


def test_related_by_entity_deduplicates_across_fields(store: CrmStore) -> None:
    """A message that matches on two entity fields must appear only once in by_entity."""
    ents = {"nif": "111111111", "client_name": "Acme"}
    store.record(make_env("<m1>"), make_verdict(entities=ents))
    store.record(make_env("<m2>"), make_verdict(entities=ents))  # shares both

    result = store.related("<m1>")
    by_entity_ids = [r["message_id"] for r in result["by_entity"]]
    assert by_entity_ids.count("<m2>") == 1  # deduplicated


def test_related_all_three_groups_independent(store: CrmStore) -> None:
    """A message can appear in multiple groups (thread + entity); each list is independent."""
    root = "<root>"
    ents = {"nif": "777888999"}
    store.record(make_env(root, from_email="a@a.com", date="2026-01-01T10:00:00"),
                 make_verdict(entities=ents))
    # reply in same thread AND shares the same NIF
    store.record(make_env("<reply>", from_email="a@a.com", in_reply_to=root, references=[root],
                          date="2026-01-02T10:00:00"), make_verdict(entities=ents))

    result = store.related(root)
    assert any(r["message_id"] == "<reply>" for r in result["thread"])
    assert any(r["message_id"] == "<reply>" for r in result["by_contact"])
    assert any(r["message_id"] == "<reply>" for r in result["by_entity"])


# ---------------------------------------------------------------------------
# _norm_entity helper
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("Acme Lda", "acme lda"),
    ("  SPACES  ", "spaces"),
    ("123456789", "123456789"),
    ("", None),
    ("   ", None),
    (None, None),
    (42, None),          # non-string
])
def test_norm_entity(raw, expected) -> None:
    assert _norm_entity(raw) == expected


# ---------------------------------------------------------------------------
# _INDEXABLE_ENTITY_KEYS completeness smoke test
# ---------------------------------------------------------------------------

def test_indexable_keys_are_subset_of_entities_fields() -> None:
    from email2data.schema import Entities
    import dataclasses
    all_fields = {f.name for f in dataclasses.fields(Entities)}
    assert _INDEXABLE_ENTITY_KEYS.issubset(all_fields), (
        f"Unknown entity keys in _INDEXABLE_ENTITY_KEYS: {_INDEXABLE_ENTITY_KEYS - all_fields}"
    )


# ---------------------------------------------------------------------------
# Threading contract (regression)
# ---------------------------------------------------------------------------

def test_connection_is_usable_from_a_worker_thread(store):
    """The webapp shares ONE CrmStore across FastAPI's threadpool: sync routes run off the thread that
    opened the connection. The connection must therefore be opened with check_same_thread=False, or
    every off-thread query (project reads via build_canonical->thread(), and /api/relations) raises
    sqlite3.ProgrammingError under uvicorn. Starlette's TestClient runs single-threaded and hides this,
    so we assert the contract directly: query from another thread and expect no error."""
    from concurrent.futures import ThreadPoolExecutor

    store.record(make_env("root@x"), make_verdict())
    with ThreadPoolExecutor(max_workers=1) as ex:
        rows = ex.submit(store.thread, "root@x").result()
        rel = ex.submit(store.related, "root@x").result()
    assert [r["message_id"] for r in rows] == ["root@x"]
    assert rel == {"thread": [], "by_contact": [], "by_entity": []}
