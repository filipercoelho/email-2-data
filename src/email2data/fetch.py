"""M0 — read-only IMAP fetch into a local .eml corpus.

SAFETY (red-team B1): we open the mailbox read-only (EXAMINE) AND fetch only with ``BODY.PEEK[]``.
We never issue STORE/DELETE/EXPUNGE/APPEND/COPY and never fetch ``RFC822``/``BODY[]`` (those set
\\Seen). PEEK is the client-side guarantee; read-only select is the belt to that suspenders.

Multi-mailbox: the folder list is **discovered from the server** on every run (IMAP ``LIST``) and
unioned with whatever the account pins in ``mailboxes``.  One connection handles all folders.
Emails from non-INBOX folders get a synthetic ``X-Email2Data-Source: <folder>`` header prepended so
downstream signal detection can set ``direction = "outbound"`` for sent mail instead of relying on
the From domain alone.

Discovery exists because a hand-curated folder list is a snapshot, and people re-organise their mail
(ADR-049).  A folder created after the snapshot was never opened, so mail filed into it between two
polls vanished from the app entirely — observed, not theorised: the 2026-07-29 20:02 proposal in the
"Órfãos da Lua" thread was filed into ``INBOX.orcamentado`` (a folder absent from
settings.json) before the next poll, and no store downstream ever saw it.
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


class FetchReport(dict):
    """Result of a fetch run: per-account counts, plus the per-account failure detail.

    It **is** a ``{account_id: messages_cached}`` dict, so every pre-existing caller
    (``sum(counts.values())``, ``counts[acc]``, ``.items()``) keeps working unchanged. What it adds
    is the detail ``fetch_all`` already tracked internally and then threw away at the call site:

      * ``failures`` — ``{account_id: detail}`` for each account that raised, where ``detail`` is the
        server's own reply (credential-safe, see ``_imap_detail``). A failed account is still present
        in the counts with 0, so ``0`` never has to be read as "failed or just idle?".
      * ``total`` — messages cached across every attempted account.
      * ``ok`` — ids that completed without error.
    """

    def __init__(self, counts: dict[str, int] | None = None,
                 failures: dict[str, str] | None = None) -> None:
        super().__init__(counts or {})
        self.failures: dict[str, str] = dict(failures or {})

    @property
    def total(self) -> int:
        return sum(self.values())

    @property
    def ok(self) -> list[str]:
        return [a for a in self if a not in self.failures]


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


# Folders discovery refuses to open. Junk only, on purpose: Trash is NOT here, because deleted mail
# is still evidence a client wrote to us (non-negotiable #2 — never silently bin a client), and
# ``orcamentos/INBOX.Trash`` has been fetched since day one. Overridable via ``imap.exclude_mailboxes``.
# Matched against the LAST path segment, case-folded, so ``INBOX.spam`` and ``spam`` both hit.
_DEFAULT_EXCLUDE = ("spam", "junk", "junk e-mail", "junk email", "bulk mail", "lixo")

# RFC 6154 special-use attributes for the same thing, for a server that names the folder something
# else. ``\Noselect`` is a container (no messages), not a mailbox we can EXAMINE.
_JUNK_ATTRS = ("\\junk", "\\spam")
_NOSELECT = "\\noselect"

# (flags) "delim" name  — the name is bare, quoted, or (handled separately) a literal.
_LIST_RE = re.compile(r'^\((?P<flags>[^)]*)\)\s+(?:"(?:[^"\\]|\\.)*"|NIL)\s+(?P<name>.+)$')


def _as_text(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    return str(value or "")


def _flags_of(text: str) -> list[str]:
    """The attribute list at the head of a LIST response line.

    Accepts it either still parenthesised (the literal-name shape, where we only have the raw head)
    or already unwrapped by the regex. Returning ``[]`` for an unwrapped list would silently disable
    the ``\\Junk``/``\\Noselect`` checks — which is exactly what it did until the tests caught it.
    """
    start, end = text.find("("), text.find(")")
    if start >= 0 and end > start:
        return text[start + 1:end].split()
    return text.split()


def _unquote(name: str) -> str:
    name = name.strip()
    if len(name) >= 2 and name.startswith('"') and name.endswith('"'):
        return name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return name


def _parse_list_line(item: Any) -> tuple[list[str], str] | None:
    """One IMAP LIST response item -> ``(attributes, mailbox name)``, or None if unparseable.

    Handles both shapes imaplib hands back: a single byte string, and the ``(head, literal)`` tuple a
    server uses when the folder name arrives as a literal. Names stay in the server's own encoding
    (modified UTF-7) — that is exactly what EXAMINE wants back, and what settings.json already holds.
    """
    if isinstance(item, tuple) and len(item) >= 2:
        return _flags_of(_as_text(item[0])), _as_text(item[1]).strip()
    match = _LIST_RE.match(_as_text(item).strip())
    if not match:
        return None
    return _flags_of(match.group("flags")), _unquote(match.group("name"))


def _is_junk(name: str, attrs: list[str], patterns: tuple[str, ...] | list[str]) -> bool:
    if any(a.lower() in _JUNK_ATTRS for a in attrs):
        return True
    leaf = re.split(r"[./\\]", name)[-1].strip().lower()
    return any(leaf == str(p).strip().lower() for p in patterns)


def _discover_mailboxes(conn: imaplib.IMAP4, patterns: tuple[str, ...] | list[str]) -> list[str] | None:
    """Every selectable folder the server LISTs, minus junk. ``None`` when LIST is unavailable.

    ``None`` is distinct from ``[]`` on purpose: a server that answers "no folders" is a fact worth
    honouring, but a LIST we could not run must fall back to the pinned list rather than silently
    narrow the fetch to nothing. LIST is read-only — it opens no mailbox and touches no flag.
    """
    try:
        typ, data = conn.list()
    except Exception:  # noqa: BLE001 — server quirk; fall back to the pinned list
        return None
    if typ != "OK":
        return None
    found: list[str] = []
    for item in data or []:
        parsed = _parse_list_line(item)
        if parsed is None:
            continue
        attrs, name = parsed
        if not name or any(a.lower() == _NOSELECT for a in attrs):
            continue
        if _is_junk(name, attrs, patterns):
            continue
        if name not in found:
            found.append(name)
    return found


def _exclude_patterns(settings: dict[str, Any]) -> tuple[str, ...] | list[str]:
    configured = settings.get("imap", {}).get("exclude_mailboxes")
    return list(configured) if isinstance(configured, list) else _DEFAULT_EXCLUDE


def _account_mailboxes(settings: dict[str, Any], account: dict[str, Any], *,
                       discovered: list[str] | None = None) -> list[str]:
    """Ordered folders to fetch: everything the server LISTs, UNION whatever the account pins.

    Discovery is what keeps the fetch in step with how people actually file their mail; the pinned
    ``mailboxes`` list stays authoritative on top of it for two reasons. It is the **escape hatch** —
    an explicitly named folder is fetched even if it matches the junk filter — and it is the
    **fallback** when LIST fails, so a server quirk can never shrink the fetch below what was
    configured. INBOX sorts first so the mailbox that matters most is drained before any cap bites.
    """
    pinned = ([str(m) for m in account["mailboxes"]] if "mailboxes" in account
              else [settings["imap"].get("mailbox", "INBOX")])
    ordered: list[str] = []
    for name in list(discovered or []) + pinned:
        if name and name not in ordered:
            ordered.append(name)
    ordered.sort(key=lambda n: n.upper() != "INBOX")  # stable: INBOX first, rest keep their order
    return ordered


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
    scope_sink: list[str] | None = None,
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
        # Identity is hashed from the ORIGINAL bytes (before the source header is injected): for the
        # rare Message-ID-less email the fallback id is sha256(raw), so prepending X-Email2Data-Source
        # would otherwise make the INBOX and Sent copies hash differently — two files, and the Sent
        # override never fires. Hashing pre-injection keeps ONE file per email regardless of folder/
        # order; for Message-ID-bearing mail (the norm) the id is unaffected either way.
        canonical = canonical_id_from_raw(raw)
        dest = corpus_dir / safe_filename(canonical)
        # ADR-038 tier-1 attribution: report every message SEEN here, not only newly-written ones.
        # A message already cached via another account must still gain a row for this mailbox, or a
        # thread shared between two inboxes would be visible to only one of their readers.
        if scope_sink is not None:
            scope_sink.append(canonical)
        if inject:
            raw = _source_header(mailbox) + raw  # but CACHE the copy with the source header for direction
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
    account_id = account["id"]
    # ADR-038: the scope token is the ADDRESS, not the account id -- margarida.reis@ and friends are
    # real inboxes with no fetch account, and keying on the address grants them uniformly.
    account_address = str(account.get("username") or "").strip().lower()

    conn = None
    written: list[Path] = []
    try:
        conn = _connect(settings, account)
        # ADR-049: ask the server what folders exist rather than trusting a snapshot in settings.json.
        # Audited either way, because "which folders did we even look in?" is the first question when
        # a message is missing — and the answer used to be unrecorded.
        discovered = _discover_mailboxes(conn, _exclude_patterns(settings))
        if discovered is None:
            audit.log(audit_log, "mailbox_discovery_failed", account_id,
                      {"reason": "list_unavailable", "falling_back_to_pinned": True})
        mailboxes = _account_mailboxes(settings, account, discovered=discovered)
        audit.log(audit_log, "mailboxes_selected", account_id,
                  {"discovered": len(discovered) if discovered is not None else None,
                   "pinned": len(account.get("mailboxes") or []), "fetching": len(mailboxes)})
        for mailbox in mailboxes:
            try:
                cursor = sync.get_cursor(account_id, mailbox) if sync is not None else None
                seen_ids: list[str] = []
                paths_w, uidvalidity, max_uid = _fetch_mailbox(
                    conn, mailbox, corpus_dir, audit_log, account_id, since_days, max_messages,
                    cursor=cursor, full=full, scope_sink=seen_ids)
                written.extend(paths_w)
                # ADR-038 tier-1 (FACT): the account we authenticated as IS where this mail landed --
                # the strongest evidence available, and the only tier for mail whose delivery headers
                # the server never wrote. Best-effort by design: attribution must never be able to
                # fail a read-only fetch, so a store error is audited and swallowed.
                if sync is not None and seen_ids and account_address:
                    try:
                        for mid in dict.fromkeys(seen_ids):
                            sync.set_message_scopes(mid, [account_address], "fetch")
                    except Exception as exc:  # noqa: BLE001 -- never fail a fetch over attribution
                        audit.log(audit_log, "scope_record_failed", account_id,
                                  {"mailbox": mailbox, "error": type(exc).__name__})
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


def fetch_all(settings: dict[str, Any], *, sync: Any | None = None, full: bool = False,
              account_ids: list[str] | None = None) -> FetchReport:
    """Fetch every configured account incrementally. Returns a ``FetchReport`` — a
    ``{account_id: messages_cached}`` dict (0 for a failed account) that also carries ``.failures``.

    Opens its own ``sync.SyncStore`` (``out/sync.db``) when ``sync`` is not supplied, so the
    "since last retrieve" watermark works out of the box. ``full=True`` re-bootstraps every mailbox.

    ``account_ids`` narrows the run to those accounts (a targeted force-sync). It filters HERE, on
    purpose: the isolation and total-outage logic below lives in this function, so a caller that
    wants one account must still come through it rather than reach for ``fetch_account`` directly.
    An id that is not configured is a caller bug, not an empty run, so it raises instead of quietly
    fetching nothing.

    Per-account isolation (red-team: a bad/expired credential must never starve the OTHER accounts):
    a ``FetchError`` from one account is AUDITED (``fetch_account_failed``) and SKIPPED so the loop
    keeps going and every healthy account still syncs and advances its watermark. Only when EVERY
    attempted account fails is the error re-raised — a total outage surfaces loudly to the CLI
    instead of a misleading "0 emails"."""
    p = paths(settings, settings["__settings_path__"])
    audit_log = p["audit_log"]
    results: dict[str, int] = {}
    accounts = settings["imap"].get("accounts", [])
    if account_ids is not None:
        wanted = {str(a) for a in account_ids}
        unknown = wanted - {a["id"] for a in accounts}
        if unknown:
            raise FetchError("unknown account_ids: " + ", ".join(sorted(unknown)))
        accounts = [a for a in accounts if a["id"] in wanted]
    audit.log(audit_log, "fetch_started", "all",
              {"accounts": len(accounts), "full": full,
               "targeted": sorted(a["id"] for a in accounts) if account_ids is not None else None})

    owns_sync = sync is None
    if owns_sync:
        from .sync import SyncStore
        sync = SyncStore(p["out_dir"] / "sync.db").connect()
    failures: dict[str, str] = {}
    try:
        for account in accounts:
            started = time.monotonic()
            try:
                files = fetch_account(settings, account, sync=sync, full=full)
            except FetchError as exc:
                # Audited + skipped: the other accounts still sync (their watermarks already
                # committed per-mailbox). Detail is the credential-safe server reply, never the secret.
                results[account["id"]] = 0
                failures[account["id"]] = _imap_detail(exc)
                audit.log(audit_log, "fetch_account_failed", account["id"],
                          {"error": _imap_detail(exc)})
                continue
            results[account["id"]] = len(files)
            audit.log(
                audit_log,
                "fetch_done",
                account["id"],
                {"messages": len(files), "elapsed_s": round(time.monotonic() - started, 2)},
            )
        if failures and len(failures) == len(accounts):
            raise FetchError("all accounts failed: "
                             + "; ".join(f"{a} ({d})" for a, d in failures.items()))
    finally:
        if owns_sync and sync is not None:
            sync.close()
    return FetchReport(results, failures=failures)
