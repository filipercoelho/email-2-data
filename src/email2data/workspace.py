"""Workspace 'write' layer — precious human decisions that overlay the regenerable job specs.

Decisions live in their OWN SQLite (``out/workspace.db``), keyed by ``(message_id, field)``, and
**survive a triage re-run** — they are never produced by the pipeline. ``merge`` overlays them onto a
jobspec dict (``source='user', confirmed=True``) and recomputes Gate-1 readiness. This is the core of
the "confirm one lead" slice — no server, fully testable. The read layer (jobspecs) stays immutable;
this layer only adds.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import jobspec as js

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    message_id TEXT NOT NULL,
    field      TEXT NOT NULL,
    value      TEXT NOT NULL,
    ts         TEXT,
    PRIMARY KEY (message_id, field)
);
CREATE TABLE IF NOT EXISTS reclassifications (
    message_id   TEXT NOT NULL,
    field        TEXT NOT NULL,
    value_auto   TEXT,
    value_human  TEXT NOT NULL,
    ts           TEXT,
    PRIMARY KEY (message_id, field)
);
-- Projects (cross-thread). A project groups MANY email threads into one canonical job spec, born at
-- lead arrival and eventually offloaded to an external estimating system. Precious + hand-curated, so
-- it lives here (survives triage re-runs) alongside decisions/reclassifications. See project.py.
CREATE TABLE IF NOT EXISTS projects (
    project_id   TEXT PRIMARY KEY,   -- "p-0001" (max existing + 1; deterministic, test-stable)
    title        TEXT NOT NULL,
    client_email TEXT,
    client_name  TEXT,
    stage        TEXT NOT NULL,      -- LEAD|GATHERING|ESTIMABLE|QUOTED|WON|LOST|ARCHIVED
    n_items      INTEGER DEFAULT 1,  -- canonical line-item count (project-owned)
    created_ts   TEXT,
    updated_ts   TEXT,
    external_id  TEXT,               -- external system id (e.g. materials-costing PRJ-xxxx); NULL until exported
    external_ts  TEXT,
    coverage     REAL,               -- denormalized Gate-1 coverage (0-1), recomputed on write/sync (v3)
    estimable    INTEGER             -- denormalized Gate-1 estimable flag, recomputed on write/sync (v3)
);
CREATE TABLE IF NOT EXISTS project_threads (
    project_id  TEXT NOT NULL,
    thread_root TEXT NOT NULL,       -- joins crm.interactions.thread_root
    added_ts    TEXT,
    PRIMARY KEY (project_id, thread_root)
);
-- The canonical, cross-thread merge target. Mirrors `decisions` but keyed by project_id, and reuses
-- the SAME wire address scheme (jobspec.address): "deadline" (job-level) or "material#0" (per item).
CREATE TABLE IF NOT EXISTS project_fields (
    project_id  TEXT NOT NULL,
    field       TEXT NOT NULL,
    value       TEXT NOT NULL,
    source_mid  TEXT,                -- provenance: message the value came from ("" if hand-typed)
    ts          TEXT,
    channel     TEXT,                -- provenance: how it was acquired (email|call|meeting|whatsapp|sms|manual) (v3)
    asserted_by TEXT,                -- provenance: who stated it (the counterparty contact) (v3)
    acquired_at TEXT,                -- when the knowledge was acquired in the real world (!= ts/recorded) (v3)
    PRIMARY KEY (project_id, field)
);
-- Append-only audit of canonical-field edits. project_fields overwrites in place; this keeps the
-- prior value/source so a hand-curated decision is never silently lost (mirrors why
-- reclassifications keeps value_auto alongside value_human). op ∈ set | clear.
CREATE TABLE IF NOT EXISTS project_field_history (
    project_id  TEXT NOT NULL,
    field       TEXT NOT NULL,       -- a spec field address, OR a reserved __kind__ for an event (v3)
    op          TEXT NOT NULL,       -- "set" | "clear" | "event" (note/decision/opinion/todo) (v3)
    old_value   TEXT,                -- value before this edit (NULL if none / for events)
    new_value   TEXT,                -- value after this edit (NULL on clear); the event text for events
    source_mid  TEXT,
    ts          TEXT,
    channel     TEXT,                -- provenance: how it was acquired (v3)
    asserted_by TEXT,                -- provenance: who stated it (v3)
    acquired_at TEXT                 -- when the knowledge was acquired (timeline sort key; v3)
);
CREATE INDEX IF NOT EXISTS ix_pfh_project ON project_field_history(project_id, field);
-- Thread-level response state (the cockpit Fila): one owner + a handled flag per email thread, keyed
-- by crm.interactions.thread_root. Precious + hand-set (survives triage re-runs), like decisions.
-- handled_ts lets the response clock REOPEN a thread when a new inbound arrives after it was handled.
CREATE TABLE IF NOT EXISTS thread_state (
    thread_root TEXT PRIMARY KEY,
    owner       TEXT,                -- team member id/label; "" or NULL = sem dono
    handled     INTEGER DEFAULT 0,
    handled_ts  TEXT,                -- UTC ISO when marked handled; NULL when not handled
    updated_ts  TEXT
);
-- Identity links (C1b): human-confirmed "this email belongs to account cluster X".
-- Overrides the deterministic clustering in accounts.py. Precious and additive — never auto-set.
CREATE TABLE IF NOT EXISTS identity_links (
    email       TEXT PRIMARY KEY,
    account_key TEXT NOT NULL,       -- the cluster key (e.g. "acme.pt" or "nif:501234567")
    ts          TEXT
);
"""

