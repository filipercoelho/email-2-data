"""The thread attachment funnel (ADR-046) — banding, content dedup, and index stability.

The load-bearing test in here is :func:`test_funnel_index_still_addresses_the_same_bytes`. Every
other property is recoverable; a reindexed ``src.index`` silently serves the *wrong file* under the
right name, which looks correct on screen and is only caught by comparing bytes.
"""

from __future__ import annotations

import hashlib
import json
from email.message import EmailMessage
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from conftest import signed_in_client
from email2data import attachments as att
from email2data.envelope import _attachments, attachment_part
from email2data.headers import parse_message
from email2data import webapp
from email2data.workspace import Workspace

# ── fixtures: real MIME, built by hand so every band is exercised ────────────────────────────────

PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415408d763f8cfc000000301010018dd8db00000000049454e44ae426082")


def _png(w: int, h: int, pad: int = 0) -> bytes:
    """A PNG whose IHDR declares ``w``x``h``. Only the header is ever read (``_image_size``)."""
    ihdr = b"\x00\x00\x00\rIHDR" + w.to_bytes(4, "big") + h.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + ihdr + b"\x00" * 4 + b"PAD" * pad + PNG_1x1[-12:]


def _build_eml(*, parts: list[tuple[str, str, bytes, str | None, str]], html: str = "") -> bytes:
    """``parts`` = [(filename, subtype, payload, content_id, disposition)]."""
    msg = EmailMessage()
    msg["From"] = "cliente@exemplo.pt"
    msg["To"] = "orcamentos@lindoservico.pt"
    msg["Subject"] = "Pedido"
    msg["Message-ID"] = "<funnel-test@exemplo.pt>"
    msg["Date"] = "Mon, 20 Jul 2026 10:00:00 +0100"
    msg.set_content("texto")
    if html:
        msg.add_alternative(html, subtype="html")
    for filename, subtype, payload, cid, disp in parts:
        maintype = "image" if subtype in ("png", "jpeg", "gif") else "application"
        kw: dict = {"maintype": maintype, "subtype": subtype, "filename": filename}
        if cid:
            kw["cid"] = cid
        msg.add_attachment(payload, **kw)
        if disp == "inline":
            msg.get_payload()[-1].replace_header("Content-Disposition",
                                                 f'inline; filename="{filename}"')
    return msg.as_bytes()


# ── the band rule ────────────────────────────────────────────────────────────────────────────────

def test_disposition_attachment_is_always_a_file():
    band, why = att.classify(disposition='attachment; filename="q.pdf"', cid_referenced=True,
                             content_type="application/pdf", size=10, px=None)
    assert band == att.BAND_FILES
    assert "attachment" in why


def test_an_unreferenced_part_is_a_file_even_when_it_is_a_tiny_image():
    """No ``cid:`` reference anywhere in the HTML means nothing embeds it — it is an attachment the
    sender's client simply did not mark. Binning it as a logo would hide a real file."""
    band, why = att.classify(disposition="", cid_referenced=False, content_type="image/png",
                             size=900, px=(50, 50))
    assert band == att.BAND_FILES
    assert "cid" in why


@pytest.mark.parametrize("px,size", [((900, 300), 5_000), ((600, 600), 5_000), ((10, 10), 300_000)])
def test_a_big_referenced_image_is_body_content(px, size):
    band, _ = att.classify(disposition="inline", cid_referenced=True, content_type="image/png",
                           size=size, px=px)
    assert band == att.BAND_INLINE


def test_a_small_wide_referenced_image_is_signature_art():
    """586x63 — a real signature banner from the corpus."""
    band, why = att.classify(disposition="inline", cid_referenced=True, content_type="image/jpeg",
                             size=6_000, px=(586, 63))
    assert band == att.BAND_SIGNATURE
    assert "586x63" in why


def test_the_pasted_cad_drawing_is_not_filed_as_a_signature():
    """THE calibration regression. 431x361 / 65 KB is a real CAD drawing with dimension annotations
    that appears in 10 corpus messages. It is under every size threshold (max side < 600, area <
    250 kpx, bytes < 200 KB), so the size-only rule buried it in the collapsed band — the failure
    that costs a quote. The postcard arm (aspect < 2.0, short side >= 250 px) rescues it."""
    band, why = att.classify(disposition="inline", cid_referenced=True, content_type="image/png",
                             size=65_000, px=(431, 361))
    assert band == att.BAND_INLINE, "the pasted drawing must not be collapsed as signature art"
    assert "431x361" in why


@pytest.mark.parametrize("px", [(513, 458), (410, 326), (353, 336), (277, 377), (262, 294)])
def test_other_measured_content_images_survive_the_funnel(px):
    """A bank screenshot, two order summaries with EUR totals, a RAL colour swatch and a payslip
    chart — all measured in the corpus, all previously collapsed."""
    band, _ = att.classify(disposition="inline", cid_referenced=True, content_type="image/png",
                           size=30_000, px=px)
    assert band == att.BAND_INLINE


@pytest.mark.parametrize("px", [(205, 278), (181, 228), (185, 83), (134, 60)])
def test_measured_signature_cards_stay_collapsed(px):
    """The Lindo Serviço signature cards and small logos — the band must stay clean, or collapsing
    it buys nothing."""
    band, _ = att.classify(disposition="inline", cid_referenced=True, content_type="image/jpeg",
                           size=9_000, px=px)
    assert band == att.BAND_SIGNATURE


def test_a_referenced_non_image_is_never_signature_art():
    """A ``cid:``-referenced PDF is a document someone embedded, not a logo. The corpus has none
    today; this pins that the first one is not filed away."""
    band, why = att.classify(disposition="inline", cid_referenced=True,
                             content_type="application/pdf", size=5_000, px=None)
    assert band == att.BAND_FILES
    assert "não é imagem" in why


def test_every_band_carries_evidence():
    """A band is an INFERENCE; one that cannot show its reason is a guess with better manners."""
    for kwargs in (
        dict(disposition="attachment", cid_referenced=False, content_type="application/pdf",
             size=1, px=None),
        dict(disposition="inline", cid_referenced=True, content_type="image/png",
             size=1, px=(800, 800)),
        dict(disposition="inline", cid_referenced=True, content_type="image/png",
             size=1, px=(100, 40)),
    ):
        band, why = att.classify(**kwargs)
        assert band in att.BANDS
        assert why and why.strip()


# ── dedup by content, never by name ──────────────────────────────────────────────────────────────

