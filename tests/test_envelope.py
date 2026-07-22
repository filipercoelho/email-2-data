import logging
import struct
from email.message import EmailMessage
from pathlib import Path

import pytest

from email2data.envelope import (MAX_BODY_CHARS, _image_size, _pdf_text, attachment_media,
                                 attachment_part, parse_eml)

ENCODED = (
    b"Subject: =?utf-8?Q?Pedido_de_or=C3=A7amento?=\r\n"
    b"From: =?utf-8?Q?Jo=C3=A3o?= Silva <joao@example.pt>\r\n"
    b"To: geral@lindoservico.pt\r\n"
    b"Date: Wed, 27 May 2026 09:00:00 +0100\r\n"
    b"Message-ID: <x1@example.pt>\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
    b"Bom dia, preciso de corte laser.\r\n"
)

HTML_ONLY = (
    b"Subject: promo\r\nFrom: a@b.pt\r\nMessage-ID: <h@b.pt>\r\n"
    b"Content-Type: text/html; charset=utf-8\r\n\r\n"
    b"<html><body><p>Ol&aacute; <b>50%</b></p></body></html>\r\n"
)


def test_rfc2047_subject_and_name_decoded():
    env = parse_eml(ENCODED)
    assert env["subject"] == "Pedido de orçamento"
    assert env["from"] == {"name": "João Silva", "email": "joao@example.pt"}
    assert env["date"].startswith("2026-05-27")
    assert "corte laser" in env["body_text"]


def test_html_only_is_stripped_to_text():
    env = parse_eml(HTML_ONLY)
    assert env["has_html"] is True
    assert "<" not in env["body_text"]
    assert "Olá" in env["body_text"] and "50%" in env["body_text"]


def test_body_is_truncated():
    big = b"Subject: x\r\nMessage-ID: <big@b>\r\n\r\n" + b"a" * (MAX_BODY_CHARS + 5000)
    assert len(parse_eml(big)["body_text"]) <= MAX_BODY_CHARS


def test_bogus_charset_does_not_raise():
    # Real mail in the wild declares unknown charsets (e.g. "unknown-8bit"). Must not crash.
    raw = (
        b"Subject: =?unknown-8bit?Q?Encomenda?=\r\n"
        b"From: Fornecedor <f@x.pt>\r\nMessage-ID: <u@x>\r\n"
        b"Content-Type: text/plain; charset=unknown-8bit\r\n\r\n"
        b"Ol\xe1, segue a encomenda.\r\n"
    )
    env = parse_eml(raw)
    assert env["subject"] == "Encomenda"
    assert "encomenda" in env["body_text"].lower()


# ── attachment_media: pixel-based image filtering + SVG-as-text ──────────────────────────────────
#
# A vector export compresses to very few bytes per pixel, so the byte filter alone let a 283-megapixel
# PNG through and Vertex 400'd on every retry (real case: portico.png, 1 MB / 17975x15776). These pin
# the header-only pixel probe AND the guard against over-blocking a normal drawing.


def _png(width: int, height: int, filler: int = 1_000_000) -> bytes:
    """Minimal PNG *header* (signature + IHDR) padded with filler — enough for a header-only probe."""
    ihdr = struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"   # 8-bit RGBA, no interlace
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr
            + b"\x00\x00\x00\x00" + b"\x00" * filler)


def _gif(width: int, height: int, filler: int = 100) -> bytes:
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\x00" * filler


def _jpeg(width: int, height: int, filler: int = 100) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sof0 = b"\xff\xc0" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", height, width) + b"\x00" * 10
    return b"\xff\xd8" + app0 + sof0 + b"\x00" * filler + b"\xff\xd9"


SVG_SRC = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<svg xmlns="http://www.w3.org/2000/svg" width="2400" height="1800" viewBox="0 0 2400 1800">\n'
    b'  <rect x="100" y="100" width="2200" height="120" fill="none" stroke="#000"/>\n'
    b'  <text x="1200" y="90">portico 2400x1800 mm - chapa corten 3 mm</text>\n'
    b'</svg>\n'
)


def _eml_with(attachments: list[tuple[str, str, bytes]], subject: str = "Pórtico",
              body: str = "Segue desenho em anexo.") -> bytes:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = "cliente@example.pt"
    msg["To"] = "geral@lindoservico.pt"
    msg["Message-ID"] = "<att@example.pt>"
    msg["Date"] = "Wed, 27 May 2026 09:00:00 +0100"
    msg.set_content(body)
    for name, ctype, data in attachments:
        maintype, _, subtype = ctype.partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return msg.as_bytes()


