"""Decode RFC822 header values to text — RFC 2047 encoded-words *and* raw 8-bit headers.

Python's default parser silently destroys the second kind. ``email.message_from_bytes`` reads the
message as ASCII+``surrogateescape``, and then compat32 wraps any header that carries non-ASCII
bytes in ``Header(charset="unknown-8bit")`` — whose ``str()`` replaces **every byte** with U+FFFD.
A perfectly good UTF-8 subject reaches the caller as::

    Pedido de or��amento ��� constru����o

one U+FFFD per byte (``ç`` is 2 bytes, ``—`` is 3), unrecoverable — the sanitization happens inside
``msg.get()``, before any decoding code can run. Senders that emit raw 8-bit headers are out of
spec but real: 2 of the 553 messages in ``corpus/`` do it.

So we parse with :data:`RAW_HEADERS` — compat32 minus that one sanitization — which leaves the
bytes in place as surrogate escapes, and decode them here. Every header value that leaves this
module is real text: no surrogate may escape into a store, because sqlite3 and ``json.dump``
raise ``UnicodeEncodeError`` on one. See ADR-043.
"""

from __future__ import annotations

from email import message_from_bytes
from email.errors import HeaderParseError
from email.header import decode_header
from email.message import Message
from email.policy import Compat32
from typing import Any

# Tried in order for bytes with no usable charset label of their own. UTF-8 first (what everything
# modern emits), then cp1252 (Outlook's smart quotes and dashes; a superset of latin-1), then
# latin-1, which cannot fail on any byte and so closes the ladder.
_FALLBACK_CHARSETS = ("utf-8", "cp1252", "latin-1")


class _RawHeaderPolicy(Compat32):
    """compat32, minus the header sanitization that replaces raw 8-bit bytes with U+FFFD."""

    def header_fetch_parse(self, name: str, value: str) -> Any:
        return value


RAW_HEADERS = _RawHeaderPolicy()


def parse_message(raw: bytes) -> Message:
    """``message_from_bytes`` that keeps raw 8-bit header bytes decodable.

    Identical to the default parser in every other respect (same ``Message`` class, same
    ``walk()`` / ``get_payload(decode=True)`` / ``get_content_charset()`` behaviour) — only header
    *retrieval* differs, returning the raw surrogate-escaped value instead of a lossy ``Header``.
    """
    return message_from_bytes(raw, policy=RAW_HEADERS)


def _has_surrogates(text: str) -> bool:
    return any("\udc80" <= ch <= "\udcff" for ch in text)


def decode_bytes(data: bytes, charset: str | None) -> str:
    """Decode header bytes, trusting the declared charset but never raising on a bad one.

    A bogus label (``unknown-8bit``) or one that does not fit the bytes falls through the ladder
    instead of aborting the parse of an otherwise good email.
    """
    for enc in ([charset] if charset else []) + list(_FALLBACK_CHARSETS):
        try:
            return data.decode(enc)
        except (LookupError, UnicodeDecodeError, ValueError):
            continue
    return data.decode("utf-8", errors="replace")  # unreachable while latin-1 closes the ladder


def repair_8bit(text: str) -> str:
    """Turn surrogate-escaped raw header bytes back into text; leave real text untouched.

    RFC 5322 gives raw 8-bit headers no charset, so the bytes are read back out of the surrogates
    and put through the fallback ladder. Characters that are already decoded survive: they are
    re-encoded as UTF-8 and decoded straight back.
    """
    if not _has_surrogates(text):
        return text
    return decode_bytes(text.encode("utf-8", errors="surrogateescape"), None)


def decode_value(value: Any) -> str:
    """One header value -> text. Handles RFC 2047 encoded-words, raw 8-bit bytes, and both at once.

    Never raises: a malformed encoded-word degrades to the repaired raw text rather than losing
    the whole header (and with it the subject of a real client email).
    """
    if not value:
        return ""
    text = value if isinstance(value, str) else str(value)
    try:
        chunks = decode_header(text)
    except HeaderParseError:
        return repair_8bit(text).strip()
    parts: list[str] = []
    for chunk, charset in chunks:
        if isinstance(chunk, bytes):
            # A header that mixes an encoded-word with plain text hands the *plain* runs back as
            # raw-unicode-escape bytes (b"or\\udcc3\\udca7amento"), so decoding with that same
            # codec is the exact inverse — it keeps 8-bit bytes as surrogates for repair below,
            # where decoding as UTF-8 would turn them into literal backslash escapes.
            piece = (chunk.decode("raw-unicode-escape") if charset is None
                     else decode_bytes(chunk, charset))
        else:
            piece = chunk
        parts.append(repair_8bit(piece))
    return "".join(parts).strip()


def header_text(msg: Message, name: str) -> str:
    """Raw value of one header as text — 8-bit repaired, but *not* RFC 2047-decoded.

    For structured headers that must be parsed before their human-readable parts are decoded:
    address headers go through ``getaddresses`` first, and ``Message-ID``/``References`` are
    tokens that must never be encoded-word-decoded.
    """
    return repair_8bit(str(msg.get(name) or ""))