def test_same_name_different_bytes_stays_two_items():
    """The measured case: one corpus thread carries ``composition.pdf`` at 154 KB and at 152 KB.
    A filename key merges them and one real document disappears."""
    parts_a = [{"index": 0, "name": "composition.pdf", "type": "application/pdf", "kind": "pdf",
                "size": 154_000, "px": None, "band": att.BAND_FILES, "band_evidence": "x",
                "sha": hashlib.sha256(b"A").hexdigest(), "cid": ""}]
    parts_b = [{"index": 0, "name": "composition.pdf", "type": "application/pdf", "kind": "pdf",
                "size": 152_000, "px": None, "band": att.BAND_FILES, "band_evidence": "x",
                "sha": hashlib.sha256(b"B").hexdigest(), "cid": ""}]
    items = att.fold_thread([
        {"message_id": "m1", "date": "2026-07-01", "from_email": "a@b.pt", "parts": parts_a},
        {"message_id": "m2", "date": "2026-07-02", "from_email": "a@b.pt", "parts": parts_b},
    ])
    assert len(items) == 2, "two different documents sharing a filename must stay two items"


def test_same_bytes_different_names_collapses_to_one_with_a_copy_count():
    sha = hashlib.sha256(b"same").hexdigest()
    mk = lambda name: [{"index": 0, "name": name, "type": "image/png", "kind": "image",  # noqa: E731
                        "size": 10, "px": None, "band": att.BAND_FILES, "band_evidence": "x",
                        "sha": sha, "cid": ""}]
    items = att.fold_thread([
        {"message_id": "m1", "date": "2026-07-01", "from_email": "a@b.pt", "parts": mk("image001.png")},
        {"message_id": "m2", "date": "2026-07-02", "from_email": "c@d.pt", "parts": mk("renamed.png")},
    ])
    assert len(items) == 1
    assert items[0]["n_copies"] == 2
    assert items[0]["src"]["message_id"] == "m1", "the FIRST carrier owns the byte link"
    assert items[0]["first_seen"] == "2026-07-01"


def test_zero_byte_parts_are_never_merged_and_get_unique_ids():
    """``get_payload(decode=True)`` yields nothing for a message/rfc822 sub-part. Those have no
    hash — folding them onto one another would silently bin attachments."""
    mk = lambda i: [{"index": i, "name": "", "type": "message/rfc822", "kind": "mail",  # noqa: E731
                     "size": 0, "px": None, "band": att.BAND_FILES, "band_evidence": "x",
                     "sha": "", "cid": ""}]
    items = att.fold_thread([
        {"message_id": "m1", "date": "d", "from_email": "a@b.pt", "parts": mk(0) + mk(1)},
    ])
    assert len(items) == 2
    ids = {i["id"] for i in items}
    assert len(ids) == 2 and "" not in ids


# ── index stability: the one thing not to get wrong ──────────────────────────────────────────────

def test_message_parts_indexes_exactly_like_attachment_part():
    """``message_parts`` must walk under the same predicate and counter as ``attachment_part``.
    Any band-driven filter that reindexes repoints every 📎 link at a different file — and it looks
    perfectly fine on screen, which is why this compares BYTES."""
    raw = _build_eml(
        html='<img src="cid:logo"><img src="cid:draw">',
        parts=[
            ("quote.pdf", "pdf", b"%PDF-1.4 quote", None, "attachment"),
            ("logo.png", "png", _png(120, 40), "logo", "inline"),
            ("draw.png", "png", _png(431, 361, pad=200), "draw", "inline"),
            ("orphan.png", "png", _png(50, 50), None, "inline"),
        ])
    parts = att.message_parts(raw)
    assert len(parts) == len(_attachments(parse_message(raw))), "the two walks disagree on COUNT"
    assert [p["band"] for p in parts] != [att.BAND_FILES] * 4, "fixture must exercise >1 band"
    for p in parts:
        served = attachment_part(raw, p["index"])
        assert served is not None, f"index {p['index']} does not resolve"
        assert hashlib.sha256(served[2]).hexdigest() == p["sha"], (
            f"index {p['index']} ({p['name']}) serves DIFFERENT bytes — the funnel reindexed")


def test_banding_does_not_reorder_the_walk():
    """The signature image sits between two files; its index must remain 1, not be pushed to the end
    by its band."""
    raw = _build_eml(
        html='<img src="cid:sig">',
        parts=[("a.pdf", "pdf", b"%PDF a", None, "attachment"),
               ("sig.png", "png", _png(180, 60), "sig", "inline"),
               ("b.pdf", "pdf", b"%PDF b", None, "attachment")])
    parts = att.message_parts(raw)
    assert [p["index"] for p in parts] == [0, 1, 2]
    assert parts[1]["band"] == att.BAND_SIGNATURE
    assert attachment_part(raw, 1)[0] == "sig.png"


# ── the endpoint + the wired thread API ──────────────────────────────────────────────────────────

def _app(tmp_path, corpus_index, crm_store=None):
    ws = Workspace(tmp_path / "w.db").connect()
    app = webapp.create_app({"llm": {}}, workspace=ws, jobspecs={}, reply_pb="pb",
                            prepared=([], [], {}), corpus_index=corpus_index,
                            crm_store=crm_store)
    return signed_in_client(TestClient(app), ws)


def test_attachment_endpoint_handles_a_message_id_containing_a_slash(tmp_path):
    """Outlook Message-IDs routinely contain ``/``. The route was a plain ``{message_id}/{index}``
    pair, so the ASGI server's percent-decoding turned the client's ``%2F`` back into a separator and
    the extra segment 404'd — **201 of 1039** corpus attachment links were dead, and it read as
    missing data rather than a routing bug."""
    eml = tmp_path / "m.eml"
    eml.write_bytes(_build_eml(parts=[("q.pdf", "pdf", b"%PDF payload", None, "attachment")]))
    mid = "mid:!&!aaa/bbb+ccc=@lindoservico.pt"
    assert "/" in mid
    c = _app(tmp_path, {mid: eml})
    r = c.get(f"/api/attachment/{quote(mid, safe='')}/0")
    assert r.status_code == 200, "an attachment on a slash-bearing Message-ID must be reachable"
    assert b"%PDF payload" in r.content


def test_attachment_endpoint_survives_a_non_ascii_filename(tmp_path):
    """``Comprovativo Pag. Lindo Serviço.pdf`` — in a pt-PT shop the accented filename is the common
    case, not the edge. A raw non-ASCII value in Content-Disposition goes out as latin-1 and is not
    valid UTF-8; RFC 6266's ``filename*`` carries it intact."""
    eml = tmp_path / "m.eml"
    eml.write_bytes(_build_eml(
        parts=[("Comprovativo Pag. Lindo Serviço.pdf", "pdf", b"%PDF x", None, "attachment")]))
    c = _app(tmp_path, {"m1": eml})
    r = c.get("/api/attachment/m1/0")
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    assert "filename*=UTF-8''" in cd and "Servi%C3%A7o" in cd
    assert cd.isascii(), "the header must be ASCII-safe on the wire"


