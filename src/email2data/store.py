"""Knowledge store (SQLite) — Phase 2 lean: the hand-curated gazetteer.

Per the red-teamed plan, the ONE thing worth maintaining at this scale is an exact-match
``domain -> counterparty`` map that encodes irreplaceable business facts (corticoenetos = CLIENT,
Spandex = SUPPLIER). It is a **hint/prior attached to the LLM call — never a short-circuit** (the body
always overrides; a sender can flip roles). The learning loop (reputation decay, verdict cache,
exemplars, thread state) is Phase 4 and intentionally not built yet.
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS gazetteer (
    domain        TEXT PRIMARY KEY,
    counterparty  TEXT NOT NULL,   -- schema.COUNTERPARTY
    note          TEXT
);
"""


class KnowledgeStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> "KnowledgeStore":
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        return self

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def seed_gazetteer(self, csv_path: str | Path) -> int:
        """(Re)load the curated gazetteer from a CSV `domain,counterparty,note`. Idempotent upsert.
        `#`-comment and blank lines are skipped. Returns rows loaded."""
        assert self._conn is not None, "call connect() first"
        n = 0
        with open(csv_path, encoding="utf-8") as fh:
            reader = csv.DictReader(r for r in fh if r.strip() and not r.lstrip().startswith("#"))
            for row in reader:
                dom = (row.get("domain") or "").strip().lower()
                cp = (row.get("counterparty") or "").strip()
                if not dom or not cp:
                    continue
                self._conn.execute(
                    "INSERT INTO gazetteer(domain, counterparty, note) VALUES (?,?,?) "
                    "ON CONFLICT(domain) DO UPDATE SET counterparty=excluded.counterparty, note=excluded.note",
                    (dom, cp, (row.get("note") or "").strip()),
                )
                n += 1
        self._conn.commit()
        return n

    def lookup(self, domain: str) -> Optional[str]:
        """Exact-match domain -> counterparty hint (or its parent domain). None if unknown."""
        if not domain or self._conn is None:
            return None
        dom = domain.strip().lower()
        row = self._conn.execute(
            "SELECT counterparty FROM gazetteer WHERE domain = ?", (dom,)
        ).fetchone()
        if row:
            return row[0]
        # also try the registrable parent (mail.x.com -> x.com), one level
        parts = dom.split(".")
        if len(parts) > 2:
            parent = ".".join(parts[-2:])
            row = self._conn.execute(
                "SELECT counterparty FROM gazetteer WHERE domain = ?", (parent,)
            ).fetchone()
            if row:
                return row[0]
        return None
