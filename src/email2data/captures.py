"""Conversational-intake capture queue + allowlist store (ADR-019/-020/-021).

The Telegram intake bot is a capture ADAPTER on the ADR-015 ledger, not a new fact store: a capture
lands here as a pending row, is scrubbed from Telegram once durably stored (ADR-020 §2 persist-then-
scrub), and is appended to a project's ledger ONLY when the user validates it (ADR-019 §5 — no
auto-apply). This is the DB layer for the ``captures`` + ``capture_users`` tables in the precious
workspace.db; it wraps the SAME sqlite3 connection as ProjectStore (it never opens or owns one).

Idempotency (ADR-020): ``capture_id`` is deterministic — ``c-<chat>-<message>`` — so a Telegram retry
maps to the same row and ``add()`` is a clean no-op (an explicit ``UNIQUE(message_id, chat_id)`` is a
second guard). A capture is never silently dropped (N2): an unmatched one stays ``stored`` in the queue.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

# Capture lifecycle (MVP subset; ADR-019 §5). The transcript column arrived with audio (Increment 1,
# v6); the extracted-field columns arrive with inference (Increment 2), each via a guarded migration.
STATUS_STORED = "stored"        # durably persisted locally; awaiting project resolution
STATUS_PARSED = "parsed"        # the user picked a project (inferred_project_id set)
STATUS_APPLIED = "applied"      # validated into the project ledger
STATUS_DISCARDED = "discarded"  # the user discarded it (kept for audit, not destroyed)
PENDING_STATUSES = (STATUS_STORED, STATUS_PARSED)

# The content-class router (ADR-019 §5.1). The MVP has no transcription engine yet, so these are
# informational labels the worker sets: a photo/quote is an artifact, the staffer's own words a
# conversation.
CONTENT_ARTIFACT = "artifact"
CONTENT_CONVERSATION = "conversation"


def _now() -> str:
    """UTC ISO-8601 to seconds — matches project._now so timeline sort keys interleave cleanly."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def capture_id_for(chat_id: int, message_id: int) -> str:
    """Deterministic capture id encoding the Telegram identity — it IS the idempotency key (ADR-020)."""
    return f"c-{chat_id}-{message_id}"