def test_thread_api_returns_a_banded_deduped_funnel(tmp_path):
    """End to end: the funnel block exists, is deduped by content ACROSS messages, spans all three
    bands, and every item addresses its own bytes through the public endpoint."""
    from email2data.crm import CrmStore
    drawing = _png(431, 361, pad=300)
    files, index = {}, {}
    for n in (1, 2):
        m = EmailMessage()
        m["From"] = "cliente@acme.pt"
        m["To"] = "orcamentos@lindoservico.pt"
        m["Subject"] = "Pedido"
        m["Date"] = f"Mon, 2{n} Jul 2026 10:00:00 +0100"
        m.set_content("texto")
        m.add_alternative('<img src="cid:draw"><img src="cid:sig">', subtype="html")
        m.add_attachment(drawing, maintype="image", subtype="png", filename="desenho.png", cid="draw")
        m.get_payload()[-1].replace_header("Content-Disposition", 'inline; filename="desenho.png"')
        m.add_attachment(_png(180, 60), maintype="image", subtype="png", filename="logo.png", cid="sig")
        m.get_payload()[-1].replace_header("Content-Disposition", 'inline; filename="logo.png"')
        m.add_attachment(b"%PDF quote", maintype="application", subtype="pdf",
                         filename="orcamento.pdf")
        f = tmp_path / f"m{n}.eml"
        f.write_bytes(m.as_bytes())
        files[f"m{n}"] = f
        index[f"m{n}"] = f

    crm = CrmStore(tmp_path / "crm.db").connect()
    verdict = {"counterparty": "CLIENT", "purpose": "PO_FROM_CLIENT", "priority": "HIGH",
               "direction": "inbound", "entities": {}}
    crm.record({"message_id": "m1", "from": {"email": "cliente@acme.pt"}, "to": [], "cc": [],
                "subject": "Pedido", "date": "2026-07-21T09:00:00", "references": [],
                "attachments": [{"filename": "desenho.png"}]}, verdict)
    crm.record({"message_id": "m2", "from": {"email": "cliente@acme.pt"}, "to": [], "cc": [],
                "subject": "Re: Pedido", "date": "2026-07-22T09:00:00", "references": ["m1"],
                "attachments": [{"filename": "desenho.png"}]}, verdict)

    c = _app(tmp_path, index, crm_store=crm)
    r = c.get("/api/thread/" + quote("mid:m1", safe=""))
    assert r.status_code == 200, r.text[:300]
    block = r.json()["attachments"]
    bands = {i["band"] for i in block["items"]}
    assert bands == {att.BAND_FILES, att.BAND_INLINE, att.BAND_SIGNATURE}, bands

    draw = [i for i in block["items"] if i["name"] == "desenho.png"]
    assert len(draw) == 1, "the same drawing in both messages must fold to ONE item"
    assert draw[0]["n_copies"] == 2
    assert draw[0]["band"] == att.BAND_INLINE, "the pasted drawing must not be collapsed"
    assert block["counts"][att.BAND_SIGNATURE] == 1

    for item in block["items"]:
        src = item["src"]
        br = c.get(f"/api/attachment/{quote(src['message_id'], safe='')}/{src['index']}")
        assert br.status_code == 200, f"{item['name']} does not resolve"
        assert hashlib.sha256(br.content).hexdigest()[:16] == item["id"], (
            f"{item['name']} serves DIFFERENT bytes than the funnel indexed")


# ── the shared UI kit ────────────────────────────────────────────────────────────────────────────

def test_the_funnel_ui_lives_in_the_shared_kit_not_in_one_lens():
    """Para Ti and Projetos must INHERIT the funnel. If a lens grows its own copy, the next fix
    lands in one place and silently misses the others."""
    from email2data import cockpit_ui, fila_page, para_ti_page, projetos_page
    kit = cockpit_ui._SHELL_UTILS      # the shared kit both lenses import
    assert "function attFunnelHTML(" in kit
    assert "function attMerge(" in kit
    for mod, name in ((fila_page, "fila"), (para_ti_page, "para_ti"), (projetos_page, "projetos")):
        src = "".join(v for v in vars(mod).values() if isinstance(v, str))
        assert "function attFunnelHTML(" not in src, f"{name} forked the shared funnel renderer"
    assert "attFunnelHTML(" in fila_page._LENS_JS, "the Fila must render the funnel"


def test_the_signature_band_is_collapsed_but_its_count_is_visible():
    """«Never silently bin» — the third band is one click away, and the human can SEE there is
    something behind it."""
    from email2data import cockpit_ui
    kit = cockpit_ui._SHELL_UTILS      # the shared kit both lenses import
    funnel = kit.split("function attFunnelHTML(")[1].split("\nfunction ")[0]
    assert "<details" in funnel, "the signature band must be collapsed, not dropped"
    assert "sig.length" in funnel, "…and its count must be rendered in the summary"


def test_previews_are_lazy_and_size_gated():
    from email2data import cockpit_ui
    kit = cockpit_ui._SHELL_UTILS      # the shared kit both lenses import
    tile = kit.split("function _attTile(")[1].split("\nfunction ")[0]
    assert 'loading="lazy"' in tile
    assert "it.preview" in tile, "the SERVER decides what is previewable; the client must not guess"
    assert att.PREVIEW_MAX_BYTES == 262_144


def test_an_empty_body_copy_is_kept_when_it_carries_different_bytes_under_the_same_name(tmp_path):
    """The message dedup keeps an empty-body Trash copy only when it carries attachments no other
    card already shows. That set was keyed by FILENAME, so a second, *different* document sharing a
    name was judged "already covered" and its whole message card vanished. Measured on the corpus:
    220 name-sharing pairs differ in bytes. Keyed by content hash, the card survives."""
    from email2data.crm import CrmStore

    def eml(body: str, payload: bytes, date: str) -> bytes:
        m = EmailMessage()
        m["From"] = "cliente@acme.pt"
        m["To"] = "orcamentos@lindoservico.pt"
        m["Subject"] = "Pedido"
        m["Date"] = date
        m.set_content(body)
        m.add_attachment(payload, maintype="application", subtype="pdf",
                         filename="composition.pdf")
        return m.as_bytes()

    index = {}
    for n, (body, payload) in enumerate(
            [("texto visivel do pedido", b"%PDF-AAAA"), ("", b"%PDF-BBBB")], start=1):
        f = tmp_path / f"m{n}.eml"
        f.write_bytes(eml(body, payload, f"Mon, 2{n} Jul 2026 10:00:00 +0100"))
        index[f"m{n}"] = f

    crm = CrmStore(tmp_path / "crm.db").connect()
    verdict = {"counterparty": "CLIENT", "purpose": "PO_FROM_CLIENT", "priority": "HIGH",
               "direction": "inbound", "entities": {}}
    crm.record({"message_id": "m1", "from": {"email": "cliente@acme.pt"}, "to": [], "cc": [],
                "subject": "Pedido", "date": "2026-07-21T09:00:00", "references": [],
                "attachments": [{"filename": "composition.pdf"}]}, verdict)
    crm.record({"message_id": "m2", "from": {"email": "cliente@acme.pt"}, "to": [], "cc": [],
                "subject": "Pedido", "date": "2026-07-22T09:00:00", "references": ["m1"],
                "attachments": [{"filename": "composition.pdf"}]}, verdict)

    c = _app(tmp_path, index, crm_store=crm)
    d = c.get("/api/thread/" + quote("mid:m1", safe="")).json()
    shown = {a["sha"] for m in d["messages"] for a in (m.get("attachments") or [])}
    assert len(shown) == 2, "the second, DIFFERENT composition.pdf was dropped by a filename key"
    # and the funnel lists both as separate items, never merged
    names = [i["name"] for i in d["attachments"]["items"]]
    assert names.count("composition.pdf") == 2


