"""Single canonical message identity, used by BOTH fetch (filenames) and envelope (result ids).

Red-team B2: three divergent id derivations would make the eval join silently drop rows. Everything
goes through ``canonical_id_from_raw`` so the value in ``corpus/`` filenames, ``results.jsonl``, and
``labels.csv`` is always the same string for the same email.
"""

from __future__ import annotations

import hashlib

from .headers import parse_message, repair_8bit


def canonical_id(message_id: str | None, raw: bytes) -> str:
    """Stable id for one email.

    Prefer the normalized RFC822 Message-ID (``mid:...``); fall back to a content hash
    (``sha256:...``) when the header is absent. Normalization (strip angle brackets/whitespace,
    lowercase) makes the id robust to how a human re-types it into labels.csv.

    ``message_id`` is coerced and 8-bit-repaired before normalizing: a Message-ID carrying raw
    non-ASCII bytes arrives from the default parser as an ``email.header.Header`` (no ``.strip()``
    -> AttributeError) and from :func:`headers.parse_message` as surrogate escapes (which no store
    can encode). Neither may take down a whole fetch over one out-of-spec header.
    """
    if message_id:
        norm = repair_8bit(str(message_id)).strip().strip("<>").strip().lower()
        if norm:
            return "mid:" + norm
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def canonical_id_from_raw(raw: bytes) -> str:
    """Parse the Message-ID out of raw bytes and return the canonical id.

    Uses the same parser as ``envelope.parse_eml`` so both derivations of the id see the same
    header bytes — the divergence this module exists to prevent."""
    msg = parse_message(raw)
    return canonical_id(msg.get("Message-ID"), raw)


def safe_filename(canonical: str) -> str:
    """Filesystem-safe .eml name derived from the canonical id.

    The canonical id can contain ``<>@/`` etc.; hash it so the filename is always a flat hex string.
    Deterministic, so re-fetching the same email maps to the same file (dedupe).
    """
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32] + ".eml"
