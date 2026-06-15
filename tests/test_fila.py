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
    assert "mid:t1" in rows and rows["mid:t1"]["clock"]["state"] == "WE_OWE"


def test_api_handled_drops_thread_from_active_queue(tmp_path):
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    assert cl.post("/api/thread/handled", json={"thread_root": "mid:t1", "handled": True}).status_code == 200
    roots = [x["thread_root"] for x in cl.get("/api/fila").json()["rows"]]
    assert "mid:t1" not in roots                   # resolved → out of the active queue


def test_api_owner_persists_to_workspace(tmp_path):
    cl, ws = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    assert cl.post("/api/thread/owner", json={"thread_root": "mid:t1", "owner": "Pedro"}).status_code == 200
    rows = {x["thread_root"]: x for x in cl.get("/api/fila").json()["rows"]}
    assert rows["mid:t1"]["owner"] == "Pedro"
    assert ws.thread_states()["mid:t1"]["owner"] == "Pedro"   # persisted to the precious overlay


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
    assert rows["mid:t1"]["trust"]["decided_by"] == "tier1:gemini-2.5-flash"
    assert rows["mid:t1"]["trust"]["confidence"] == 0.9


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


def _env_out(mid, hours_ago, to="maria@acme.pt", subject="Re: Orçamento 50 placas"):
    """An outbound reply from us to the contact (direction set on the verdict)."""
    return {"message_id": mid, "date": (NOW - timedelta(hours=hours_ago)).isoformat(),
            "from": {"email": "orcamentos@lindoservico.pt", "name": "Nós"},
            "reply_to": {"email": "", "name": ""},
            "to": [{"email": to, "name": "Maria"}], "cc": [],
            "references": ["t1"], "in_reply_to": "t1", "attachments": [], "subject": subject}


def test_contrapartes_detail_api_returns_insight_and_navigation(tmp_path):
    """The detail endpoint is a hub, not a bare cluster: rollup stats + a navigable timeline (every
    row carries thread_root + message_id + direction so the UI can deep-link into the Fila/inbox)."""
    crm = _crm_with([(_env("t1", 3), _verdict()),
                     (_env_out("t2", 2), {**_verdict(), "direction": "outbound"})])
    cl, _ = _client(tmp_path, crm)
    data = cl.get("/api/contrapartes/acme.pt").json()
    assert data["cluster"]["key"] == "acme.pt"
    st = data["stats"]
    assert st["messages"] >= 2
    assert st["inbound"] >= 1 and st["outbound"] >= 1          # direction split computed
    assert st["primary_email"] == "maria@acme.pt"             # the address we heard from
    assert st["threads"] == 1                                  # t2 references t1 → one conversation
    assert data["timeline"] and all(t.get("thread_root") and t.get("message_id")
                                    for t in data["timeline"])
    assert "gates" in data and "projects" in data and "fila_rows" in data
    assert cl.get("/api/contrapartes/nope.pt").status_code == 404


def test_contrapartes_detail_page_links_to_related_data(tmp_path):
    """The page deep-links to where the data lives: email chips + 'Histórico completo' → the inbox
    report; open threads → the Fila (?thread=); timeline rows → the inbox; projects → the workbench —
    plus the insight strip + purpose breakdown are wired."""
    crm = _crm_with([(_env("t1", 3), _verdict())])
    cl, _ = _client(tmp_path, crm)
    cl.post("/api/projects", json={"title": "Placas Acme", "client_email": "maria@acme.pt"})
    html = cl.get("/contrapartes/acme.pt").text
    assert 'id="_stats"' in html and "function statCard(" in html          # insight strip
    assert "/inbox#tab=contacts&sel=" in html                              # email chips → inbox history
    assert "inboxEmail" in html and "/inbox#tab=emails&sel=" in html       # timeline rows → inbox
    assert "/?thread='+encodeURIComponent" in html                         # open threads → Fila
    assert "/projetos/'+encodeURIComponent(p.project_id)" in html          # projects → workbench
    assert "Decisões pendentes" in html and "O que trocámos" in html       # para-ti + purpose sections


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


def test_projetos_back_button_restores_list_visibility(tmp_path):
    """Regression: the ← Projetos back button routes selected=null → render() → renderList(),
    which must re-show #_list and hide #_detail. Without these two lines the detail panel stayed
    visible and the back button looked dead."""
    html = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))[0].get("/projetos").text
    js = html.split("function renderList(")[1].split("function ")[0]
    assert "$('#_detail').classList.add('hidden')" in js
    assert "$('#_list').classList.remove('hidden')" in js


