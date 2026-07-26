"""Knowledge store (SQLite) — Phase 2 lean: the hand-curated gazetteer.

Per the red-teamed plan, the ONE thing worth maintaining at this scale is an exact-match
``key -> counterparty`` map that encodes irreplaceable business facts (cork-example = CLIENT,
Laminex = SUPPLIER). It is a **hint/prior attached to the LLM call — never a short-circuit** (the body
always overrides; a sender can flip roles). The learning loop (reputation decay, verdict cache,
exemplars, thread state) is Phase 4 and intentionally not built yet.

A ``key`` is either a **full email** (``joao@gmail.com``) or a **domain** (``laminex.com``). Email keys
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
        # check_same_thread=False for consistency with the other stores: only used in single-threaded
        # CLI triage today, but this keeps it safe if it is ever shared across the webapp threadpool.
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
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

    def count(self) -> int:
        """Rows currently in the gazetteer table."""
        assert self._conn is not None, "call connect() first"
        return int(self._conn.execute("SELECT count(*) FROM gazetteer").fetchone()[0])

    def counts_by_counterparty(self) -> dict[str, int]:
        """Row counts per counterparty — the *shape* of the curated knowledge with no key revealed
        (the keys are real client/supplier domains; personal data does not belong in a status line)."""
        assert self._conn is not None, "call connect() first"
        return dict(
            self._conn.execute(
                "SELECT counterparty, count(*) FROM gazetteer GROUP BY counterparty "
                "ORDER BY counterparty"
            )
        )

    def seed_or_warn(self, csv_path: str | Path) -> Optional[int]:
        """Seed from the CSV when it is there — and when it is NOT, say so out loud.

        The CSV is the source of truth, which makes a missing CSV over a **non-empty** table the one
        genuinely dangerous state: the table goes on serving whatever it was last seeded with, so the
        priors still fire — including the ADR-005 veto that stops an offline IGNORE — while nobody can
        read or edit them. That is a frozen snapshot masquerading as a curated list, and it used to be
        entirely silent (`if gaz.exists()` with no else). A missing CSV over an *empty* table is just a
        fresh install and stays quiet. Returns rows loaded, or None when there was no CSV.
        """
        assert self._conn is not None, "call connect() first"
        path = Path(csv_path)
        if path.exists():
            return self.seed_gazetteer(path)
        n = self.count()
        if n:
            _warn(
                f"gazetteer: {path} is MISSING but {n} row(s) are still live in "
                f"{self.db_path.name} — those priors are frozen at their last seed and cannot be "
                "edited. Recover the source of truth with `email2data gazetteer export`."
            )
        return None

    def export_gazetteer(self, csv_path: str | Path) -> int:
        """Write the live table back out as a seedable CSV — the inverse of ``seed_gazetteer``.

        This exists because the CSV is gitignored (it names real clients), so it is the one store
        input with no second copy anywhere: lose it and the priors survive only inside
        ``knowledge.db``, unreadable and uneditable. Round-tripping them back out recovers the source
        of truth without anyone retyping real client names from memory. Returns rows written.
        """
        assert self._conn is not None, "call connect() first"
        rows = self._conn.execute(
            "SELECT key, counterparty, note FROM gazetteer ORDER BY counterparty, key"
        ).fetchall()
        path = Path(csv_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(
                "# Hand-curated key -> counterparty prior (ADR-005): a HINT attached to the LLM call and\n"
                "# the veto that stops an offline IGNORE — never a verdict. The body always overrides.\n"
                "# THIS FILE IS THE SOURCE OF TRUTH: triage/sync REPLACE the table from it, so a key\n"
                "# deleted here is deleted from out/knowledge.db on the next run.\n"
                "# Gitignored on purpose (it names real clients/suppliers). Regenerate it from the live\n"
                "# table with `email2data gazetteer export --force`.\n"
            )
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["domain", "counterparty", "note"])
            w.writerows(rows)
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
