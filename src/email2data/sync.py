"""Incremental sync — the watermark store + the one orchestrator shared by CLI, button, and startup.

Two stages re-do work if run naively:
  * fetch re-downloads every message body each run (file-exists only stops a re-write);
  * triage overwrites results.jsonl, re-spending Tier-1 LLM tokens on already-classified mail.

This module adds the "since last retrieve" cursor for fetch (per-mailbox IMAP UID watermark, see
``fetch.py``) and a single ``run_sync`` that pulls only new mail then classifies only the new emails.
Triage's own incremental gate lives in ``cascade.triage_corpus`` (it keys off results.jsonl, the
source of truth — no second cursor to drift).

It also owns ``message_scope`` (ADR-038): which of our inboxes each message reached, the durable
fact a per-user visibility layer filters on. Derivation lives in ``scopes.py``; only storage is here.
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

-- Which of OUR inboxes a message reached (ADR-038). One row per (message, address): a message CC'd
-- to two of our mailboxes gets two rows, and a thread's visibility scope is the UNION over its
-- messages — so a shared thread is never hidden from one of its legitimate readers.
--
-- ``address`` is the scope token and is deliberately an ADDRESS, not a configured account id: mail
-- reaches margarida.reis@ / carmen.martins@ / lindoservico@, which are real inboxes we do NOT
-- fetch, and keying on the address lets those be granted without inventing a fetch account.
--
-- ``source`` records the EVIDENCE CLASS, per the PROFILE.md FACT/INFERENCE/UNKNOWN rule:
--   'fetch'       FACT      — the account we authenticated as when we cached it (strongest)
--   'header'      FACT      — the server's own Envelope-to / Delivered-To / X-Original-To
--   'participant' INFERENCE — one of our addresses in From/To/Cc/Reply-To (no delivery header)
-- A message with no row at all is UNKNOWN and folds to scopes.SCOPE_UNATTRIBUTED (admin-visible).
-- Stronger sources overwrite weaker ones; the reverse is refused (see ``set_message_scopes``).
CREATE TABLE IF NOT EXISTS message_scope (
    message_id  TEXT NOT NULL,       -- identity.canonical_id ("mid:..." / "sha256:...")
    address     TEXT NOT NULL,       -- lowercased mailbox address (the grantable scope token)
    source      TEXT NOT NULL,       -- 'fetch' | 'header' | 'participant'
    updated_ts  TEXT,
    PRIMARY KEY (message_id, address)
);

CREATE INDEX IF NOT EXISTS idx_message_scope_address ON message_scope(address);
"""

# Evidence ranking for ``message_scope.source``. A re-attribution may only move UP this ladder, so a
# cheap participant guess can never overwrite what the IMAP server actually told us at fetch time.
# Lives beside the schema because the ordering IS part of the table's contract.
SOURCE_RANK = {"participant": 1, "header": 2, "fetch": 3}


