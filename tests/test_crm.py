"""CRM PoC: participant extraction + contact rollup (deterministic, no LLM)."""

import hashlib
import json
from email.message import EmailMessage

from email2data import attachments as att
from email2data.crm import CrmStore, attach_kinds, build_crm, participants


def _env(mid, frm, to=None, cc=None, date="2026-05-20", subject="s", reply_to=None):
    return {
        "message_id": mid, "date": date, "subject": subject,
        "from": frm, "reply_to": reply_to or {},
        "to": to or [], "cc": cc or [], "references": [], "in_reply_to": None, "attachments": [],
    }


def _v(cp="CLIENT", purpose="PO_FROM_CLIENT", direction="inbound"):
    return {"counterparty": cp, "purpose": purpose, "direction": direction, "priority": "HIGH", "urgency": 70}


def test_participants_roles():
    env = _env("m1", {"email": "Joao@Cliente.PT", "name": "João"},
               to=[{"email": "diogo@lindoservico.pt", "name": "Diogo"}],
               cc=[{"email": "ana@cliente.pt", "name": "Ana"}],
               reply_to={"email": "vendas@cliente.pt", "name": "Vendas"})
    roles = {e: r for e, _, r in participants(env)}
    assert roles == {"joao@cliente.pt": "from", "diogo@lindoservico.pt": "to",
                     "ana@cliente.pt": "cc", "vendas@cliente.pt": "reply_to"}


def test_rollup_counts_recency_and_types(tmp_path):
    s = CrmStore(tmp_path / "crm.db").connect()
    # two emails from the same client contact, different dates/purposes
    s.record(_env("m1", {"email": "joao@cliente.pt", "name": "João"},
                  to=[{"email": "diogo@lindoservico.pt", "name": "Diogo"}], date="2026-05-10"),
             _v(purpose="ESTIMATE_REQUEST_FROM_CLIENT"))
    s.record(_env("m2", {"email": "joao@cliente.pt", "name": "João C."},
                  to=[{"email": "diogo@lindoservico.pt"}], date="2026-05-20"),
             _v(purpose="PO_FROM_CLIENT"))
    joao = {r["email"]: r for r in s.top_contacts(external_only=False)}["joao@cliente.pt"]
    assert joao["msg_count"] == 2 and joao["from_count"] == 2
    assert joao["display_name"] == "João C."           # latest non-empty name
    assert joao["last_from_date"] == "2026-05-20"        # recency of last contact
    assert joao["is_internal"] == 0
    assert json.loads(joao["purpose_counts"]) == {"ESTIMATE_REQUEST_FROM_CLIENT": 1, "PO_FROM_CLIENT": 1}
    s.close()


def test_internal_flag_and_external_filter(tmp_path):
    s = CrmStore(tmp_path / "crm.db").connect()
    s.record(_env("m1", {"email": "joao@cliente.pt"},
                  to=[{"email": "diogo@lindoservico.pt"}]), _v())
    by_email = {r["email"]: r for r in s.top_contacts(external_only=False)}
    assert by_email["diogo@lindoservico.pt"]["is_internal"] == 1
    assert by_email["joao@cliente.pt"]["is_internal"] == 0
    assert all(r["email"] != "diogo@lindoservico.pt" for r in s.top_contacts(external_only=True))
    s.close()


def test_counts_and_interaction_idempotent(tmp_path):
    s = CrmStore(tmp_path / "crm.db").connect()
    env, v = _env("m1", {"email": "a@x.pt"}, to=[{"email": "b@y.pt"}]), _v()
    s.record(env, v)
    s.record(env, v)  # same message_id -> interaction replaced, contacts NOT re-bumped
    assert s.counts()["interactions"] == 1
    assert {r["email"]: r["msg_count"] for r in s.top_contacts(external_only=False)} == {"a@x.pt": 1, "b@y.pt": 1}
    s.close()


def test_attach_kinds_derivation_ranked_and_deduped():
    """Typed 📎 (v4, ADR-034): distinct categories, ranked cad>vetor>pdf>folha>img>doc>zip, from the
    envelope's real attachment shape ({"filename",...}). For a fabrication shop the CAD/vector/PDF
    distinction is «can I quote without opening the file?»."""
    # ranked, not filename order: a PDF then a DWG then a JPG comes back cad,pdf,img
    assert attach_kinds([{"filename": "quote.pdf"}, {"filename": "part.DWG"}, {"filename": "photo.jpg"}]) \
        == "cad,pdf,img"
    # deduped: two images collapse to one «img»
    assert attach_kinds([{"filename": "a.png"}, {"filename": "b.jpeg"}]) == "img"
    # the envelope key is «filename», not «name» — this is the bug the wrong key would have hidden
    assert attach_kinds([{"filename": "d.step"}]) == "cad"
    # unknown / extensionless / empty → no phantom category
    assert attach_kinds([{"filename": "noext"}, {"filename": "weird.qqq"}]) == ""
    assert attach_kinds([]) == ""
    # bare strings and the legacy «name» key still work (robust to caller shape)
    assert attach_kinds(["drawing.dxf", {"name": "sheet.xlsx"}]) == "cad,folha"


