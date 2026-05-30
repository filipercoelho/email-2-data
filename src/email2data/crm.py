"""CRM PoC — turn every triaged email into a relationship record. Deterministic, no LLM, ~free.

Model = one event log + one rollup (an org rollup is just a query over contacts, so it's not a table):

  * ``interactions`` — one immutable row per email (the source of truth: participants, thread root,
    direction, and the verdict we already compute). Everything else can be rebuilt from this.
  * ``contacts`` — one row per person, rolled up from the interactions: who they are, how often and in
    what role we exchange mail, **when we last heard from them**, and **what kinds** of mail (the
    counterparty/purpose tallies).

Stored in its own ``out/crm.db`` so it stays decoupled from the gazetteer store. It is a deliberate
aggregation of personal data (names/emails) — local-only and gitignored, same boundary as results.jsonl.

Deferred (the "features to ponder"): identity resolution (one person, many addresses), the
person↔person relationship graph, org tables/UI, and wiring this into the live triage loop.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from .signals import OUR_DOMAIN

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
    thread_root  TEXT,
    is_reply     INTEGER,
    is_forward   INTEGER,
    has_attach   INTEGER,
    n_recipients INTEGER
);
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
    counterparty_counts TEXT,    -- JSON tally {CLIENT: n, ...}  ("what kinds of relationship")
    purpose_counts      TEXT,    -- JSON tally {PO_FROM_CLIENT: n, ...}  ("email types")
    is_automated        INTEGER
);
"""


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


class CrmStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> "CrmStore":
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        return self

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def record(self, env: dict[str, Any], verdict: dict[str, Any]) -> None:
        """Record one email: its interaction row + a contact upsert for every participant."""
        assert self._conn is not None, "call connect() first"
        mid = env.get("message_id") or ""
        date = env.get("date") or ""
        parts = participants(env)
        # Idempotent: if we've already recorded this message, replace the interaction but do NOT
        # re-bump the cumulative contact rollups.
        seen = self._conn.execute("SELECT 1 FROM interactions WHERE message_id=?", (mid,)).fetchone()
        self._conn.execute(
            "INSERT OR REPLACE INTO interactions(message_id, date, from_email, direction, counterparty,"
            " purpose, priority, urgency, subject, thread_root, is_reply, is_forward, has_attach, n_recipients)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                mid, date, (env.get("from") or {}).get("email", ""), verdict.get("direction", ""),
                verdict.get("counterparty", ""), verdict.get("purpose", ""), verdict.get("priority", ""),
                int(verdict.get("urgency", 0) or 0), env.get("subject", ""), _thread_root(env),
                int(bool(env.get("in_reply_to") or env.get("references"))), 0,
                int(bool(env.get("attachments"))), len(env.get("to") or []) + len(env.get("cc") or []),
            ),
        )
        if not seen:
            for email, name, role in parts:
                self._upsert_contact(email, name, role, date, verdict)
        self._conn.commit()

    def _upsert_contact(self, email, name, role, date, verdict) -> None:
        cp, pur = verdict.get("counterparty", ""), verdict.get("purpose", "")
        domain = email.rsplit("@", 1)[-1] if "@" in email else ""
        is_internal = int(domain == OUR_DOMAIN or domain.endswith("." + OUR_DOMAIN))
        row = self._conn.execute("SELECT * FROM contacts WHERE email = ?", (email,)).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO contacts(email, display_name, domain, is_internal, first_seen, last_seen,"
                " last_from_date, msg_count, from_count, to_count, cc_count, last_counterparty,"
                " last_purpose, counterparty_counts, purpose_counts, is_automated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (email, name, domain, is_internal, date, date, date if role == "from" else "",
                 1, int(role == "from"), int(role == "to"), int(role == "cc"),
                 cp, pur, _bump(None, cp), _bump(None, pur), 0),
            )
            return
        self._conn.execute(
            "UPDATE contacts SET display_name=?, first_seen=?, last_seen=?, last_from_date=?, msg_count=?,"
            " from_count=?, to_count=?, cc_count=?, last_counterparty=?, last_purpose=?,"
            " counterparty_counts=?, purpose_counts=? WHERE email=?",
            (
                name or row["display_name"],
                min(x for x in (row["first_seen"], date) if x) if (row["first_seen"] or date) else "",
                max(row["last_seen"] or "", date),
                max(row["last_from_date"] or "", date) if role == "from" else row["last_from_date"],
                row["msg_count"] + 1,
                row["from_count"] + int(role == "from"), row["to_count"] + int(role == "to"),
                row["cc_count"] + int(role == "cc"),
                cp or row["last_counterparty"], pur or row["last_purpose"],
                _bump(row["counterparty_counts"], cp), _bump(row["purpose_counts"], pur),
                email,
            ),
        )

    def counts(self) -> dict[str, int]:
        c = self._conn.execute("SELECT COUNT(*) n FROM contacts").fetchone()["n"]
        i = self._conn.execute("SELECT COUNT(*) n FROM interactions").fetchone()["n"]
        ext = self._conn.execute("SELECT COUNT(*) n FROM contacts WHERE is_internal=0").fetchone()["n"]
        return {"contacts": c, "interactions": i, "external": ext}

    def top_contacts(self, limit: int = 15, external_only: bool = True) -> list[dict[str, Any]]:
        where = "WHERE is_internal=0" if external_only else ""
        rows = self._conn.execute(
            f"SELECT * FROM contacts {where} ORDER BY msg_count DESC, last_seen DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
