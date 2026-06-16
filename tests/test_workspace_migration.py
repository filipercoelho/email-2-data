"""Precious-DB upgrade path (ADR-010/-015): the one DB that is never rebuilt must gain v3 columns
in place. CREATE TABLE IF NOT EXISTS cannot add columns to an existing table, so this exercises the
REAL upgrade — a prior-version DB *with rows* — not a fresh in-memory one (the gap the red-team flagged).
"""

from __future__ import annotations

import sqlite3

from email2data import project as p
from email2data.captures import CaptureStore
from email2data.workspace import SCHEMA_VERSION, Workspace

# The v2 shape of the three tables the v3 migration alters — WITHOUT the new columns. Hand-written so
# the test reproduces a real old DB rather than depending on today's SCHEMA string.
_V2_SCHEMA = """
CREATE TABLE projects (
    project_id TEXT PRIMARY KEY, title TEXT NOT NULL, client_email TEXT, client_name TEXT,
    stage TEXT NOT NULL, n_items INTEGER DEFAULT 1, created_ts TEXT, updated_ts TEXT,
    external_id TEXT, external_ts TEXT
);
CREATE TABLE project_fields (
    project_id TEXT NOT NULL, field TEXT NOT NULL, value TEXT NOT NULL,
    source_mid TEXT, ts TEXT, PRIMARY KEY (project_id, field)
);
CREATE TABLE project_field_history (
    project_id TEXT NOT NULL, field TEXT NOT NULL, op TEXT NOT NULL,
    old_value TEXT, new_value TEXT, source_mid TEXT, ts TEXT
);
"""


def _make_v2_db(path):
    """A v2 workspace.db with rows: one email-sourced field, one hand-typed field, one history row."""
    c = sqlite3.connect(path)
    c.executescript(_V2_SCHEMA)
    c.execute("INSERT INTO projects(project_id,title,stage,n_items,created_ts,updated_ts)"
              " VALUES ('p-0001','Velho','LEAD',1,'2026-06-01','2026-06-01')")
    c.execute("INSERT INTO project_fields(project_id,field,value,source_mid,ts)"
              " VALUES ('p-0001','deadline','2026-07-01','mid:abc','2026-06-01')")
    c.execute("INSERT INTO project_fields(project_id,field,value,source_mid,ts)"
              " VALUES ('p-0001','budget','500','','2026-06-02')")  # hand-typed (sentinel '')
    c.execute("INSERT INTO project_field_history(project_id,field,op,old_value,new_value,source_mid,ts)"
              " VALUES ('p-0001','deadline','set',NULL,'2026-07-01','mid:abc','2026-06-01')")
    c.execute("PRAGMA user_version = 2")
    c.commit()
    c.close()


def _cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _has_table(conn, name):
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def test_v2_to_v3_adds_columns_backfills_and_roundtrips(tmp_path):
    db = tmp_path / "workspace.db"
    _make_v2_db(db)

    ws = Workspace(db).connect()          # runs executescript(SCHEMA) (no-op on existing) + _migrate
    conn = ws._conn

    # 1) the version is stamped to the latest AND the v3 columns actually exist on the pre-existing tables
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    for table in ("project_fields", "project_field_history"):
        assert {"channel", "asserted_by", "acquired_at"} <= _cols(conn, table), table
    assert {"coverage", "estimable"} <= _cols(conn, "projects")

    # 2) old rows survive and are backfilled: email-sourced -> 'email', hand-typed sentinel -> 'manual'
    store = p.ProjectStore(conn)
    prov = store.field_provenance("p-0001")
    assert prov["deadline"]["channel"] == "email" and prov["deadline"]["acquired_at"] == "2026-06-01"
    assert prov["budget"]["channel"] == "manual"

    # 3) a provenance-bearing write round-trips (would raise "no such column" if the ALTER was skipped)
    store.set_field("p-0001", "material", "inox 304", source_mid="",
                    channel="call", asserted_by="João", acquired_at="2026-06-13")
    assert store.field_provenance("p-0001")["material"] == {
        "source_mid": "", "channel": "call", "asserted_by": "João", "acquired_at": "2026-06-13"}
    ws.close()