def test_flat_line_art_is_not_demoted_out_of_the_visible_band():
    """The duck guard. A 2437x2441 CAD cut drawing with red annotations weighs 28 KB — **0.0117
    bytes per pixel**, which is LOWER than the Instagram icon (0.0095-0.047) that sits in the same
    band. Line art and logo art compress identically, so any density-based demotion added later
    buries exactly the drawing this funnel exists to surface. If you are here because you added one:
    re-run the duck first (see the module docstring)."""
    band, _ = att.classify(disposition="inline", cid_referenced=True, content_type="image/png",
                           size=28_000, px=(2437, 2441))
    assert band == att.BAND_INLINE

    # …and the supplier catalogue photo that an anchor-wrapping rule would have dropped
    band, _ = att.classify(disposition="inline", cid_referenced=True, content_type="image/jpeg",
                           size=137_000, px=(700, 519))
    assert band == att.BAND_INLINE


def test_flat_logo_art_sorts_below_real_content_inside_a_band():
    """No demotion arm ships, so the leak is bounded by ORDER instead: largest-first inside a band
    puts the 4 MB screenshot above the 11 KB social icon."""
    def part(sha, size, name):
        return {"index": 0, "name": name, "type": "image/png", "kind": "image", "size": size,
                "px": [1280, 1280], "band": att.BAND_INLINE, "band_evidence": "x", "sha": sha,
                "cid": "c"}
    items = att.fold_thread([{"message_id": "m1", "date": "d", "from_email": "a@b.pt",
                              "parts": [part("a" * 64, 11_000, "icon.png"),
                                        part("b" * 64, 4_000_000, "screenshot.png")]}])
    assert [i["name"] for i in items] == ["screenshot.png", "icon.png"]


# ── the branding register (ADR-048) ──────────────────────────────────────────────────────────────
#
# ADR-046's bands read ONE part in isolation, which is why a 1280x1280 Facebook icon sat in the
# visible band: nothing about those bytes says "logo". ADR-048 adds the one signal that is not a
# per-part guess — how many unrelated threads the same bytes ride into. These tests pin the measured
# gap (content tops out at 2 threads, branding starts at 5), the omission itself, and above all that
# omitting an item does NOT shift the positional indices the 📎 chips are built from.

def _own_domain_eml(mid: str, *, images: list[tuple[str, bytes]],
                    sender: str = "orcamentos@lindoservico.pt", attach: bytes | None = None) -> bytes:
    """A message from us with ``images`` pasted into the HTML body (so they are cid:-referenced)."""
    m = EmailMessage()
    m["From"] = sender
    m["To"] = "cliente@acme.pt"
    m["Subject"] = "Orçamento"
    m["Message-ID"] = f"<{mid}>"
    m["Date"] = "Mon, 20 Jul 2026 10:00:00 +0100"
    m.set_content("texto")
    m.add_alternative("".join(f'<img src="cid:{n}">' for n, _ in images), subtype="html")
    for name, payload in images:
        m.add_attachment(payload, maintype="image", subtype="png", filename=f"{name}.png", cid=name)
        m.get_payload()[-1].replace_header("Content-Disposition", f'inline; filename="{name}.png"')
    if attach is not None:
        m.add_attachment(attach, maintype="application", subtype="pdf", filename="orcamento.pdf")
    return m.as_bytes()


def test_only_inline_images_we_sent_are_eligible_for_the_register():
    """Scope, three ways. The register observes art LINDO attaches, so an external sender is out
    entirely; a real attached document is out (it is FICHEIROS — someone chose to send it); and a
    cid:-referenced non-image is out (a PDF embedded in a body is a document, never a logo)."""
    ours = _own_domain_eml("a@lindoservico.pt", images=[("logo", _png(180, 60))],
                           attach=b"%PDF quote")
    got = att.register_candidates(ours, sender="orcamentos@lindoservico.pt")
    assert [c["name"] for c in got] == ["logo.png"], "the attached PDF must not enter the register"

    assert att.register_candidates(ours, sender="compras@fornecedor.pt") == [], \
        "a supplier's signature art is not ours to omit — the decision is scoped to our own domain"
    assert att.register_candidates(ours, sender="") == []


def test_a_subdomain_of_ours_is_still_ours_and_a_lookalike_is_not():
    assert att.is_own_domain("pedro@lindoservico.pt")
    assert att.is_own_domain("Pedro.Ferreira@MAIL.lindoservico.pt")
    assert not att.is_own_domain("spoof@notlindoservico.pt"), \
        "endswith without the dot would swallow any domain ending in ours"
    assert not att.is_own_domain("nobody")


def test_the_register_threshold_sits_in_the_measured_gap():
    """The corpus measurement, as arithmetic. Real content maxes out at 2 distinct threads (the
    cotton-bag product photo); the narrowest-spread branding art is the animated footer at 5. The
    threshold has to fall strictly between, or one side of the trade breaks."""
    spread = {"cotton_bag": 2, "cad_drawing": 1, "press_kit": 1,
              "footer_gif": 5, "facebook_icon": 41, "lindo_wordmark": 41}
    hidden = att.branding_shas(spread)
    assert hidden == {"footer_gif", "facebook_icon", "lindo_wordmark"}
    assert 2 < att.BRANDING_MIN_THREADS <= 5, (
        f"BRANDING_MIN_THREADS={att.BRANDING_MIN_THREADS} leaves the measured gap: content was seen "
        "in up to 2 threads, branding in 5 or more")
    assert att.branding_shas({}) == set(), "no register built yet must hide nothing"


def _part(sha: str, *, index: int, name: str, size: int = 40_000, band: str | None = None) -> dict:
    return {"index": index, "name": name, "type": "image/png", "kind": "image", "size": size,
            "px": [400, 300], "band": band or att.BAND_INLINE, "band_evidence": "x",
            "sha": sha, "cid": "c"}


