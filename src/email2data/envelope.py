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


# Outlook embeds signature logos as image001.png, image002.gif, … — almost never job content, so the
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


def parse_eml(raw: bytes) -> dict[str, Any]:
    """Raw RFC822 bytes -> trimmed envelope.v1 dict (see approach.md data flow)."""
    msg = message_from_bytes(raw)
    body_text, has_html = _extract_body(msg)
    return {
        "message_id": canonical_id(msg.get("Message-ID"), raw),
        "subject": _decode_header(msg.get("Subject")),
        "from": _addr(str(msg.get("From") or "")),
        "reply_to": _addr(str(msg.get("Reply-To") or "")),
        "to": _addr_list(str(msg.get("To") or "")),
        "cc": _addr_list(str(msg.get("Cc") or "")),
        "date": _date_iso(msg),
        "in_reply_to": str(msg.get("In-Reply-To") or "").strip() or None,
        "references": _references(str(msg.get("References") or "")),
        "body_text": body_text,
        "has_html": has_html,
        "attachments": _attachments(msg),
    }