def test_fresh_db_is_latest_and_migrate_is_idempotent(tmp_path):
    db = tmp_path / "fresh.db"
    ws = Workspace(db).connect()
    assert ws._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert _has_table(ws._conn, "captures") and _has_table(ws._conn, "capture_users")
    ws.close()
    # Reconnecting an already-migrated DB must be a clean no-op (guarded ALTERs / early return).
    ws2 = Workspace(db).connect()
    assert {"channel", "acquired_at"} <= _cols(ws2._conn, "project_fields")
    ws2.close()


def test_events_are_appended_not_current_values(tmp_path):
    ws = Workspace(tmp_path / "w.db").connect()
    store = p.ProjectStore(ws._conn)
    pid = store.create("A")
    store.add_event(pid, "decision", "avançar em inox", channel="call",
                    asserted_by="Pedro", acquired_at="2026-06-13")
    store.add_event(pid, "note", "cliente sem pressa", channel="meeting", acquired_at="2026-06-12")
    # events live in history only, never as a current field value
    assert store.fields_for(pid) == {}
    tl = store.timeline(pid)
    assert [r["op"] for r in tl] == ["event", "event"]
    assert tl[0]["acquired_at"] == "2026-06-13" and tl[0]["new_value"] == "avançar em inox"  # newest first
    assert tl[0]["field"] == "__decision__" and tl[0]["channel"] == "call"
    ws.close()


# The v3 shape of the two tables the v4 migration touches — WITHOUT the v4 close-out columns, and with
# a single owner on thread_state (the column the v4 backfill carries into thread_owners).
_V3_PARTIAL = """
CREATE TABLE projects (
    project_id TEXT PRIMARY KEY, title TEXT NOT NULL, client_email TEXT, client_name TEXT,
    stage TEXT NOT NULL, n_items INTEGER DEFAULT 1, created_ts TEXT, updated_ts TEXT,
    external_id TEXT, external_ts TEXT, coverage REAL, estimable INTEGER
);
CREATE TABLE thread_state (
    thread_root TEXT PRIMARY KEY, owner TEXT, handled INTEGER DEFAULT 0, handled_ts TEXT, updated_ts TEXT
);
"""


def _make_v3_db(path):
    c = sqlite3.connect(path)
    c.executescript(_V3_PARTIAL)
    c.execute("INSERT INTO projects(project_id,title,stage,created_ts,updated_ts)"
              " VALUES ('p-0001','Velho','LEAD','2026-06-01','2026-06-01')")
    c.execute("INSERT INTO thread_state(thread_root,owner,handled,updated_ts)"
              " VALUES ('t1','Pedro',0,'2026-06-01')")          # a pre-v4 single owner
    c.execute("INSERT INTO thread_state(thread_root,owner,handled,updated_ts)"
              " VALUES ('t2','',1,'2026-06-01')")                # handled, no owner
    c.execute("PRAGMA user_version = 3")
    c.commit()
    c.close()


def test_v3_to_v4_adds_closeout_columns_and_backfills_single_owner(tmp_path):
    db = tmp_path / "workspace.db"
    _make_v3_db(db)

    ws = Workspace(db).connect()                       # executescript(SCHEMA) (no-op on existing) + _migrate
    conn = ws._conn

    # version stamped to latest; the close-out columns now exist on the pre-existing projects table
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert {"close_party", "close_reason", "closed_at"} <= _cols(conn, "projects")

    # the single owner is carried forward into the multi-owner join table — no Fila assignment lost
    assert ws.thread_owners() == {"t1": ["Pedro"]}
    st = ws.thread_states()
    assert st["t1"]["owners"] == ["Pedro"] and st["t1"]["owner"] == "Pedro"   # legacy single still readable
    assert st["t2"]["owners"] == [] and st["t2"]["handled"] is True           # handled-only thread survives
    ws.close()


def test_multi_owner_thread_roundtrip(tmp_path):
    ws = Workspace(tmp_path / "w.db").connect()
    ws.set_thread_owners("t1", ["Pedro", "Rita", "Pedro", " "])   # de-dupes + trims blanks
    assert ws.thread_owners()["t1"] == ["Pedro", "Rita"]
    ws.add_thread_owner("t1", "Filipe")
    assert set(ws.thread_owners()["t1"]) == {"Pedro", "Rita", "Filipe"}
    ws.remove_thread_owner("t1", "Pedro")
    assert set(ws.thread_owners()["t1"]) == {"Rita", "Filipe"}
    ws.set_thread_owners("t1", [])                                 # clear
    assert "t1" not in ws.thread_owners()
    ws.close()


