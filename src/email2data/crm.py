"""CRM PoC — turn every triaged email into a relationship record. Deterministic, no LLM, ~free.

Model = one event log + rollups + two relation indexes:

  * ``interactions`` — one immutable row per email (source of truth: participants, thread root,
    direction, verdict, and extracted entities). Everything else can be rebuilt from this.
  * ``participants`` — one row per (message, email, role) pair; powers the "all emails touching
    this contact in any role" query without scanning the full interactions table.
  * ``entity_refs`` — inverted index: (entity_key, entity_value) → message_id; powers cross-thread
    matching by shared entities (NIF, IBAN, client name, product, …).
  * ``contacts`` — one row per person, rolled up from interactions: tallies, last-seen, role counts.

Stored in ``out/crm.db`` (local-only, gitignored). ``cmd_crm`` rebuilds it clean from corpus +
results.jsonl on every run, so the schema never needs ALTER TABLE migrations.

Deferred: identity resolution (one person, many addresses), org tables/UI, live-triage wiring.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from .signals import OUR_DOMAIN

# Bump this if the schema changes in a way that requires a full rebuild reminder.
SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS interactions (
    message_id   TEXT PRIMARY KEY,
    date         TEXT,
    from_email   TEXT,
    direction    TEXT,
    counterparty TEXT,
    purpose      TEXT,
    priority     TEXT,
    urgency      INTEGER,
    subject      TEXT,
    thread_root  TEXT,    -- first References header, or message_id for root messages
    is_reply     INTEGER,
    is_forward   INTEGER,
    has_attach   INTEGER,
    n_recipients INTEGER,
    entities     TEXT,    -- JSON blob of schema.Entities fields; NULL for offline-triaged mail
    confidence   REAL,    -- verdict trust 0-1 (Tier-2 metadata; surfaced as the Fila "trust" tag)
    decided_by   TEXT,    -- which tier/engine decided: "tier0:bulk" | "tier1:gemini-2.5-flash" | ...
    reason       TEXT     -- the model's one-line justification (the Fila "Porquê?")
);
-- Partial index: thread queries are only useful for actual replies (non-null thread_root).
CREATE INDEX IF NOT EXISTS ix_interactions_thread ON interactions(thread_root);
CREATE INDEX IF NOT EXISTS ix_interactions_from   ON interactions(from_email);

CREATE TABLE IF NOT EXISTS participants (
    message_id   TEXT NOT NULL,
    email        TEXT NOT NULL,
    role         TEXT NOT NULL,   -- from / to / cc / reply_to
    PRIMARY KEY (message_id, email, role)
);
-- The primary lookup axis: "which messages involved this address?"
CREATE INDEX IF NOT EXISTS ix_participants_email ON participants(email);

CREATE TABLE IF NOT EXISTS entity_refs (
    entity_key   TEXT NOT NULL,   -- Entities field name (client_name, nif, iban, …)
    entity_value TEXT NOT NULL,   -- normalised: lower().strip()
    message_id   TEXT NOT NULL,
    PRIMARY KEY (entity_key, entity_value, message_id)
);
CREATE INDEX IF NOT EXISTS ix_entity_refs_lookup ON entity_refs(entity_key, entity_value);

CREATE TABLE IF NOT EXISTS contacts (
    email               TEXT PRIMARY KEY,
    display_name        TEXT,
    domain              TEXT,
    is_internal         INTEGER,
    first_seen          TEXT,
    last_seen           TEXT,
    last_from_date      TEXT,    -- last time this person was the SENDER ("when did I last hear from them")
    msg_count           INTEGER,
    from_count          INTEGER,
    to_count            INTEGER,
    cc_count            INTEGER,
    last_counterparty   TEXT,
    last_purpose        TEXT,
    counterparty_counts TEXT,    -- JSON tally {CLIENT: n, …}
    purpose_counts      TEXT,    -- JSON tally {PO_FROM_CLIENT: n, …}
    is_automated        INTEGER
);
"""

# Entity fields worth indexing for cross-thread matching.  Freeform prose fields
# (action_requested, money) are skipped — they produce too many spurious matches.
_INDEXABLE_ENTITY_KEYS = frozenset({
    "client_name", "client_email", "nif", "iban", "product_or_service", "deadline",
})


def participants(env: dict[str, Any]) -> list[tuple[str, str, str]]:
    """All people in the headers as (email, name, role), role ∈ from/to/cc/reply_to."""
    out: list[tuple[str, str, str]] = []
    for role, key in (("from", "from"), ("reply_to", "reply_to")):
        a = env.get(key) or {}
        if a.get("email"):
            out.append((a["email"].lower().strip(), (a.get("name") or "").strip(), role))
    for role in ("to", "cc"):
        for a in env.get(role) or []:
            if a.get("email"):
                out.append((a["email"].lower().strip(), (a.get("name") or "").strip(), role))
    return out