def test_record_stores_attach_kinds_and_all_interactions_surfaces_it(tmp_path):
    """record() computes attach_kinds at write time (it is data, not a view) and all_interactions()
    (SELECT *) carries it, so cockpit.fold_threads can union it onto the Fila row."""
    s = CrmStore(tmp_path / "crm.db").connect()
    env = _env("m1", {"email": "joao@cliente.pt"}, to=[{"email": "diogo@lindoservico.pt"}])
    env["attachments"] = [{"filename": "peca.dwg", "content_type": "image/vnd.dwg", "size_bytes": 100},
                          {"filename": "orcamento.pdf", "content_type": "application/pdf", "size_bytes": 50}]
    s.record(env, _v())
    # a message with no attachments stores NULL, never an empty phantom string
    s.record(_env("m2", {"email": "ana@cliente.pt"}, to=[{"email": "diogo@lindoservico.pt"}]), _v())
    by_mid = {it["message_id"]: it for it in s.all_interactions()}
    assert by_mid["m1"]["attach_kinds"] == "cad,pdf"
    assert by_mid["m1"]["has_attach"] == 1
    assert by_mid["m2"]["attach_kinds"] is None


def test_record_persists_speech_act_for_the_obligation_fold(tmp_path):
    """ADR-036: record() writes the speech_act column and all_interactions() (SELECT *) surfaces it,
    so cockpit.derive_obligation can fold it. A verdict without the key stores "" → legacy fold."""
    s = CrmStore(tmp_path / "crm.db").connect()
    s.record(_env("m1", {"email": "laminex@x.pt"}, to=[{"email": "geral@lindoservico.pt"}]),
             {**_v(cp="SUPPLIER", purpose="SUPPLIER_INVOICE"), "speech_act": "OBLIGATION"})
    s.record(_env("m2", {"email": "ana@cliente.pt"}, to=[{"email": "diogo@lindoservico.pt"}]), _v())
    by_mid = {it["message_id"]: it for it in s.all_interactions()}
    assert by_mid["m1"]["speech_act"] == "OBLIGATION"
    assert by_mid["m2"]["speech_act"] == ""      # absent in the verdict → empty (fold reads it as UNKNOWN)
    assert by_mid["m2"]["has_attach"] == 0
    s.close()


# ── the ADR-048 branding register ────────────────────────────────────────────────────────────────
#
# The register measures how many UNRELATED THREADS the same inline image rides into. Message count
# was tried first and does not work: on the real corpus the annotated CAD drawing and a client
# press-kit slide each appear in 5 messages, exactly like the animated footer banner. Thread spread
# separates them cleanly — content 1-2 threads, branding 5-41.

_LOGO = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415408d763f8cfc000000301010018dd8db00000000049454e44ae426082")


