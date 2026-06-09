"""Fila — A1 (API) + A4/A6 render. Exercises the routes through the injectable ``create_app`` with a
real (in-memory) CRM + a tmp precious Workspace; no network, no LLM, no files. Does NOT touch the
WIP-laden test_webapp.py.
"""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from email2data import fila_page
from email2data.crm import CrmStore
from email2data.webapp import create_app
from email2data.workspace import Workspace

NOW = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)


def _env(mid, hours_ago, frm="maria@acme.pt", subject="Orçamento 50 placas"):
    return {"message_id": mid, "date": (NOW - timedelta(hours=hours_ago)).isoformat(),
            "from": {"email": frm, "name": "Maria"}, "reply_to": {"email": "", "name": ""},
            "to": [{"email": "orcamentos@lindoservico.pt", "name": ""}], "cc": [],
            "references": [], "in_reply_to": None, "attachments": [], "subject": subject}


def _verdict(cp="CLIENT", purpose="ESTIMATE_REQUEST_FROM_CLIENT"):
    return {"direction": "inbound", "counterparty": cp, "purpose": purpose,
            "priority": "HIGH", "urgency": 80, "entities": {},
            "confidence": 0.9, "decided_by": "tier1:gemini-2.5-flash", "reason": "pede orçamento"}


def _crm_with(records):
    c = CrmStore(":memory:").connect()
    for env, verdict in records:
        c.record(env, verdict)
    return c


def _client(tmp_path, crm):
    ws = Workspace(tmp_path / "w.db").connect()
    app = create_app({"team": ["Pedro", "Filipe"]}, workspace=ws, jobspecs={},
                     prepared=([], [], {}), reply_pb="", crm_store=crm)
    return TestClient(app), ws


def test_api_fila_lists_we_owe_thread(tmp_path):
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    r = cl.get("/api/fila")
    assert r.status_code == 200
    data = r.json()
    assert data["team"] == ["Pedro", "Filipe"]
    rows = {x["thread_root"]: x for x in data["rows"]}
    assert "t1" in rows and rows["t1"]["clock"]["state"] == "WE_OWE"


def test_api_handled_drops_thread_from_active_queue(tmp_path):
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    assert cl.post("/api/thread/handled", json={"thread_root": "t1", "handled": True}).status_code == 200
    roots = [x["thread_root"] for x in cl.get("/api/fila").json()["rows"]]
    assert "t1" not in roots                       # resolved → out of the active queue


def test_api_owner_persists_to_workspace(tmp_path):
    cl, ws = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    assert cl.post("/api/thread/owner", json={"thread_root": "t1", "owner": "Pedro"}).status_code == 200
    rows = {x["thread_root"]: x for x in cl.get("/api/fila").json()["rows"]}
    assert rows["t1"]["owner"] == "Pedro"
    assert ws.thread_states()["t1"]["owner"] == "Pedro"   # persisted to the precious overlay


def test_api_thread_requires_root(tmp_path):
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    assert cl.post("/api/thread/handled", json={}).status_code == 400
    assert cl.post("/api/thread/owner", json={"owner": "x"}).status_code == 400


def test_fila_page_renders(tmp_path):
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    r = cl.get("/fila")
    assert r.status_code == 200
    assert "Fila" in r.text and "Orçamento 50 placas" in r.text


def test_api_fila_empty_without_crm(tmp_path):
    cl, _ = _client(tmp_path, None)                # no relations DB available
    assert cl.get("/api/fila").json()["rows"] == []


def test_build_fila_html_smoke():
    html = fila_page.build_fila_html(
        [{"thread_root": "t1", "subject": "Teste", "counterparty": "CLIENT", "purpose": "X",
          "contact": "a@b.pt", "n_messages": 2, "has_attachment": True, "owner": "Pedro",
          "clock": {"state": "WE_OWE", "age_hours": 6, "band": "amber",
                    "label": "devemos resposta há 6 h", "since": None}}],
        ["Pedro"], now_iso="2026-06-03T12:00:00")
    assert "<html" in html and "Teste" in html and "devemos resposta" in html


def test_api_fila_includes_trust(tmp_path):
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    rows = {x["thread_root"]: x for x in cl.get("/api/fila").json()["rows"]}
    assert rows["t1"]["trust"]["decided_by"] == "tier1:gemini-2.5-flash"
    assert rows["t1"]["trust"]["confidence"] == 0.9


def test_crm_carries_trust_fields():
    c = _crm_with([(_env("t1", 3), _verdict())])
    row = c.all_interactions()[0]
    assert row["confidence"] == 0.9 and row["decided_by"] == "tier1:gemini-2.5-flash"
    assert row["reason"] == "pede orçamento"


def test_home_serves_fila(tmp_path):
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    r = cl.get("/")
    assert r.status_code == 200 and "Fila" in r.text


def test_inbox_serves_report(tmp_path):
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    assert cl.get("/inbox").status_code == 200


# ── C2 Contrapartes routes ───────────────────────────────────────────────────

def test_contrapartes_list_serves_page(tmp_path):
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    r = cl.get("/contrapartes")
    assert r.status_code == 200 and "Contrapartes" in r.text


def test_api_contrapartes_returns_clusters(tmp_path):
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    data = cl.get("/api/contrapartes").json()
    assert isinstance(data, list)
    # maria@acme.pt → domain cluster "acme.pt"
    keys = [c["key"] for c in data]
    assert any("acme" in k for k in keys)


def test_contrapartes_detail_200(tmp_path):
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    clusters = cl.get("/api/contrapartes").json()
    if clusters:
        key = clusters[0]["key"]
        r = cl.get(f"/contrapartes/{key}")
        assert r.status_code == 200


def test_contrapartes_detail_404_unknown(tmp_path):
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    assert cl.get("/contrapartes/does.not.exist").status_code == 404


# ── C3 Para ti routes ────────────────────────────────────────────────────────

def test_para_ti_serves_page(tmp_path):
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict(cp="LEAD"))]))
    r = cl.get("/para-ti")
    assert r.status_code == 200 and "Para ti" in r.text


def test_api_para_ti_returns_items(tmp_path):
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict(cp="LEAD"))]))
    data = cl.get("/api/para-ti").json()
    assert "items" in data and isinstance(data["items"], list)


def test_identity_confirm_persists(tmp_path):
    cl, ws = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    r = cl.post("/api/identity/confirm",
                json={"email": "test@gmail.com", "account_key": "acme.pt"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert ws.identity_links().get("test@gmail.com") == "acme.pt"


def test_identity_confirm_requires_both_fields(tmp_path):
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    assert cl.post("/api/identity/confirm", json={"email": "x@y.com"}).status_code == 400


# ── C4 Projetos route ────────────────────────────────────────────────────────

def test_projetos_serves_page(tmp_path):
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    r = cl.get("/projetos")
    assert r.status_code == 200 and "Projetos" in r.text


# ── C5 Nav counts in shell ───────────────────────────────────────────────────

def test_fila_page_contains_nav_links(tmp_path):
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    html = cl.get("/").text
    for href in ["/contrapartes", "/projetos", "/para-ti"]:
        assert href in html
