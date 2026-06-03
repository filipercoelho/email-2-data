"""Incremental sync — the watermark store + the one orchestrator shared by CLI, button, and startup.

Two stages re-do work if run naively:
  * fetch re-downloads every message body each run (file-exists only stops a re-write);
  * triage overwrites results.jsonl, re-spending Tier-1 LLM tokens on already-classified mail.

This module adds the "since last retrieve" cursor for fetch (per-mailbox IMAP UID watermark, see
``fetch.py``) and a single ``run_sync`` that pulls only new mail then classifies only the new emails.
Triage's own incremental gate lives in ``cascade.triage_corpus`` (it keys off results.jsonl, the
source of truth — no second cursor to drift).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS fetch_cursor (
    account_id   TEXT NOT NULL,
    mailbox      TEXT NOT NULL,
    uidvalidity  INTEGER NOT NULL,   -- IMAP UIDVALIDITY epoch; a change invalidates last_uid
    last_uid     INTEGER NOT NULL,   -- highest UID fetched so far in this epoch
    updated_ts   TEXT,
    PRIMARY KEY (account_id, mailbox)
);
"""


class SyncStore:
    """Per-(account, mailbox) IMAP UID watermark. Lives at ``out/sync.db``.

    Mirrors the lightweight style of ``store.KnowledgeStore`` (check_same_thread=False so the webapp
    threadpool / startup thread can share it safely).
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> "SyncStore":
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        return self

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def get_cursor(self, account_id: str, mailbox: str) -> Optional[tuple[int, int]]:
        """Return ``(uidvalidity, last_uid)`` for a mailbox, or ``None`` if never fetched."""
        assert self._conn is not None, "SyncStore not connected"
        row = self._conn.execute(
            "SELECT uidvalidity, last_uid FROM fetch_cursor WHERE account_id=? AND mailbox=?",
            (account_id, mailbox),
        ).fetchone()
        return (int(row[0]), int(row[1])) if row else None

    def set_cursor(self, account_id: str, mailbox: str, uidvalidity: int, last_uid: int) -> None:
        assert self._conn is not None, "SyncStore not connected"
        self._conn.execute(
            "INSERT INTO fetch_cursor (account_id, mailbox, uidvalidity, last_uid, updated_ts) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(account_id, mailbox) DO UPDATE SET "
            "uidvalidity=excluded.uidvalidity, last_uid=excluded.last_uid, updated_ts=excluded.updated_ts",
            (account_id, mailbox, int(uidvalidity), int(last_uid),
             datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        self._conn.commit()


def run_sync(
    settings: dict[str, Any],
    *,
    do_fetch: bool = True,
    do_triage: bool = True,
    do_crm: bool = True,
    full: bool = False,
) -> dict[str, int]:
    """Pull only new mail, then classify only the new emails. Shared by CLI, button, and startup.

    Token spend is bounded by both layers being incremental: fetch skips already-seen UIDs, triage
    skips message_ids already in results.jsonl. ``full=True`` forces a bootstrap + full reclassify.
    ``do_crm`` rebuilds ``out/crm.db`` from the (now-updated) verdicts so the cockpit Fila never reads a
    stale relations DB — cheap (deterministic, no LLM) and keeps thread/response state current.
    """
    from . import cascade, crm, fetch

    out: dict[str, int] = {"fetched": 0, "triaged_new": 0, "triaged_skipped": 0,
                           "offline": 0, "llm": 0, "failed": 0, "crm_recorded": 0}
    if do_fetch:
        counts = fetch.fetch_all(settings, full=full)
        out["fetched"] = sum(counts.values())
    if do_triage:
        store = cascade.build_store(settings)
        try:
            t = cascade.triage_corpus(settings, store, full=full)
        finally:
            store.close()
        out["triaged_new"] = t.get("new", t.get("corpus", 0))
        out["triaged_skipped"] = t.get("skipped", 0)
        out["offline"] = t.get("offline", 0)
        out["llm"] = t.get("llm", 0)
        out["failed"] = t.get("failed", 0)
    if do_crm:
        out["crm_recorded"] = crm.build_crm(settings).get("recorded", 0)
    return out


def open_store(settings: dict[str, Any]) -> SyncStore:
    """Open the SyncStore at ``out/sync.db`` for the given settings."""
    p = paths(settings, settings["__settings_path__"])
    return SyncStore(p["out_dir"] / "sync.db").connect()