def _thread_root(env: dict[str, Any]) -> str:
    refs = env.get("references") or []
    return refs[0] if refs else (env.get("in_reply_to") or env.get("message_id") or "")


def _bump(tally_json: Optional[str], key: str) -> str:
    d = json.loads(tally_json) if tally_json else {}
    if key:
        d[key] = d.get(key, 0) + 1
    return json.dumps(d, ensure_ascii=False)


def _norm_entity(value: Any) -> Optional[str]:
    """Normalise an entity value for indexing. Returns None when the value is unusable."""
    if not value or not isinstance(value, str):
        return None
    v = value.lower().strip()
    return v if v else None


class CrmStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> "CrmStore":
        # check_same_thread=False: the webapp shares this connection across FastAPI's threadpool
        # (sync routes run off-thread). Single-user, serial access, so cross-thread reuse is safe.
        # Without this, project reads (build_canonical -> thread()) and /api/relations raise
        # sqlite3.ProgrammingError under uvicorn. Mirrors Workspace.connect.
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        return self

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # -------------------------------------------------------------------------
    # Write path
    # -------------------------------------------------------------------------

    def record(self, env: dict[str, Any], verdict: dict[str, Any]) -> None:
        """Record one email: interaction row + participants + entity refs + contact upsert.

        Idempotent on message_id: the interaction row is replaced (INSERT OR REPLACE) but
        contact rollups and the relation indexes are only written on the *first* insert so
        cumulative tallies don't double-count. The rebuild idiom in cmd_crm (db.unlink before
        each run) makes this moot in normal use; idempotency is for tests and incremental runs.
        """
        assert self._conn is not None, "call connect() first"
        mid = env.get("message_id") or ""
        date = env.get("date") or ""
        parts = participants(env)
        ents_raw: dict[str, Any] = verdict.get("entities") or {}
        ents_json = json.dumps(ents_raw, ensure_ascii=False) if ents_raw else None

        seen = self._conn.execute(
            "SELECT 1 FROM interactions WHERE message_id=?", (mid,)
        ).fetchone()

        self._conn.execute(
            "INSERT OR REPLACE INTO interactions"
            "(message_id, date, from_email, direction, counterparty, purpose, priority, urgency,"
            " subject, thread_root, is_reply, is_forward, has_attach, n_recipients, entities,"
            " confidence, decided_by, reason)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                mid, date,
                (env.get("from") or {}).get("email", ""),
                verdict.get("direction", ""),
                verdict.get("counterparty", ""),
                verdict.get("purpose", ""),
                verdict.get("priority", ""),
                int(verdict.get("urgency", 0) or 0),
                env.get("subject", ""),
                _thread_root(env),
                int(bool(env.get("in_reply_to") or env.get("references"))),
                0,
                int(bool(env.get("attachments"))),
                len(env.get("to") or []) + len(env.get("cc") or []),
                ents_json,
                verdict.get("confidence"),
                verdict.get("decided_by", "") or "",
                verdict.get("reason", "") or "",
            ),
        )

        if not seen:
            # Contact rollups (cumulative — only on first insert)
            for email, name, role in parts:
                self._upsert_contact(email, name, role, date, verdict)

            # Participants index
            self._conn.executemany(
                "INSERT OR IGNORE INTO participants(message_id, email, role) VALUES (?,?,?)",
                [(mid, email, role) for email, _, role in parts],
            )

            # Entity inverted index
            entity_rows = [
                (key, _norm_entity(val), mid)
                for key, val in ents_raw.items()
                if key in _INDEXABLE_ENTITY_KEYS and _norm_entity(val) is not None
            ]
            if entity_rows:
                self._conn.executemany(
                    "INSERT OR IGNORE INTO entity_refs(entity_key, entity_value, message_id)"
                    " VALUES (?,?,?)",
                    entity_rows,
                )

        self._conn.commit()

    def _upsert_contact(self, email, name, role, date, verdict) -> None:
        cp, pur = verdict.get("counterparty", ""), verdict.get("purpose", "")
        domain = email.rsplit("@", 1)[-1] if "@" in email else ""
        is_internal = int(domain == OUR_DOMAIN or domain.endswith("." + OUR_DOMAIN))
        row = self._conn.execute(
            "SELECT * FROM contacts WHERE email = ?", (email,)
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO contacts"
                "(email, display_name, domain, is_internal, first_seen, last_seen,"
                " last_from_date, msg_count, from_count, to_count, cc_count, last_counterparty,"
                " last_purpose, counterparty_counts, purpose_counts, is_automated)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    email, name, domain, is_internal, date, date,
                    date if role == "from" else "",
                    1, int(role == "from"), int(role == "to"), int(role == "cc"),
                    cp, pur, _bump(None, cp), _bump(None, pur), 0,
                ),
            )
            return
        self._conn.execute(
            "UPDATE contacts SET display_name=?, first_seen=?, last_seen=?, last_from_date=?,"
            " msg_count=?, from_count=?, to_count=?, cc_count=?, last_counterparty=?,"
            " last_purpose=?, counterparty_counts=?, purpose_counts=? WHERE email=?",
            (
                name or row["display_name"],
                min(x for x in (row["first_seen"], date) if x) if (row["first_seen"] or date) else "",
                max(row["last_seen"] or "", date),
                max(row["last_from_date"] or "", date) if role == "from" else row["last_from_date"],
                row["msg_count"] + 1,
                row["from_count"] + int(role == "from"),
                row["to_count"] + int(role == "to"),
                row["cc_count"] + int(role == "cc"),
                cp or row["last_counterparty"],
                pur or row["last_purpose"],
                _bump(row["counterparty_counts"], cp),
                _bump(row["purpose_counts"], pur),
                email,
            ),
        )

    # -------------------------------------------------------------------------
    # Relation queries — the three capabilities
    # -------------------------------------------------------------------------

    def thread(self, thread_root: str) -> list[dict[str, Any]]:
        """All interactions sharing thread_root, ordered oldest-first.

        A root message is its own thread_root, so this always returns at least that message.
        Callers that want only *siblings* should filter out the seed message_id themselves.
        """
        rows = self._conn.execute(
            "SELECT * FROM interactions WHERE thread_root = ? ORDER BY date ASC",
            (thread_root,),
        ).fetchall()
        return [dict(r) for r in rows]

    def thread_root_for(self, message_id: str) -> Optional[str]:
        """The thread_root of one message (for attaching a thread to a project by any message_id)."""
        row = self._conn.execute(
            "SELECT thread_root FROM interactions WHERE message_id=?", (message_id,)).fetchone()
        return row["thread_root"] if row else None

    def by_contact(self, email: str) -> list[dict[str, Any]]:
        """All interactions where *email* appeared in any header role (from/to/cc/reply_to).

        Returns most-recent-first. Uses the participants index, so this is O(matches) rather
        than a full-table scan.
        """
        rows = self._conn.execute(
            """
            SELECT i.*
              FROM interactions i
              JOIN participants p ON p.message_id = i.message_id
             WHERE p.email = ?
             ORDER BY i.date DESC
            """,
            (email.lower().strip(),),
        ).fetchall()
        return [dict(r) for r in rows]

    def by_entity(self, key: str, value: str) -> list[dict[str, Any]]:
        """All interactions that carry *value* for entity field *key* (normalised exact match).

        ``key`` must be one of ``_INDEXABLE_ENTITY_KEYS``; unindexed keys return an empty list.
        ``value`` is normalised (lower + strip) before lookup, matching how it was stored.
        """
        norm = _norm_entity(value)
        if norm is None or key not in _INDEXABLE_ENTITY_KEYS:
            return []
        rows = self._conn.execute(
            """
            SELECT i.*
              FROM interactions i
              JOIN entity_refs e ON e.message_id = i.message_id
             WHERE e.entity_key = ? AND e.entity_value = ?
             ORDER BY i.date DESC
            """,
            (key, norm),
        ).fetchall()
        return [dict(r) for r in rows]

    def related(self, message_id: str) -> dict[str, list[dict[str, Any]]]:
        """Aggregate all three relation types for one message.

        Returns::

            {
              "thread":     [...],   # siblings in the same email thread
              "by_contact": [...],   # other messages involving the same sender, any thread
              "by_entity":  [...],   # messages sharing an entity value (NIF, client name, …)
            }

        The seed *message_id* is excluded from every list.  Each entry in ``by_entity`` carries
        an extra ``_matched_entity`` key naming the field that caused the match.  Lists may
        overlap (a message can be both a thread sibling *and* share an entity); callers that
        want a deduplicated union should merge on ``message_id`` themselves.
        Returns empty lists for all three groups when *message_id* is unknown.
        """
        row = self._conn.execute(
            "SELECT * FROM interactions WHERE message_id = ?", (message_id,)
        ).fetchone()
        if row is None:
            return {"thread": [], "by_contact": [], "by_entity": []}

        interaction = dict(row)
        thread_root = interaction.get("thread_root") or ""
        from_email = interaction.get("from_email") or ""
        ents_raw: dict[str, Any] = json.loads(interaction.get("entities") or "{}") or {}

        thread = [r for r in self.thread(thread_root) if r["message_id"] != message_id]

        by_contact = [r for r in self.by_contact(from_email) if r["message_id"] != message_id]

        # Cross-entity: collect matches across all indexable entity fields, deduplicated by
        # message_id.  Each match is annotated with which field caused it (_matched_entity).
        seen_mids: set[str] = set()
        by_entity: list[dict[str, Any]] = []
        for key in _INDEXABLE_ENTITY_KEYS:
            val = ents_raw.get(key)
            if not val:
                continue
            for r in self.by_entity(key, str(val)):
                if r["message_id"] != message_id and r["message_id"] not in seen_mids:
                    seen_mids.add(r["message_id"])
                    r["_matched_entity"] = key
                    by_entity.append(r)
        by_entity.sort(key=lambda r: r.get("date") or "", reverse=True)

        return {"thread": thread, "by_contact": by_contact, "by_entity": by_entity}

    # -------------------------------------------------------------------------
    # Reporting helpers
    # -------------------------------------------------------------------------

    def all_interactions(self) -> list[dict[str, Any]]:
        """Every interaction row, oldest-first. Feeds the cockpit Fila (thread fold + response clock)
        in ``cockpit.py`` — one query, folded in memory (fine at this scale)."""
        rows = self._conn.execute("SELECT * FROM interactions ORDER BY date ASC").fetchall()
        return [dict(r) for r in rows]

    def counts(self) -> dict[str, int]:
        c = self._conn.execute("SELECT COUNT(*) n FROM contacts").fetchone()["n"]
        i = self._conn.execute("SELECT COUNT(*) n FROM interactions").fetchone()["n"]
        p = self._conn.execute("SELECT COUNT(*) n FROM participants").fetchone()["n"]
        er = self._conn.execute("SELECT COUNT(*) n FROM entity_refs").fetchone()["n"]
        ext = self._conn.execute(
            "SELECT COUNT(*) n FROM contacts WHERE is_internal=0"
        ).fetchone()["n"]
        return {"contacts": c, "interactions": i, "participants": p, "entity_refs": er, "external": ext}

    def top_contacts(self, limit: int = 15, external_only: bool = True) -> list[dict[str, Any]]:
        where = "WHERE is_internal=0" if external_only else ""
        rows = self._conn.execute(
            f"SELECT * FROM contacts {where} ORDER BY msg_count DESC, last_seen DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def build_crm(settings: dict[str, Any]) -> dict[str, int]:
    """Rebuild ``out/crm.db`` clean from the corpus + current verdicts (deterministic, no LLM).

    Extracted from ``cli.cmd_crm`` so the SAME rebuild runs as part of ``sync`` — otherwise the cockpit
    Fila (which reads ``all_interactions``) would serve a relations DB that lags the latest triage.
    Also writes ``contacts.jsonl`` (the report's contact rollup). Returns counts."""
    from .config import paths as _paths
    from .envelope import parse_eml

    p = _paths(settings, settings["__settings_path__"])
    results_path = p["out_dir"] / "results.jsonl"
    if not results_path.exists():
        return {"recorded": 0, "skipped": 0, "interactions": 0, "contacts": 0, "external": 0}
    verdicts = {r["message_id"]: r for r in
                (json.loads(x) for x in results_path.read_text().splitlines() if x.strip())}
    db = p["out_dir"] / "crm.db"
    if db.exists():
        db.unlink()  # rebuild clean — contact rollups are cumulative
    store = CrmStore(db).connect()
    recorded = skipped = 0
    for eml in sorted(p["corpus_dir"].glob("*.eml")):
        try:
            env = parse_eml(eml.read_bytes())
        except Exception:  # noqa: BLE001 — isolate per-email parse failures
            skipped += 1
            continue
        v = verdicts.get(env["message_id"])
        if not v:
            skipped += 1
            continue
        store.record(env, v)
        recorded += 1
    rollup = store.top_contacts(limit=10_000, external_only=False)
    (p["out_dir"] / "contacts.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rollup), encoding="utf-8")
    c = store.counts()
    store.close()
    return {"recorded": recorded, "skipped": skipped, "interactions": c["interactions"],
            "contacts": c["contacts"], "external": c["external"]}
