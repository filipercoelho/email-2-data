"""The thread attachment funnel (ADR-046) — banding, content dedup, and index stability.

The load-bearing test in here is :func:`test_funnel_index_still_addresses_the_same_bytes`. Every
other property is recoverable; a reindexed ``src.index`` silently serves the *wrong file* under the
right name, which looks correct on screen and is only caught by comparing bytes.
"""

from __future__ import annotations

import hashlib
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