def test_recurring_branding_art_is_omitted_from_the_funnel_entirely():
    """Not collapsed, not counted — gone. ADR-048 narrowed ADR-046's "nothing is ever dropped" on
    purpose, so the count must not leak the omission back into the UI either."""
    parts = [_part("a" * 64, index=0, name="desenho.png"),
             _part("b" * 64, index=1, name="image001.png", band=att.BAND_SIGNATURE),
             _part("c" * 64, index=2, name="image002.png")]
    msgs = [{"message_id": "m1", "date": "2026-07-20", "from_email": "orcamentos@lindoservico.pt",
             "parts": parts}]

    before = att.fold_thread(msgs)
    assert len(before) == 3 and att.band_counts(before)[att.BAND_SIGNATURE] == 1

    after = att.fold_thread(msgs, branding={"b" * 64, "c" * 64})
    assert [i["name"] for i in after] == ["desenho.png"]
    counts = att.band_counts(after)
    assert counts[att.BAND_SIGNATURE] == 0 and counts[att.BAND_INLINE] == 1, \
        "an omitted item must not survive as a count"


def test_omitting_an_item_does_not_shift_the_indices_the_chips_address():
    """The load-bearing one. The per-message 📎 chips are POSITIONAL against
    ``/api/attachment/{message_id}/{index}``, so an item removed before that counter increments
    repoints every later link at the wrong file — and it looks perfect on screen. The surviving
    items must keep the ``index`` the walk gave them, holes and all."""
    parts = [_part("a" * 64, index=0, name="logo.png"),
             _part("b" * 64, index=1, name="desenho.png"),
             _part("c" * 64, index=2, name="icon.png"),
             _part("d" * 64, index=3, name="foto.png")]
    items = att.fold_thread(
        [{"message_id": "m1", "date": "d", "from_email": "orcamentos@lindoservico.pt",
          "parts": parts}], branding={"a" * 64, "c" * 64})
    by_name = {i["name"]: i["src"]["index"] for i in items}
    assert by_name == {"desenho.png": 1, "foto.png": 3}, (
        "surviving items were renumbered — every 📎 link on the message card now serves the "
        "wrong bytes under the right name")


def test_an_omitted_hash_is_omitted_whoever_forwarded_it_back():
    """The hash identifies the artefact, not the sender. A supplier quoting our mail carries our
    logo in their reply; it is still our logo. Matching on the sender here would leave the icon
    visible on exactly the threads with the most forwarding."""
    items = att.fold_thread(
        [{"message_id": "m1", "date": "d", "from_email": "compras@fornecedor.pt",
          "parts": [_part("a" * 64, index=0, name="image001.png"),
                    _part("b" * 64, index=1, name="peça.png")]}],
        branding={"a" * 64})
    assert [i["name"] for i in items] == ["peça.png"]


def test_the_funnel_omits_recurring_art_end_to_end_and_keeps_the_drawing(tmp_path):
    """Through ``/api/thread``, with the register in the real crm.db: the recurring logo is absent
    from the payload while the postcard-shaped drawing that shares its message stays visible."""
    from email2data.crm import CrmStore
    logo, drawing = _png(180, 60), _png(431, 361, pad=300)
    eml = tmp_path / "m1.eml"
    eml.write_bytes(_own_domain_eml("m1@lindoservico.pt",
                                    images=[("sig", logo), ("draw", drawing)]))
    logo_sha = hashlib.sha256(logo).hexdigest()

    crm = CrmStore(tmp_path / "crm.db").connect()
    crm.record({"message_id": "m1", "from": {"email": "orcamentos@lindoservico.pt"}, "to": [],
                "cc": [], "subject": "Orçamento", "date": "2026-07-20T09:00:00",
                "references": [], "attachments": []},
               {"counterparty": "CLIENT", "purpose": "OUTBOUND_QUOTE", "priority": "HIGH",
                "direction": "outbound", "entities": {}})
    # The logo rode into three unrelated threads; the drawing into one.
    crm.write_asset_spread({
        logo_sha: {"threads": {"t1", "t2", "t3"}, "messages": {"m1", "m2", "m3"},
                   "sample_name": "image001.png", "px": "180x60", "size": len(logo)},
        hashlib.sha256(drawing).hexdigest(): {"threads": {"t1"}, "messages": {"m1"},
                                             "sample_name": "image002.png", "px": "431x361",
                                             "size": len(drawing)},
    })

    c = _app(tmp_path, {"m1": eml}, crm_store=crm)
    block = c.get("/api/thread/" + quote("mid:m1", safe="")).json()["attachments"]
    names = [i["name"] for i in block["items"]]
    assert "draw.png" in names, "the drawing must survive — it lives in one conversation"
    assert "sig.png" not in names, "the recurring logo must be gone from the payload"
    assert logo_sha[:16] not in {i["id"] for i in block["items"]}
    assert block["counts"][att.BAND_SIGNATURE] == 0

    # …and the surviving item still serves ITS OWN bytes through the public endpoint.
    item = [i for i in block["items"] if i["name"] == "draw.png"][0]
    br = c.get(f"/api/attachment/{quote(item['src']['message_id'], safe='')}/{item['src']['index']}")
    assert br.status_code == 200
    assert hashlib.sha256(br.content).hexdigest()[:16] == item["id"]


def test_the_per_message_chips_still_list_every_part(tmp_path):
    """ADR-048 scopes the omission to the AGGREGATE view, which is what was asked for. The message
    card's own chips are index-aligned with the byte endpoint, so they keep every part — and that is
    also the last way to reach a wrongly-omitted file from the UI."""
    from email2data.crm import CrmStore
    logo = _png(180, 60)
    eml = tmp_path / "m1.eml"
    eml.write_bytes(_own_domain_eml("m1@lindoservico.pt", images=[("sig", logo)]))
    crm = CrmStore(tmp_path / "crm.db").connect()
    crm.record({"message_id": "m1", "from": {"email": "orcamentos@lindoservico.pt"}, "to": [],
                "cc": [], "subject": "Orçamento", "date": "2026-07-20T09:00:00",
                "references": [], "attachments": []},
               {"counterparty": "CLIENT", "purpose": "OUTBOUND_QUOTE", "priority": "HIGH",
                "direction": "outbound", "entities": {}})
    crm.write_asset_spread({hashlib.sha256(logo).hexdigest():
                            {"threads": {"t1", "t2", "t3"}, "messages": {"m1", "m2", "m3"},
                             "sample_name": "image001.png", "px": "180x60", "size": len(logo)}})
    c = _app(tmp_path, {"m1": eml}, crm_store=crm)
    d = c.get("/api/thread/" + quote("mid:m1", safe="")).json()
    assert d["attachments"]["items"] == []
    chips = [a["name"] for m in d["messages"] for a in (m.get("attachments") or [])]
    assert chips == ["sig.png"], "the positional chip list must stay complete"