def test_huge_pixel_image_is_dropped_even_though_its_byte_size_is_normal():
    data = _png(17975, 15776)                        # 283.6 MP — what Vertex rejects with a 400
    assert 20_000 < len(data) < 6_000_000            # ...yet it sails through the byte filter
    media = attachment_media(_eml_with([("portico.png", "image/png", data)]))
    assert media["images"] == []


def test_normal_large_drawing_still_passes_the_pixel_filter():
    """Guard against over-blocking: a real 3000x2000 drawing must survive."""
    data = _png(3000, 2000)
    raw = _eml_with([("desenho.png", "image/png", data)])
    assert [im["filename"] for im in attachment_media(raw)["images"]] == ["desenho.png"]
    # ...and the pixel probe is genuinely reading THIS image's header, not passing everything through:
    assert attachment_media(raw, max_image_pixels=1_000)["images"] == []
    assert attachment_media(raw, max_image_side=1_000)["images"] == []


def test_svg_attachment_arrives_as_text_not_dropped_by_the_byte_floor():
    """An SVG is machine-readable geometry and is far smaller than min_image_bytes — the image path
    silently discarded the most precise drawing in the email (real case: portico.svg, 580 B)."""
    assert len(SVG_SRC) < 20_000                     # below min_image_bytes: the old image path binned it
    media = attachment_media(_eml_with([("portico.svg", "image/svg+xml", SVG_SRC)]))
    assert media["images"] == []
    assert [t["filename"] for t in media["texts"]] == ["portico.svg"]
    assert "<svg" in media["texts"][0]["text"] and "corten" in media["texts"][0]["text"]


def test_svg_text_is_truncated_to_max_svg_chars():
    media = attachment_media(_eml_with([("portico.svg", "image/svg+xml", SVG_SRC)]), max_svg_chars=40)
    assert len(media["texts"][0]["text"]) == 40


@pytest.mark.parametrize("data,expected", [
    (_png(17975, 15776, filler=64), (17975, 15776)),
    (_png(3000, 2000, filler=64), (3000, 2000)),
    (_gif(640, 480), (640, 480)),
    (_jpeg(1920, 1080), (1920, 1080)),
    (b"not an image at all, just bytes", None),
    (b"", None),
    (b"\x89PNG\r\n\x1a\n" + b"\x00" * 4, None),      # truncated PNG: no IHDR -> unmeasurable, not a guess
])
def test_image_size_reads_the_header_only(data, expected):
    assert _image_size(data) == expected


# ── imageNNN.png: signature logo vs. the client's product photo ──────────────────────────────────
#
# Outlook renames EVERY inline image to imageNNN.ext, so the name cannot tell a logo from job content.
# Filtering on bytes alone dropped a 235x240 / 74 KB photo of an embroidered cushion — the entire
# subject of the "almofadas bordadas" lead (p-0003). Density and aspect are what actually separate
# them on the real corpus; the numbers below are measured from it.


def test_signature_logo_is_still_dropped():
    """The recurring Outlook logo: 4322x4320 at 177 KB = 0.01 bytes/px. No photo compresses like that."""
    data = _png(4322, 4320, filler=176_000)
    media = attachment_media(_eml_with([("image003.png", "image/png", data)]))
    assert media["images"] == []


def test_signature_banner_is_still_dropped_by_aspect():
    """A wide strip (874x89, 9.8:1) is a signature banner — dense, but no drawing has that shape."""
    data = _png(874, 89, filler=63_000)
    assert len(data) / (874 * 89) > 0.5                  # dense enough that density alone would keep it
    assert attachment_media(_eml_with([("image001.png", "image/png", data)]))["images"] == []


def test_animated_signature_banner_cannot_inflate_its_density():
    """A 102-frame 500x234 banner scores 1.61 B/px whole-file but 0.016 per frame — it is a logo."""
    frames = b"\x00\x21\xf9\x04" * 102
    data = _gif(500, 234, filler=0) + frames + b"\x00" * (188_601 - 6 - 4 - len(frames))
    assert len(data) / (500 * 234) > 0.5                 # whole-file density alone would keep it
    assert attachment_media(_eml_with([("image012.gif", "image/gif", data)]))["images"] == []


