"""The thread attachment funnel — one deduped, banded list of files per thread (ADR-046).

A thread's attachment parts are not one kind of thing. Three kinds arrive down the same MIME
pipe and a single flat list of 📎 chips shows them with equal weight, so the quote PDF sits
beside forty copies of the sender's Facebook icon. This module sorts them into three **bands**
and deduplicates by content, so the file a human is looking for is the one they see.

Bands (the value is the label the UI renders — user-facing strings are pt-PT):

  ``FICHEIROS``    a real attached document. ``Content-Disposition: attachment``, or a part no
                   HTML body references by ``cid:``. This is what "anexo" means to a human.
  ``IMAGENS``      an image pasted *into the body* — a drawing, a screenshot, a photo. Worth
                   seeing inline; it usually carries the actual request.
  ``ASSINATURAS``  signature and branding art. Collapsed behind a count, **never dropped** —
                   one click away, which keeps this inside the "never silently bin" rule
                   (VISION non-negotiable #2) rather than beside it.

Every band is an **INFERENCE**, never a FACT: nothing in MIME states "this is a logo". Each item
therefore carries :attr:`band_evidence`, the deterministic reason it landed where it did, so a
human can see the rule that moved it and disagree with it. Per PROFILE.md, an inference that
cannot show its evidence is a hallucination with better manners.

Calibration (measured 2026-07-26 over the 553-message corpus, 395 distinct inline parts)
-----------------------------------------------------------------------------------------
The first cut of this rule banded by size alone: a ``cid:``-referenced image was body content if
it exceeded 600 px on a side, 250 kpx, or 200 KB, and signature art otherwise. Sweeping the
corpus and **looking at the images** showed that rule burying 11 distinct real-content images in
the collapsed band — among them a 431x361 CAD drawing with dimension annotations, two order
summaries showing totals (EUR 59,34 and EUR 6.578,38), a 353x336 RAL 5013 colour swatch, a
payslip breakdown, and a 513x458 bank-portfolio screenshot. That is precisely the failure that
costs a quote, and no size threshold fixes it: a 377x377 BANEMA logo and a 353x336 colour swatch
are the same shape and the same weight.

What *does* separate them, measured, is **aspect ratio**. Pasted content is squarish — the widest
real-content image in the corpus is 1.79:1. Signature banners are wide — the narrowest is 2.14:1.
That gap is clean, so a second arm was added: a ``cid:``-referenced image is also body content
when it is *postcard-shaped* (aspect < 2.0) and at least 250 px on its short side.

Result on the corpus: 11 distinct content images rescued from the collapsed band, at the cost of
3 squarish logos (BANEMA 377x377, a 465x283 signature banner, a 504x325 logo sheet) now showing
inline. That trade is deliberate and asymmetric — a leaked logo costs a glance, a buried drawing
costs a quote. The ASSINATURAS band stays clean regardless: 164 icons at 150 px or under, 80 wide
banners, 26 squarish-but-small.

The 250 px floor is a measured operating point, not a round number. Dropping it to 200 px would
rescue two ~230 px product photos but leak seven Lindo Serviço signature cards (205x278, 181x228)
into the visible band — a worse trade. Those two photos stay one click away, not gone.

Known limitation: high-resolution logos land in IMAGENS, and that is deliberate
--------------------------------------------------------------------------------
Looking at the rendered funnel showed Instagram/Facebook/LinkedIn icons (4322x4320, 1920x1919,
1280x1280) sitting in the visible band — pixel-huge, so the "big" arm promotes them. Three ways to
demote them were measured, and **all three would have buried real content**:

  * **density** (bytes per pixel, the signal :func:`email2data.envelope._is_signature_image` uses
    for the LLM path) — a 2437x2441 CAD cut drawing of a duck, with red annotation marks, scores
    **0.0117 B/px**, *below* the Instagram icon's 0.0095–0.047. Flat line art and flat logo art
    compress identically. That heuristic is only safe over there because it is gated behind the
    ``imageNNN.ext`` name pattern and a 200 KB floor.
  * **filename** — Outlook renames every inline image, and the rescued 431x361 drawing is
    ``image031.jpg``. A name proves nothing in either direction.
  * **anchor-wrapping** (is the ``<img>`` inside an ``<a href>``? social icons are) — a stone
    supplier's whole product catalogue is link-wrapped (``1_gtr_macondo_..._295x195x2.jpg`` and
    16 siblings, 700x~500). This would drop the photographs a quote is built from.

So no demotion arm ships. A leaked logo costs a glance; a buried drawing costs a quote, and the
band exists to stop the second. The leak is bounded instead by :func:`fold_thread`'s sort — items
run largest-first inside a band, and flat logo art is small, so it lands at the bottom. **Do not
"fix" this with a density test without re-running the duck.**

Dedup
-----
The key is the **sha256 of the decoded bytes, never the filename**. Measured on the same corpus:
784 byte-identical duplicate parts per-thread versus 603 same-name, and 220 pairs that share a
name while differing in bytes (a real thread carries ``composition.pdf`` twice, at 154 KB and
152 KB). Hashing catches strictly more duplicates and never merges two different documents into
one row — a filename key does both jobs worse.

Index stability (the thing not to get wrong)
--------------------------------------------
:func:`message_parts` walks parts under **exactly** the predicate
:func:`email2data.envelope._attachments` uses, and yields ``index`` from that walk — the same
counter :func:`email2data.envelope.attachment_part` re-walks to serve bytes at
``/api/attachment/{message_id}/{index}``. Banding is therefore **additive metadata only**. Any
filter applied *before* that counter increments would silently repoint every existing 📎 link at
the wrong file, and it would look fine on screen. :mod:`tests.test_attachments` pins the ordering
against ``attachment_part`` directly so a future filter cannot land quietly.
"""

