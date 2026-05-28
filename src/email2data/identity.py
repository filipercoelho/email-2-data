"""Single canonical message identity, used by BOTH fetch (filenames) and envelope (result ids).

Red-team B2: three divergent id derivations would make the eval join silently drop rows. Everything
goes through ``canonical_id_from_raw`` so the value in ``corpus/`` filenames, ``results.jsonl``, and
``labels.csv`` is always the same string for the same email.
"""

from __future__ import annotations

import email
import hashlib


def canonical_id(message_id: str | None, raw: bytes) -> str:
    """Stable id for one email.

    Prefer the normalized RFC822 Message-ID (``mid:...``); fall back to a content hash
    (``sha256:...``) when the header is absent. Normalization (strip angle brackets/whitespace,
    lowercase) makes the id robust to how a human re-types it into labels.csv.
    """
    if message_id:
        norm = message_id.strip().strip("<>").strip().lower()
        if norm:
            return "mid:" + norm
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def canonical_id_from_raw(raw: bytes) -> str:
    """Parse the Message-ID out of raw bytes and return the canonical id."""
    msg = email.message_from_bytes(raw)
    return canonical_id(msg.get("Message-ID"), raw)


def safe_filename(canonical: str) -> str:
    """Filesystem-safe .eml name derived from the canonical id.

    The canonical id can contain ``<>@/`` etc.; hash it so the filename is always a flat hex string.
    Deterministic, so re-fetching the same email maps to the same file (dedupe).
    """
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32] + ".eml"
