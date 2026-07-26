"""Parse a raw .eml into an EmailEnvelope dict. Pure function of bytes — no network, no I/O."""

from __future__ import annotations

import logging
import re
import struct
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime
from html import unescape
from typing import Any, Optional

from .headers import decode_value, header_text, parse_message
from .identity import canonical_id

logger = logging.getLogger(__name__)

MAX_BODY_CHARS = 20_000

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANKLINES_RE = re.compile(r"\n{3,}")


def _addr(value: str | None) -> dict[str, str]:
    pairs = getaddresses([value or ""])
    if not pairs:
        return {"name": "", "email": ""}
    name, email_addr = pairs[0]
    return {"name": decode_value(name), "email": email_addr.lower()}


def _addr_list(value: str | None) -> list[dict[str, str]]:
    return [
        {"name": decode_value(n), "email": e.lower()}
        for n, e in getaddresses([value or ""])
        if e
    ]


def _strip_html(html: str) -> str:
    text = _TAG_RE.sub(" ", html)
    text = unescape(text)
    text = _WS_RE.sub(" ", text)
    return _BLANKLINES_RE.sub("\n\n", text).strip()


def _part_text(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, ValueError):
        return payload.decode("utf-8", errors="replace")


def _extract_body(msg: Message) -> tuple[str, bool]:
    """Return (plain_text, has_html). Prefer text/plain; fall back to stripped text/html."""
    plain_chunks: list[str] = []
    html_chunks: list[str] = []
    has_html = False

    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        disp = str(part.get("Content-Disposition") or "").lower()
        if "attachment" in disp:
            continue
        if ctype == "text/plain":
            plain_chunks.append(_part_text(part))
        elif ctype == "text/html":
            has_html = True
            html_chunks.append(_part_text(part))

    if plain_chunks:
        body = "\n".join(c for c in plain_chunks if c).strip()
    elif html_chunks:
        body = _strip_html("\n".join(html_chunks))
    else:
        body = ""
    return body[:MAX_BODY_CHARS], has_html


def _attachments(msg: Message) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for part in msg.walk():
        disp = str(part.get("Content-Disposition") or "").lower()
        filename = part.get_filename()
        if "attachment" not in disp and not filename:
            continue
        payload = part.get_payload(decode=True)
        out.append(
            {
                "filename": decode_value(filename) if filename else None,
                "content_type": part.get_content_type(),
                "size_bytes": len(payload) if payload else 0,
            }
        )
    return out


def _date_iso(msg: Message) -> str | None:
    raw = header_text(msg, "Date")
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).isoformat()
    except (TypeError, ValueError):
        return None


def _references(value: str | None) -> list[str]:
    if not value:
        return []
    return re.findall(r"<[^>]+>", value)


def attachment_part(raw: bytes, index: int) -> tuple[str, str, bytes] | None:
    """Return (filename, content_type, payload_bytes) for the Nth attachment, in the SAME order as
    ``_attachments``. Bytes only — no parsing/extraction (we never read the contents). None if the
    index is out of range. Used to serve an attachment for view/download in the UI."""
    i = 0
    for part in parse_message(raw).walk():
        disp = str(part.get("Content-Disposition") or "").lower()
        filename = part.get_filename()
        if "attachment" not in disp and not filename:
            continue
        if i == index:
            payload = part.get_payload(decode=True) or b""
            name = decode_value(filename) if filename else f"anexo-{index}"
            return name, (part.get_content_type() or "application/octet-stream"), payload
        i += 1
    return None


# ── Embedded-message extraction ──────────────────────────────────────────────
#
# Outlook's text/plain rendering of a reply/forward chain inserts a header block for each prior
# message:
#
#   From: Name <email>          (or PT: De:)
#   Sent: date string           (or PT: Enviada: / Data:)
#   To: email                   (or PT: Para:)
#   Cc: email                   (optional)
#   Subject: text               (or PT: Assunto:)
#
#   [message body, terminated by the next such block or end-of-string]
#
# Gmail uses different markers ("On DATE, NAME <email> wrote:" / "Em DATA, NAME <email> escreveu:")
# but those only introduce a single ">" quoted block — we handle those via splitQuote in the UI.
# This extractor targets the Outlook block form which carries full From/To/Date metadata.