def test_roster_add_remove_is_additive_and_deduped(tmp_path):
    ws = Workspace(tmp_path / "w.db").connect()
    assert ws.roster() == []
    ws.roster_add("Sofia")
    ws.roster_add("Sofia")          # idempotent
    ws.roster_add("  ")             # blank ignored
    assert ws.roster() == ["Sofia"]
    ws.roster_remove("Sofia")
    assert ws.roster() == []
    ws.close()


# The v4 shape of the projects table (close-out columns present) WITHOUT the v5 intake capture tables —
# the state of a real DB upgraded to v4 before conversational intake (ADR-019/-020/-021) landed.
_V4_PARTIAL = """
CREATE TABLE projects (
    project_id TEXT PRIMARY KEY, title TEXT NOT NULL, client_email TEXT, client_name TEXT,
    stage TEXT NOT NULL, n_items INTEGER DEFAULT 1, created_ts TEXT, updated_ts TEXT,
    external_id TEXT, external_ts TEXT, coverage REAL, estimable INTEGER,
    close_party TEXT, close_reason TEXT, closed_at TEXT
);
"""


def _make_v4_db(path):
    c = sqlite3.connect(path)
    c.executescript(_V4_PARTIAL)
    c.execute("INSERT INTO projects(project_id,title,stage,created_ts,updated_ts)"
              " VALUES ('p-0007','Estante Sousa','LEAD','2026-06-10','2026-06-10')")
    c.execute("PRAGMA user_version = 4")
    c.commit()
    c.close()


def test_v4_to_v5_adds_capture_tables_and_preserves_projects(tmp_path):
    """v5 adds the intake capture queue + allowlist as BRAND-NEW tables (delivered by SCHEMA, no ALTER).
    A real v4 DB with rows must gain them in place without losing the precious project (ADR-019/-020)."""
    db = tmp_path / "workspace.db"
    _make_v4_db(db)

    ws = Workspace(db).connect()        # executescript(SCHEMA) creates the new tables; _migrate stamps v5
    conn = ws._conn

    # version stamped to latest; the two brand-new intake tables now exist
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 5
    assert _has_table(conn, "captures") and _has_table(conn, "capture_users")

    # the pre-existing project survives the migration untouched (precious DB, never rebuilt)
    assert conn.execute(
        "SELECT title FROM projects WHERE project_id='p-0007'").fetchone()[0] == "Estante Sousa"

    # a capture round-trips through the new store (would raise "no such table" if SCHEMA was not updated)
    cap = CaptureStore(conn)
    cid, created = cap.add(telegram_message_id=11, telegram_chat_id=99, raw_text="prazo 30 jun",
                           channel="call", asserted_by="Pedro")
    assert created is True and cid == "c-99-11"
    assert [c["capture_id"] for c in cap.list_pending()] == ["c-99-11"]
    ws.close()


def test_connect_enables_wal_and_busy_timeout(tmp_path):
    # The intake worker is a SEPARATE process writing this precious DB alongside the webapp (R4);
    # WAL + busy_timeout are the foundation that lets them coexist instead of "database is locked".
    ws = Workspace(tmp_path / "w.db").connect()
    assert ws._conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert ws._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    ws.close()


def test_wal_lets_a_writer_proceed_while_a_reader_holds_a_transaction(tmp_path):
    """Under the default rollback journal a held READ (a page-building cursor) blocks a writer; WAL
    lets the intake worker's write land while the webapp holds a read transaction (R4). Pins the
    cross-process concurrency foundation — this raised 'database is locked' before WAL."""
    db = tmp_path / "w.db"
    reader = Workspace(db).connect()
    reader._conn.execute("BEGIN")
    reader._conn.execute("SELECT * FROM captures").fetchall()   # hold a SHARED read lock

    writer = Workspace(db).connect()                            # the worker's own connection
    cid, created = CaptureStore(writer._conn).add(
        telegram_message_id=1, telegram_chat_id=2, raw_text="x")
    assert created is True                                      # would lock + raise pre-WAL
    assert CaptureStore(writer._conn).get(cid)["raw_text"] == "x"

    reader._conn.execute("ROLLBACK")
    reader.close()
    writer.close()
