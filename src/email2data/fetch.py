"""M0 — read-only IMAP fetch into a local .eml corpus.

SAFETY (red-team B1): we open the mailbox read-only (EXAMINE) AND fetch only with ``BODY.PEEK[]``.
We never issue STORE/DELETE/EXPUNGE/APPEND/COPY and never fetch ``RFC822``/``BODY[]`` (those set
\\Seen). PEEK is the client-side guarantee; read-only select is the belt to that suspenders.

Multi-mailbox: each account may list ``mailboxes`` (e.g. ``["INBOX", "Enviados"]``).  One
connection handles all folders.  Emails from non-INBOX folders get a synthetic
``X-Email2Data-Source: <folder>`` header prepended so downstream signal detection can set
``direction = "outbound"`` for sent mail instead of relying on the From domain alone.
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
from .signals import is_sent_folder

# Forbidden anywhere in this module — asserted by a test. If you need one of these, you are no longer
# building a read-only system; stop and rethink.
_FORBIDDEN_IMAP = ("STORE", "EXPUNGE", "DELETE", "APPEND", "COPY", "RFC822", "BODY[]")

_FETCH_ITEM = "(BODY.PEEK[])"
_BODY_RE = re.compile(rb"BODY\[\]", re.IGNORECASE)


class FetchError(Exception):
    """An account failed to fetch. Message names the account + the server's response, never the
    credentials (imaplib's error carries the server's tagged NO/BAD reply, e.g.
    ``[AUTHENTICATIONFAILED] Authentication failed.`` — the password is never echoed)."""


def _imap_detail(exc: Exception) -> str:
    """Human-readable IMAP error detail (server response), safe to surface — no credentials in it."""
    parts = []
    for a in getattr(exc, "args", ()) or ():
        parts.append(a.decode("utf-8", "replace") if isinstance(a, (bytes, bytearray)) else str(a))
    detail = " ".join(p for p in parts if p).strip()
    return detail or type(exc).__name__


def _imap_date(days: int) -> str:
    since = datetime.now(timezone.utc) - timedelta(days=max(days, 0))
    return since.strftime("%d-%b-%Y")  # IMAP date format, e.g. 01-May-2026


def _connect(settings: dict[str, Any], account: dict[str, Any]) -> imaplib.IMAP4:
    """Open an authenticated IMAP connection — does NOT select a mailbox yet."""
    imap = settings["imap"]
    host, port = imap["host"], int(imap.get("port", 993))
    if imap.get("use_ssl", True):
        conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(host, port)
    else:
        conn = imaplib.IMAP4(host, port)
    conn.login(account["username"], account_password(account))
    return conn


def _account_mailboxes(settings: dict[str, Any], account: dict[str, Any]) -> list[str]:
    """Return the ordered list of IMAP folders to fetch for this account.

    Per-account ``mailboxes`` list takes precedence; falls back to the top-level
    ``imap.mailbox`` (default ``"INBOX"``).
    """
    if "mailboxes" in account:
        return list(account["mailboxes"])
    return [settings["imap"].get("mailbox", "INBOX")]


def _source_header(folder: str) -> bytes:
    """Synthetic header injected into emails fetched from non-INBOX folders."""
    return f"X-Email2Data-Source: {folder}\r\n".encode()


_SOURCE_HDR_RE = re.compile(rb"^X-Email2Data-Source:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


def _existing_is_sent(dest: Path) -> bool:
    """Whether the already-cached .eml carries a Sent ``X-Email2Data-Source`` header. The header is
    prepended at the very top, so a small read suffices. Used to decide if a new Sent-folder copy
    should override a non-Sent cached one."""
    try:
        head = dest.read_bytes()[:1024]
    except OSError:
        return False
    m = _SOURCE_HDR_RE.search(head)
    return bool(m) and is_sent_folder(m.group(1).decode("ascii", "ignore"))


def _quote_mailbox(name: str) -> str:
    """Quote a mailbox name for SELECT/EXAMINE. imaplib does not quote, so a folder whose name
    contains a space (e.g. ``INBOX.Pedidos orcamento``) is otherwise split into two arguments and
    the select fails with NONEXISTENT. Already-quoted names are passed through."""
    if name.startswith('"') and name.endswith('"'):
        return name
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _extract_rfc822(fetch_response: Any) -> bytes | None:
    for item in fetch_response or []:
        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return None


def _read_uidvalidity(conn: imaplib.IMAP4) -> int | None:
    """UIDVALIDITY epoch of the currently-selected mailbox, or None if the server omits it.

    A change in this value means the server renumbered UIDs — any stored ``last_uid`` is meaningless
    and we must re-bootstrap.
    """
    try:
        _typ, data = conn.response("UIDVALIDITY")
    except Exception:  # noqa: BLE001 — server quirk; treat as unknown
        return None
    for item in data or []:
        if item is None:
            continue
        if isinstance(item, (bytes, bytearray)):
            item = item.decode("ascii", "ignore")
        try:
            return int(item)
        except (TypeError, ValueError):
            return None
    return None


def _fetch_mailbox(
    conn: imaplib.IMAP4,
    mailbox: str,
    corpus_dir: Path,
    audit_log: Path,
    account_id: str,
    since_days: int,
    max_messages: int,
    *,
    cursor: tuple[int, int] | None = None,
    full: bool = False,
) -> tuple[list[Path], int | None, int]:
    """Fetch from one already-authenticated IMAP connection.

    Selects ``mailbox`` read-only, then searches one of two ways:
      * **incremental** — a usable cursor ``(uidvalidity, last_uid)`` whose epoch still matches and
        ``full`` is False: ``UID <last_uid+1>:*`` (only mail arrived since the last retrieve);
      * **bootstrap** — otherwise: ``SINCE <since_days>`` capped at ``max_messages`` (first run, epoch
        change, or a forced ``full`` rebuild).

    Fetches with BODY.PEEK[]. Non-INBOX folders get ``X-Email2Data-Source`` prepended. Already-cached
    messages (by message_id filename) are skipped silently — the belt to the cursor's suspenders.

    Returns ``(written_paths, uidvalidity, max_uid_seen)`` so the caller can persist the new cursor.
    ``max_uid_seen`` is 0 when nothing matched.
    """
    typ, _ = conn.select(_quote_mailbox(mailbox), readonly=True)  # EXAMINE
    if typ != "OK":
        audit.log(audit_log, "mailbox_skip", account_id, {"mailbox": mailbox, "reason": "select_failed"})
        return [], None, 0

    uidvalidity = _read_uidvalidity(conn)
    last_uid = 0
    incremental = (
        not full
        and cursor is not None
        and uidvalidity is not None
        and cursor[0] == uidvalidity
    )
    if incremental:
        last_uid = cursor[1]  # type: ignore[index]

    if incremental:
        typ, data = conn.uid("SEARCH", None, "UID", f"{last_uid + 1}:*")
    else:
        typ, data = conn.uid("SEARCH", None, "SINCE", _imap_date(since_days))
    if typ != "OK":
        raise FetchError(f"SEARCH failed for account {account_id!r} mailbox {mailbox!r}")
    uids = (data[0] or b"").split()

    if incremental:
        # IMAP ``N:*`` always echoes the highest message even when N exceeds it — filter so we never
        # re-pull the watermark message. Oldest-first + cap so a large backlog is drained across runs
        # (watermark advances only to what we actually fetched) instead of skipping old new mail.
        uids = [u for u in uids if int(u) > last_uid]
        uids = uids[:max_messages]
    else:
        uids = uids[-max_messages:]  # most recent N

    audit.log(audit_log, "fetch_mode", account_id,
              {"mailbox": mailbox, "mode": "incremental" if incremental else "bootstrap",
               "from_uid": last_uid if incremental else None, "candidates": len(uids)})

    inject = mailbox.upper() != "INBOX"
    is_sent = is_sent_folder(mailbox)
    written: list[Path] = []
    max_uid = last_uid
    for uid in uids:
        typ, resp = conn.uid("FETCH", uid, _FETCH_ITEM)
        if typ != "OK":
            continue
        raw = _extract_rfc822(resp)
        if not raw:
            continue
        try:
            max_uid = max(max_uid, int(uid))
        except (TypeError, ValueError):
            pass
        if inject:
            raw = _source_header(mailbox) + raw
        dest = corpus_dir / safe_filename(canonical_id_from_raw(raw))
        if dest.exists():
            # First-writer-wins — EXCEPT a Sent-folder copy must override a non-Sent cached one, so a
            # message that lives in BOTH an INBOX and a Sent folder is classified outbound regardless
            # of fetch order (the X-Email2Data-Source header drives direction downstream). Order-
            # independent: we check the cached file, not which mailbox happened to run first.
            if is_sent and not _existing_is_sent(dest):
                dest.write_bytes(raw)
                audit.log(audit_log, "message_source_upgraded", account_id,
                          {"file": dest.name, "mailbox": mailbox})
            written.append(dest)
            continue
        dest.write_bytes(raw)
        written.append(dest)
        audit.log(audit_log, "message_cached", account_id, {"file": dest.name, "mailbox": mailbox})
    return written, uidvalidity, max_uid


def fetch_account(settings: dict[str, Any], account: dict[str, Any], *,
                  sync: Any | None = None, full: bool = False) -> list[Path]:
    """Fetch one account incrementally. ``sync`` is a ``sync.SyncStore`` holding the per-mailbox UID
    watermark; when omitted, fetch is stateless (bootstrap every time). ``full`` forces a bootstrap and
    still advances the cursor."""
    p = paths(settings, settings["__settings_path__"])
    corpus_dir, audit_log = p["corpus_dir"], p["audit_log"]
    since_days = int(settings.get("fetch", {}).get("since_days", 30))
    max_messages = int(settings.get("fetch", {}).get("max_messages", 200))
    mailboxes = _account_mailboxes(settings, account)
    account_id = account["id"]

    conn = None
    written: list[Path] = []
    try:
        conn = _connect(settings, account)
        for mailbox in mailboxes:
            try:
                cursor = sync.get_cursor(account_id, mailbox) if sync is not None else None
                paths_w, uidvalidity, max_uid = _fetch_mailbox(
                    conn, mailbox, corpus_dir, audit_log, account_id, since_days, max_messages,
                    cursor=cursor, full=full)
                written.extend(paths_w)
                # Persist only when we actually saw mail AND know the epoch — an empty poll must not
                # clobber a good watermark, and a missing UIDVALIDITY can't anchor one.
                if sync is not None and uidvalidity is not None and max_uid > 0:
                    sync.set_cursor(account_id, mailbox, uidvalidity, max_uid)
            except FetchError:
                raise
            except imaplib.IMAP4.error as exc:
                raise FetchError(f"IMAP error for account {account_id!r} mailbox {mailbox!r}: "
                                 f"{_imap_detail(exc)}") from exc
        return written
    except imaplib.IMAP4.error as exc:
        raise FetchError(f"IMAP error for account {account_id!r}: {_imap_detail(exc)}") from exc
    finally:
        if conn is not None:
            try:
                conn.logout()
            except Exception:
                pass


def fetch_all(settings: dict[str, Any], *, sync: Any | None = None, full: bool = False) -> dict[str, int]:
    """Fetch every configured account incrementally. Returns {account_id: messages_cached}.

    Opens its own ``sync.SyncStore`` (``out/sync.db``) when ``sync`` is not supplied, so the
    "since last retrieve" watermark works out of the box. ``full=True`` re-bootstraps every mailbox.
    Per-account failures are audited and re-raised so the CLI can report which account broke."""
    p = paths(settings, settings["__settings_path__"])
    audit_log = p["audit_log"]
    results: dict[str, int] = {}
    accounts = settings["imap"].get("accounts", [])
    audit.log(audit_log, "fetch_started", "all", {"accounts": len(accounts), "full": full})

    owns_sync = sync is None
    if owns_sync:
        from .sync import SyncStore
        sync = SyncStore(p["out_dir"] / "sync.db").connect()
    try:
        for account in accounts:
            started = time.monotonic()
            files = fetch_account(settings, account, sync=sync, full=full)
            results[account["id"]] = len(files)
            audit.log(
                audit_log,
                "fetch_done",
                account["id"],
                {"messages": len(files), "elapsed_s": round(time.monotonic() - started, 2)},
            )
    finally:
        if owns_sync and sync is not None:
            sync.close()
    return results
