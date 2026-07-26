"""Workspace 'write' layer — precious human decisions that overlay the regenerable job specs.

Decisions live in their OWN SQLite (``out/workspace.db``), keyed by ``(message_id, field)``, and
**survive a triage re-run** — they are never produced by the pipeline. ``merge`` overlays them onto a
jobspec dict (``source='user', confirmed=True``) and recomputes Gate-1 readiness. This is the core of
the "confirm one lead" slice — no server, fully testable. The read layer (jobspecs) stays immutable;
this layer only adds.
"""

from __future__ import annotations

import sqlite3
import unicodedata
import uuid
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
    transcript           TEXT,               -- pt-PT transcript of a voice/audio capture; NULL until transcribed (v6)
    extracted_fields_json TEXT,              -- JSON {field_addr: value} the LLM extracted; never auto-applied (v7)
    confidence           REAL,               -- 0-1 extraction confidence (Increment 2); NULL until extracted (v7)
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
-- Para ti dismissals (v8): "Ignorar" on a decision card is a HUMAN DECISION — it must survive a
-- reload. Before this table the dismissal lived in a JS Set, so every ignored proposal resurrected
-- on the next page load (the toast said "ignorado" and kept nothing — a broken promise). Keyed by
-- the same content key the lens uses (kind|thread_root-or-email), never by list index. Deleting a
-- row un-dismisses (the undo path), so the table holds only CURRENT dismissals — an audit trail of
-- flip-flops is not needed here; the thread itself always stays visible in the Fila (never binned).
CREATE TABLE IF NOT EXISTS para_ti_dismissals (
    item_key TEXT PRIMARY KEY,   -- para_ti.item_key(): "kind|thread_root" or "kind|email" (v8)
    kind     TEXT,               -- rever_classificacao | propor_projeto | confirmar_identidade (v8)
    ts       TEXT                -- UTC ISO when dismissed (v8)
);
-- Adiar / snooze (v9, ADR-033 P3): a thread the human deferred. PRECIOUS and hand-set. The row is
-- only half the rule — cockpit.build_fila wakes a snoozed thread when until_ts passes OR when a NEW
-- INBOUND arrives after created_ts (never silently bin a client: a hidden thread can never be lost
-- to the counterparty's move). Deleting the row un-defers (the undo path).
CREATE TABLE IF NOT EXISTS thread_snooze (
    thread_root TEXT PRIMARY KEY,
    until_ts    TEXT NOT NULL,   -- UTC ISO wake time (v9)
    created_ts  TEXT             -- when it was snoozed — the wake-on-inbound reference (v9)
);
-- Human display names for counterparty clusters (v8): the clustering keys (nif:274023911,
-- free:someone@gmail.com) are machine identity, not something to show a person managing clients.
-- A row here overrides the derived display_name everywhere the cluster is rendered. Precious and
-- hand-set; an empty/deleted row falls back to the automatic derivation.
CREATE TABLE IF NOT EXISTS counterparty_names (
    key  TEXT PRIMARY KEY,       -- the cluster key (accounts.AccountCluster.key) (v8)
    name TEXT NOT NULL,          -- the human-chosen display name (v8)
    ts   TEXT                    -- UTC ISO when set (v8)
);

-- People (v10, ADR-039): the ONE namespace for anyone who can be ASSIGNED work. Login is an
-- ATTRIBUTE here, not a separate kind of record -- some people are held accountable *through* a
-- platform user and never sign in themselves (they may not even have a mailbox). Before v10 the
-- roster was free text in two places (settings.json `team` UNION the in-app `roster` table), so
-- "who is Rita" had no single answer and could never carry a permission.
--
-- ``name`` is UNIQUE and is BOTH the display name AND the join key: thread_owners.owner,
-- project_owners.owner and captures.asserted_by all store a name, and all three stayed TEXT. That
-- is deliberate -- at v10 those tables hold 0, 0 and 3 rows respectively, so an id migration would
-- have bought churn across six modules and nothing else. A rename must therefore cascade (see
-- ``rename_person``), which is the price of the name-as-key choice.
--
-- ``responsible_id`` is REQUIRED for a non-login person and enforced by the CHECK below, not by
-- convention: work assigned to someone who never logs in must appear in some signed-in human's
-- view, or it sits in a queue nobody opens -- the "never silently bin a client" non-negotiable
-- reaching ownership. Credentials live in a SEPARATE auth.db; no password material is ever here.
CREATE TABLE IF NOT EXISTS people (
    person_id       TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    -- Uniqueness runs on this folded key, NOT on ``name`` with COLLATE NOCASE: SQLite's NOCASE only
    -- folds ASCII, so "Luis" and "LUIS" collide but "Luis" and "LUIS" spelled with an accented I do
    -- NOT -- and the team has accented names. Without this column the UNIQUE constraint would let
    -- one person exist twice, each owning half a queue. NFKC + casefold, set by ``_name_key``.
    name_key        TEXT NOT NULL UNIQUE,
    -- Where a password-reset link is sent (v11, ADR-042). '' means "no address on file", which is
    -- the honest default: this column is NEVER inferred from person_scopes or imap.accounts[] --
    -- a scope grant is an inbox someone READS, not proof of their own address, and guessing here
    -- would mail a credential-bearing link to the wrong human. An admin sets it explicitly.
    -- It is contact data, not password material, so it belongs in the precious DB beside `name`
    -- and not in auth.db (which holds only secrets).
    email           TEXT NOT NULL DEFAULT '',
    -- The person's own closing (v12, ADR-047): a template, not rendered text, so a rename or a new
    -- phone number updates every future draft instead of leaving stale contact details in a block
    -- somebody pasted once. '' means "use the install-wide config/signature_template.md" -- the
    -- ordinary case, not an error. `job_title`/`phone` exist ONLY to fill {cargo}/{telefone}: they
    -- are self-service contact data the person types about themselves, which is why they live here
    -- and are editable from «A minha conta» while `email` (the reset-link destination) is not --
    -- letting a session holder redirect password recovery is a takeover, letting them change their
    -- own job title is a Tuesday.
    signature       TEXT NOT NULL DEFAULT '',
    job_title       TEXT NOT NULL DEFAULT '',
    phone           TEXT NOT NULL DEFAULT '',
    can_login       INTEGER NOT NULL DEFAULT 0,
    is_admin        INTEGER NOT NULL DEFAULT 0,
    responsible_id  TEXT REFERENCES people(person_id),
    active          INTEGER NOT NULL DEFAULT 1,
    created_ts      TEXT NOT NULL,
    updated_ts      TEXT NOT NULL,
    CHECK (can_login IN (0, 1) AND is_admin IN (0, 1) AND active IN (0, 1)),
    -- an admin must be able to sign in, else the grant is unusable
    CHECK (is_admin = 0 OR can_login = 1),
    -- the accountability rule, structural rather than advisory
    CHECK (can_login = 1 OR responsible_id IS NOT NULL)
);

-- Which inbox scopes a person may SEE (v10). Tokens are ADR-038 scope tokens: a delivery address,
-- or scopes.SCOPE_UNATTRIBUTED for the admin-visible bucket. Absent rows mean "no inbox granted";
-- an admin bypasses this table entirely (scopes.visible), so an admin is never locked out by it.
CREATE TABLE IF NOT EXISTS person_scopes (
    person_id   TEXT NOT NULL REFERENCES people(person_id),
    scope       TEXT NOT NULL,
    ts          TEXT,
    PRIMARY KEY (person_id, scope)
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
# v6 (2026-06-22): Increment 1 (audio) — captures.transcript, a NEW COLUMN on the pre-existing captures
# table, so it needs a guarded ALTER in _migrate (ADR-020 preserve-at-core: transcript + original audio).
# v7 (2026-06-22): Increment 2 (inference) — captures.extracted_fields_json + captures.confidence (the
# LLM-extracted field VALUES the user validates field-by-field; never auto-applied). Two more guarded ALTERs.
# v8 (2026-07-20): para_ti_dismissals (persisted "Ignorar" on decision cards — was a JS Set that lost
# every dismissal on reload) + counterparty_names (human display-name override for cluster keys).
# Both brand-new TABLES, delivered by SCHEMA's CREATE IF NOT EXISTS with no ALTER needed.
# v10 (2026-07-25): people + person_scopes — the single assignable-identity namespace and its
# inbox grants (ADR-039). Both brand-new TABLES, delivered by SCHEMA's CREATE IF NOT EXISTS
# with no ALTER needed. Seeding is NOT done here: _migrate has no access to settings.json, and
# creating the first admin is a human act — see `email2data auth setup`.
# v9 (2026-07-23): thread_snooze — the Fila's Adiar store (ADR-033 P3). A brand-new TABLE, delivered
# by SCHEMA with no ALTER; the wake-on-date-OR-new-inbound rule lives in cockpit.build_fila.
# v11 (2026-07-26): people.email — where a password-reset link is sent (ADR-042). A NEW COLUMN on the
# pre-existing people table, so SCHEMA's CREATE-IF-NOT-EXISTS cannot deliver it and it needs the
# guarded ALTER in _migrate. Backfill is deliberately EMPTY: there is no address anywhere in this
# system to backfill FROM, and inventing one would mail a reset link to a guess.
# v12 (2026-07-26): people.signature + people.job_title + people.phone — the per-person closing of a
# reply draft and the two profile fields that fill {cargo}/{telefone} (ADR-047). THREE new COLUMNS on
# the pre-existing people table, so SCHEMA cannot deliver them and each needs its own guarded ALTER.
# Every row lands at '', which is a real state meaning "close with the install default", so there is
# nothing to backfill and no behaviour change for anyone who never opens the editor.
SCHEMA_VERSION = 12

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


def _name_key(name: str) -> str:
    """The uniqueness/lookup key for a person name: whitespace-collapsed, NFKC-normalised, casefolded.

    ``casefold`` rather than ``lower`` so eszett/sigma-style pairs fold too; NFKC first so a composed
    "í" and a decomposed "i + combining acute" are the same person rather than two.
    """
    return unicodedata.normalize("NFKC", " ".join((name or "").split())).casefold()


def normalize_email(email: str) -> str:
    """Trim + lowercase an address, or return '' for blank. Raises ValueError on a malformed one.

    Deliberately a *shape* check, not RFC 5322: one ``@``, something either side, a dot in the
    domain, no whitespace. The purpose is to catch a typo before it becomes a reset link mailed into
    the void -- not to adjudicate exotic-but-legal addresses. Rejecting a valid address is the
    cheaper failure here (an admin retypes it) than accepting an invalid one (a person silently
    cannot recover, and finds out only when locked out).

    Lowercased because it is compared and displayed, never used as a login key -- ``name`` is the
    login key (ADR-041), so two people with case-different addresses is not a collision this must
    resolve.
    """
    addr = (email or "").strip().lower()
    if not addr:
        return ""
    if any(c.isspace() for c in addr):
        raise ValueError(f"endereço inválido: {email!r} — não pode conter espaços")
    local, sep, domain = addr.partition("@")
    if not sep or not local or not domain or "@" in domain or "." not in domain:
        raise ValueError(f"endereço inválido: {email!r} — esperado algo como nome@dominio.pt")
    if domain.startswith(".") or domain.endswith(".") or ".." in addr:
        raise ValueError(f"endereço inválido: {email!r} — domínio malformado")
    return addr


def event_field(kind: str) -> str:
    """The reserved project_field_history.field address for an event of ``kind`` (e.g. ``__note__``)."""
    return f"__{kind}__"


class WorkspaceVersionError(RuntimeError):
    """The precious DB is behind ``SCHEMA_VERSION`` and the opener refused to migrate it. Raised by the
    single-migrator gate (``connect(migrate=False)``) so a SEPARATE process (the intake worker) never
    upgrades workspace.db — only the webapp/CLI migrates (ADR-021 / M2 prereq)."""


class Workspace:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    def connect(self, *, migrate: bool = True) -> "Workspace":
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
        if not migrate:
            # Single-migrator gate (ADR-021 / M2 prereq): the intake worker opens read/write but must
            # NOT migrate the precious, never-rebuilt DB — only the webapp/CLI does. Assert the schema
            # is current; if behind, refuse rather than racing a migration from a second process.
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version < SCHEMA_VERSION:
                raise WorkspaceVersionError(
                    f"workspace.db is at v{version}; run `email2data serve` once to upgrade it to "
                    f"v{SCHEMA_VERSION} — the intake worker will not migrate the precious DB")
            return self
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
        # before _migrate — so there is no ALTER to do here. (A guarded `if version < 5:` block would
        # be needed ONLY if a change added a COLUMN to a pre-existing table — which is exactly v6.)
        if version < 6:
            # Increment 1 (audio): the pt-PT transcript of a voice/audio capture. A NEW COLUMN on the
            # pre-existing captures table, so SCHEMA's CREATE-IF-NOT-EXISTS can't add it — an explicit
            # guarded ALTER does. (ADR-020: the transcript joins the original audio + verbatim text.)
            self._add_column("captures", "transcript", "TEXT")
        if version < 7:
            # Increment 2 (inference): the LLM-extracted field VALUES + a 0-1 confidence. Stored only —
            # NEVER auto-applied; the user validates each field before it touches a project (R9). Two
            # more NEW COLUMNS on the pre-existing captures table, so two more guarded ALTERs.
            self._add_column("captures", "extracted_fields_json", "TEXT")
            self._add_column("captures", "confidence", "REAL")
        # v8 (para_ti_dismissals + counterparty_names) adds only NEW TABLES — delivered by SCHEMA
        # above (CREATE IF NOT EXISTS runs before _migrate), so there is no ALTER to do here.
        # v9 (thread_snooze) likewise: a new TABLE only, delivered by SCHEMA — no ALTER.
        # v10 (people + person_scopes) likewise: new TABLES only — no ALTER.
        if version < 11:
            # people.email (ADR-042): the reset-link destination. A NEW COLUMN on a table that
            # already exists on every real install, so SCHEMA cannot add it. No backfill runs and
            # none is possible — person_scopes holds inboxes a person may READ and
            # imap.accounts[].username holds mailboxes the app FETCHES; neither is evidence of
            # whose address it is. Every row lands at '' and an admin fills it in.
            self._add_column("people", "email", "TEXT NOT NULL DEFAULT ''")
        if version < 12:
            # The per-person signature (ADR-047) and the two profile fields that fill it. Three NEW
            # COLUMNS on a table that already exists on every real install. No backfill: '' means
            # "use config/signature_template.md", so every existing person keeps closing exactly as
            # they did before this version, with their name filled in.
            self._add_column("people", "signature", "TEXT NOT NULL DEFAULT ''")
            self._add_column("people", "job_title", "TEXT NOT NULL DEFAULT ''")
            self._add_column("people", "phone", "TEXT NOT NULL DEFAULT ''")
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

    # -- Adiar / snooze (v9, ADR-033 P3) ----------------------------------------------------------

    def set_thread_snooze(self, thread_root: str, until_ts: str, ts: str = "") -> None:
        """Defer a thread until ``until_ts``. Idempotent upsert; ``created_ts`` restarts on each
        re-snooze so the wake-on-inbound reference is the LATEST human decision."""
        assert self._conn is not None, "call connect() first"
        root = (thread_root or "").strip()
        if not root or not until_ts:
            return
        self._conn.execute(
            "INSERT INTO thread_snooze(thread_root, until_ts, created_ts) VALUES (?,?,?) "
            "ON CONFLICT(thread_root) DO UPDATE SET until_ts=excluded.until_ts, "
            "created_ts=excluded.created_ts",
            (root, until_ts, ts or self._now_iso()),
        )
        self._conn.commit()

    def clear_thread_snooze(self, thread_root: str) -> None:
        """Un-defer (the undo path) — the thread rejoins the active queue on the next build."""
        assert self._conn is not None, "call connect() first"
        self._conn.execute("DELETE FROM thread_snooze WHERE thread_root=?",
                           ((thread_root or "").strip(),))
        self._conn.commit()

    def thread_snoozes(self) -> dict[str, dict[str, str]]:
        """``{thread_root: {until_ts, created_ts}}`` — consumed by ``cockpit.build_fila``."""
        assert self._conn is not None, "call connect() first"
        rows = self._conn.execute(
            "SELECT thread_root, until_ts, created_ts FROM thread_snooze").fetchall()
        return {r["thread_root"]: {"until_ts": r["until_ts"], "created_ts": r["created_ts"] or ""}
                for r in rows}

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

    # -- people: the single assignable-identity namespace (v10, ADR-039) -------------------------
    #
    # ``name`` is the join key into thread_owners.owner / project_owners.owner / captures.asserted_by,
    # all of which are still TEXT. Every write below normalises whitespace but PRESERVES case, because
    # the name is also what the UI renders. Lookups are case-insensitive (the column is COLLATE NOCASE)
    # so "filipe" and "Filipe" can never become two people.

    def create_person(self, name: str, *, can_login: bool = False, is_admin: bool = False,
                      responsible: str = "", email: str = "", ts: str = "") -> dict[str, Any]:
        """Create a person. Returns the new row.

        ``responsible`` is another person's NAME and is REQUIRED when ``can_login`` is False -- the DB
        CHECK enforces it too, but resolving it here gives a usable error instead of an opaque
        IntegrityError. Raises ValueError on a blank/duplicate name, a missing responsible person, or
        a non-login person with no responsible.

        ``email`` is optional and defaults to '' (no address on file). It is never derived from
        anything -- see ``normalize_email`` and the v11 note in ``_migrate``.
        """
        assert self._conn is not None, "call connect() first"
        nm = " ".join((name or "").split())
        if not nm:
            raise ValueError("a person needs a name")
        addr = normalize_email(email)
        key = _name_key(nm)
        if self.person(nm) is not None:
            raise ValueError(f"person {nm!r} already exists")
        responsible_id = None
        if not can_login:
            rnm = " ".join((responsible or "").split())
            if not rnm:
                raise ValueError(
                    f"{nm!r} cannot sign in, so they need a responsible person -- work assigned to "
                    f"them must appear in some signed-in user's view")
            row = self.person(rnm)
            if row is None:
                raise ValueError(f"responsible person {rnm!r} does not exist")
            if not row["can_login"]:
                raise ValueError(f"responsible person {rnm!r} cannot sign in either")
            responsible_id = row["person_id"]
        now = ts or self._now_iso()
        person_id = f"PER-{uuid.uuid4().hex[:8].upper()}"
        self._conn.execute(
            "INSERT INTO people (person_id, name, name_key, email, can_login, is_admin, "
            "responsible_id, active, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,1,?,?)",
            (person_id, nm, key, addr, int(bool(can_login)), int(bool(is_admin)), responsible_id,
             now, now))
        self._conn.commit()
        return self.person(nm)  # type: ignore[return-value]

    def person(self, name: str) -> dict[str, Any] | None:
        """One person by name (case-insensitive), or None."""
        assert self._conn is not None, "call connect() first"
        row = self._conn.execute(
            "SELECT * FROM people WHERE name_key = ?", (_name_key(name),)).fetchone()
        return self._person_row(row) if row else None

    def person_by_id(self, person_id: str) -> dict[str, Any] | None:
        assert self._conn is not None, "call connect() first"
        row = self._conn.execute(
            "SELECT * FROM people WHERE person_id = ?", (person_id,)).fetchone()
        return self._person_row(row) if row else None

    def people(self, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        """Every person, name-ordered. The effective owner roster once v10 is seeded."""
        assert self._conn is not None, "call connect() first"
        q = "SELECT * FROM people" + ("" if include_inactive else " WHERE active = 1") + " ORDER BY name"
        return [self._person_row(r) for r in self._conn.execute(q).fetchall()]

    def _person_row(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        for flag in ("can_login", "is_admin", "active"):
            d[flag] = bool(d[flag])
        d["scopes"] = self.person_scopes(d["person_id"])
        return d

    def set_person_scopes(self, person_id: str, scopes: list[str], ts: str = "") -> None:
        """REPLACE the person's granted inbox scopes (ADR-038 tokens). ``[]`` revokes all.

        Idempotent -- the resulting set depends only on the argument, never on prior state.
        """
        assert self._conn is not None, "call connect() first"
        now = ts or self._now_iso()
        self._conn.execute("DELETE FROM person_scopes WHERE person_id=?", (person_id,))
        for scope in dict.fromkeys(s.strip().lower() for s in (scopes or []) if s and s.strip()):
            self._conn.execute(
                "INSERT OR IGNORE INTO person_scopes(person_id, scope, ts) VALUES (?,?,?)",
                (person_id, scope, now))
        self._conn.commit()

    def person_scopes(self, person_id: str) -> list[str]:
        """The inbox scopes granted to one person (sorted). Empty = none granted."""
        assert self._conn is not None, "call connect() first"
        return [r["scope"] for r in self._conn.execute(
            "SELECT scope FROM person_scopes WHERE person_id=? ORDER BY scope", (person_id,)).fetchall()]

    # -- the lifecycle: promote, deactivate, remove (ADR-041) ------------------------------------
    #
    # The invariant every one of these enforces: THE INSTALL CAN NEVER REACH ZERO ACTIVE
    # ADMINISTRATORS. /setup 404s as soon as any credential exists (ADR-039), so an install with no
    # admin cannot be repaired from the app at all -- the recovery is deleting auth.db by hand and
    # re-onboarding everybody. Enforced in the store rather than in the webapp so the CLI and any
    # later caller inherit it; a rule that lives in one route is a rule the next route forgets.

    def _active_admin_ids(self) -> set[str]:
        assert self._conn is not None, "call connect() first"
        return {r["person_id"] for r in self._conn.execute(
            "SELECT person_id FROM people WHERE is_admin = 1 AND active = 1").fetchall()}

    def _require_person(self, person_id: str) -> dict[str, Any]:
        row = self.person_by_id(person_id)
        if row is None:
            raise ValueError(f"person {person_id!r} does not exist")
        return row

    def _refuse_if_last_admin(self, person: dict[str, Any], what: str) -> None:
        admins = self._active_admin_ids()
        if admins == {person["person_id"]}:
            raise ValueError(
                f"{person['name']!r} é o único administrador ativo — {what} deixaria a instalação "
                f"sem ninguém que possa administrar, e o /setup já está fechado. "
                f"Promove outra pessoa primeiro.")

    def set_person_admin(self, person_id: str, is_admin: bool, ts: str = "") -> dict[str, Any]:
        """Promote or demote. Idempotent; returns the updated row."""
        assert self._conn is not None, "call connect() first"
        person = self._require_person(person_id)
        if is_admin and not person["can_login"]:
            raise ValueError(
                f"{person['name']!r} não tem acesso à plataforma — um administrador que não pode "
                f"entrar é uma permissão que ninguém exerce.")
        if not is_admin:
            self._refuse_if_last_admin(person, "despromovê-lo")
        self._conn.execute("UPDATE people SET is_admin=?, updated_ts=? WHERE person_id=?",
                           (int(bool(is_admin)), ts or self._now_iso(), person_id))
        self._conn.commit()
        return self.person_by_id(person_id)  # type: ignore[return-value]

    def set_person_email(self, person_id: str, email: str, ts: str = "") -> dict[str, Any]:
        """Set (or clear, with '') where this person's password-reset link is sent. Returns the row.

        Clearing is allowed and is not a soft delete: it means "no address on file", after which
        ``/recuperar`` simply has nowhere to send and the person recovers the pre-ADR-042 way, via an
        admin. That is a real state, so it must be expressible.

        Raises ValueError on a malformed address, or when another person already holds it -- an
        address that maps to two people is a reset link whose destination is ambiguous, and this
        column exists precisely to be unambiguous.
        """
        assert self._conn is not None, "call connect() first"
        self._require_person(person_id)
        addr = normalize_email(email)
        if addr:
            clash = self._conn.execute(
                "SELECT name FROM people WHERE email = ? AND person_id != ?",
                (addr, person_id)).fetchone()
            if clash is not None:
                raise ValueError(
                    f"{addr} já está associado a {clash['name']!r} — um endereço só pode pertencer "
                    f"a uma pessoa, senão o link de recuperação tem dois destinos possíveis.")
        self._conn.execute("UPDATE people SET email=?, updated_ts=? WHERE person_id=?",
                           (addr, ts or self._now_iso(), person_id))
        self._conn.commit()
        return self.person_by_id(person_id)  # type: ignore[return-value]

    def set_person_profile(self, person_id: str, *, signature: str | None = None,
                           job_title: str | None = None, phone: str | None = None,
                           ts: str = "") -> tuple[dict[str, Any], bool]:
        """Set the person's own signature template and the profile fields that fill it (ADR-047).

        Returns ``(person_row, converted_from_html)``. The second value is not decoration: a real
        signature is COPIED out of Outlook or Gmail, so it arrives as an HTML table of logos and
        inline styles, and this method flattens it to the plain text every draft surface actually
        uses. Rewriting what somebody pasted without telling them is its own failure, so the caller
        is handed the fact and must say so.

        Every argument is optional and ``None`` means "leave it alone", so a form that posts only the
        signature cannot silently blank a phone number it never showed. ``''`` is a real value and
        clears the field -- for ``signature`` that means "close with the install default", which is
        the state most people are in and must stay reachable after someone has tried a custom one.

        Raises ValueError when the signature carries a placeholder the renderer cannot fill (the
        alternative is a client email with a literal ``{telemovel}`` in it), or when a non-empty
        paste flattens to nothing -- an HTML block that is entirely images has no text to keep, and
        silently turning that into "use the install default" would look like the save was ignored.
        Validation is HERE and not in the route so the CLI and any later caller inherit it.
        """
        assert self._conn is not None, "call connect() first"
        self._require_person(person_id)
        sets: list[str] = []
        args: list[Any] = []
        converted = False
        if signature is not None:
            from .signature import normalize_signature, unknown_tokens
            text, converted = normalize_signature(signature)
            if converted and not text:
                raise ValueError(
                    "a assinatura colada é HTML sem texto — só imagens. Escreve o texto do fecho "
                    "(nome, função, contacto); os rascunhos são texto simples.")
            bad = unknown_tokens(text)
            if bad:
                raise ValueError(
                    f"a assinatura usa {', '.join(bad)}, que não existe(m). Tokens disponíveis: "
                    f"{{nome}}, {{cargo}}, {{telefone}}, {{email}}.")
            sets.append("signature=?")
            args.append(text)
        if job_title is not None:
            sets.append("job_title=?")
            args.append(" ".join(job_title.split()))
        if phone is not None:
            sets.append("phone=?")
            args.append(" ".join(phone.split()))
        if sets:
            sets.append("updated_ts=?")
            args.append(ts or self._now_iso())
            self._conn.execute(f"UPDATE people SET {', '.join(sets)} WHERE person_id=?",
                               (*args, person_id))
            self._conn.commit()
        return self.person_by_id(person_id), converted  # type: ignore[return-value]

    def person_by_email(self, email: str) -> dict[str, Any] | None:
        """The ACTIVE, login-capable person at ``email``, or None. The reset flow's only lookup.

        Blank never matches: most rows carry '' by default, so a blank probe would otherwise return
        an arbitrary person and mail them a reset link. Inactive and non-login people are excluded
        here rather than at the call site -- a deactivated person is exactly who a stale address
        would still reach, and they must not be able to sign back in through recovery.
        """
        assert self._conn is not None, "call connect() first"
        try:
            addr = normalize_email(email)
        except ValueError:
            return None
        if not addr:
            return None
        row = self._conn.execute(
            "SELECT * FROM people WHERE email = ? AND active = 1 AND can_login = 1",
            (addr,)).fetchone()
        return self._person_row(row) if row else None

    def set_person_active(self, person_id: str, active: bool, ts: str = "") -> dict[str, Any]:
        """Deactivate (the normal way someone leaves) or bring them back. Idempotent.

        Deliberately NOT a delete: past assignments stay attributed to them, so who decided what is
        preserved. ``people()`` drops them, so they stop being offered as an owner.
        """
        assert self._conn is not None, "call connect() first"
        person = self._require_person(person_id)
        if not active:
            self._refuse_if_last_admin(person, "desativá-lo")
        self._conn.execute("UPDATE people SET active=?, updated_ts=? WHERE person_id=?",
                           (int(bool(active)), ts or self._now_iso(), person_id))
        self._conn.commit()
        return self.person_by_id(person_id)  # type: ignore[return-value]

    def person_history(self, name: str) -> dict[str, int]:
        """Where a person's NAME still appears as a foreign key. ``{}`` = safe to remove.

        Name-as-key (see the ``people`` schema comment) means a DELETE cannot cascade the way a real
        FK would: the owner rows would simply point at somebody who no longer exists, and the thread
        would lose its owner without anyone being told.
        """
        assert self._conn is not None, "call connect() first"
        nm = " ".join((name or "").split())
        counts = {}
        for table, column in (("thread_owners", "owner"), ("project_owners", "owner"),
                              ("captures", "asserted_by"), ("capture_users", "roster_owner")):
            n = self._conn.execute(
                f'SELECT COUNT(*) AS n FROM "{table}" WHERE "{column}"=?', (nm,)).fetchone()["n"]
            if n:
                counts[table] = int(n)
        return counts

    def delete_person(self, person_id: str) -> None:
        """Remove a person outright — only the ones who never did anything.

        The honest scope of this call is "undo a mistyped name". Anyone who owns a thread, a project,
        a capture, or another person's accountability is refused: removing them would rewrite the
        record of who decided what. ``set_person_active(False)`` is the answer for someone who left.
        """
        assert self._conn is not None, "call connect() first"
        person = self._require_person(person_id)
        self._refuse_if_last_admin(person, "removê-lo")
        history = self.person_history(person["name"])
        if history:
            where = ", ".join(f"{t} ({n})" for t, n in sorted(history.items()))
            raise ValueError(
                f"{person['name']!r} tem histórico atribuído — {where}. Remover apagaria o registo "
                f"de quem decidiu o quê. Desativa-o em vez de o remover.")
        dependents = [r["name"] for r in self._conn.execute(
            "SELECT name FROM people WHERE responsible_id=? ORDER BY name", (person_id,)).fetchall()]
        if dependents:
            raise ValueError(
                f"{person['name']!r} é o responsável de {', '.join(dependents)} — sem ele, o "
                f"trabalho dessas pessoas deixa de aparecer na vista de alguém.")
        self._conn.execute("DELETE FROM person_scopes WHERE person_id=?", (person_id,))
        self._conn.execute("DELETE FROM people WHERE person_id=?", (person_id,))
        self._conn.commit()

    def backfill_people_from_roster(self, team: list[str] | None = None) -> list[str]:
        """Fold the legacy owner rosters into ``people``. Idempotent; returns the names created.

        Before this the owner picker read ``settings.json team`` ∪ the in-app ``roster`` table while
        permissions read ``people`` — two vocabularies for one question, so "Rita" could be an owner
        and not a person. Assigning her work was possible; granting her anything was not.

        Backfilled names are **assignable-only**: they were free text a moment ago and nobody decided
        they could sign in. The accountability rule then requires a responsible user, so this needs an
        active admin and does nothing without one — on a virgin install it simply runs again next
        boot. Existing people are never touched, including deactivated ones: someone who left is
        still in ``settings.team`` (config nobody edits), and re-creating them every boot would make
        deactivation impossible to keep.
        """
        assert self._conn is not None, "call connect() first"
        admins = sorted(self._active_admin_ids())
        if not admins:
            return []
        responsible_id = admins[0]
        known = {_name_key(p["name"]) for p in self.people(include_inactive=True)}
        created = []
        for name in list(team or []) + self.roster():
            nm = " ".join((name or "").split())
            key = _name_key(nm)
            if not nm or key in known:
                continue
            known.add(key)
            now = self._now_iso()
            person_id = f"PER-{uuid.uuid4().hex[:8].upper()}"
            self._conn.execute(
                "INSERT INTO people (person_id, name, name_key, can_login, is_admin, "
                "responsible_id, active, created_ts, updated_ts) VALUES (?,?,?,0,0,?,1,?,?)",
                (person_id, nm, key, responsible_id, now, now))
            created.append(nm)
        if created:
            self._conn.commit()
        return created

    def rename_person(self, old: str, new: str) -> int:
        """Rename a person, CASCADING into every table that stores the name as a foreign key.

        This is the price of name-as-key (see the ``people`` schema comment): thread_owners.owner,
        project_owners.owner and captures.asserted_by all hold the NAME, so a bare UPDATE on
        ``people`` would orphan every assignment silently. One transaction, so a partial rename can
        never be committed. Returns the number of rows touched outside ``people``.
        """
        assert self._conn is not None, "call connect() first"
        src = " ".join((old or "").split())
        dst = " ".join((new or "").split())
        if not dst:
            raise ValueError("a person needs a name")
        if self.person(src) is None:
            raise ValueError(f"person {src!r} does not exist")
        clash = self.person(dst)
        if clash is not None and _name_key(clash["name"]) != _name_key(src):
            raise ValueError(f"person {dst!r} already exists")
        touched = 0
        try:
            self._conn.execute("BEGIN")
            for table, column in (("thread_owners", "owner"), ("project_owners", "owner"),
                                  ("captures", "asserted_by"), ("capture_users", "roster_owner"),
                                  ("roster", "name")):
                cur = self._conn.execute(
                    f'UPDATE OR IGNORE "{table}" SET "{column}"=? WHERE "{column}"=?', (dst, src))
                touched += cur.rowcount or 0
            self._conn.execute(
                "UPDATE people SET name=?, name_key=?, updated_ts=? WHERE name_key=?",
                (dst, _name_key(dst), self._now_iso(), _name_key(src)))
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return touched

    # -- Para ti dismissals (v8) ------------------------------------------------------------

    def dismiss_para_ti(self, item_key: str, kind: str = "", ts: str = "") -> None:
        """Persist an "Ignorar" on a Para ti decision card. Idempotent upsert.

        The thread itself is untouched (still in the Fila; never binned) — this only stops the
        SAME proposal from resurrecting on every page load."""
        assert self._conn is not None, "call connect() first"
        key = (item_key or "").strip()
        if not key:
            return
        self._conn.execute(
            "INSERT INTO para_ti_dismissals(item_key, kind, ts) VALUES (?,?,?) "
            "ON CONFLICT(item_key) DO UPDATE SET kind=excluded.kind, ts=excluded.ts",
            (key, kind or key.split("|", 1)[0], ts or self._now_iso()),
        )
        self._conn.commit()

    def undismiss_para_ti(self, item_key: str) -> None:
        """Reverse a dismissal (the Z/undo path) — the proposal reappears on the next build."""
        assert self._conn is not None, "call connect() first"
        self._conn.execute("DELETE FROM para_ti_dismissals WHERE item_key=?",
                           ((item_key or "").strip(),))
        self._conn.commit()

    def para_ti_dismissed(self) -> dict[str, str]:
        """``{item_key: ts}`` — all currently-dismissed Para ti items (para_ti.all_items filters on it)."""
        assert self._conn is not None, "call connect() first"
        rows = self._conn.execute("SELECT item_key, ts FROM para_ti_dismissals").fetchall()
        return {r["item_key"]: r["ts"] or "" for r in rows}

    # -- counterparty display names (v8) ----------------------------------------------------

    def set_counterparty_name(self, key: str, name: str, ts: str = "") -> None:
        """Set (or clear, with an empty ``name``) the human display name for a cluster key."""
        assert self._conn is not None, "call connect() first"
        k = (key or "").strip()
        if not k:
            return
        n = (name or "").strip()
        if not n:
            self._conn.execute("DELETE FROM counterparty_names WHERE key=?", (k,))
        else:
            self._conn.execute(
                "INSERT INTO counterparty_names(key, name, ts) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET name=excluded.name, ts=excluded.ts",
                (k, n, ts or self._now_iso()),
            )
        self._conn.commit()

    def counterparty_names(self) -> dict[str, str]:
        """``{cluster_key: human display name}`` — the precious overrides."""
        assert self._conn is not None, "call connect() first"
        rows = self._conn.execute("SELECT key, name FROM counterparty_names").fetchall()
        return {r["key"]: r["name"] for r in rows}

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
