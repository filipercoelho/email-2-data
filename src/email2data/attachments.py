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

Recurring branding art is omitted entirely (ADR-048)
----------------------------------------------------
The three bands above are decided from **one part in isolation**, and that is why the limitation
below survived: nothing about a single 1280x1280 PNG says "Facebook icon". A fourth signal, measured
across the *corpus* rather than within one part, does separate them cleanly — **how many unrelated
threads the same bytes ride into**. Branding travels with every signature; a drawing lives in one
conversation. Measured over the 825-message corpus (544 threads):

  * the Facebook, Instagram and LinkedIn icons appear in **41 threads** each, the Lindo Serviço
    wordmark in 41, a small wordmark in 38, spacer gifs in 8–32, an animated footer in 5;
  * every image confirmed by eye to be real content sits at **1–2 threads** — the annotated CAD
    drawing (431x361, 5 messages of *one* thread), a client press-kit slide (1140x566), a cotton-bag
    product photo (262x294, 2 threads), the duck cut drawing.

So content tops out at 2 threads and branding starts at 5: :data:`BRANDING_MIN_THREADS` sits in that
measured gap. Items over it are **omitted from the funnel payload**, which is a deliberate narrowing
of ADR-046's "nothing is ever dropped" — see ADR-048 for the decision and
``email2data assets status`` for the audit trail that replaces the click-through. Note that
message-count prevalence does **not** work here and was tried first: the CAD drawing and the
press-kit slide each appear in 5 messages, exactly like the animated footer.

The register is **band-blind** on purpose — an eligible part is any ``cid:``-referenced image from
:data:`email2data.signals.OUR_DOMAIN`, in ``IMAGENS`` or ``ASSINATURAS`` alike. Dropping half of a
proven logo's copies while leaving the other half behind a count would be incoherent. It is built by
:func:`email2data.crm.build_crm` into ``crm.db`` (regenerable) and read per render; when that table
is absent or stale the set is **empty and nothing is dropped**, so the failure direction is "shows
too much", never "hides silently".

Known limitation: high-resolution logos land in IMAGENS, and that is deliberate
--------------------------------------------------------------------------------
This is what ADR-048 above now fixes for *recurring* art; it remains true for a logo seen in one or
two threads, which still lands in IMAGENS.

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

So no *per-part* demotion arm ships. A leaked logo costs a glance; a buried drawing costs a quote,
and the band exists to stop the second. The residual leak is bounded by :func:`fold_thread`'s sort —
items run largest-first inside a band, and flat logo art is small, so it lands at the bottom. **Do
not "fix" this with a density test without re-running the duck.** The cross-thread register above is
allowed to demote precisely because it is not a per-part guess: it is arithmetic over observed
recurrence, and the duck appears in one thread.

Dedup
-----
The key is the **sha256 of the decoded bytes, never the filename**. Measured on the same corpus:
784 byte-identical duplicate parts per-thread versus 603 same-name, and 220 pairs that share a
name while differing in bytes (a real thread carries ``composition.pdf`` twice, at 154 KB and
152 KB). Hashing catches strictly more duplicates and never merges two different documents into
one row — a filename key does both jobs worse.

A second source: intake capture media (ADR-052)
-----------------------------------------------
Everything above is about MIME. A project's files do not all arrive by mail — a drawing photographed
on the shop floor and sent through the ADR-019 Telegram intake is a project file by every measure a
human uses, and until ADR-052 it could only ever be seen as an 84 px thumbnail on the timeline, and
only ever the *first* one per capture.

:func:`capture_media_items` folds those into the SAME item shape, and — the load-bearing part — it
**hashes the bytes**, so a capture joins the content dedup above rather than sitting beside it. The
same photo mailed and then re-sent through Telegram is one file with ``n_copies: 2``, not two rows
that look like two files. Without a hash they could only have been given ``src``-derived ids, which
is a stable handle and nothing more: two identical drawings would still read as two drawings.

