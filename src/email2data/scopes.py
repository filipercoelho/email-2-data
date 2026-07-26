"""Mail-account attribution — which of *our* inboxes a message reached.

Phase A of the multi-user work (ADR-038). Nothing here decides **policy** (who is granted which
inbox); it only answers the factual question "which of our mailboxes did this message land in?",
durably, so a later visibility layer has something real to filter on. Storage is the
``message_scope`` table in ``sync.db`` (see :mod:`email2data.sync`).

Three evidence tiers, strongest first — the PROFILE.md FACT / INFERENCE / UNKNOWN rule applied to
this axis:

  1. ``fetch``        FACT       recorded live by :mod:`email2data.fetch` — the account we
                                 authenticated as when the message was cached. Cannot be wrong, and
                                 is the only tier available for mail whose delivery headers the
                                 receiving server never wrote.
  2. ``header``       FACT       the receiving server's own ``Envelope-to`` / ``Delivered-To`` /
                                 ``X-Original-To`` / ``X-Rcpt-To``. Says where the message *landed*,
                                 unlike ``To``/``Cc`` which say where the sender *aimed* it.
  3. ``participant``  INFERENCE  one of our addresses in ``From``/``To``/``Cc``/``Reply-To``, used
                                 only when no delivery header survived. Weaker evidence — but for
                                 *visibility* it is close to the right semantic anyway: you were a
                                 party to the mail.

A message that yields nothing from all three tiers is **UNKNOWN**. It is never silently dropped: it
folds to :data:`SCOPE_UNATTRIBUTED`, which the visibility layer shows to admins. That is the
standing "never silently bin a client" non-negotiable applied here — a filter that hides mail nobody
is watching loses revenue exactly like a false IGNORE does.

Measured on the 2026-07-25 corpus (550 cached messages): 448 ``header``, 102 ``participant``,
**0 unknown**. The unattributed bucket exists for future mail, not because today's corpus needs it.
"""

from __future__ import annotations

import sqlite3
from email import message_from_bytes
from pathlib import Path
from email.utils import getaddresses
from typing import Any, Iterable

from .config import paths
from .identity import canonical_id_from_raw
from .signals import OUR_DOMAIN

# The scope token used when no tier could attribute a message. Deliberately a real, grantable token
# rather than absence/NULL, so the visibility layer can GRANT it (to an admin, or to a delegate)
# instead of special-casing "this message has no rows" at every call site.
SCOPE_UNATTRIBUTED = "sem-atribuicao"

# Written by the RECEIVING server, so they record where the message actually landed.
DELIVERY_HEADERS = ("Envelope-to", "Delivered-To", "X-Original-To", "X-Rcpt-To")

# Fallback tier. ``From``/``Reply-To`` are included because Sent folders are fetched too, so our own
# outbound mail is attributable to the sending mailbox.
PARTICIPANT_HEADERS = ("From", "To", "Cc", "Reply-To")


def _addresses(values: Iterable[str | None]) -> list[str]:
    """Lowercased, de-duplicated addresses parsed from raw header values, first-seen order kept."""
    out: list[str] = []
    for _, addr in getaddresses([v for v in values if v]):
        a = (addr or "").strip().lower()
        if a and "@" in a and a not in out:
            out.append(a)
    return out


def is_internal(address: str, our_domain: str = OUR_DOMAIN) -> bool:
    """True when ``address`` is one of ours — the same domain test the rest of the app uses."""
    domain = address.rsplit("@", 1)[-1].lower() if "@" in address else ""
    return bool(domain) and (domain == our_domain or domain.endswith("." + our_domain))


def derive(raw: bytes, *, our_domain: str = OUR_DOMAIN) -> tuple[list[str], str]:
    """``(addresses, source)`` for one raw message — the tier-2 / tier-3 derivation.

    Returns ``([], "")`` when neither tier finds one of our own mailboxes; the caller folds that to
    :data:`SCOPE_UNATTRIBUTED`. Only *our* addresses are ever returned — a client's address is not a
    scope, it is a counterparty.

    Never raises. A truncated or malformed MIME blob degrades to UNKNOWN rather than aborting a
    whole backfill run over one bad cached file.
    """
    try:
        msg = message_from_bytes(raw)
    except Exception:  # noqa: BLE001 — one corrupt cached file must not stop attribution
        return [], ""
    for headers, source in ((DELIVERY_HEADERS, "header"), (PARTICIPANT_HEADERS, "participant")):
        found: list[str] = []
        for header in headers:
            try:
                values = msg.get_all(header) or []
            except Exception:  # noqa: BLE001 — defensive: broken header table
                values = []
            found.extend(values)
        ours = [a for a in _addresses(found) if is_internal(a, our_domain)]
        if ours:
            return ours, source
    return [], ""