from __future__ import annotations

import hashlib
import logging
from email.message import Message
from typing import Any, Iterable

from .envelope import _image_size
from .headers import decode_value, parse_message

logger = logging.getLogger(__name__)

# ── Bands (pt-PT — these strings are rendered) ───────────────────────────────────────────────
BAND_FILES = "FICHEIROS"
BAND_INLINE = "IMAGENS"
BAND_SIGNATURE = "ASSINATURAS"
BANDS = (BAND_FILES, BAND_INLINE, BAND_SIGNATURE)

# ── Band thresholds (see the calibration note above — every number here was measured) ────────
BIG_SIDE = 600            # px on the long side
BIG_PIXELS = 250_000      # total px
BIG_BYTES = 200_000       # encoded size
POSTCARD_MAX_ASPECT = 2.0   # widest real-content image measured: 1.79
POSTCARD_MIN_SIDE = 250     # px on the short side

# Previews are size-gated: an <img> is only worth lazy-loading when the bytes are small enough
# that a dozen of them do not cost a thread-open. ~70% of previewable images sit under this.
PREVIEW_MAX_BYTES = 262_144   # 256 KB

_IMAGE_EXT = ("png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff", "heic", "svg")
_KIND_BY_EXT = {
    "pdf": "pdf",
    "doc": "doc", "docx": "doc", "odt": "doc", "rtf": "doc", "txt": "doc",
    "xls": "sheet", "xlsx": "sheet", "csv": "sheet", "ods": "sheet",
    "ppt": "slides", "pptx": "slides",
    "zip": "archive", "rar": "archive", "7z": "archive", "gz": "archive", "tar": "archive",
    "dwg": "cad", "dxf": "cad", "step": "cad", "stp": "cad", "iges": "cad", "igs": "cad",
    "stl": "cad", "3dm": "cad", "skp": "cad",
    "eml": "mail", "msg": "mail",
}


def _ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def kind_of(name: str, content_type: str) -> str:
    """A coarse category for the UI tile. Never raises; unknown folds to ``"file"``."""
    ct = (content_type or "").lower()
    ext = _ext(name or "")
    if ct == "application/pdf" or ext == "pdf":
        return "pdf"
    if ct.startswith("image/") or ext in _IMAGE_EXT:
        return "image"
    if ext in _KIND_BY_EXT:
        return _KIND_BY_EXT[ext]
    if ct.startswith("text/"):
        return "doc"
    if "spreadsheet" in ct or "excel" in ct:
        return "sheet"
    if "word" in ct or "opendocument.text" in ct:
        return "doc"
    if "presentation" in ct or "powerpoint" in ct:
        return "slides"
    if "zip" in ct or "compressed" in ct or "tar" in ct:
        return "archive"
    if ct.startswith("message/"):
        return "mail"
    return "file"