def test_a_funnel_with_no_register_hides_nothing(tmp_path):
    """Fail-open, and it must stay that way. A missing or pre-v6 crm.db means "no evidence", and the
    honest response to no evidence is the ADR-046 behaviour — show everything. Hiding on stale
    evidence is the failure mode this whole decision sits next to."""
    from email2data.crm import CrmStore
    eml = tmp_path / "m1.eml"
    eml.write_bytes(_own_domain_eml("m1@lindoservico.pt", images=[("sig", _png(180, 60))]))
    crm = CrmStore(tmp_path / "crm.db").connect()
    crm.record({"message_id": "m1", "from": {"email": "orcamentos@lindoservico.pt"}, "to": [],
                "cc": [], "subject": "Orçamento", "date": "2026-07-20T09:00:00",
                "references": [], "attachments": []},
               {"counterparty": "CLIENT", "purpose": "OUTBOUND_QUOTE", "priority": "HIGH",
                "direction": "outbound", "entities": {}})
    crm._conn.execute("DROP TABLE asset_spread")      # a v5 crm.db, exactly
    c = _app(tmp_path, {"m1": eml}, crm_store=crm)
    block = c.get("/api/thread/" + quote("mid:m1", safe="")).json()["attachments"]
    assert [i["name"] for i in block["items"]] == ["sig.png"]


# ── the project-wide file list: «Ficheiros» (ADR-052) ────────────────────────────────────────────
#
# Everything above is one thread's funnel. A PROJECT spans several threads and, since ADR-019, an
# intake channel that is not MIME at all. These pin the fold across both, and above all the two ways
# it can be confidently WRONG: a tile built with the array index as its options object, and a tile
# naming a sender the merge order never entitled it to name.