_OUTLOOK_HBLOCK_RE = re.compile(
    r"(?:^|\n)"                                         # start of line
    r"(?:From|De)\s*:\s*"                               # From: / De:
    r"[^\n]*?"                                          # optional display name (non-greedy)
    r"<?(?:<mailto:)?"                                  # optional < and <mailto: (Outlook artifact)
    r"([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})"  # email address
    r"[^\n]*\n"                                         # rest of line
    r"(?:[^\n]*\n){0,4}"                                # up to 4 more header lines
    r"(?:Sent|Enviada?|Data|Date)\s*:\s*([^\n]{4,50})\n"     # date line (required)
    r"(?:[^\n]*\n){0,3}",                               # optional To/Cc/Subject lines
    re.I | re.MULTILINE,
)


def _clean_outlook_email(raw: str) -> str:
    """Strip Outlook's '<mailto:email> email' artifacts to plain 'email'."""
    m = re.search(r"([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})", raw)
    return m.group(1) if m else raw.strip()


def extract_embedded_messages(body_text: str) -> list[dict[str, Any]]:
    """Parse Outlook inline header blocks from a forwarded/reply chain body.

    Returns a list of dicts (newest-last chronological order), each representing one embedded
    message that was NOT a separate IMAP message but is only available as quoted text:
        {from_email, to_emails, date_raw, subject, body, source: 'embedded'}

    Returns [] when no embedded blocks are found — callers should treat this as "no additional
    messages to surface beyond what the thread index already shows."

    Deduplication: if two blocks have the same (from_email, date_raw) they are collapsed to one
    (e.g. the same Yigit Bora email appears in every forward in the chain)."""
    body = body_text.replace("\r\n", "\n")
    blocks: list[tuple[int, int]] = []  # (match_start, match_end) of each header block
    for m in _OUTLOOK_HBLOCK_RE.finditer(body):
        blocks.append((m.start(), m.end()))
    if not blocks:
        return []

    # Each block's body runs from its end to the start of the next block (or end-of-string).
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for idx, (bstart, bend) in enumerate(blocks):
        next_start = blocks[idx + 1][0] if idx + 1 < len(blocks) else len(body)
        block_text = body[bstart:bend]
        msg_body = body[bend:next_start].strip()

        # Parse fields from the block header text
        from_m = re.search(
            r"(?:From|De)\s*:\s*(?:[^\n<]*<)?(?:<mailto:)?([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})",
            block_text, re.I)
        date_m = re.search(
            r"(?:Sent|Enviada?|Data|Date)\s*:\s*([^\n]{4,50})", block_text, re.I)
        to_m = re.findall(
            r"(?:To|Para|Cc)\s*:([^\n]+)", block_text, re.I)
        subj_m = re.search(
            r"(?:Subject|Assunto)\s*:\s*([^\n]+)", block_text, re.I)

        from_email = _clean_outlook_email(from_m.group(1)) if from_m else ""
        date_raw = date_m.group(1).strip() if date_m else ""
        to_emails = [e for line in to_m
                     for e in re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", line)]
        subject = subj_m.group(1).strip() if subj_m else ""

        key = (from_email.lower(), date_raw.lower()[:20])
        if key in seen or not from_email:
            continue
        seen.add(key)
        results.append({
            "from_email": from_email,
            "to_emails": to_emails,
            "date_raw": date_raw,
            "subject": subject,
            "body": msg_body,
            "source": "embedded",
        })
        # Recurse one level: a forwarded block's body may itself contain a deeper chain
        for nested in _extract_blocks(msg_body, seen):
            results.append(nested)

    # Oldest first: Outlook nests newest-first (the most recent forward is the outermost block)
    results.reverse()
    return results


def _extract_blocks(body: str, seen: set[tuple[str, str]]) -> list[dict[str, Any]]:
    """Inner recursive helper — shares the dedup `seen` set with the caller."""
    body = body.replace("\r\n", "\n")
    blocks: list[tuple[int, int]] = [(m.start(), m.end()) for m in _OUTLOOK_HBLOCK_RE.finditer(body)]
    results = []
    for idx, (bstart, bend) in enumerate(blocks):
        next_start = blocks[idx + 1][0] if idx + 1 < len(blocks) else len(body)
        block_text = body[bstart:bend]
        msg_body = body[bend:next_start].strip()
        from_m = re.search(
            r"(?:From|De)\s*:\s*[^\n]*?<?(?:<mailto:)?([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})",
            block_text, re.I)
        date_m = re.search(r"(?:Sent|Enviada?|Data|Date)\s*:\s*([^\n]{4,50})", block_text, re.I)
        to_lines = re.findall(r"(?:To|Para|Cc)\s*:([^\n]+)", block_text, re.I)
        subj_m = re.search(r"(?:Subject|Assunto)\s*:\s*([^\n]+)", block_text, re.I)
        from_email = _clean_outlook_email(from_m.group(1)) if from_m else ""
        date_raw = date_m.group(1).strip() if date_m else ""
        to_emails = [e for line in to_lines
                     for e in re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", line)]
        subject = subj_m.group(1).strip() if subj_m else ""
        key = (from_email.lower(), date_raw.lower()[:20])
        if key in seen or not from_email:
            continue
        seen.add(key)
        results.append({"from_email": from_email, "to_emails": to_emails,
                         "date_raw": date_raw, "subject": subject,
                         "body": msg_body, "source": "embedded"})
        results.extend(_extract_blocks(msg_body, seen))
    return results


# ── Email body cleaner ───────────────────────────────────────────────────────
#
# Removes CSS artifacts (Outlook HTML→text leakage), signature blocks, URL-only
# lines, image placeholders, and invisible characters — leaving only human-written
# text. Conservative: only removes things with high confidence. The raw body is
# always preserved alongside; this is a display aid, not a destructive transform.

# CSS: selector { ... } on a single line — targeted at Outlook/Office artifacts.
# Covers: v:*/o:*/w:* (VML), @font-face/@page, .MsoXxx / p.MsoXxx / span.EmailStyleXxx,
# a:link, div.WordSection, and the generic "selector { prop: val; }" pattern where the
# selector contains known MS-Office or CSS control characters (no PT/EN words before {).
_CSS_LINE = re.compile(
    r"^\s*"
    r"(?:"
        r"@[\w\-]+[^{]*\{[^}]*\}"                                   # @font-face, @page, @media
        r"|[vow]\s*\\?:\s*[*\w]+\s*\{[^}]*\}"                       # v:*, o:*, w:* VML
        r"|[.#]?[\w\-]*(?:Mso|mso|shape|Word|Email)[\w\-]*[^{]*\{[^}]*\}"  # MS-specific classes
        r"|(?:p|li|div|span|a|td|th)(?:\.[A-Za-z\d]+)?(?:\s*,\s*(?:p|li|div|span|a|td|th)(?:\.[A-Za-z\d]+)?)*\s*\{[^}]*\}"  # type selectors
        r"|a:(?:link|visited|hover|active)[^{]*\{[^}]*\}"           # link pseudo-classes
        r"|[.#][A-Za-z][\w\-]*\s*\{[^}]*\}"                         # .class or #id alone
    r")\s*$",
    re.I,
)
# Inline image references: <imageNNN.ext> (Outlook inline signature images)
_INLINE_IMG = re.compile(r"<image\d+\.[a-zA-Z]{2,4}>", re.I)
# URL-only line (possibly with angle brackets or leading space)
_URL_LINE = re.compile(r"^\s*<?https?://[^\s>]+>?\s*$")
# www-only line
_WWW_LINE = re.compile(r"^\s*<?www\.[^\s>]+>?\s*$")
# Phone-only line: digits, spaces, hyphens, parentheses, plus — no alpha chars
_PHONE_LINE = re.compile(r"^\s*\+?[\d\s()\-\.]{7,25}\s*$")
# Postal code / address line (PT: NNNN-NNN or NNNN NNN at start)
_POSTAL_LINE = re.compile(r"^\s*\d{4}[-\s]\d{3}\b")
# Invisible/BOM characters
_INVISIBLE = re.compile(r"[﻿​‌‍­]")
# Closing salutations — what comes after these (up to the next real content) is the signature
_CLOSING = re.compile(
    r"^\s*(?:"
    r"melhores\s+cumprimentos|com\s+os\s+melhores\s+cumprimentos|"
    r"cumprimentos|atenciosamente|"
    r"obrigad[ao]\s*[!.,]?|obrigad[ao]\s+por|"
    r"abraço[s]?|até\s+\w+|"
    r"best\s+regards|kind\s+regards|regards|"
    r"thank\s+you\s*[!.,]?|thanks\s*[!.,]?|cheers"
    r")\s*[!.,]?\s*$",
    re.I,
)
# "Enviado do meu iPhone/Android/…" mobile footers
_MOBILE_FOOTER = re.compile(r"^\s*enviado\s+do\s+meu\s+\w", re.I)


def _is_sig_element(line: str) -> bool:
    """True when a line is a known signature element (phone, address, URL, image, footer)."""
    s = line.strip()
    if not s:
        return True  # blank lines inside a signature zone are part of it
    # Remove any embedded URLs from the stripped value before checking the remaining text
    s_no_url = re.sub(r"\s*<?https?://\S+>?|<?www\.[^\s>]+>?", "", s).strip()
    if not s_no_url:
        return True  # line was purely URL(s)
    if _INLINE_IMG.match(s):
        return True
    if _URL_LINE.match(s) or _WWW_LINE.match(s):
        return True
    if _PHONE_LINE.match(s_no_url) and not re.search(r"[a-zA-Z]", s_no_url):
        return True
    if _POSTAL_LINE.match(s):
        return True
    if _MOBILE_FOOTER.match(s):
        return True
    # Street address: starts with a common PT street prefix, OR ends with a street number
    if re.match(r"^(?:rua|av(?:enida)?\.?|praça|al(?:ameda)?\.?|largo|travessa|estrada|r\.)\b",
                s, re.I):
        return True
    if re.match(r"^[^@.!?]{3,45},?\s*\d{1,5}\s*$", s) and len(s.split()) <= 7 and \
            not re.search(r"[€$%]|\b\d{3,}\b", s):
        # "Rua da Centeira, 7" — short line ending in a number, no price indicators
        return True
    # ALL-CAPS short name (person's name in signature) — min 6 chars to avoid acronyms
    if re.match(r"^[A-ZÁÀÃÂÉÊÍÓÔÕÚÇ][A-ZÁÀÃÂÉÊÍÓÔÕÚÇ\s]{5,39}$", s) and \
            1 <= len(s.split()) <= 4:
        return True
    # Short role/title line (≤5 words, no digits, no sentence punctuation) — sig zone only
    if re.match(r"^[A-ZÁa-záàãâéêíóôõúç][^.!?\d]{0,50}$", s) and len(s.split()) <= 5:
        return True
    return False


def clean_email_body(text: str) -> str:
    """Remove technical noise from a plaintext email body, keeping only human-written content.

    Removes: CSS style blocks (Outlook HTML→text artifacts), inline image placeholders,
    URL-only lines, signature blocks (triggered by closing salutations), mobile footers,
    and invisible/BOM characters. Collapses excessive blank lines.

    The original text is never modified in-place — callers store both for UI toggle."""
    if not text:
        return ""
    text = _INVISIBLE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Strip inline image refs from within any line (e.g. "See <image001.png> attached")
    text = _INLINE_IMG.sub("", text)

    lines = text.split("\n")
    out: list[str] = []
    i = 0
    in_sig = False  # True once we've passed a closing salutation

    while i < len(lines):
        line = lines[i]
        s = line.strip()

        # CSS line — skip entirely
        if _CSS_LINE.match(s):
            i += 1
            continue

        # Mobile footer — skip
        if _MOBILE_FOOTER.match(s):
            i += 1
            continue

        # URL-only / www-only line — skip
        if _URL_LINE.match(s) or _WWW_LINE.match(s):
            i += 1
            continue

        # Phone-only line — skip
        if _PHONE_LINE.match(s) and not re.search(r"[a-zA-Z]", s):
            i += 1
            continue

        # Postal address line — skip
        if _POSTAL_LINE.match(s):
            i += 1
            continue

        # Closing salutation: keep the closing itself, then enter signature zone
        if _CLOSING.match(s):
            # Look ahead: if ALL following non-blank lines until the next quoted header block
            # are signature elements, suppress the whole block (closing included). This handles
            # the common "Melhores cumprimentos\n\nSOFIA DIAS\n..." pattern.
            lookahead = i + 1
            while lookahead < len(lines) and _is_sig_element(lines[lookahead]):
                lookahead += 1
            # If we reached another message block or end-of-text, skip the whole closing+sig
            next_content = lines[lookahead].strip() if lookahead < len(lines) else ""
            is_quoted_hdr = bool(re.match(r"(?:From|De|Sent|Enviada?|To|Para|Subject|Assunto)\s*:", next_content, re.I))
            if lookahead >= len(lines) or is_quoted_hdr:
                i = lookahead  # jump past the entire closing+signature
                continue
            # Otherwise: real content follows the signature — keep the closing, enter sig zone
            out.append(line.rstrip())
            in_sig = True
            i += 1
            continue

        # Inside a signature zone: only emit non-sig-element lines
        if in_sig:
            if not s:
                # blank line: emit one blank (collapse multiples), but don't exit the zone
                if out and out[-1] != "":
                    out.append("")
                i += 1
                continue
            if _is_sig_element(line):
                i += 1
                continue
            # Non-signature content after the closing — exit signature zone, keep the line
            in_sig = False

        # Normal line: strip URL artifacts, then check if anything meaningful remains
        cleaned_line = re.sub(r"\s*<?https?://\S+>?|<?www\.[^\s>]+>?", "", line).rstrip()
        # Re-check filters on the URL-stripped line
        stripped_clean = cleaned_line.strip()
        if not stripped_clean:
            if out and out[-1] != "":
                out.append("")
            i += 1
            continue
        if _PHONE_LINE.match(stripped_clean) and not re.search(r"[a-zA-Z]", stripped_clean):
            i += 1
            continue
        if _POSTAL_LINE.match(stripped_clean):
            i += 1
            continue
        if cleaned_line or (out and out[-1] != ""):
            out.append(cleaned_line)
        i += 1

    # Collapse 3+ consecutive blank lines to 2
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(out))
    return result.strip()


# ── Outlook rewrites *every* inline image to image001.png, image002.gif, … — a signature logo and a
# photo of the client's product are indistinguishable by name alone, so the name only makes an image a
# *candidate* for dropping; :func:`_is_signature_image` decides. See that function for why.
_SIG_IMG = re.compile(r"^image\d{3,}\.(png|gif|jpe?g|bmp)$", re.I)


def _gif_frames(data: bytes) -> int:
    """Frame count of a GIF, counted from Graphic Control Extension headers — never decodes.

    Only used to keep the density test honest: an animated banner's bytes are spread over N frames, so
    whole-file bytes-per-pixel overstates its detail by a factor of N (real case: a 102-frame 500x234
    signature banner scoring 1.61 B/px — 0.016 per frame). Under-counting is the safe direction: it
    can only make an image look denser, i.e. more likely to be kept.
    """
    return max(1, data.count(b"\x00\x21\xf9\x04")) if data[:3] == b"GIF" else 1


def _is_signature_image(payload: bytes, size: Optional[tuple[int, int]], *,
                        min_density: float, max_aspect: float, keep_bytes: int) -> bool:
    """Is this ``imageNNN.ext`` attachment *provably* a signature logo/banner rather than job content?

    Outlook renames all inline images, so the name proves nothing. Filtering on bytes alone silently
    binned the single most informative artifact of a real lead (``p-0003`` "almofadas bordadas": a
    235x240 / 74 KB photo of the embroidered cushion — the whole subject of the quote — dropped as a
    "logo" because it sat under the byte threshold).

    The costs here are asymmetric, so this deliberately answers "no" when unsure. A false *drop* loses
    the drawing the spec model most needed and is invisible downstream; a false *admit* costs a few
    cents and is squeezed out anyway by the ``max_images`` size ranking below, since logos are small.
    That is the same principle as the never-silently-bin-a-client rule applied to attachments.

    Two signals are decisive on the real corpus (see tests); everything else is admitted:
      * **density** — a logo is a flat export with few colours (0.01–0.05 bytes/px for the recurring
        Outlook logos), a photo or render is dense (0.98–1.78). Below ``min_density`` it is not a
        photo. Measured *per frame*, so an animated banner cannot inflate its way past the test.
      * **aspect** — a signature *banner* is a wide strip (up to 9.8:1); job photos measured 1.0–1.7:1.

    Known false admits on the corpus, accepted as the cheap side of the trade: a Bureau Veritas cert
    badge (0.81 B/px, 2.7:1) and the Lindo Serviço logo itself (0.58 B/px, 2.2:1).
    """
    if len(payload) >= keep_bytes:
        return False                      # big enough to be real content whatever it is named
    if size is None:
        return False                      # unmeasurable: never drop a readable drawing on a guess
    w, h = size
    if w <= 0 or h <= 0:
        return False
    if len(payload) / (w * h * _gif_frames(payload)) < min_density:
        return True                       # flat export — a photo cannot compress this well
    return max(w, h) / min(w, h) > max_aspect   # wide strip — a signature banner, not a drawing


def _is_pdf(name: str, ctype: str) -> bool:
    return (ctype or "").lower() == "application/pdf" or (name or "").lower().endswith(".pdf")


def _is_svg(name: str, ctype: str) -> bool:
    return (ctype or "").lower() == "image/svg+xml" or (name or "").lower().endswith(".svg")


def _image_size(data: bytes) -> Optional[tuple[int, int]]:
    """``(width, height)`` in pixels, read from the file *header* only — never decodes the image.

    A vector export compresses to very few bytes per pixel, so a byte-size filter alone cannot tell a
    normal drawing from a 283-megapixel one (real case: ``portico.png``, 1 MB / 17975x15776, which
    Vertex rejects with ``400 INVALID_ARGUMENT: Provided image is not valid``). Decoding to measure
    would defeat the purpose, hence the header parse. Returns ``None`` for formats we cannot measure —
    the caller must let those through rather than drop a readable drawing on a guess.
    """
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
            w, h = struct.unpack(">II", data[16:24])
            return int(w), int(h)
        if data[:6] in (b"GIF87a", b"GIF89a"):
            w, h = struct.unpack("<HH", data[6:10])
            return int(w), int(h)
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            if data[12:16] == b"VP8X":
                w = int.from_bytes(data[24:27], "little") + 1
                h = int.from_bytes(data[27:30], "little") + 1
                return w, h
            if data[12:16] == b"VP8 ":
                w, h = struct.unpack("<HH", data[26:30])
                return int(w) & 0x3FFF, int(h) & 0x3FFF
        if data[:2] == b"\xff\xd8":  # JPEG — walk the segment chain to the frame header
            i, n = 2, len(data)
            while i + 9 < n:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                # SOF0-SOF15 carry the dimensions; C4/C8/CC are Huffman/arithmetic tables, not frames.
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return int(w), int(h)
                i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    except Exception:  # noqa: BLE001 — a truncated/corrupt header must not be fatal
        return None
    return None


def _pdf_text(payload: bytes, max_chars: int) -> str:
    """Extract text from a PDF (pure-Python pypdf). Best-effort: returns "" on any failure or for
    scanned/image-only PDFs (which carry no text layer — those go through the image path instead).

    A *missing* pypdf is not "best-effort" — it is a broken install that silently turns every PDF into
    an empty string, which looks exactly like a scanned PDF and is therefore invisible. pypdf is a
    hard dependency (``pyproject.toml``), so that case is logged loudly rather than swallowed; it is
    still not raised, because ``attachment_media`` must never fail a whole email over one attachment.
    ``tests/test_envelope.py`` asserts the dependency is importable so a stale venv fails the suite.
    """
    try:
        import io
    except ImportError:  # pragma: no cover — stdlib
        return ""
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.error("pypdf is not installed — PDF attachments cannot be read and are being skipped "
                     "silently. It is a required dependency: install it (pip install -e .).")
        return ""
    try:
        reader = PdfReader(io.BytesIO(payload))
        chunks: list[str] = []
        total = 0
        for page in reader.pages:
            t = (page.extract_text() or "").strip()
            if not t:
                continue
            chunks.append(t)
            total += len(t)
            if total >= max_chars:
                break
        return "\n\n".join(chunks)[:max_chars].strip()
    except Exception:  # noqa: BLE001 — extraction is best-effort, never fatal
        return ""


def attachment_media(raw: bytes, *, max_images: int = 4, min_image_bytes: int = 20_000,
                     max_image_bytes: int = 6_000_000, total_image_budget: int = 12_000_000,
                     max_pdf_chars: int = 6_000, max_image_pixels: int = 33_177_600,
                     max_image_side: int = 8_192, max_svg_chars: int = 6_000,
                     sig_min_density: float = 0.5, sig_max_aspect: float = 3.0,
                     sig_keep_bytes: int = 200_000) -> dict[str, Any]:
    """Best-effort *content* extraction from attachments, for the spec LLM (NOT for display).

    Returns ``{"texts": [{"filename","text"}], "images": [{"filename","mime","data": bytes}]}``:
      * PDFs   → extracted text (pypdf).
      * SVGs   → their XML source as text. An SVG is machine-readable geometry, and it is usually
        far *smaller* than ``min_image_bytes`` — sending it down the image path meant the byte floor
        silently discarded the most precise drawing in the email (real case: ``portico.svg``, 580 B).
      * images → the raw bytes, so a multimodal model can read the drawing directly. Inline images
        that are *provably* signature logos are skipped (see :func:`_is_signature_image` — the test is
        deliberately conservative, because a wrongly dropped drawing is invisible downstream); the
        largest survivors win within a byte budget.

    Oversized images are dropped by *pixel* count as well as bytes: Vertex rejects them outright and
    ``llm`` then burns every retry on the same deterministic 400, which surfaces as an empty spec
    rather than an error (see :func:`_image_size`). Dropping the image keeps the rest of the email
    extractable — a partial spec beats no spec.

    Never raises — a bad/unreadable attachment simply contributes nothing.
    """
    texts: list[dict[str, str]] = []
    imgs: list[dict[str, Any]] = []
    i = 0
    for part in parse_message(raw).walk():
        disp = str(part.get("Content-Disposition") or "").lower()
        filename = part.get_filename()
        if "attachment" not in disp and not filename:
            continue
        name = decode_value(filename) if filename else f"anexo-{i}"
        ctype = (part.get_content_type() or "").lower()
        payload = part.get_payload(decode=True) or b""
        i += 1
        if not payload:
            continue
        if _is_pdf(name, ctype):
            t = _pdf_text(payload, max_pdf_chars)
            if t:
                texts.append({"filename": name, "text": t})
        elif _is_svg(name, ctype):
            t = payload.decode("utf-8", "replace").strip()[:max_svg_chars]
            if t:
                texts.append({"filename": name, "text": t})
        elif ctype.startswith("image/"):
            if not (min_image_bytes <= len(payload) <= max_image_bytes):
                continue
            size = _image_size(payload)
            if _SIG_IMG.match(name or "") and _is_signature_image(
                    payload, size, min_density=sig_min_density,
                    max_aspect=sig_max_aspect, keep_bytes=sig_keep_bytes):
                continue
            if size is not None:
                w, h = size
                if w * h > max_image_pixels or max(w, h) > max_image_side:
                    continue
            imgs.append({"filename": name, "mime": ctype, "data": payload})
    imgs.sort(key=lambda x: len(x["data"]), reverse=True)  # biggest = most likely the real drawing
    picked: list[dict[str, Any]] = []
    budget = total_image_budget
    for im in imgs:
        if len(picked) >= max_images or len(im["data"]) > budget:
            continue
        picked.append(im)
        budget -= len(im["data"])
    return {"texts": texts, "images": picked}


# Outlook inline header block — present in the quoted body when IMAP headers were stripped.
# Matches "De: Name <email>" or "From: Name <email>" at the start of a forwarded block.
_OUTLOOK_FROM_RE = re.compile(
    r"(?:^|\n)(?:De|From):\s*(?:[^\n<]*<)?([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})>?",
    re.I,
)
# Matches "Enviada: 3 de junho de 2026 15:55" or "Sent: Monday, June 3, 2026"
_OUTLOOK_DATE_RE = re.compile(
    r"(?:^|\n)(?:Enviada?|Data|Sent|Date):\s*([^\n]{4,40})",
    re.I,
)


def _recover_from_body(body_text: str) -> tuple[str, str]:
    """Last-resort header recovery for messages whose From/Date were stripped (e.g. Outlook items
    saved to Trash). Parses the first Outlook inline header block found in the body text.
    Returns (email_addr, raw_date_str) — empty strings when nothing found."""
    email_addr = ""
    raw_date = ""
    fm = _OUTLOOK_FROM_RE.search(body_text)
    if fm:
        email_addr = fm.group(1).strip()
    dm = _OUTLOOK_DATE_RE.search(body_text)
    if dm:
        raw_date = dm.group(1).strip()
    return email_addr, raw_date


def parse_eml(raw: bytes) -> dict[str, Any]:
    """Raw RFC822 bytes -> trimmed envelope.v1 dict (see approach.md data flow)."""
    msg = parse_message(raw)
    body_text, has_html = _extract_body(msg)
    from_parsed = _addr(header_text(msg, "From"))
    date_parsed = _date_iso(msg)
    # Recovery: some Outlook messages saved to Trash lose From/Date headers entirely.
    # The sender and date are only in the quoted body block — recover them as a fallback.
    if not from_parsed.get("email") and body_text:
        recovered_email, _recovered_date = _recover_from_body(body_text)
        if recovered_email:
            from_parsed = {"name": "", "email": recovered_email}
    return {
        "message_id": canonical_id(header_text(msg, "Message-ID"), raw),
        "subject": decode_value(msg.get("Subject")),
        "from": from_parsed,
        "reply_to": _addr(header_text(msg, "Reply-To")),
        "to": _addr_list(header_text(msg, "To")),
        "cc": _addr_list(header_text(msg, "Cc")),
        "date": date_parsed,
        "in_reply_to": header_text(msg, "In-Reply-To").strip() or None,
        "references": _references(header_text(msg, "References")),
        "body_text": body_text,
        "has_html": has_html,
        "attachments": _attachments(msg),
    }