They are **not** laundered into looking like email attachments. Every capture item carries
``source: "capture"``, its own ``band_evidence`` naming the channel, and a ``src`` of
``{capture_id, index}`` instead of ``{message_id, index}`` — so the byte link goes to
``/api/captures/{cid}/media/{i}`` and the provenance line says who registered it, not who sent it.
They land in ``FICHEIROS`` rather than in a fourth band, deliberately: a fourth band would have to
hold a file that is *both* mailed and captured, which is one file, and the three bands are a measured
calibration this has no evidence to extend.

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
from collections.abc import Collection, Mapping
from email.message import Message
from pathlib import Path
from typing import Any, Iterable

from .envelope import _image_size
from .headers import decode_value, parse_message
from .signals import OUR_DOMAIN

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

# ── The branding register (ADR-048) ──────────────────────────────────────────────────────────
# How many DISTINCT threads the same bytes must ride into before they are branding rather than
# content. Measured on the corpus: content tops out at 2 threads, branding starts at 5 — see the
# module docstring. This is the one number to re-measure before changing, and the direction of a
# wrong guess is asymmetric: too low buries a drawing, too high shows an extra logo.
BRANDING_MIN_THREADS = 3

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


def is_own_domain(email: str) -> bool:
    """Is this address one of ours? The register only observes mail Lindo itself sent (ADR-048)."""
    domain = (email or "").strip().lower().rsplit("@", 1)[-1] if "@" in (email or "") else ""
    return bool(domain) and (domain == OUR_DOMAIN or domain.endswith("." + OUR_DOMAIN))


def register_candidates(raw: bytes, *, sender: str) -> list[dict[str, Any]]:
    """The parts of one message eligible for the ADR-048 branding register.

    Eligible = sent from our own domain **and** a ``cid:``-referenced image, i.e. anything the band
    rule did not file as ``FICHEIROS``. Band-blind beyond that (see the module docstring): a proven
    logo must not be half-dropped and half-collapsed. A part with no bytes is skipped — it has no
    content hash, so it cannot be recognised again anyway.

    Returns ``[]`` for mail from anyone else, which is what keeps this decision scoped to art *we*
    attach. Never raises: :func:`message_parts` already swallows a bad message.
    """
    if not is_own_domain(sender):
        return []
    return [p for p in message_parts(raw, want_pdf_pages=False)
            if p["sha"] and p["band"] != BAND_FILES and p["type"].startswith("image/")]


def branding_shas(spread: Mapping[str, int], *,
                  min_threads: int = BRANDING_MIN_THREADS) -> set[str]:
    """``{sha}`` for the hashes recurring across enough unrelated threads to be branding.

    ``spread`` is ``{sha: n_distinct_threads}``. Pure arithmetic over observed recurrence — the
    whole point of ADR-048 is that this is not a per-image guess. An empty ``spread`` (no register
    built yet) yields an empty set, so nothing is dropped.
    """
    return {sha for sha, n in spread.items() if n >= min_threads}


def fold_thread(messages: Iterable[dict[str, Any]], *,
                branding: Collection[str] = ()) -> list[dict[str, Any]]:
    """Deduplicate a thread's parts by content hash into the funnel's item list.

    ``messages`` is an iterable of ``{"message_id", "date", "from_email", "parts"}`` in the order
    the thread should be read (chronological). The **first** occurrence wins: it supplies ``src``
    (so the byte link points at a message that really carries those bytes), ``first_seen`` and
    ``from_email``; later byte-identical copies only bump ``n_copies``.

    Call this with **every** interaction of the thread, including messages a later render step
    suppresses — a file whose only carrier is a deduplicated Trash copy still has to surface.

    Parts with no bytes (``sha == ""``) cannot be hashed and are never merged; each is kept as its
    own item. Losing a zero-byte attachment silently would be the same defect as a false IGNORE.

    ``branding`` is the ADR-048 set of content hashes proven to be recurring signature/branding art
    (:func:`branding_shas`). Those are **omitted from the result entirely** — no item, no count, no
    click-through; ``email2data assets status`` is where a misfire is audited instead. Dropping here
    rather than in :func:`message_parts` is load-bearing: the per-message 📎 chips are positional
    against ``/api/attachment/{message_id}/{index}``, so a part removed before that counter would
    repoint every remaining link at the wrong file. Default is empty — drop nothing.

    Matching is on the **full hash**, not the truncated ``id``, and regardless of who sent the copy
    in hand: the hash identifies the artefact, and a supplier forwarding our logo back is still
    forwarding our logo.
    """
    items: dict[str, dict[str, Any]] = {}
    unhashable: list[dict[str, Any]] = []
    dropped = set(branding)
    for msg in messages:
        mid = msg.get("message_id") or ""
        date = msg.get("date") or ""
        sender = msg.get("from_email") or ""
        for part in msg.get("parts") or []:
            if part["sha"] and part["sha"] in dropped:
                continue
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


