"""CaptureStore: the conversational-intake queue + allowlist (ADR-019/-020/-021).

Pins the MVP store contract: a default-deny allowlist (ADR-019 §6), an idempotent capture insert (a
Telegram retry is a clean no-op — ADR-020), the pending-edits queue the user validates (R9), and that
a discarded capture is RETAINED for audit (ADR-020 preserve-at-core), never destroyed.
"""

from __future__ import annotations

import sqlite3

from email2data.captures import CaptureStore
from email2data.workspace import SCHEMA


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def test_allowlist_is_default_deny():
    cap = CaptureStore(_conn())
    assert cap.is_allowed(123) is False              # unknown sender -> denied (ADR-019 §6)
    cap.allow(123, display_name="Pedro", roster_owner="Pedro", added_by="admin")
    assert cap.is_allowed(123) is True
    cap.disable(123)
    assert cap.is_allowed(123) is False              # soft-disabled -> denied, but the audit row is kept
    assert cap.get_user(123)["display_name"] == "Pedro"
    cap.allow(123)                                   # re-enable
    assert cap.is_allowed(123) is True


def test_add_persists_a_pending_capture():
    cap = CaptureStore(_conn())
    cid, created = cap.add(telegram_message_id=11, telegram_chat_id=99,
                           content_class="conversation", raw_text="prazo 30 jun",
                           media_paths=["c-99-11/photo.jpg"], channel="call", asserted_by="Pedro")
    assert created is True and cid == "c-99-11"
    assert [c["capture_id"] for c in cap.list_pending()] == ["c-99-11"]
    got = cap.get(cid)
    assert got["raw_text"] == "prazo 30 jun"
    assert got["media_paths"] == ["c-99-11/photo.jpg"]   # JSON round-trips back to a list
    assert got["status"] == "stored" and got["inferred_project_id"] is None


def test_capture_insert_is_idempotent_on_retry():
    # A Telegram retry of the same (chat, message) must NOT create a second row (ADR-020 idempotency).
    cap = CaptureStore(_conn())
    cid1, created1 = cap.add(telegram_message_id=11, telegram_chat_id=99, raw_text="primeiro")
    cid2, created2 = cap.add(telegram_message_id=11, telegram_chat_id=99, raw_text="reenviado")
    assert created1 is True and created2 is False        # the second is a clean no-op
    assert cid1 == cid2 == "c-99-11"
    count = cap._conn.execute("SELECT COUNT(*) FROM captures").fetchone()[0]
    assert count == 1                                    # exactly one row, the original text preserved
    assert cap.get(cid1)["raw_text"] == "primeiro"


def test_resolve_then_apply_lifecycle():
    cap = CaptureStore(_conn())
    cid, _ = cap.add(telegram_message_id=11, telegram_chat_id=99, raw_text="nota")
    cap.set_project(cid, "p-0007")
    resolved = cap.get(cid)
    assert resolved["inferred_project_id"] == "p-0007" and resolved["status"] == "parsed"
    assert [c["capture_id"] for c in cap.list_pending()] == ["c-99-11"]   # parsed is still pending (R9)
    cap.mark_scrubbed(cid)
    assert cap.get(cid)["telegram_scrubbed_at"]          # stamped (ADR-020 persist-then-scrub)
    cap.mark_applied(cid)
    done = cap.get(cid)
    assert done["status"] == "applied" and done["applied_ts"]
    assert cap.list_pending() == []                      # an applied capture leaves the queue


def test_discarded_capture_is_retained_for_audit():
    # preserve-at-core (ADR-020): discard removes it from the pending queue but NEVER destroys the row.
    cap = CaptureStore(_conn())
    cid, _ = cap.add(telegram_message_id=12, telegram_chat_id=99, raw_text="engano")
    cap.discard(cid)
    assert cap.list_pending() == []
    assert cap.get(cid)["status"] == "discarded"         # the row + its media are still there


def test_re_enable_preserves_labels():
    # A bare allow(id) re-enable must NOT wipe the sender->owner mapping that feeds asserted_by.
    cap = CaptureStore(_conn())
    cap.allow(7, display_name="Pedro", roster_owner="Pedro Ferreira", added_by="admin")
    cap.disable(7)
    cap.allow(7)                                         # bare re-enable
    u = cap.get_user(7)
    assert u["enabled"] == 1
    assert u["display_name"] == "Pedro" and u["roster_owner"] == "Pedro Ferreira"
    assert u["added_by"] == "admin"                      # the original audit is preserved
    cap.allow(7, display_name="Pedro F.")                # an explicit non-empty label DOES update
    assert cap.get_user(7)["display_name"] == "Pedro F."
    assert cap.get_user(7)["roster_owner"] == "Pedro Ferreira"   # untouched (not supplied)


def test_terminal_captures_are_immutable():
    # preserve-at-core (ADR-020): an applied or discarded capture is terminal — never resurrected.
    cap = CaptureStore(_conn())
    cid, _ = cap.add(telegram_message_id=1, telegram_chat_id=2, raw_text="x")
    cap.set_project(cid, "p-0001")
    cap.mark_applied(cid)
    cap.discard(cid)                                     # no-op: already terminal (applied)
    assert cap.get(cid)["status"] == "applied"
    cap.set_project(cid, "p-0002")                       # no-op: terminal, mapping unchanged
    assert cap.get(cid)["inferred_project_id"] == "p-0001"

    cid2, _ = cap.add(telegram_message_id=3, telegram_chat_id=2, raw_text="y")
    cap.discard(cid2)
    cap.mark_applied(cid2)                               # no-op: already discarded (terminal)
    assert cap.get(cid2)["status"] == "discarded"