def html_bodies(msg: Message) -> str:
    """Every ``text/html`` body of the message concatenated — the haystack for ``cid:`` lookups.

    Deliberately *not* the cleaned/---stripped body: a ``cid:`` reference lives in an ``src``
    attribute, which HTML stripping removes. Decoding is lossy-tolerant (``errors="replace"``)
    because a mis-declared charset must not decide an attachment's band.
    """
    chunks: list[str] = []
    for part in msg.walk():
        if part.get_content_type() != "text/html":
            continue
        try:
            payload = part.get_payload(decode=True) or b""
        except Exception:  # noqa: BLE001 — a corrupt part must not fail the whole message
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            chunks.append(payload.decode(charset, errors="replace"))
        except (LookupError, ValueError):
            chunks.append(payload.decode("utf-8", errors="replace"))
    return "".join(chunks)


def classify(*, disposition: str, cid_referenced: bool, content_type: str,
             size: int, px: tuple[int, int] | None) -> tuple[str, str]:
    """``(band, band_evidence)`` for one part. Pure, deterministic, no I/O.

    The evidence string is pt-PT and states the rule that fired, with the measurement that
    triggered it — it is rendered next to the item so a human can audit the INFERENCE.
    """
    disp = (disposition or "").lower()
    if "attachment" in disp:
        return BAND_FILES, "Content-Disposition: attachment"
    if not cid_referenced:
        return BAND_FILES, "sem referência cid: em nenhum corpo HTML"
    # From here down the part IS referenced by the body. Only an image can be signature art —
    # a cid:-referenced PDF is a document someone chose to embed, never a logo. The corpus has
    # none today; this branch exists so the first one is not filed as a signature.
    if not (content_type or "").lower().startswith("image/"):
        return BAND_FILES, f"referenciado no corpo mas não é imagem ({content_type or 'tipo desconhecido'})"
    if px:
        w, h = px
        if max(w, h) > BIG_SIDE:
            return BAND_INLINE, f"imagem no corpo, {w}x{h}px (lado > {BIG_SIDE}px)"
        if w * h > BIG_PIXELS:
            return BAND_INLINE, f"imagem no corpo, {w}x{h}px (> {BIG_PIXELS // 1000}kpx)"
    if size > BIG_BYTES:
        return BAND_INLINE, f"imagem no corpo, {size // 1024}KB (> {BIG_BYTES // 1024}KB)"
    if px:
        w, h = px
        short, long_ = min(w, h), max(w, h)
        if short >= POSTCARD_MIN_SIDE and long_ < short * POSTCARD_MAX_ASPECT:
            # The measured arm: content is squarish (<=1.79:1), signature banners are wide (>=2.14:1).
            return BAND_INLINE, f"imagem no corpo, {w}x{h}px (formato de conteúdo, não faixa)"
        return BAND_SIGNATURE, f"provável assinatura/logótipo, {w}x{h}px"
    return BAND_SIGNATURE, f"provável assinatura/logótipo, {size // 1024}KB (dimensões ilegíveis)"


def _pdf_pages(payload: bytes) -> int | None:
    """Page count for a PDF, or ``None``. Best-effort — a broken PDF must not fail a thread."""
    try:
        import io

        from pypdf import PdfReader
    except ImportError:
        logger.error("pypdf is not installed — PDF page counts unavailable. It is a required "
                     "dependency: install it (pip install -e .).")
        return None
    try:
        return len(PdfReader(io.BytesIO(payload)).pages)
    except Exception:  # noqa: BLE001 — best-effort metadata, never fatal
        return None