def _run_kit_js(body: str):
    """Execute the SHIPPED funnel kit in node, so these assert on BEHAVIOUR, not on source text.

    A grep can tell you `.map(_attTile)` is gone; only running it can tell you what the second
    argument actually is once it is.
    """
    import json as _json
    import shutil
    import subprocess

    from email2data import cockpit_ui
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available — the funnel JS can't be executed")
    kit = cockpit_ui._SHELL_UTILS
    esc = kit[kit.index("const esc="):].split("\n")[0]
    fns = kit[kit.index("const _ATT_GLYPH="):kit.index("function msgThreadHTML(")]
    r = subprocess.run([node, "-e", esc + "\n" + fns + "\n" + body],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return _json.loads(r.stdout)


def _kit_item(iid: str, **kw) -> dict:
    base = {"id": iid, "name": iid + ".pdf", "type": "application/pdf", "kind": "pdf",
            "size": 1000, "px": None, "band": att.BAND_FILES, "band_evidence": "x",
            "src": {"message_id": "m-" + iid, "index": 0}, "first_seen": "2026-07-01T09:00:00",
            "from_email": "a@acme.pt", "n_copies": 1, "preview": False}
    base.update(kw)
    return base


def test_the_tile_helper_is_never_passed_the_array_index_as_options():
    """``Array.map`` passes ``(item, index, array)``. The moment ``_attTile`` grew a second
    parameter, a bare ``lst.map(_attTile)`` started handing it the ARRAY INDEX as its options
    object — falsy for tile 0 and truthy for every tile after it. The Fila's first tile looks
    perfect and every later one sprouts a source line built from a number, which is the worst kind of
    defect: it renders. It breaks the Fila and Para Ti and NOT Projetos, so a Projetos-only check
    would have passed."""
    from email2data import cockpit_ui
    kit = cockpit_ui._SHELL_UTILS
    assert ".map(_attTile)" not in kit, (
        "a bare .map(_attTile) feeds the tile helper the array index as its options object — "
        "use .map(it=>_attTile(it,o))")
    assert kit.count("_attTile(it,o)") >= 2, "both bands must forward the SAME options object"

    # …and prove it by running it: with no options, no tile carries a source line — including the
    # ones after the first, which is exactly what the index bug got wrong.
    html = _run_kit_js(
        "const items=[%s];"
        "console.log(JSON.stringify(["
        "  attFunnelHTML({items:items,counts:{FICHEIROS:3}}),"
        "  attFunnelHTML({items:items,counts:{FICHEIROS:3}},{showSource:true})]));"
        % ",".join(json.dumps(_kit_item(x, thread_root="t1")) for x in ("aa", "bb", "cc")))
    assert "atti-src" not in html[0], "a lens that asked for no source line must get none, in ANY tile"
    assert html[1].count("atti-src") == 3, "…and when it is asked for, EVERY tile carries it"


def test_the_merge_keeps_the_chronologically_first_carrier():
    """``attMerge`` was first-BLOCK-wins, i.e. ``project_threads.added_ts`` order — whichever thread
    happened to be attached first. That was harmless only while nothing rendered a sender. A tile
    that says *«maria@acme.pt · 2026-07-01»* about a file whose first carrier was someone else is a
    confident lie, so the chronology fix is a precondition of the source line, not polish.

    Executed rather than grepped: the sort key, the direction, and the dedup order all have to agree,
    and source text cannot show that."""
    late = _kit_item("dup", from_email="tarde@acme.pt", first_seen="2026-08-20T10:00:00",
                     src={"message_id": "m-late", "index": 3}, thread_root="t-late")
    early = _kit_item("dup", from_email="cedo@acme.pt", first_seen="2026-06-01T08:00:00",
                      src={"message_id": "m-early", "index": 1}, thread_root="t-early")
    undated = _kit_item("dup", from_email="sem-data@acme.pt", first_seen="",
                        src={"message_id": "m-nodate", "index": 0}, thread_root="t-nodate")
    got = _run_kit_js(
        "console.log(JSON.stringify(attMerge([{items:[%s,%s]},{items:[%s]}]).items));"
        % (json.dumps(undated), json.dumps(late), json.dumps(early)))
    assert len(got) == 1 and got[0]["n_copies"] == 3, "the three copies must fold to one file"
    assert got[0]["from_email"] == "cedo@acme.pt", (
        "the merge kept the first BLOCK's copy, not the chronologically first carrier")
    assert got[0]["src"] == {"message_id": "m-early", "index": 1}, (
        "src must follow the winning carrier, or the byte link points at the wrong message")
    assert got[0]["thread_root"] == "t-early", "…and so must the «ver na fila» jump target"


def test_the_source_line_never_nests_an_anchor_inside_the_tile_anchor():
    """The tile IS an ``<a>``. The source line carries its own «ver na fila →» link, and an ``<a>``
    inside an ``<a>`` is invalid HTML that browsers silently un-nest — the DOM you get is not the
    string you wrote, and the file link stops covering the tile. So the source line has to be a
    sibling of the tile, never a child."""
    html = _run_kit_js(
        "console.log(JSON.stringify([_attTile(%s,{showSource:true}),_attTile(%s,{})]));"
        % (json.dumps(_kit_item("aa", thread_root="t1")), json.dumps(_kit_item("bb"))))
    with_src = html[0]
    assert with_src.startswith("<div class=\"attw\">"), "the tile must be wrapped, not nested into"
    tile = with_src[with_src.index("<a class=\"atti"):]
    tile = tile[tile.index(">") + 1:tile.index("</a>")]      # the tile anchor's CHILDREN
    assert "<a " not in tile, f"an anchor was nested inside the tile anchor: {tile!r}"
    assert "/fila?thread=" in with_src, "the jump must name the Fila in full, never the root"
    assert "atti-src" not in html[1], "no options object → no source line at all"
    # The address is its OWN shrinkable span. Found by looking at the render: as one nowrap line, a
    # real address filled the 132 px tile and pushed «ver na fila →» off the right edge — the one
    # action the line offers, clipped on every tile, and invisible to every assertion above.
    assert "atti-who" in with_src, "the address must be separately shrinkable from the jump link"
    who = with_src[with_src.index("atti-who"):with_src.index("atti-jump")]
    assert "acme.pt" in who and "ver na fila" not in who


def test_the_funnel_heading_names_the_scope_it_folded():
    """«Ficheiros da conversa» was a literal, printed over a list that in a Projetos panel spans N
    conversations. Existing one-argument callers keep the thread wording."""
    html = _run_kit_js(
        "const a={items:[%s],counts:{FICHEIROS:1}};"
        "console.log(JSON.stringify([attFunnelHTML(a),"
        "attFunnelHTML(a,{title:'Ficheiros do projeto'})]));" % json.dumps(_kit_item("aa")))
    assert "Ficheiros da conversa" in html[0] and "Ficheiros do projeto" not in html[0]
    assert "Ficheiros do projeto" in html[1] and "Ficheiros da conversa" not in html[1]


def test_the_projetos_lens_renders_the_project_wide_funnel():
    """Projetos reached the funnel only transitively, through ``msgThreadHTML`` inside «Origem» — so
    it was capped inside a 420 px scroll box and had no heading of its own. The «Ficheiros» tab calls
    the shared renderer directly, with the project title and the source line switched on."""
    from email2data import projetos_page
    js = projetos_page._LENS_JS
    assert "attFunnelHTML(" in js, "the Ficheiros panel must render the funnel itself"
    assert "title:'Ficheiros do projeto'" in js
    assert "showSource:true" in js, "a project-wide list must say which email brought each file"
    assert 'data-tab="ficheiros"' in js and "function renderFiles(" in js


def test_the_files_tab_badge_and_its_panel_are_built_from_one_object():
    """The «Rever classificação» lesson: a chip that counts one population while its destination
    lists another drifts silently. The badge and the panel are written in the same pass, from the
    same ``items`` array — so they cannot disagree, and no test needs to assert a literal count."""
    from email2data import projetos_page
    fn = projetos_page._LENS_JS.split("function renderFiles(")[1].split("\nfunction ")[0]
    assert "const items=" in fn and "const n=items.length" in fn
    assert "attFunnelHTML(c.att" in fn, "the panel renders the object the count came from"
    assert "'Ficheiros'+(n?" in fn, "…and the badge is written from that same n"
    assert "getJSON(" not in fn, "renderFiles must not fetch — it renders what loadSource cached"
    # The zero case is an ABSENT badge, never a hidden one: `.ptab-btn .bdg{display:inline-block}`
    # (0,2,0) outranks the UA's [hidden] rule (0,1,0), so a zero badge paints an empty grey pill.
    assert 'class="bdg" hidden' not in projetos_page._LENS_JS


def test_an_unreachable_thread_costs_only_that_thread():
    """``getJSON`` throws on any non-2xx and ``/api/thread`` 404s BOTH for a thread this person was
    never granted (ADR-045) and for a dangling root. The ``try`` wrapped the whole root loop, so one
    such thread lost every other thread's messages and files and printed «falhou ao carregar
    contexto» — total silent loss on a partial failure."""
    from email2data import projetos_page
    fn = projetos_page._LENS_JS.split("async function loadSource(")[1].split("\nfunction ")[0]
    loop = fn[fn.index("for(const root of roots)"):]
    assert loop.index("try{") < loop.index("await getJSON"), "the try must be INSIDE the root loop"
    assert "failed.push(root)" in loop, "a failed root is collected, not raised"
    assert "renderFiles()" in fn, "…and the files tab is painted on every exit path"
    assert "não carregou" in projetos_page._LENS_JS, "the incompleteness must be SAID, not implied"


# ── intake captures as a second source (ADR-052) ─────────────────────────────────────────────────

def _capture(cid: str, *, media: list[str], when: str = "2026-07-14T11:00:00",
             who: str = "Rita", channel: str = "telegram") -> dict:
    return {"capture_id": cid, "media_paths": media, "acquired_at": when,
            "asserted_by": who, "channel": channel}


def test_capture_media_carry_a_content_hash_so_they_join_the_dedup(tmp_path):
    """The load-bearing property. Capture media had no sha256, so the only stable handle available
    was a ``src``-derived id — which identifies a *slot*, not an artefact. The same drawing mailed by
    the client and then re-sent through Telegram would have read as two different files sitting side
    by side in a list whose entire promise is «one row per file».

    Hashing the bytes here puts captures INSIDE the ADR-046 dedup instead of beside it."""
    drawing = b"%PDF desenho da peca"
    (tmp_path / "c-1").mkdir()
    (tmp_path / "c-1" / "desenho.pdf").write_bytes(drawing)
    items = att.capture_media_items([_capture("c-1", media=["c-1/desenho.pdf"])],
                                    media_root=tmp_path)
    assert len(items) == 1
    assert items[0]["id"] == hashlib.sha256(drawing).hexdigest()[:16], (
        "a capture must be keyed by its CONTENT, or it can never fold with an identical email part")

    # …and the fold really happens: an email part carrying the same bytes has the same id.
    mailed = att.fold_thread([{"message_id": "m1", "date": "2026-07-20T09:00:00",
                               "from_email": "cliente@acme.pt",
                               "parts": [{"index": 0, "name": "desenho.pdf", "type": "application/pdf",
                                          "kind": "pdf", "size": len(drawing), "px": None,
                                          "band": att.BAND_FILES, "band_evidence": "y",
                                          "sha": hashlib.sha256(drawing).hexdigest(), "cid": ""}]}])
    assert mailed[0]["id"] == items[0]["id"]
    merged = _run_kit_js("console.log(JSON.stringify(attMerge([{items:%s},{items:%s}]).items));"
                         % (json.dumps(mailed), json.dumps(items)))
    assert len(merged) == 1, "the same drawing by two routes must be ONE file"
    assert merged[0]["n_copies"] == 2
    # The capture came first (14 Jul < 20 Jul), so it is the carrier the tile will name.
    assert merged[0]["source"] == "capture" and merged[0]["asserted_by"] == "Rita"


def test_every_capture_media_index_is_listed_not_only_the_first(tmp_path):
    """The project timeline hardcoded ``/media/0``, so a capture carrying a photo AND a drawing hid
    the drawing completely — and there was no other surface it could have appeared on."""
    (tmp_path / "c-2").mkdir()
    for n, payload in enumerate([b"\x89PNG foto", b"%PDF plano", b"DXF corte"]):
        (tmp_path / "c-2" / f"f{n}.bin").write_bytes(payload)
    items = att.capture_media_items(
        [_capture("c-2", media=[f"c-2/f{n}.bin" for n in range(3)])], media_root=tmp_path)
    assert [i["src"]["index"] for i in items] == [0, 1, 2]
    assert all(i["src"]["capture_id"] == "c-2" for i in items)

    from email2data import projetos_page
    fn = projetos_page._LENS_JS.split("function timelineHTML(")[1].split("\nfunction ")[0]
    assert "/media/0\"" not in fn and "/media/0'" not in fn, "the timeline still hardcodes index 0"
    assert "_capIndex()" in fn, "the count must come from the capture data, not from a guess"


def test_a_capture_is_never_laundered_into_looking_like_an_email_attachment(tmp_path):
    """Two sources with different properties. A capture is somebody standing in a workshop taking a
    photo — the FACT/INFERENCE rule applies unchanged, so the item has to say what it is: its own
    ``source``, its own ``band_evidence`` naming the channel, and a byte link that goes to the
    captures route rather than to a message that does not exist."""
    (tmp_path / "c-3").mkdir()
    (tmp_path / "c-3" / "foto.png").write_bytes(PNG_1x1)
    it = att.capture_media_items([_capture("c-3", media=["c-3/foto.png"])], media_root=tmp_path)[0]
    assert it["source"] == "capture"
    assert "message_id" not in it["src"] and it["src"]["capture_id"] == "c-3"
    assert "captura" in it["band_evidence"] and "telegram" in it["band_evidence"]
    assert it["band"] == att.BAND_FILES, (
        "a file a person deliberately sent belongs with the documents, not in a fourth band the "
        "corpus calibration has no evidence for")
    assert it["from_email"] == "" and it["asserted_by"] == "Rita"
    assert it["first_seen"] == "2026-07-14T11:00:00", "chronology drives the merge — it must be set"
    # …and the tile addresses the right route
    html = _run_kit_js("console.log(JSON.stringify([_attTile(%s,{showSource:true})]));"
                       % json.dumps(it))[0]
    assert 'href="/api/captures/c-3/media/0"' in html
    assert "/api/attachment/" not in html
    assert "ver na fila" not in html, "a capture has no conversation to jump to"
    assert "Rita" in html and "telegram" in html


def test_a_media_file_missing_from_the_sole_copy_store_is_listed_not_skipped(tmp_path):
    """ADR-020 says ``captures_dir`` is the sole copy. A gap in it is an incident to SEE, not a row
    to quietly drop — the same reasoning that keeps zero-byte MIME parts in the funnel. Silently
    shortening the list is how «never silently bin» dies."""
    items = att.capture_media_items([_capture("c-4", media=["c-4/desapareceu.pdf"])],
                                    media_root=tmp_path)
    assert len(items) == 1
    assert items[0]["missing"] is True and items[0]["size"] == 0
    assert "em falta" in items[0]["band_evidence"]
    assert items[0]["preview"] is False
    assert not items[0]["id"].startswith(tuple("0123456789abcdef")), (
        "a hashless id must use a prefix OUTSIDE the hex alphabet, or it can collide with a real "
        "sha256 prefix and silently merge two different files")


def test_media_paths_cannot_escape_the_captures_directory(tmp_path):
    """The same traversal guard the bytes route applies. Reading is a smaller blast radius than
    writing, but ``media_paths`` is a JSON column and the hash is computed from whatever it names."""
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"nao me leias")
    root = tmp_path / "captures"
    root.mkdir()
    items = att.capture_media_items([_capture("c-5", media=["../secret.txt", "/etc/hosts"])],
                                    media_root=root)
    assert [i["missing"] for i in items] == [True, True]
    assert all(i["size"] == 0 for i in items)