def test_single_frame_dense_gif_is_still_kept():
    """Guard against over-blocking GIFs: the frame divisor must not bin a real one-frame image."""
    data = _gif(235, 240, filler=74_000)
    assert [im["filename"] for im in
            attachment_media(_eml_with([("image001.gif", "image/gif", data)]))["images"]] == ["image001.gif"]


def test_product_photo_named_like_a_signature_logo_is_kept():
    """The p-0003 regression: 235x240 / 74 KB cushion photo, named image001.png by Outlook."""
    data = _png(235, 240, filler=74_000)
    assert len(data) < 200_000                           # under the old byte escape hatch, so it was binned
    media = attachment_media(_eml_with([("image001.png", "image/png", data)]))
    assert [im["filename"] for im in media["images"]] == ["image001.png"]


def test_unmeasurable_signature_named_image_is_kept_not_guessed():
    """Consistent with _image_size: an unreadable header must not cost us a real drawing."""
    data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 30_000       # signature only, no IHDR -> unmeasurable
    assert _image_size(data) is None
    media = attachment_media(_eml_with([("image002.png", "image/png", data)]))
    assert [im["filename"] for im in media["images"]] == ["image002.png"]


def test_pypdf_is_installed():
    """pypdf is a hard dependency, and _pdf_text returns "" when it is missing — indistinguishable
    from a scanned PDF. A stale venv must fail the suite, not silently stop reading every PDF."""
    import pypdf                                          # noqa: F401


def test_missing_pypdf_is_logged_not_swallowed(monkeypatch, caplog):
    import builtins

    real_import = builtins.__import__

    def _no_pypdf(name, *a, **kw):
        if name == "pypdf":
            raise ImportError("No module named 'pypdf'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _no_pypdf)
    with caplog.at_level(logging.ERROR, logger="email2data.envelope"):
        assert _pdf_text(b"%PDF-1.4 whatever", 6_000) == ""
    assert "pypdf is not installed" in caplog.text


# corpus/ is gitignored (real client mail), so this only runs on a box that has it.
CORTEN_EML = Path(__file__).resolve().parents[1] / "corpus" / "9b44202cd390943044ed25dc86f47536.eml"
CUSHION_EML = Path(__file__).resolve().parents[1] / "corpus" / "693dfa126b47521c8f4e20a101331bc8.eml"
NCR_REPLY_EML = Path(__file__).resolve().parents[1] / "corpus" / "be19050e5d31483a12b32436cc725653.eml"


@pytest.mark.skipif(not CUSHION_EML.exists(), reason="corpus/ is gitignored — real-mail check is local-only")
def test_real_cushion_photo_reaches_the_model():
    """p-0003 end to end: the lead's only attachment must not be binned as a signature logo."""
    media = attachment_media(CUSHION_EML.read_bytes())
    assert [im["filename"] for im in media["images"]] == ["image001.png"]


@pytest.mark.skipif(not NCR_REPLY_EML.exists(), reason="corpus/ is gitignored — real-mail check is local-only")
def test_real_ncr_reply_extracts_pdf_text_and_drops_its_signature_strips():
    """p-0004: the transport-guide PDF must yield text, while the six 51x51/150x30 strips stay out."""
    media = attachment_media(NCR_REPLY_EML.read_bytes())
    assert [t["filename"] for t in media["texts"]] == ["GT 2026 000151.pdf"]
    assert "GT2026/000151" in media["texts"][0]["text"]
    assert media["images"] == []


@pytest.mark.skipif(not CORTEN_EML.exists(), reason="corpus/ is gitignored — real-mail check is local-only")
def test_real_corten_email_drops_the_283mp_png_and_keeps_the_svg():
    raw = CORTEN_EML.read_bytes()
    names = [a["filename"] for a in parse_eml(raw)["attachments"]]
    assert "portico.png" in names and "portico.svg" in names
    png = next(i for i, n in enumerate(names) if n == "portico.png")
    assert _image_size(attachment_part(raw, png)[2]) == (17975, 15776)   # the actual 400-trigger
    media = attachment_media(raw)
    assert [im["filename"] for im in media["images"]] == []              # not sent to Vertex
    assert "portico.svg" in [t["filename"] for t in media["texts"]]      # geometry still reaches the model