# Precious-DB schema version. Bumped when `SCHEMA` changes shape; `Workspace.connect` records it in
# PRAGMA user_version and runs any pending migrations (see `_migrate`). Unlike crm.db, this database
# is never rebuilt, so it must evolve in place. v3 (2026-06-14): provenance columns + denormalized
# coverage/estimable + reserved __kind__ events in project_field_history (ADR-015).
SCHEMA_VERSION = 3

RECLASSIFY_FIELDS = frozenset({"counterparty", "purpose", "priority"})
# Reserved decision field: how many line items this job has (human override of the LLM's item count).
# Stored in the same decisions table; it is structural, not a spec field, so it is never confirmed back.
ITEM_COUNT_FIELD = "__n_items__"

# Off-email knowledge (ADR-015): NOTE/DECISION/OPINION/TODO are append-only rows in
# project_field_history under a reserved field namespace (op="event"), mirroring ITEM_COUNT_FIELD.
# A single table → the timeline is one indexed SELECT, no second store, no UNION.
EVENT_KINDS = ("note", "decision", "opinion", "todo")
EVENT_OP = "event"


def event_field(kind: str) -> str:
    """The reserved project_field_history.field address for an event of ``kind`` (e.g. ``__note__``)."""
    return f"__{kind}__"


class Workspace:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> "Workspace":
        # check_same_thread=False: FastAPI dispatches sync routes to a threadpool; this is a single-user
        # local app so cross-thread reuse of one connection is safe (access is effectively serial).
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._migrate()
        return self

    def _add_column(self, table: str, col: str, decl: str) -> None:
        """Idempotently add a column to an existing table. ``CREATE TABLE IF NOT EXISTS`` cannot add
        columns to a table that already exists, so a new column on the never-rebuilt precious DB MUST
        go through an explicit ALTER here. Guarded by ``PRAGMA table_info`` so it is a no-op on a fresh
        DB (which already has the column from ``SCHEMA``) and safe to re-run."""
        assert self._conn is not None
        cols = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    def _migrate(self) -> None:
        """Bring the precious DB up to ``SCHEMA_VERSION`` in place. This DB is never rebuilt (unlike
        crm.db), so it must evolve via migrations rather than a drop-and-recreate.

        ``SCHEMA`` (all ``CREATE TABLE IF NOT EXISTS``) additively delivers NEW TABLES and brings a
        fresh DB to the latest shape. But it CANNOT add a new COLUMN to a table that already exists —
        so every column added in a new version needs an explicit, guarded ``ALTER TABLE ADD COLUMN``
        in a numbered ``if version < N:`` block here, BEFORE the version stamp. A forgotten ALTER
        silently ships a column-less DB that throws "no such column" on first write (ADR-010/-015).
        """
        assert self._conn is not None
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            return
        if version < 3:
            # Provenance bundle on the canonical field tables + the audit/event log.
            for table in ("project_fields", "project_field_history"):
                self._add_column(table, "channel", "TEXT")
                self._add_column(table, "asserted_by", "TEXT")
                self._add_column(table, "acquired_at", "TEXT")
            # Denormalized Gate-1 summary on the project row (read by the cheap list view).
            self._add_column("projects", "coverage", "REAL")
            self._add_column("projects", "estimable", "INTEGER")
            # Backfill provenance for existing rows: a real source_mid came from an email; the
            # ''/sentinel hand-typed rows are manual. acquired_at falls back to the record time.
            self._conn.execute(
                "UPDATE project_fields SET channel = CASE WHEN source_mid IS NOT NULL "
                "AND source_mid NOT IN ('', 'user') THEN 'email' ELSE 'manual' END, "
                "acquired_at = ts WHERE channel IS NULL")
            self._conn.execute(
                "UPDATE project_field_history SET channel = CASE WHEN source_mid IS NOT NULL "
                "AND source_mid NOT IN ('', 'user') THEN 'email' ELSE 'manual' END, "
                "acquired_at = ts WHERE channel IS NULL")
        self._conn.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def confirm(self, message_id: str, field: str, value: str, ts: str = "") -> None:
        """Record/overwrite one human decision (the authoritative source). Idempotent upsert."""
        assert self._conn is not None, "call connect() first"
        self._conn.execute(
            "INSERT INTO decisions(message_id, field, value, ts) VALUES (?,?,?,?) "
            "ON CONFLICT(message_id, field) DO UPDATE SET value=excluded.value, ts=excluded.ts",
            (message_id, field, value, ts),
        )
        self._conn.commit()

    def clear(self, message_id: str, field: str) -> None:
        assert self._conn is not None, "call connect() first"
        self._conn.execute("DELETE FROM decisions WHERE message_id=? AND field=?", (message_id, field))
        self._conn.commit()

    def decisions_for(self, message_id: str) -> dict[str, str]:
        assert self._conn is not None, "call connect() first"
        rows = self._conn.execute(
            "SELECT field, value FROM decisions WHERE message_id=?", (message_id,)).fetchall()
        return {r["field"]: r["value"] for r in rows}

    def reclassify(self, message_id: str, field: str, value_auto: str | None, value_human: str) -> None:
        """Record a human correction to a triage verdict field. Stores the auto value alongside
        for use as a labeled training pair (auto→human) later."""
        assert self._conn is not None, "call connect() first"
        assert field in RECLASSIFY_FIELDS, f"unknown reclassify field: {field}"
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._conn.execute(
            "INSERT INTO reclassifications(message_id,field,value_auto,value_human,ts) VALUES(?,?,?,?,?)"
            " ON CONFLICT(message_id,field) DO UPDATE SET"
            "  value_auto=excluded.value_auto, value_human=excluded.value_human, ts=excluded.ts",
            (message_id, field, value_auto, value_human, ts),
        )
        self._conn.commit()

    def clear_reclassify(self, message_id: str, field: str) -> None:
        assert self._conn is not None, "call connect() first"
        self._conn.execute(
            "DELETE FROM reclassifications WHERE message_id=? AND field=?", (message_id, field)
        )
        self._conn.commit()

    def get_reclassifications(self) -> dict[str, dict[str, str]]:
        """Return {message_id: {field: value_human, …}} for embedding in the report HTML."""
        assert self._conn is not None, "call connect() first"
        rows = self._conn.execute(
            "SELECT message_id, field, value_human FROM reclassifications"
        ).fetchall()
        result: dict[str, dict[str, str]] = {}
        for r in rows:
            result.setdefault(r["message_id"], {})[r["field"]] = r["value_human"]
        return result

    # -- thread state (cockpit Fila: owner + handled, keyed by thread_root) ----------------------------

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def set_thread_owner(self, thread_root: str, owner: str, ts: str = "") -> None:
        """Assign (or clear, with ``owner=""``) the owner of a thread. Idempotent upsert."""
        assert self._conn is not None, "call connect() first"
        self._conn.execute(
            "INSERT INTO thread_state(thread_root, owner, updated_ts) VALUES (?,?,?) "
            "ON CONFLICT(thread_root) DO UPDATE SET owner=excluded.owner, updated_ts=excluded.updated_ts",
            (thread_root, owner, ts or self._now_iso()),
        )
        self._conn.commit()

    def set_thread_handled(self, thread_root: str, handled: bool, ts: str = "") -> None:
        """Mark a thread handled / unhandled. Sets ``handled_ts`` on handle (so a later inbound reopens
        it), clears it on unhandle (the undo path)."""
        assert self._conn is not None, "call connect() first"
        when = ts or self._now_iso()
        self._conn.execute(
            "INSERT INTO thread_state(thread_root, handled, handled_ts, updated_ts) VALUES (?,?,?,?) "
            "ON CONFLICT(thread_root) DO UPDATE SET handled=excluded.handled, "
            "handled_ts=excluded.handled_ts, updated_ts=excluded.updated_ts",
            (thread_root, int(handled), when if handled else None, when),
        )
        self._conn.commit()

    def thread_states(self) -> dict[str, dict[str, Any]]:
        """``{thread_root: {owner, handled, handled_ts}}`` for the cockpit overlay (mirrors
        ``get_reclassifications``; embedded in the report / consumed by ``cockpit.build_fila``)."""
        assert self._conn is not None, "call connect() first"
        rows = self._conn.execute(
            "SELECT thread_root, owner, handled, handled_ts FROM thread_state").fetchall()
        return {r["thread_root"]: {"owner": r["owner"] or "", "handled": bool(r["handled"]),
                                   "handled_ts": r["handled_ts"]} for r in rows}

    # -- identity links (C1b) ---------------------------------------------------------------

    def set_identity_link(self, email: str, account_key: str, ts: str = "") -> None:
        """Confirm that ``email`` belongs to account cluster ``account_key``.

        Overrides the deterministic clustering in ``accounts.cluster()`` for this address.
        Idempotent upsert — safe to call again if the user changes their mind."""
        assert self._conn is not None, "call connect() first"
        self._conn.execute(
            "INSERT INTO identity_links(email, account_key, ts) VALUES (?,?,?) "
            "ON CONFLICT(email) DO UPDATE SET account_key=excluded.account_key, ts=excluded.ts",
            (email.lower().strip(), account_key, ts or self._now_iso()),
        )
        self._conn.commit()

    def identity_links(self) -> dict[str, str]:
        """``{email: account_key}`` — all confirmed identity links for the account clusterer."""
        assert self._conn is not None, "call connect() first"
        rows = self._conn.execute("SELECT email, account_key FROM identity_links").fetchall()
        return {r["email"]: r["account_key"] for r in rows}

    def set_item_count(self, message_id: str, n: int, ts: str = "") -> None:
        """Record the human-chosen number of line items (add/remove rows in the workspace)."""
        self.confirm(message_id, ITEM_COUNT_FIELD, str(max(1, int(n))), ts)

    def remove_item(self, message_id: str, index: int) -> None:
        """Drop line item ``index``: delete its per-item decisions, shift the rows above it down by one,
        and decrement the item count. Keeps addresses contiguous so the spec has no gaps."""
        assert self._conn is not None, "call connect() first"
        decisions = self.decisions_for(message_id)
        n = int(decisions.get(ITEM_COUNT_FIELD) or 0)
        # Rebuild the per-item decisions with item `index` removed and higher indices renumbered.
        per_item: dict[int, dict[str, str]] = {}
        for addr, value in decisions.items():
            base, i = js.parse_address(addr)
            if i is not None:
                per_item.setdefault(i, {})[base] = value
        for i in sorted(per_item):  # clear all per-item rows first, then rewrite the survivors
            for base in per_item[i]:
                self.clear(message_id, js.address(base, i))
        for i, fields in per_item.items():
            if i == index:
                continue
            new_i = i - 1 if i > index else i
            for base, value in fields.items():
                self.confirm(message_id, js.address(base, new_i), value)
        if n:
            self.set_item_count(message_id, max(1, n - 1))

    def merge(self, spec_dict: dict[str, Any]) -> tuple[js.JobSpec, dict[str, Any]]:
        """Overlay this job's decisions onto its auto-spec, then recompute Gate-1 readiness.

        The item count is itself an overlayable decision: if the human added/removed rows we pad with
        empty line items or truncate to match before applying per-item confirmations."""
        spec = js.JobSpec.from_dict(spec_dict)
        decisions = self.decisions_for(spec.message_id)
        n = int(decisions.pop(ITEM_COUNT_FIELD, "") or len(spec.items) or 1)
        n = max(1, n)
        while len(spec.items) < n:
            spec.items.append({k: js.SpecField() for k in js.ITEM_KEYS})
        del spec.items[n:]
        for field, value in decisions.items():
            js.confirm(spec, field, value)  # source=user, confirmed=True
        return spec, js.readiness(spec)