def test_projetos_page_reflects_open_project_in_url(tmp_path):
    """REST deep-linking: opening a project drives the address bar to /projetos/<pid> (pushState),
    the id is read back from the path on load + Back/Forward (popstate), and the old URL-wipe that
    discarded the deep-link (replaceState to '/projetos' then loadDetail) is gone."""
    html = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))[0].get("/projetos").text
    assert "function _pidFromURL(" in html and "function closeDetail(" in html
    assert "history.pushState(null,''," in html and "/projetos/'+encodeURIComponent(" in html
    assert "addEventListener('popstate'" in html
    assert "location.pathname.match(/^\\/projetos\\/(.+)$/)" in html   # path is the id source


def test_projetos_detail_route_serves_the_lens(tmp_path):
    """The /projetos/<pid> page route exists so a direct load / refresh / shared link returns the
    lens (200) instead of 404; 404 for an id with no project."""
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    pid = cl.post("/api/projects", json={"title": "X"}).json()["project_id"]
    assert cl.get(f"/projetos/{pid}").status_code == 200
    assert cl.get("/projetos/p-9999").status_code == 404


def test_fila_page_reflects_filter_and_open_thread_in_url(tmp_path):
    """Deep-linkable Fila: the counterparty filter and the expanded thread are written to the query
    string (?counterparty= / ?thread=) and re-applied on load + Back/Forward; the old URL-wipe that
    discarded the ?focus= deep-link is gone, and project chips point at /projetos/<pid>."""
    html = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))[0].get("/").text
    assert "function syncURL(" in html and "function applyURLState(" in html
    assert "p.set('counterparty'" in html and "p.set('thread'" in html
    assert "get('counterparty')" in html and "get('thread')" in html
    assert "get('focus')" in html                          # legacy deep-link still honoured
    assert "addEventListener('popstate'" in html
    assert "history.replaceState(null, '', '/')" not in html   # no longer discards URL state
    assert "/projetos?p=" not in html and "/projetos/'+encodeURIComponent(" in html


def test_fila_multi_filter_dimensions(tmp_path):
    """Multi-filter: purpose, urgency band, owner, domain, search, attachment, and age filters
    are wired to URL state and applied by view().  The filter bar (_fbar) and inline search input
    (_search) appear in the rendered page, and renderFbar() / clearFilters() are present."""
    html = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))[0].get("/").text
    # URL serialisation covers all new filter dimensions
    for key in ("purpose", "band", "domain", "search", "minDays", "attachment"):
        assert f"p.set('{key}'" in html, f"syncURL must write '{key}'"
    # URL deserialisation reads each key back
    for key in ("purpose", "band", "domain", "search"):
        assert f"get('{key}')" in html, f"applyURLState must read '{key}'"
    # view() gates on each filter dimension
    for expr in ("filters.counterparty", "filters.purpose", "filters.band",
                 "filters.search", "filters.domain", "filters.minAgeDays", "filters.hasAttachment"):
        assert expr in html, f"view() must apply {expr}"
    # UI scaffolding
    assert 'id="_fbar"' in html, "filter bar element must be present"
    assert 'id="_search"' in html, "search input must be present"
    assert "renderFbar(" in html, "renderFbar helper must be defined"
    assert "clearFilters(" in html, "clearFilters must be defined"
    # Esc still clears filters (onEsc calls clearFilters)
    assert "function onEsc()" in html and "clearFilters()" in html


def test_para_ti_keyboard_accept_honours_navigation_only_items(tmp_path):
    """Regression: acceptItem() handled only acc.api; a navigation-only accept (acc.href/nav,
    e.g. 'Ver na Fila') made the keyboard 'y' a silent no-op. It must navigate instead."""
    html = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict(cp="LEAD"))]))[0].get("/para-ti").text
    fn = html.split("async function acceptItem(")[1].split("function dismissItem(")[0]
    assert "acc.href||acc.nav" in fn and "location.href=acc.href||acc.nav" in fn


# ── C5 Nav counts in shell ───────────────────────────────────────────────────

def test_fila_page_contains_nav_links(tmp_path):
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    html = cl.get("/").text
    for href in ["/contrapartes", "/projetos", "/para-ti"]:
        assert href in html


def test_fila_page_ships_purpose_label_and_reclassify():
    """Phase A: the Fila renders the PT purpose label + a clickable picker to correct the verdict
    (purpose AND counterparty) inline, wired to /api/reclassify."""
    html = fila_page.build_fila_html(
        [{"thread_root": "t1", "message_id": "m1", "subject": "Pedido",
          "counterparty": "CLIENT", "purpose": "ESTIMATE_REQUEST_FROM_CLIENT",
          "auto": {"counterparty": "CLIENT", "purpose": "ESTIMATE_REQUEST_FROM_CLIENT"},
          "clock": {"band": "green", "label": "agora"}, "trust": {}}],
        team=["Pedro"])
    assert "const LABELS" in html                         # PT label dict embedded for the pickers
    assert "Pedido de orçamento" in html                  # a PT purpose label present
    assert 'data-act="reclassPur"' in html and 'data-act="reclassCp"' in html
    assert "function reclassify(" in html and "/api/reclassify" in html
