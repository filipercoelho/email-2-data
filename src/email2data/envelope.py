"""Parse a raw .eml into an EmailEnvelope dict. Pure function of bytes — no network, no I/O."""

from __future__ import annotations

import re
from email import message_from_bytes
from email.header import decode_header
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime
from html import unescape
from typing import Any

from .identity import canonical_id

MAX_BODY_CHARS = 20_000

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANKLINES_RE = re.compile(r"\n{3,}")


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    parts = []
    for chunk, enc in decode_header(str(value)):
        if isinstance(chunk, bytes):
            try:
                parts.append(chunk.decode(enc or "utf-8", errors="replace"))
            except (LookupError, ValueError):
                # bogus/unknown charset label (e.g. "unknown-8bit") — fall back, never raise
                parts.append(chunk.decode("utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts).strip()


def _addr(value: str | None) -> dict[str, str]:
    pairs = getaddresses([value or ""])
    if not pairs:
        return {"name": "", "email": ""}
    name, email_addr = pairs[0]
    return {"name": _decode_header(name), "email": email_addr.lower()}


def _addr_list(value: str | None) -> list[dict[str, str]]:
    return [
        {"name": _decode_header(n), "email": e.lower()}
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
                "filename": _decode_header(filename) if filename else None,
                "content_type": part.get_content_type(),
                "size_bytes": len(payload) if payload else 0,
            }
        )
    return out


def _date_iso(msg: Message) -> str | None:
    raw = msg.get("Date")
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
    for part in message_from_bytes(raw).walk():
        disp = str(part.get("Content-Disposition") or "").lower()
        filename = part.get_filename()
        if "attachment" not in disp and not filename:
            continue
        if i == index:
            payload = part.get_payload(decode=True) or b""
            name = _decode_header(filename) if filename else f"anexo-{index}"
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
            # the common "Melhores cumprimentos\n\nANA MATOS\n..." pattern.
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


# ── Outlook embeds signature logos as image001.png, image002.gif, … — almost never job content, so the
# small ones are skipped before sending attachments to the spec model (drawings, not logos).
_SIG_IMG = re.compile(r"^image\d{3,}\.(png|gif|jpe?g|bmp)$", re.I)


def _is_pdf(name: str, ctype: str) -> bool:
    return (ctype or "").lower() == "application/pdf" or (name or "").lower().endswith(".pdf")


def _pdf_text(payload: bytes, max_chars: int) -> str:
    """Extract text from a PDF (pure-Python pypdf). Best-effort: returns "" on any failure or for
    scanned/image-only PDFs (which carry no text layer — those go through the image path instead)."""
    try:
        import io

        from pypdf import PdfReader
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
                     max_pdf_chars: int = 6_000) -> dict[str, Any]:
    """Best-effort *content* extraction from attachments, for the spec LLM (NOT for display).

    Returns ``{"texts": [{"filename","text"}], "images": [{"filename","mime","data": bytes}]}``:
      * PDFs   → extracted text (pypdf).
      * images → the raw bytes, so a multimodal model can read the drawing directly. Tiny inline
        signature logos are skipped; the largest real images win within a byte budget.
    Never raises — a bad/unreadable attachment simply contributes nothing.
    """
    texts: list[dict[str, str]] = []
    imgs: list[dict[str, Any]] = []
    i = 0
    for part in message_from_bytes(raw).walk():
        disp = str(part.get("Content-Disposition") or "").lower()
        filename = part.get_filename()
        if "attachment" not in disp and not filename:
            continue
        name = _decode_header(filename) if filename else f"anexo-{i}"
        ctype = (part.get_content_type() or "").lower()
        payload = part.get_payload(decode=True) or b""
        i += 1
        if not payload:
            continue
        if _is_pdf(name, ctype):
            t = _pdf_text(payload, max_pdf_chars)
            if t:
                texts.append({"filename": name, "text": t})
        elif ctype.startswith("image/"):
            if not (min_image_bytes <= len(payload) <= max_image_bytes):
                continue
            if _SIG_IMG.match(name or "") and len(payload) < 200_000:
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
    msg = message_from_bytes(raw)
    body_text, has_html = _extract_body(msg)
    from_parsed = _addr(str(msg.get("From") or ""))
    date_parsed = _date_iso(msg)
    # Recovery: some Outlook messages saved to Trash lose From/Date headers entirely.
    # The sender and date are only in the quoted body block — recover them as a fallback.
    if not from_parsed.get("email") and body_text:
        recovered_email, _recovered_date = _recover_from_body(body_text)
        if recovered_email:
            from_parsed = {"name": "", "email": recovered_email}
    return {
        "message_id": canonical_id(msg.get("Message-ID"), raw),
        "subject": _decode_header(msg.get("Subject")),
        "from": from_parsed,
        "reply_to": _addr(str(msg.get("Reply-To") or "")),
        "to": _addr_list(str(msg.get("To") or "")),
        "cc": _addr_list(str(msg.get("Cc") or "")),
        "date": date_parsed,
        "in_reply_to": str(msg.get("In-Reply-To") or "").strip() or None,
        "references": _references(str(msg.get("References") or "")),
        "body_text": body_text,
        "has_html": has_html,
        "attachments": _attachments(msg),
    }
