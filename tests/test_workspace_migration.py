"""Precious-DB upgrade path (ADR-010/-015): the one DB that is never rebuilt must gain v3 columns
in place. CREATE TABLE IF NOT EXISTS cannot add columns to an existing table, so this exercises the
REAL upgrade — a prior-version DB *with rows* — not a fresh in-memory one (the gap the red-team flagged).
"""

from __future__ import annotations

import sqlite3

from email2data import project as p
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


def test_v2_to_v3_adds_columns_backfills_and_roundtrips(tmp_path):
    db = tmp_path / "workspace.db"
    _make_v2_db(db)

    ws = Workspace(db).connect()          # runs executescript(SCHEMA) (no-op on existing) + _migrate
    conn = ws._conn

    # 1) the version is stamped AND the new columns actually exist on the pre-existing tables
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 3
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


def test_fresh_db_is_v3_and_migrate_is_idempotent(tmp_path):
    db = tmp_path / "fresh.db"
    ws = Workspace(db).connect()
    assert ws._conn.execute("PRAGMA user_version").fetchone()[0] == 3
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
