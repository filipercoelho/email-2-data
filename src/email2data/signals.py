"""Tier-0 deterministic signals — Phase 2 (lean v1).

Header-cheap facts that need no LLM. Two roles:
  * `is_bulk`/`is_automated` — the ONLY thing allowed to bin offline (header-only IGNORE).
  * `direction`, `is_forward`, `sender_domain` — FEATURES attached to the LLM call, never verdicts.

Per the red-teamed plan we do NOT parse forwarded banners here (deferred); we only FLAG a likely
forward so the cascade escalates it to the body-reading LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from email.message import Message
from email.utils import getaddresses, parseaddr

OUR_DOMAIN = "lindoservico.pt"

# Folder names that indicate "we sent this" — matched case-insensitively.
_SENT_FOLDER_RE = re.compile(r"\b(sent|enviados?)\b", re.I)


def is_sent_folder(name: str) -> bool:
    """True if an IMAP folder name denotes a Sent/Enviados folder. The ONE definition of "we sent
    this" — used by direction classification here and by fetch's dedup (a Sent copy must win over a
    non-Sent one so a message present in both INBOX and Sent is classified outbound either way)."""
    return bool(name and _SENT_FOLDER_RE.search(name))

# Header signals for bulk (mass mail) vs automated (auto-replies/notifications). RFC 2369 / 3834.
_NO_REPLY_RE = re.compile(r"\b(no[-_.]?reply|do[-_.]?not[-_.]?reply|mailer-daemon|postmaster)\b", re.I)
# Forward/quote banners across clients + PT (detection only — we do not parse the block).
_FORWARD_MARKERS = (
    "---------- forwarded message",
    "begin forwarded message",
    "-----original message-----",
    "-----mensagem original-----",
    "mensagem reencaminhada",
    "mensagem encaminhada",
)


@dataclass
class Signals:
    sender_domain: str
    direction: str            # "inbound" | "internal" (our domain→our domain) | "outbound" (Sent folder)
    source_mailbox: str       # IMAP folder this came from; "" for INBOX / unknown
    is_bulk: bool             # mass/marketing mail (List-*, Feedback-ID, Precedence bulk/list)
    is_automated: bool        # auto-reply/notification (Auto-Submitted, X-Auto-Response-Suppress, no-reply)
    is_forward: bool          # body/subject looks like a forward/quote of another message
    bulk_evidence: str = ""   # which header tripped it (for the evidence trail)

    @property
    def ignorable_offline(self) -> bool:
        """Only true mass/marketing bulk (List-*/Feedback-ID/Precedence) may be binned offline.
        Automated/transactional mail (Auto-Submitted, no-reply invoices, system notices) is NOT
        binned here — it's a feature for the LLM. Over-binning automated supplier invoices as BULK
        was a measured Tier-0 precision bug; this is the fix."""
        return self.is_bulk


def _h(msg: Message, name: str) -> str:
    """Header value coerced to str (real headers can be email.header.Header)."""
    return str(msg.get(name) or "").strip()


def _has_external_recipient(msg: Message) -> bool:
    """True when any To:/Cc: address belongs to a domain outside lindoservico.pt."""
    to_cc = ", ".join(filter(None, [_h(msg, "To"), _h(msg, "Cc")]))
    for _, addr in getaddresses([to_cc]):
        if "@" not in addr:
            continue
        dom = addr.rsplit("@", 1)[-1].lower()
        if dom and dom != OUR_DOMAIN and not dom.endswith("." + OUR_DOMAIN):
            return True
    return False


def header_signals(msg: Message) -> Signals:
    source_mailbox = _h(msg, "X-Email2Data-Source")
    _, frm = parseaddr(_h(msg, "From"))
    domain = (frm.rsplit("@", 1)[-1] if "@" in frm else "").lower()
    if is_sent_folder(source_mailbox):
        direction = "outbound"
    elif domain == OUR_DOMAIN or domain.endswith("." + OUR_DOMAIN):
        # A reply FROM a colleague TO an external client (e.g. CC'd to orcamentos) is outbound —
        # it should flip the response clock. A pure colleague-to-colleague forward stays internal.
        direction = "outbound" if _has_external_recipient(msg) else "internal"
    else:
        direction = "inbound"

    bulk_hit = ""
    if _h(msg, "List-Unsubscribe") or _h(msg, "List-Id"):
        bulk_hit = "List-*"
    elif _h(msg, "Feedback-ID"):
        bulk_hit = "Feedback-ID"
    elif _h(msg, "Precedence").lower() in ("bulk", "list", "junk"):
        bulk_hit = "Precedence"
    is_bulk = bool(bulk_hit)

    auto = (
        (_h(msg, "Auto-Submitted") and _h(msg, "Auto-Submitted").lower() != "no")
        or bool(_h(msg, "X-Auto-Response-Suppress"))
        or bool(_h(msg, "X-Autoreply"))
        or _h(msg, "Return-Path") == "<>"
        or bool(_NO_REPLY_RE.search(frm))
    )
    return Signals(
        sender_domain=domain,
        direction=direction,
        source_mailbox=source_mailbox,
        is_bulk=is_bulk,
        is_automated=bool(auto),
        is_forward=False,
        bulk_evidence=bulk_hit or ("automated" if auto else ""),
    )


def detect_forward(subject: str, body_text: str) -> bool:
    """True if the message looks like a forward/quote of another message (detection only)."""
    hay = f"{subject}\n{body_text[:4000]}".lower()
    return any(m in hay for m in _FORWARD_MARKERS)


def enrich(signals: Signals, subject: str, body_text: str) -> Signals:
    signals.is_forward = detect_forward(subject, body_text)
    return signals


def facts_block(signals: Signals, gazetteer_hint: str | None,
                recipient_domains: list[str] | None = None) -> str:
    """Compact, human-readable facts to attach to the LLM prompt (never a verdict).

    ``recipient_domains`` is passed ONLY for outbound emails (direction=outbound): it tells the model
    who Lindo is writing TO so it can determine counterparty from the recipient, not the @lindoservico.pt
    sender. For inbound/internal the To: is always @lindoservico.pt and provides no information.
    """
    lines = [
        f"sender_domain={signals.sender_domain or '(unknown)'}",
        f"direction={signals.direction}",
        f"automated={'yes' if signals.is_automated else 'no'}",
        f"looks_forwarded={'yes' if signals.is_forward else 'no'}",
    ]
    if recipient_domains:
        # For outbound: the counterparty is the RECIPIENT, not the @lindoservico.pt sender.
        lines.append(f"recipient_domains={', '.join(recipient_domains)} (use these to determine counterparty for outbound)")
    if gazetteer_hint:
        lines.append(f"known_counterparty_hint={gazetteer_hint} (PRIOR only — the body overrides)")
    return "; ".join(lines)
