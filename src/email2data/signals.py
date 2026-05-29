"""Tier-0 deterministic signals — Phase 2 (SCAFFOLD: types defined, bodies stubbed).

Header-cheap facts that need no LLM and feed the cascade. Two were validated on 154 real emails:
direction (sender domain vs ours) and bulk (List-Unsubscribe) — the latter is the most reliable
deprioritization lever we have. ``is_forward``/``original_*`` come from forwarding.py and exist so an
internal forward of a client order is attributed to the CLIENT, not to "internal" (VISION tenet 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from email.message import Message

OUR_DOMAIN = "lindoservico.pt"


@dataclass
class Signals:
    sender_domain: str
    direction: str           # one of schema.DIRECTION: inbound | internal | outbound
    is_bulk: bool            # List-Unsubscribe / List-Id / Precedence: bulk|list
    is_forward: bool         # body/subject wraps a forwarded or quoted external original
    original_from: str | None = None     # external sender mined from the forwarded original
    original_subject: str | None = None


def header_signals(msg: Message) -> Signals:
    """CONTRACT: derive sender_domain, direction (internal if sender domain == OUR_DOMAIN else
    inbound), and is_bulk (any of List-Unsubscribe / List-Id / Precedence in {bulk,list}). Coerce all
    header reads to str (real headers can be email.header.Header). Forward fields are filled by
    ``enrich_with_forward``."""
    raise NotImplementedError("Phase 2")


def enrich_with_forward(signals: Signals, body_text: str) -> Signals:
    """CONTRACT: if the body is an internal forward/reply wrapping an external original (see
    forwarding.extract_original), set is_forward and original_from/original_subject so the cascade can
    classify by the ORIGINAL counterparty. Returns a new/updated Signals."""
    raise NotImplementedError("Phase 2")