def backfill(
    settings: dict[str, Any],
    *,
    sync: Any,
    our_domain: str = OUR_DOMAIN,
) -> dict[str, Any]:
    """Attribute every cached ``corpus/*.eml`` from its headers. Idempotent and re-runnable.

    Existing rows are only ever **upgraded** (see ``SyncStore.set_message_scopes``), so re-running
    this after a live fetch can never downgrade a tier-1 ``fetch`` row to a derived one. Running it
    twice in a row writes zero rows the second time.

    Returns per-tier message counts plus ``rows`` (scope rows actually written) and ``by_address``.
    """
    p = paths(settings, settings["__settings_path__"])
    corpus_dir = Path(p["corpus_dir"])
    out: dict[str, Any] = {
        "messages": 0, "header": 0, "participant": 0, "unattributed": 0,
        "rows": 0, "unreadable": 0, "by_address": {},
    }
    if not corpus_dir.is_dir():
        return out
    for eml in sorted(corpus_dir.glob("*.eml")):
        try:
            raw = eml.read_bytes()
        except OSError:
            out["unreadable"] += 1
            continue
        out["messages"] += 1
        try:
            message_id = canonical_id_from_raw(raw)
        except Exception:  # noqa: BLE001 — unparseable: cannot key it, so cannot attribute it
            out["unattributed"] += 1
            continue
        addresses, source = derive(raw, our_domain=our_domain)
        if not addresses:
            out["unattributed"] += 1
            continue
        out[source] += 1
        out["rows"] += sync.set_message_scopes(message_id, addresses, source)
        for a in addresses:
            out["by_address"][a] = out["by_address"].get(a, 0) + 1
    return out


def thread_scopes(sync: Any, crm_db: str | Path) -> dict[str, set[str]]:
    """``{thread_root: {address, ...}}`` — the **union** of every member message's scopes.

    Union, never intersection: a thread that reached both ``orcamentos@`` and ``luis.coelho@`` must
    stay visible to either reader. A thread holding even one unattributed message additionally
    carries :data:`SCOPE_UNATTRIBUTED`, so it still surfaces for an admin. The union can therefore
    only ever *widen* visibility — the safe direction, and the one the "never silently bin"
    non-negotiable demands.

    Reads ``crm.db`` (regenerable) for the ``message_id -> thread_root`` mapping. A missing crm.db
    yields ``{}`` rather than raising — the caller renders an unscoped page, it does not crash.
    """
    crm_path = Path(crm_db)
    if not crm_path.exists():
        return {}
    per_message = sync.all_message_scopes()
    conn = sqlite3.connect(f"file:{crm_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT message_id, thread_root FROM interactions"
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    out: dict[str, set[str]] = {}
    for message_id, thread_root in rows:
        root = thread_root or message_id or ""
        if not root:
            continue
        bucket = out.setdefault(root, set())
        scopes_for_message = per_message.get(message_id) or {}
        if scopes_for_message:
            bucket.update(scopes_for_message)
        else:
            bucket.add(SCOPE_UNATTRIBUTED)
    return out


def visible(thread_scope: Iterable[str], granted: Iterable[str], *, is_admin: bool = False) -> bool:
    """Whether a thread with ``thread_scope`` may be shown to someone granted ``granted``.

    Admins see everything, including :data:`SCOPE_UNATTRIBUTED` — that is what makes the
    unattributed bucket *watched* rather than merely not-hidden. For everyone else this is a plain
    set intersection, so a grant can only ever add visibility.

    A thread with an empty scope set (possible only if crm.db names a message we have never cached)
    is treated as unattributed rather than as visible-to-all — fail closed for non-admins, and still
    reachable by an admin.
    """
    if is_admin:
        return True
    scope = set(thread_scope) or {SCOPE_UNATTRIBUTED}
    return bool(scope & set(granted))