def capture_media_items(captures: Iterable[Mapping[str, Any]], *,
                        media_root: Path) -> list[dict[str, Any]]:
    """The intake-capture half of a project's file list, in :func:`fold_thread` item shape (ADR-052).

    ``captures`` is an iterable of capture rows (``CaptureStore.get``/``_row`` output, so
    ``media_paths`` is already a decoded list). ``media_root`` is the sole-copy ``captures_dir``
    (ADR-020); every path is resolved under it and anything escaping is refused, the same guard
    ``GET /api/captures/{cid}/media/{index}`` applies to the bytes.

    **Every** index is enumerated, not just ``0``: a capture carrying a photo *and* a PDF used to hide
    the PDF, because the timeline renderer only ever asked for ``media/0``.

    The bytes are **read and hashed** here. That is the whole point — an item with a sha256 joins the
    ADR-046 content dedup, so the same drawing mailed and then re-sent through Telegram folds into one
    row instead of reading as two files. It costs one full read per media file per call; measured on
    the live install that is 3 captures, and the revisit trigger is in ADR-052.

    A media file that is **missing or unreadable** is still listed, with ``missing: True``, size 0 and
    a plain-language evidence line. ADR-020 says this store is the sole copy, so a gap in it is an
    incident to see, never a row to quietly skip — the same reasoning as the zero-byte parts above.
    """
    import mimetypes

    out: list[dict[str, Any]] = []
    root = Path(media_root).resolve()
    for cap in captures or []:
        cid = str(cap.get("capture_id") or "")
        channel = str(cap.get("channel") or "").strip() or "intake"
        who = str(cap.get("asserted_by") or "").strip()
        when = str(cap.get("acquired_at") or cap.get("created_ts") or "")
        for index, rel in enumerate(cap.get("media_paths") or []):
            name = str(rel).rsplit("/", 1)[-1] or f"captura-{index}"
            payload = b""
            try:
                full = (root / str(rel)).resolve()
                if root in full.parents and full.is_file():
                    payload = full.read_bytes()
            except OSError:  # noqa: PERF203 — one unreadable file must not blank the list
                payload = b""
            ctype = (mimetypes.guess_type(name)[0] or "application/octet-stream").lower()
            kind = kind_of(name, ctype)
            px = _image_size(payload) if payload and ctype.startswith("image/") else None
            sha = hashlib.sha256(payload).hexdigest() if payload else ""
            item: dict[str, Any] = {
                # Same rule as fold_thread's zero-byte handle: the fallback prefix must be OUTSIDE
                # [0-9a-f] so a derived id can never collide with a real sha256 prefix.
                "id": sha[:16] if sha else
                      "k" + hashlib.sha256(f"{cid}:{index}".encode()).hexdigest()[:15],
                "name": name,
                "type": ctype,
                "kind": kind,
                "size": len(payload),
                "px": list(px) if px else None,
                "band": BAND_FILES,
                "band_evidence": (f"captura por {channel} — registada por uma pessoa, não anexada a um email"
                                  if payload else
                                  f"captura por {channel} — ficheiro em falta no arquivo de capturas"),
                "src": {"capture_id": cid, "index": index},
                "first_seen": when,
                "from_email": "",
                "asserted_by": who,
                "channel": channel,
                "source": "capture",
                "n_copies": 1,
                "preview": kind == "image" and 0 < len(payload) <= PREVIEW_MAX_BYTES,
            }
            if not payload:
                item["missing"] = True
            if kind == "pdf" and payload:
                item["pages"] = _pdf_pages(payload)
            out.append(item)
    return out


def band_counts(items: Iterable[dict[str, Any]]) -> dict[str, int]:
    """``{band: n}`` for all three bands, zeros included — the UI renders a count per band."""
    counts = {b: 0 for b in BANDS}
    for item in items:
        counts[item["band"]] = counts.get(item["band"], 0) + 1
    return counts
