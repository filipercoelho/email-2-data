"""M0 — read-only IMAP fetch into a local .eml corpus.

SAFETY (red-team B1): we open the mailbox read-only (EXAMINE) AND fetch only with ``BODY.PEEK[]``.
We never issue STORE/DELETE/EXPUNGE/APPEND/COPY and never fetch ``RFC822``/``BODY[]`` (those set
\\Seen). PEEK is the client-side guarantee; read-only select is the belt to that suspenders.
"""

from __future__ import annotations

import imaplib
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import audit
from .config import account_password, paths
from .identity import canonical_id_from_raw, safe_filename

# Forbidden anywhere in this module — asserted by a test. If you need one of these, you are no longer
# building a read-only system; stop and rethink.
_FORBIDDEN_IMAP = ("STORE", "EXPUNGE", "DELETE", "APPEND", "COPY", "RFC822", "BODY[]")

_FETCH_ITEM = "(BODY.PEEK[])"
_BODY_RE = re.compile(rb"BODY\[\]", re.IGNORECASE)


class FetchError(Exception):
    """An account failed to fetch. Message names the account, never the credentials."""


def _imap_date(days: int) -> str:
    since = datetime.now(timezone.utc) - timedelta(days=max(days, 0))
    return since.strftime("%d-%b-%Y")  # IMAP date format, e.g. 01-May-2026


def _connect(settings: dict[str, Any], account: dict[str, Any]) -> imaplib.IMAP4:
    imap = settings["imap"]
    host, port = imap["host"], int(imap.get("port", 993))
    if imap.get("use_ssl", True):
        conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(host, port)
    else:
        conn = imaplib.IMAP4(host, port)
    conn.login(account["username"], account_password(account))
    typ, _ = conn.select(imap.get("mailbox", "INBOX"), readonly=True)  # EXAMINE
    if typ != "OK":
        raise FetchError(f"could not open mailbox read-only for account {account['id']!r}")
    return conn


def _extract_rfc822(fetch_response: Any) -> bytes | None:
    for item in fetch_response or []:
        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return None


def fetch_account(settings: dict[str, Any], account: dict[str, Any]) -> list[Path]:
    p = paths(settings, settings["__settings_path__"])
    corpus_dir, audit_log = p["corpus_dir"], p["audit_log"]
    since_days = int(settings.get("fetch", {}).get("since_days", 30))
    max_messages = int(settings.get("fetch", {}).get("max_messages", 200))

    conn = None
    written: list[Path] = []
    try:
        conn = _connect(settings, account)
        typ, data = conn.uid("SEARCH", None, "SINCE", _imap_date(since_days))
        if typ != "OK":
            raise FetchError(f"SEARCH failed for account {account['id']!r}")
        uids = (data[0] or b"").split()
        uids = uids[-max_messages:]  # most recent N

        for uid in uids:
            typ, resp = conn.uid("FETCH", uid, _FETCH_ITEM)
            if typ != "OK":
                continue
            raw = _extract_rfc822(resp)
            if not raw:
                continue
            dest = corpus_dir / safe_filename(canonical_id_from_raw(raw))
            if dest.exists():
                written.append(dest)
                continue
            dest.write_bytes(raw)
            written.append(dest)
            audit.log(audit_log, "message_cached", account["id"], {"file": dest.name})
        return written
    except imaplib.IMAP4.error as exc:
        # Do not interpolate the exception verbatim into user output beyond the type — it can echo
        # server text; keep it terse and account-scoped.
        raise FetchError(f"IMAP error for account {account['id']!r}: {type(exc).__name__}") from exc
    finally:
        if conn is not None:
            try:
                conn.logout()
            except Exception:
                pass


def fetch_all(settings: dict[str, Any]) -> dict[str, int]:
    """Fetch every configured account. Returns {account_id: messages_cached}. Per-account failures
    are audited and re-raised so the CLI can report which account broke."""
    p = paths(settings, settings["__settings_path__"])
    audit_log = p["audit_log"]
    results: dict[str, int] = {}
    accounts = settings["imap"].get("accounts", [])
    audit.log(audit_log, "fetch_started", "all", {"accounts": len(accounts)})
    for account in accounts:
        started = time.monotonic()
        files = fetch_account(settings, account)
        results[account["id"]] = len(files)
        audit.log(
            audit_log,
            "fetch_done",
            account["id"],
            {"messages": len(files), "elapsed_s": round(time.monotonic() - started, 2)},
        )
    return results