def _png(w: int, h: int, tag: bytes = b"") -> bytes:
    """A PNG whose IHDR declares ``w``x``h``; ``tag`` makes the bytes (and so the hash) distinct."""
    ihdr = (b"\x00\x00\x00\rIHDR" + w.to_bytes(4, "big") + h.to_bytes(4, "big")
            + b"\x08\x02\x00\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + ihdr + b"\x00" * 4 + tag + _LOGO[-12:]


def _corpus_eml(*, mid: str, refs: list[str], sender: str, images: dict[str, bytes]) -> bytes:
    m = EmailMessage()
    m["From"] = sender
    m["To"] = "cliente@acme.pt"
    m["Subject"] = "Orçamento"
    m["Message-ID"] = f"<{mid}>"
    m["Date"] = "Mon, 20 Jul 2026 10:00:00 +0100"
    if refs:
        m["References"] = " ".join(f"<{r}>" for r in refs)
    m.set_content("texto")
    m.add_alternative("".join(f'<img src="cid:{n}">' for n in images), subtype="html")
    for name, payload in images.items():
        m.add_attachment(payload, maintype="image", subtype="png", filename=f"{name}.png", cid=name)
        m.get_payload()[-1].replace_header("Content-Disposition", f'inline; filename="{name}.png"')
    return m.as_bytes()


def _project(tmp_path, messages):
    """A minimal on-disk project: corpus/*.eml + out/results.jsonl, ready for ``build_crm``."""
    (tmp_path / "config").mkdir(exist_ok=True)
    sp = tmp_path / "config" / "settings.json"
    sp.write_text(json.dumps({"paths": {"out_dir": "out", "corpus_dir": "corpus"}}),
                  encoding="utf-8")
    settings = {"paths": {"out_dir": "out", "corpus_dir": "corpus"}, "__settings_path__": str(sp)}
    (tmp_path / "corpus").mkdir(exist_ok=True)
    (tmp_path / "out").mkdir(exist_ok=True)
    lines = []
    for i, (mid, raw) in enumerate(messages):
        (tmp_path / "corpus" / f"m{i}.eml").write_bytes(raw)
        lines.append(json.dumps({"message_id": f"mid:{mid}", "counterparty": "CLIENT",
                                 "purpose": "OUTBOUND_QUOTE", "priority": "HIGH",
                                 "direction": "outbound", "urgency": 50}))
    (tmp_path / "out" / "results.jsonl").write_text("\n".join(lines), encoding="utf-8")
    return settings


def test_the_register_counts_threads_not_messages(tmp_path):
    """The defect the whole ADR turns on. ``logo`` rides three unrelated conversations; ``drawing``
    appears in FIVE messages of ONE conversation — which is what a real drawing being replied to
    looks like. A message-count register buries the drawing and this test is why it cannot."""
    logo, drawing = _png(180, 60, b"LOGO"), _png(431, 361, b"DRAW" * 80)
    msgs = []
    for t in (1, 2, 3):                    # three separate root messages = three thread_roots
        msgs.append((f"t{t}", _corpus_eml(mid=f"t{t}", refs=[], sender="orcamentos@lindoservico.pt",
                                          images={"sig": logo})))
    for r in range(5):                     # five messages, all replying into thread t1
        msgs.append((f"d{r}", _corpus_eml(mid=f"d{r}", refs=["t1"],
                                          sender="orcamentos@lindoservico.pt",
                                          images={"draw": drawing})))
    settings = _project(tmp_path, msgs)
    counts = build_crm(settings)

    store = CrmStore(tmp_path / "out" / "crm.db").connect()
    spread = store.asset_spread()
    logo_sha, draw_sha = hashlib.sha256(logo).hexdigest(), hashlib.sha256(drawing).hexdigest()
    assert spread[logo_sha] == 3, "the logo rode three unrelated threads"
    assert spread[draw_sha] == 1, "five replies in one conversation is ONE thread, not five"

    hidden = att.branding_shas(spread)
    assert logo_sha in hidden and draw_sha not in hidden
    assert counts["assets"] == 2 and counts["branding"] == 1, \
        "the crm build must report how much the funnel will hide"
    rows = {r["sha"]: r for r in store.asset_spread_rows()}
    assert rows[draw_sha]["n_messages"] == 5 and rows[draw_sha]["n_threads"] == 1, \
        "the weaker message signal is kept for the audit, just not used for the decision"
    assert rows[logo_sha]["px"] == "180x60" and rows[logo_sha]["sample_name"] == "sig.png"
    store.close()


def test_the_register_ignores_art_other_people_sent(tmp_path):
    """Scope: the decision omits art LINDO attaches. A supplier's recurring signature logo stays in
    the collapsed ADR-046 band, because deciding for someone else's branding was never asked for."""
    theirs = _png(180, 60, b"THEIRS")
    msgs = [(f"s{t}", _corpus_eml(mid=f"s{t}", refs=[], sender="compras@fornecedor.pt",
                                  images={"sig": theirs})) for t in (1, 2, 3, 4)]
    build_crm(_project(tmp_path, msgs))
    store = CrmStore(tmp_path / "out" / "crm.db").connect()
    assert store.asset_spread() == {}, "an external sender's art must not enter the register"
    store.close()


def test_the_register_is_rebuilt_whole_and_never_accumulates(tmp_path):
    """It is a measurement, not a curated list. A logo that stops being sent must leave the register
    on the next rebuild, or the funnel keeps hiding a file on evidence that no longer exists."""
    s = CrmStore(tmp_path / "crm.db").connect()
    s.write_asset_spread({"old": {"threads": {"a", "b", "c"}, "messages": {"m"},
                                 "sample_name": "gone.png", "px": "1x1", "size": 9}})
    assert s.asset_spread() == {"old": 3}
    s.write_asset_spread({"new": {"threads": {"a"}, "messages": {"m"},
                                 "sample_name": "here.png", "px": "1x1", "size": 9}})
    assert s.asset_spread() == {"new": 1}, "a stale hash survived the rebuild"
    s.close()


def test_a_pre_v6_db_reports_an_empty_register_instead_of_raising(tmp_path):
    """Fail-open. ``crm.db`` is regenerable and may predate this table; the reader must degrade to
    "no evidence, hide nothing" rather than 500 the thread panel."""
    s = CrmStore(tmp_path / "crm.db").connect()
    s._conn.execute("DROP TABLE asset_spread")
    assert s.asset_spread() == {}
    assert s.asset_spread_rows() == []
    assert att.branding_shas(s.asset_spread()) == set()
    s.close()
