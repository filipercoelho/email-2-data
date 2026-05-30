"""Knowledge store (SQLite) — Phase 2 lean: the hand-curated gazetteer.

Per the red-teamed plan, the ONE thing worth maintaining at this scale is an exact-match
``key -> counterparty`` map that encodes irreplaceable business facts (corticoenetos = CLIENT,
Spandex = SUPPLIER). It is a **hint/prior attached to the LLM call — never a short-circuit** (the body
always overrides; a sender can flip roles). The learning loop (reputation decay, verdict cache,
exemplars, thread state) is Phase 4 and intentionally not built yet.

A ``key`` is either a **full email** (``joao@gmail.com``) or a **domain** (``spandex.com``). Email keys
matter for free-mail senders (gmail/sapo/hotmail/live) where the domain says nothing about the person —
that gap caused a real misclassification in testing. ``lookup`` resolves most-specific first:
exact email → exact domain → registrable parent domain.

The CSV is the source of truth: ``seed_gazetteer`` **replaces** the table on every load, so a key
removed from the CSV is removed from the DB (no stale rows). SQLite (not a dict) is kept deliberately —
Phase 4's learning store will need it.
"""

from __future__ import annotations

import csv
import sqlite3
import sys
from pathlib import Path
from typing import Optional

from .schema import COUNTERPARTY

SCHEMA = """
CREATE TABLE IF NOT EXISTS gazetteer (
    key           TEXT PRIMARY KEY,   -- a full email or a domain (lowercased)
    counterparty  TEXT NOT NULL,      -- schema.COUNTERPARTY
    note          TEXT
);
"""

# Common multi-label public suffixes so we never treat a suffix as a registrable domain. This is a
# small PT-focused subset, NOT the full Public Suffix List (deferred — a dep isn't earned at 9 rows).
_MULTI_SUFFIX = {
    "com.pt", "gov.pt", "edu.pt", "org.pt", "com.br", "com.es",
    "co.uk", "org.uk", "co.jp",
}


def _warn(msg: str) -> None:
    print(f"  warning: {msg}", file=sys.stderr)


def _norm(s: str) -> str:
    """Lowercase, strip whitespace and a trailing FQDN dot."""
    return (s or "").strip().lower().rstrip(".")


def _domain_of(identifier: str) -> str:
    """Bare domain from an email or domain; drops a leading ``www.``."""
    dom = identifier.split("@", 1)[1] if "@" in identifier else identifier
    return dom[4:] if dom.startswith("www.") else dom


def _registrable(domain: str) -> str:
    """Registrable parent: last 2 labels, or last 3 when the last 2 are a known multi-label suffix."""
    parts = domain.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _MULTI_SUFFIX:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain


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
        """(Re)load the curated gazetteer from a CSV `domain,counterparty,note`. The CSV is the source
        of truth: the table is REPLACED, so removed keys don't linger. `#`-comment and blank lines are
        skipped; rows with an unknown counterparty or duplicate key are warned about. Returns rows loaded.
        (The first CSV column is named `domain` for back-compat but may hold an email or a domain.)"""
        assert self._conn is not None, "call connect() first"
        rows: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        with open(csv_path, encoding="utf-8") as fh:
            reader = csv.DictReader(r for r in fh if r.strip() and not r.lstrip().startswith("#"))
            for row in reader:
                key = _norm(row.get("domain") or "")
                cp = (row.get("counterparty") or "").strip()
                if not key or not cp:
                    continue
                if cp not in COUNTERPARTY:
                    _warn(f"gazetteer: skipping {key!r} — invalid counterparty {cp!r}")
                    continue
                if key in seen:
                    _warn(f"gazetteer: duplicate key {key!r} — later row wins")
                seen.add(key)
                rows.append((key, cp, (row.get("note") or "").strip()))
        self._conn.execute("DELETE FROM gazetteer")  # replace, don't accumulate (stale-row fix)
        self._conn.executemany(
            "INSERT INTO gazetteer(key, counterparty, note) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET counterparty=excluded.counterparty, note=excluded.note",
            rows,
        )
        self._conn.commit()
        return len(rows)

    def lookup(self, identifier: str) -> Optional[str]:
        """Resolve a sender (email or domain) to a counterparty hint, most-specific first:
        exact email → exact domain → registrable parent domain. None if unknown."""
        if not identifier or self._conn is None:
            return None
        ident = _norm(identifier)
        candidates: list[str] = []
        if "@" in ident:
            candidates.append(ident)                 # exact email
        dom = _domain_of(ident)
        candidates.append(dom)                        # exact domain
        reg = _registrable(dom)
        if reg != dom:
            candidates.append(reg)                    # registrable parent
        for key in candidates:
            row = self._conn.execute(
                "SELECT counterparty FROM gazetteer WHERE key = ?", (key,)
            ).fetchone()
            if row:
                return row[0]
        return None
