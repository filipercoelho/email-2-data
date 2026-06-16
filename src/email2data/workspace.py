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
    estimable    INTEGER,            -- denormalized Gate-1 estimable flag, recomputed on write/sync (v3)
    close_party  TEXT,               -- CANCELLED/LOST close-out: who ended it — client|supplier|our (v4)
    close_reason TEXT,               -- free-text why the project was cancelled/lost (v4)
    closed_at    TEXT                 -- UTC ISO when it was closed; NULL while open (v4)
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
-- Multi-owner (v4): owners are a SET per Fila thread / per project, so several team members can
-- co-own. Join tables (mirror project_threads); the legacy single thread_state.owner column is
-- backfilled into thread_owners on migration and then read/written only via these tables.
CREATE TABLE IF NOT EXISTS thread_owners (
    thread_root TEXT NOT NULL,
    owner       TEXT NOT NULL,
    ts          TEXT,
    PRIMARY KEY (thread_root, owner)
);
CREATE TABLE IF NOT EXISTS project_owners (
    project_id  TEXT NOT NULL,
    owner       TEXT NOT NULL,
    ts          TEXT,
    PRIMARY KEY (project_id, owner)
);
-- In-app owner roster (v4): names addable from the UI, AUGMENTING the static settings.json `team`
-- ("define new owners" without editing config). Effective roster = settings.team ∪ this table.
CREATE TABLE IF NOT EXISTS roster (
    name     TEXT PRIMARY KEY,
    added_ts TEXT
);
-- Conversational intake (ADR-019/-020/-021): the Telegram capture queue. A capture is durably
-- persisted HERE *before* it is scrubbed from Telegram (ADR-020 §2 persist-then-scrub), and stays
-- until the user validates it into a project (ADR-019 §5 — no auto-apply). Precious: once Telegram is
-- scrubbed it is not a fallback, so this row + its media on disk are the only copy (ADR-020 §4).
CREATE TABLE IF NOT EXISTS captures (
    capture_id           TEXT PRIMARY KEY,   -- "c-<chat>-<message>" (deterministic = idempotency key) (v5)
    telegram_message_id  INTEGER,            -- source Telegram message id (v5)
    telegram_chat_id     INTEGER,            -- source Telegram chat/user id (v5)
    content_class        TEXT,               -- artifact | conversation (content-class router; ADR-019 §5.1) (v5)
    raw_text             TEXT,               -- verbatim text the staffer sent ("" if none) (v5)
    media_paths          TEXT,               -- JSON array of media files on disk, relative to captures_dir (v5)
    inferred_project_id  TEXT,               -- the project the user picked; NULL until resolved (v5)
    channel              TEXT,               -- real-world channel (call|meeting|whatsapp|sms|manual) (v5)
    asserted_by          TEXT,               -- who stated it (the sender's roster name) (v5)
    acquired_at          TEXT,               -- real-world acquisition time (timeline sort key) (v5)
    status               TEXT NOT NULL DEFAULT 'stored',  -- stored|parsed|applied|discarded (v5)
    telegram_scrubbed_at TEXT,               -- UTC ISO when the source was deleted from Telegram (v5)
    created_ts           TEXT,               -- UTC ISO when the capture row was created (v5)
    applied_ts           TEXT,               -- UTC ISO when validated into a project; NULL until then (v5)
    UNIQUE (telegram_message_id, telegram_chat_id)   -- explicit idempotency guarantee (ADR-020)
);
CREATE INDEX IF NOT EXISTS ix_captures_status ON captures(status, created_ts);
-- Intake allowlist (ADR-019 §6 / ADR-021): default-deny identity for the bot, keyed by numeric
-- Telegram user id. enabled=0 soft-disables without losing the audit. This is the app's first identity
-- model; display_name maps a sender to a roster owner for asserted_by attribution.
CREATE TABLE IF NOT EXISTS capture_users (
    telegram_user_id INTEGER PRIMARY KEY,    -- numeric Telegram user id (v5)
    display_name     TEXT,                    -- greeting + maps to the roster owner (v5)
    roster_owner     TEXT,                    -- effective owner name for asserted_by (v5)
    enabled          INTEGER NOT NULL DEFAULT 1,  -- soft-disable flag (v5)
    added_by         TEXT,                    -- who added this sender (v5)
    added_at         TEXT                     -- UTC ISO when added (v5)
);
"""

# Precious-DB schema version. Bumped when `SCHEMA` changes shape; `Workspace.connect` records it in
# PRAGMA user_version and runs any pending migrations (see `_migrate`). Unlike crm.db, this database
# is never rebuilt, so it must evolve in place. v3 (2026-06-14): provenance columns + denormalized
# coverage/estimable + reserved __kind__ events in project_field_history (ADR-015). v4 (2026-06-15):
# project close-out columns (CANCELLED/LOST party+reason+closed_at) + multi-owner join tables
# (thread_owners/project_owners, single owner backfilled) + in-app roster (ADR-017/-018).
# v5 (2026-06-16): conversational-intake capture queue + allowlist (captures/capture_users tables,
# brand-new so delivered by SCHEMA with no ALTER; ADR-019/-020/-021).
SCHEMA_VERSION = 5

# Who ended a project (CANCELLED/LOST close-out). From Lindo's POV; "our" = our own decision.
CLOSE_PARTIES = ("client", "supplier", "our")

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
        # Concurrency foundation (solution-design R4): the conversational-intake worker is a SEPARATE
        # process that opens its own connection to this precious DB and writes `captures` alongside the
        # webapp. WAL lets that worker coexist with the webapp's reads/writes instead of mutually
        # blocking under the default rollback journal; busy_timeout (per-connection, so it must be set
        # on EVERY opener) makes brief concurrent writers serialize instead of raising "database is
        # locked". WAL is a persistent, idempotent header flag — safe on a populated DB — but it adds
        # -wal/-shm sidecar files the backup set MUST include (see docs/05-reference/data-stores.md).
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
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
        if version < 4:
            # Project close-out (cancellation/loss reason). The thread_owners/project_owners/roster
            # tables are delivered by SCHEMA (CREATE IF NOT EXISTS, run before _migrate) — only the new
            # COLUMNS need an explicit ALTER here.
            self._add_column("projects", "close_party", "TEXT")
            self._add_column("projects", "close_reason", "TEXT")
            self._add_column("projects", "closed_at", "TEXT")
            # Carry every existing single owner forward into the multi-owner join table so no Fila
            # assignment is lost when ownership moves from thread_state.owner to thread_owners.
            self._conn.execute(
                "INSERT OR IGNORE INTO thread_owners(thread_root, owner, ts) "
                "SELECT thread_root, owner, updated_ts FROM thread_state "
                "WHERE owner IS NOT NULL AND owner != ''")
        # v5 (captures + capture_users) adds only NEW TABLES — delivered by SCHEMA above, which runs
        # before _migrate — so there is no ALTER to do here; the stamp below records the upgrade. Add a
        # guarded `if version < 5:` block ONLY if a future change adds a COLUMN to a pre-existing table.
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
        """Legacy single-owner setter: REPLACE the owner set with ``[owner]`` (or clear on ``""``).
        Kept so existing callers/tests work; multi-owner goes through ``set_thread_owners``."""
        self.set_thread_owners(thread_root, [owner] if owner else [], ts)

    def set_thread_owners(self, thread_root: str, owners: list[str], ts: str = "") -> None:
        """Replace the FULL owner set of a thread (multi-owner). ``[]`` clears it. Idempotent — the set
        is rewritten each call, so it mirrors a multi-select picker. De-dupes + trims blanks."""
        assert self._conn is not None, "call connect() first"
        when = ts or self._now_iso()
        self._conn.execute("DELETE FROM thread_owners WHERE thread_root=?", (thread_root,))
        for o in dict.fromkeys(n.strip() for n in (owners or []) if n and n.strip()):
            self._conn.execute(
                "INSERT OR IGNORE INTO thread_owners(thread_root, owner, ts) VALUES (?,?,?)",
                (thread_root, o, when))
        self._conn.commit()

    def add_thread_owner(self, thread_root: str, owner: str, ts: str = "") -> None:
        """Add one owner to a thread without disturbing the others (granular toggle)."""
        assert self._conn is not None, "call connect() first"
        nm = (owner or "").strip()
        if nm:
            self._conn.execute(
                "INSERT OR IGNORE INTO thread_owners(thread_root, owner, ts) VALUES (?,?,?)",
                (thread_root, nm, ts or self._now_iso()))
            self._conn.commit()

    def remove_thread_owner(self, thread_root: str, owner: str) -> None:
        """Remove one owner from a thread, leaving the rest."""
        assert self._conn is not None, "call connect() first"
        self._conn.execute("DELETE FROM thread_owners WHERE thread_root=? AND owner=?",
                           (thread_root, owner))
        self._conn.commit()

    def thread_owners(self) -> dict[str, list[str]]:
        """``{thread_root: [owner, ...]}`` — multi-owner assignments, ordered by assignment time."""
        assert self._conn is not None, "call connect() first"
        rows = self._conn.execute(
            "SELECT thread_root, owner FROM thread_owners ORDER BY ts, owner").fetchall()
        out: dict[str, list[str]] = {}
        for r in rows:
            out.setdefault(r["thread_root"], []).append(r["owner"])
        return out

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
        """``{thread_root: {owner, owners, handled, handled_ts}}`` for the cockpit overlay. ``owners`` is
        the multi-owner list (source of truth, from thread_owners); ``owner`` is its first element for
        legacy single-owner readers. A thread can appear via owners-only or handled-only, so both
        tables are unioned (mirrors ``get_reclassifications``; consumed by ``cockpit.build_fila``)."""
        assert self._conn is not None, "call connect() first"
        handled = {r["thread_root"]: r for r in self._conn.execute(
            "SELECT thread_root, handled, handled_ts FROM thread_state").fetchall()}
        owners = self.thread_owners()
        out: dict[str, dict[str, Any]] = {}
        for root in set(handled) | set(owners):
            own = owners.get(root, [])
            h = handled.get(root)
            out[root] = {"owner": own[0] if own else "", "owners": own,
                         "handled": bool(h["handled"]) if h else False,
                         "handled_ts": h["handled_ts"] if h else None}
        return out

    # -- in-app owner roster (v4: "define new owners" without editing settings.json) --------------

    def roster_add(self, name: str, ts: str = "") -> None:
        """Add an owner name to the in-app roster (augments settings.team). Idempotent."""
        assert self._conn is not None, "call connect() first"
        nm = (name or "").strip()
        if nm:
            self._conn.execute("INSERT OR IGNORE INTO roster(name, added_ts) VALUES (?,?)",
                               (nm, ts or self._now_iso()))
            self._conn.commit()

    def roster_remove(self, name: str) -> None:
        """Remove an in-app-added owner name (settings.team names live in config, not here)."""
        assert self._conn is not None, "call connect() first"
        self._conn.execute("DELETE FROM roster WHERE name=?", ((name or "").strip(),))
        self._conn.commit()

    def roster(self) -> list[str]:
        """The in-app-added owner names (sorted). Effective roster = settings.team ∪ this."""
        assert self._conn is not None, "call connect() first"
        return [r["name"] for r in self._conn.execute(
            "SELECT name FROM roster ORDER BY name").fetchall()]

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