def test_capture_media_reaches_the_project_file_list(tmp_path):
    """End to end: a capture applied to a project shows up in the project's file endpoint, in the
    ADR-046 funnel shape the client merges. «All communications» means email AND intake."""
    from email2data.captures import CaptureStore
    caps = tmp_path / "captures"
    (caps / "c-9").mkdir(parents=True)
    (caps / "c-9" / "peca.pdf").write_bytes(b"%PDF a peca")
    ws = Workspace(tmp_path / "w.db").connect()
    cstore = CaptureStore(ws._conn)
    cstore.add(telegram_message_id=9, telegram_chat_id=1, raw_text="foto da peça",
               media_paths=["c-9/peca.pdf"], channel="telegram", asserted_by="Rita")
    app = webapp.create_app({"llm": {}}, workspace=ws, jobspecs={}, reply_pb="pb",
                            prepared=([], [], {}), capture_store=cstore, captures_dir=caps)
    c = signed_in_client(TestClient(app), ws)
    pid = c.post("/api/projects", json={"title": "Peça Acme"}).json()["project_id"]
    c.post("/api/captures/c-1-9/apply", json={"project_id": pid, "kind": "note"})

    d = c.get(f"/api/projects/{pid}/captures").json()
    assert d["n_captures"] == 1
    assert [i["name"] for i in d["items"]] == ["peca.pdf"]
    assert d["counts"][att.BAND_FILES] == 1
    assert d["items"][0]["id"] == hashlib.sha256(b"%PDF a peca").hexdigest()[:16]
    # the byte link the tile builds actually serves those bytes
    src = d["items"][0]["src"]
    r = c.get(f"/api/captures/{src['capture_id']}/media/{src['index']}")
    assert r.status_code == 200 and r.content == b"%PDF a peca"


def test_a_project_with_no_captures_returns_an_empty_block_not_an_error(tmp_path):
    """The client merges this unconditionally; a 404 or a 500 here would blank the whole file list
    for the 10 of 13 live projects that have no capture at all."""
    from email2data.captures import CaptureStore
    ws = Workspace(tmp_path / "w.db").connect()
    app = webapp.create_app({"llm": {}}, workspace=ws, jobspecs={}, reply_pb="pb",
                            prepared=([], [], {}), capture_store=CaptureStore(ws._conn),
                            captures_dir=tmp_path / "captures")
    c = signed_in_client(TestClient(app), ws)
    pid = c.post("/api/projects", json={"title": "Vazio"}).json()["project_id"]
    r = c.get(f"/api/projects/{pid}/captures")
    assert r.status_code == 200
    assert r.json() == {"items": [], "counts": {b: 0 for b in att.BANDS},
                        "bands": list(att.BANDS), "n_captures": 0}
    assert c.get("/api/projects/p-9999/captures").status_code == 404