def message_parts(raw: bytes, *, want_pdf_pages: bool = True) -> list[dict[str, Any]]:
    """Every attachment part of one message, **in ``attachment_part`` index order**.

    The walk predicate and the index counter are a deliberate copy of
    :func:`email2data.envelope._attachments` / :func:`email2data.envelope.attachment_part`.
    Do not filter inside this loop — see the module docstring. Returns ``[]`` for unparseable
    bytes rather than raising: one bad message must not blank a thread's file list.
    """
    try:
        msg = parse_message(raw)
    except Exception:  # noqa: BLE001
        return []
    html = html_bodies(msg)
    out: list[dict[str, Any]] = []
    index = 0
    for part in msg.walk():
        disp = str(part.get("Content-Disposition") or "").lower()
        filename = part.get_filename()
        if "attachment" not in disp and not filename:
            continue
        # index is fixed HERE, before any classification, and every `continue` above this line
        # is one attachment_part also skips. Nothing below may skip.
        try:
            payload = part.get_payload(decode=True) or b""
        except Exception:  # noqa: BLE001
            payload = b""
        name = decode_value(filename) if filename else ""
        ctype = (part.get_content_type() or "application/octet-stream").lower()
        cid = (part.get("Content-ID") or "").strip("<> \n\t")
        cid_referenced = bool(cid and html and cid in html)
        px = _image_size(payload) if ctype.startswith("image/") else None
        band, evidence = classify(disposition=disp, cid_referenced=cid_referenced,
                                  content_type=ctype, size=len(payload), px=px)
        kind = kind_of(name, ctype)
        rec: dict[str, Any] = {
            "index": index,
            "name": name,
            "type": ctype,
            "kind": kind,
            "size": len(payload),
            "px": list(px) if px else None,
            "band": band,
            "band_evidence": evidence,
            "sha": hashlib.sha256(payload).hexdigest() if payload else "",
            "cid": cid,
        }
        if want_pdf_pages and kind == "pdf" and payload:
            rec["pages"] = _pdf_pages(payload)
        out.append(rec)
        index += 1
    return out


def fold_thread(messages: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate a thread's parts by content hash into the funnel's item list.

    ``messages`` is an iterable of ``{"message_id", "date", "from_email", "parts"}`` in the order
    the thread should be read (chronological). The **first** occurrence wins: it supplies ``src``
    (so the byte link points at a message that really carries those bytes), ``first_seen`` and
    ``from_email``; later byte-identical copies only bump ``n_copies``.

    Call this with **every** interaction of the thread, including messages a later render step
    suppresses — a file whose only carrier is a deduplicated Trash copy still has to surface.

    Parts with no bytes (``sha == ""``) cannot be hashed and are never merged; each is kept as its
    own item. Losing a zero-byte attachment silently would be the same defect as a false IGNORE.
    """
    items: dict[str, dict[str, Any]] = {}
    unhashable: list[dict[str, Any]] = []
    for msg in messages:
        mid = msg.get("message_id") or ""
        date = msg.get("date") or ""
        sender = msg.get("from_email") or ""
        for part in msg.get("parts") or []:
            item = {
                # A zero-byte part (a read-receipt stub, or a message/rfc822 sub-part, which
                # ``get_payload(decode=True)`` yields nothing for) has no content hash. It still
                # needs a unique, stable handle for the DOM, so derive one from its source instead
                # of leaving an empty string that every such item would share.
                "id": (part["sha"][:16] if part["sha"] else
                       "n" + hashlib.sha256(f"{mid}:{part['index']}".encode()).hexdigest()[:15]),
                "name": part["name"] or "(sem nome)",
                "type": part["type"],
                "kind": part["kind"],
                "size": part["size"],
                "px": part["px"],
                "band": part["band"],
                "band_evidence": part["band_evidence"],
                "src": {"message_id": mid, "index": part["index"]},
                "first_seen": date,
                "from_email": sender,
                "n_copies": 1,
                # The preview gate: only a small image is worth a lazy <img>; everything else
                # renders as a kind tile. Decided server-side so the client never guesses.
                "preview": part["kind"] == "image" and 0 < part["size"] <= PREVIEW_MAX_BYTES,
            }
            if part.get("pages") is not None:
                item["pages"] = part["pages"]
            if not part["sha"]:
                unhashable.append(item)
                continue
            seen = items.get(part["sha"])
            if seen is None:
                items[part["sha"]] = item
            else:
                seen["n_copies"] += 1
                # A later copy may carry a name the first one lacked (Outlook drops filenames on
                # some Trash copies). Take the better name; never overwrite a real one.
                if seen["name"] == "(sem nome)" and item["name"] != "(sem nome)":
                    seen["name"] = item["name"]
    out = list(items.values()) + unhashable
    # Band order first (documents before body images before signatures), then largest first
    # inside a band — the biggest file is nearly always the one being asked about.
    order = {BAND_FILES: 0, BAND_INLINE: 1, BAND_SIGNATURE: 2}
    out.sort(key=lambda i: (order.get(i["band"], 9), -i["size"], i["name"].lower()))
    return out


def band_counts(items: Iterable[dict[str, Any]]) -> dict[str, int]:
    """``{band: n}`` for all three bands, zeros included — the UI renders a count per band."""
    counts = {b: 0 for b in BANDS}
    for item in items:
        counts[item["band"]] = counts.get(item["band"], 0) + 1
    return counts