class CaptureStore:
    """DB layer for the intake capture queue + allowlist. Wraps an existing sqlite3 connection — share
    the one from a connected Workspace, exactly like ProjectStore (it does not open or own one)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # -- allowlist: default-deny identity for the bot (ADR-019 §6) --------------------------------

    def allow(self, telegram_user_id: int, display_name: str = "", roster_owner: str = "",
              added_by: str = "") -> None:
        """Add or re-enable a sender. Idempotent upsert: re-enables a soft-disabled row and updates its
        labels only when new non-empty ones are supplied — a bare ``allow(id)`` PRESERVES the existing
        display_name/roster_owner (the sender->owner mapping that feeds asserted_by) and the original
        ``added_by``/``added_at`` audit."""
        self._conn.execute(
            "INSERT INTO capture_users(telegram_user_id, display_name, roster_owner, enabled,"
            " added_by, added_at) VALUES (?,?,?,1,?,?)"
            " ON CONFLICT(telegram_user_id) DO UPDATE SET"
            "  display_name=COALESCE(NULLIF(excluded.display_name, ''), capture_users.display_name),"
            "  roster_owner=COALESCE(NULLIF(excluded.roster_owner, ''), capture_users.roster_owner),"
            "  enabled=1",
            (telegram_user_id, display_name, roster_owner, added_by, _now()))
        self._conn.commit()

    def disable(self, telegram_user_id: int) -> None:
        """Soft-disable a sender (keeps the audit row); a disabled sender is treated as not allowed."""
        self._conn.execute(
            "UPDATE capture_users SET enabled=0 WHERE telegram_user_id=?", (telegram_user_id,))
        self._conn.commit()

    def get_user(self, telegram_user_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM capture_users WHERE telegram_user_id=?", (telegram_user_id,)).fetchone()
        return dict(row) if row else None

    def is_allowed(self, telegram_user_id: int) -> bool:
        """Default-deny: only a present, enabled sender is allowed (ADR-019 §6)."""
        row = self._conn.execute(
            "SELECT enabled FROM capture_users WHERE telegram_user_id=?",
            (telegram_user_id,)).fetchone()
        return bool(row) and bool(row["enabled"])

    # -- capture queue ----------------------------------------------------------------------------

    def add(self, *, telegram_message_id: int, telegram_chat_id: int,
            content_class: str = CONTENT_CONVERSATION, raw_text: str = "",
            media_paths: list[str] | None = None, channel: str = "manual",
            asserted_by: str = "", acquired_at: str = "") -> tuple[str, bool]:
        """Persist a capture into the queue, idempotently. The worker calls this BEFORE scrubbing
        Telegram (ADR-020 persist-then-scrub). Returns ``(capture_id, created)`` — ``created`` is
        False when a Telegram retry hit the existing row (a clean no-op, never a second write)."""
        cid = capture_id_for(telegram_chat_id, telegram_message_id)
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO captures(capture_id, telegram_message_id, telegram_chat_id,"
            " content_class, raw_text, media_paths, inferred_project_id, channel, asserted_by,"
            " acquired_at, status, telegram_scrubbed_at, created_ts, applied_ts)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, telegram_message_id, telegram_chat_id, content_class, raw_text,
             json.dumps(list(media_paths or [])), None, channel, asserted_by,
             acquired_at or _now(), STATUS_STORED, None, _now(), None))
        self._conn.commit()
        return cid, cur.rowcount > 0

    def get(self, capture_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM captures WHERE capture_id=?", (capture_id,)).fetchone()
        return self._row(row) if row else None

    def list_pending(self) -> list[dict[str, Any]]:
        """The pending-edits queue (the webapp 'Caixa de Capturas'), newest-first — stored + parsed
        captures that still await the user's validation (ADR-019 §5 / R9)."""
        placeholders = ",".join("?" for _ in PENDING_STATUSES)
        rows = self._conn.execute(
            f"SELECT * FROM captures WHERE status IN ({placeholders})"
            " ORDER BY created_ts DESC, capture_id DESC", PENDING_STATUSES).fetchall()
        return [self._row(r) for r in rows]

    def set_project(self, capture_id: str, project_id: str) -> None:
        """Record the project the user picked (ADR-019 §5.2). Moves stored -> parsed; the capture
        still requires explicit validation before it touches the project (no auto-apply). Guarded to
        pending captures so a terminal (applied/discarded) one is never silently mutated."""
        ph = ",".join("?" for _ in PENDING_STATUSES)
        self._conn.execute(
            f"UPDATE captures SET inferred_project_id=?, status=?"
            f" WHERE capture_id=? AND status IN ({ph})",
            (project_id, STATUS_PARSED, capture_id, *PENDING_STATUSES))
        self._conn.commit()

    def set_transcript(self, capture_id: str, text: str) -> None:
        """Store the pt-PT transcript of a voice/audio capture (Increment 1, v6). Best-effort and
        idempotent: called AFTER the capture is durably persisted + scrubbed, so a transcription failure
        leaves the row intact (audio preserved, transcript NULL) for manual handling — the capture is
        PRECIOUS, inference is not (ADR-020 preserve-at-core)."""
        self._conn.execute(
            "UPDATE captures SET transcript=? WHERE capture_id=?", (text, capture_id))
        self._conn.commit()

    def set_extracted_fields(self, capture_id: str, fields: dict[str, str], confidence: float) -> None:
        """Store the LLM-extracted field VALUES + a 0-1 confidence (Increment 2, v7). Stored ONLY —
        never auto-applied: the user validates each field into the project via /field (R9). Best-effort
        and idempotent, like set_transcript; an extraction failure simply leaves these NULL."""
        self._conn.execute(
            "UPDATE captures SET extracted_fields_json=?, confidence=? WHERE capture_id=?",
            (json.dumps(dict(fields or {})), float(confidence), capture_id))
        self._conn.commit()

    def mark_scrubbed(self, capture_id: str, ts: str = "") -> None:
        """Stamp when the source was deleted from Telegram (ADR-020 §2) — called only AFTER the
        capture is durably stored, never the reverse (persist-then-scrub)."""
        self._conn.execute(
            "UPDATE captures SET telegram_scrubbed_at=? WHERE capture_id=?",
            (ts or _now(), capture_id))
        self._conn.commit()

    def mark_applied(self, capture_id: str, ts: str = "") -> None:
        """Record that a capture was validated into its project (ADR-019 §5). The append to the
        project ledger itself is done by the caller via ProjectStore; this is the queue transition.
        Guarded to pending captures so 'applied' is reached only from stored/parsed, never resurrected
        from a discarded one (preserve-at-core)."""
        ph = ",".join("?" for _ in PENDING_STATUSES)
        self._conn.execute(
            f"UPDATE captures SET status=?, applied_ts=?"
            f" WHERE capture_id=? AND status IN ({ph})",
            (STATUS_APPLIED, ts or _now(), capture_id, *PENDING_STATUSES))
        self._conn.commit()

    def discard(self, capture_id: str) -> None:
        """Drop a capture from the pending queue WITHOUT applying it. The row + its media are retained
        (ADR-020 preserve-at-core) for audit — nothing is destroyed. Guarded to pending captures so an
        already-applied capture is not retroactively flipped to discarded."""
        ph = ",".join("?" for _ in PENDING_STATUSES)
        self._conn.execute(
            f"UPDATE captures SET status=? WHERE capture_id=? AND status IN ({ph})",
            (STATUS_DISCARDED, capture_id, *PENDING_STATUSES))
        self._conn.commit()

    # -- helpers ----------------------------------------------------------------------------------

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        """Materialise a capture row, decoding the JSON ``media_paths`` (→ always a list) and
        ``extracted_fields_json`` (→ always a dict) back into Python — even if the column holds NULL,
        "" or a non-container value written out-of-band."""
        d = dict(row)
        try:
            paths = json.loads(d["media_paths"]) if d.get("media_paths") else []
        except (TypeError, ValueError):
            paths = []
        d["media_paths"] = paths if isinstance(paths, list) else []
        try:
            fields = json.loads(d["extracted_fields_json"]) if d.get("extracted_fields_json") else {}
        except (TypeError, ValueError):
            fields = {}
        d["extracted_fields"] = fields if isinstance(fields, dict) else {}
        return d