class SyncStore:
    """Per-(account, mailbox) IMAP UID watermark + per-message inbox attribution. ``out/sync.db``.

    Mirrors the lightweight style of ``store.KnowledgeStore`` (check_same_thread=False so the webapp
    threadpool / startup thread can share it safely).

    This DB is regenerable by design. Losing it costs the tier-1 ``fetch`` attribution rows, which
    ``scopes.backfill`` then re-derives from headers at tier 2/3 — strictly *less* precise, never
    wrong, and it degrades toward the admin-visible bucket rather than toward hiding mail.
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

    # -- inbox attribution (ADR-038) -----------------------------------------------------------

    def set_message_scopes(self, message_id: str, addresses: list[str], source: str) -> int:
        """Record ``message_id`` as having reached each of ``addresses``. Returns rows written.

        **Upgrade-only**: an existing row is overwritten only when ``source`` outranks the stored one
        (``SOURCE_RANK``). That is what makes ``scopes.backfill`` safe to re-run alongside a live
        fetch — a header/participant derivation can never clobber the account we authenticated as.
        Re-recording the same (message, address, source) writes nothing, so callers are idempotent.
        """
        assert self._conn is not None, "SyncStore not connected"
        rank = SOURCE_RANK.get(source)
        if rank is None:
            raise ValueError(f"unknown message_scope source {source!r} "
                             f"(expected one of {sorted(SOURCE_RANK)})")
        if not message_id:
            return 0
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        written = 0
        for address in dict.fromkeys(
            a.strip().lower() for a in (addresses or []) if a and a.strip()
        ):
            row = self._conn.execute(
                "SELECT source FROM message_scope WHERE message_id=? AND address=?",
                (message_id, address),
            ).fetchone()
            if row is not None and SOURCE_RANK.get(row[0], 0) >= rank:
                continue  # already attributed by equal-or-stronger evidence
            self._conn.execute(
                "INSERT INTO message_scope (message_id, address, source, updated_ts) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(message_id, address) DO UPDATE SET "
                "source=excluded.source, updated_ts=excluded.updated_ts",
                (message_id, address, source, now),
            )
            written += 1
        if written:
            self._conn.commit()
        return written

    def message_scopes(self, message_id: str) -> dict[str, str]:
        """``{address: source}`` for one message — ``{}`` when unattributed."""
        assert self._conn is not None, "SyncStore not connected"
        rows = self._conn.execute(
            "SELECT address, source FROM message_scope WHERE message_id=? ORDER BY address",
            (message_id,),
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def all_message_scopes(self) -> dict[str, dict[str, str]]:
        """``{message_id: {address: source}}`` for every attributed message.

        One query for the whole table: the caller (``scopes.thread_scopes``) needs it per page
        render, and the row count is bounded by the corpus (~hundreds), so this stays cheaper than
        a per-thread query fan-out.
        """
        assert self._conn is not None, "SyncStore not connected"
        out: dict[str, dict[str, str]] = {}
        for message_id, address, source in self._conn.execute(
            "SELECT message_id, address, source FROM message_scope ORDER BY message_id, address"
        ):
            out.setdefault(message_id, {})[address] = source
        return out

    def scope_address_counts(self) -> dict[str, int]:
        """``{address: attributed_message_count}``, for the ``scopes status`` report + admin UI."""
        assert self._conn is not None, "SyncStore not connected"
        return {
            r[0]: int(r[1])
            for r in self._conn.execute(
                "SELECT address, COUNT(*) FROM message_scope GROUP BY address ORDER BY COUNT(*) DESC"
            )
        }

    def scope_source_counts(self) -> dict[str, int]:
        """``{source: row_count}`` — how much of the attribution is FACT vs INFERENCE."""
        assert self._conn is not None, "SyncStore not connected"
        return {
            r[0]: int(r[1])
            for r in self._conn.execute(
                "SELECT source, COUNT(*) FROM message_scope GROUP BY source"
            )
        }


def run_sync(
    settings: dict[str, Any],
    *,
    do_fetch: bool = True,
    do_triage: bool = True,
    do_crm: bool = True,
    full: bool = False,
    account_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Pull only new mail, then classify only the new emails. Shared by CLI, button, and startup.

    Token spend is bounded by both layers being incremental: fetch skips already-seen UIDs, triage
    skips message_ids already in results.jsonl. ``full=True`` forces a bootstrap + full reclassify.
    ``do_crm`` rebuilds ``out/crm.db`` from the (now-updated) verdicts so the cockpit Fila never reads a
    stale relations DB — cheap (deterministic, no LLM) and keeps thread/response state current.

    The three ``do_*`` switches select which stages run; ``do_fetch=True, do_triage=False`` is the
    "pull mail, spend no Tier-1 tokens" mode (triage is the only stage that calls the LLM, and it is
    not even constructed when off). ``account_ids`` targets a subset of accounts and is passed
    through to ``fetch.fetch_all`` so the per-account isolation there still applies.

    Return keys — the original six are unchanged for existing callers (CLI, the Sincronizar button,
    ``report.py``'s toast); the rest are additive:

      * ``per_account`` — ``{account_id: messages_cached}`` for this run;
      * ``account_failures`` — ``{account_id: detail}`` for accounts that failed (named separately
        from ``failed``, which counts Tier-1 triage failures, not accounts);
      * ``stages`` — which stages actually ran, so a 0 from a skipped stage is never misread as a
        measured 0.
    """
    from . import cascade, crm, fetch

    out: dict[str, Any] = {"fetched": 0, "triaged_new": 0, "triaged_skipped": 0,
                           "offline": 0, "llm": 0, "failed": 0, "crm_recorded": 0,
                           "per_account": {}, "account_failures": {},
                           "stages": {"fetch": do_fetch, "triage": do_triage, "crm": do_crm}}
    if do_fetch:
        counts = fetch.fetch_all(settings, full=full, account_ids=account_ids)
        out["fetched"] = sum(counts.values())
        out["per_account"] = dict(counts)
        out["account_failures"] = dict(getattr(counts, "failures", {}) or {})
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
