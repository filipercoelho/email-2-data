"""Fila — A1 (API) + A4/A6 render. Exercises the routes through the injectable ``create_app`` with a
real (in-memory) CRM + a tmp precious Workspace; no network, no LLM, no files. Does NOT touch the
WIP-laden test_webapp.py.
"""

import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

import pytest
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
    app = create_app({"team": ["Diogo", "Bruno"]}, workspace=ws, jobspecs={},
                     prepared=([], [], {}), reply_pb="", crm_store=crm)
    return TestClient(app), ws


def test_api_fila_lists_we_owe_thread(tmp_path):
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    r = cl.get("/api/fila")
    assert r.status_code == 200
    data = r.json()
    assert data["team"] == ["Diogo", "Bruno"]
    rows = {x["thread_root"]: x for x in data["rows"]}
    assert "mid:t1" in rows and rows["mid:t1"]["clock"]["state"] == "WE_OWE"


def test_api_handled_drops_thread_from_active_queue(tmp_path):
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    assert cl.post("/api/thread/handled", json={"thread_root": "mid:t1", "handled": True}).status_code == 200
    roots = [x["thread_root"] for x in cl.get("/api/fila").json()["rows"]]
    assert "mid:t1" not in roots                   # resolved → out of the active queue


def test_api_owner_persists_to_workspace(tmp_path):
    cl, ws = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    assert cl.post("/api/thread/owner", json={"thread_root": "mid:t1", "owner": "Diogo"}).status_code == 200
    rows = {x["thread_root"]: x for x in cl.get("/api/fila").json()["rows"]}
    assert rows["mid:t1"]["owner"] == "Diogo"
    assert ws.thread_states()["mid:t1"]["owner"] == "Diogo"   # persisted to the precious overlay


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
          "contact": "a@b.pt", "n_messages": 2, "has_attachment": True, "owner": "Diogo",
          "clock": {"state": "WE_OWE", "age_hours": 6, "band": "amber",
                    "label": "devemos resposta há 6 h", "since": None}}],
        ["Diogo"], now_iso="2026-06-03T12:00:00")
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
    """The page deep-links stay INSIDE the cockpit: email chips → the Fila (?search=); open threads
    AND timeline rows → the Fila (?thread=); pending decisions → the exact Para-ti card (?item=);
    projects → the workbench. NOTHING may link into the legacy /inbox app — those 111 history links
    dumped the user into a contradicting surface (the exact bug this pins against regressing)."""
    crm = _crm_with([(_env("t1", 3), _verdict())])
    cl, _ = _client(tmp_path, crm)
    cl.post("/api/projects", json={"title": "Placas Acme", "client_email": "maria@acme.pt"})
    html = cl.get("/contrapartes/acme.pt").text
    assert 'id="_stats"' in html and "function statCard(" in html          # insight strip
    assert "/inbox#" not in html                                           # NEVER the legacy app
    assert "/?search='+encodeURIComponent" in html                         # email chips → Fila search
    assert "/?thread='+encodeURIComponent" in html                         # threads + timeline → Fila
    assert "/para-ti?item='+encodeURIComponent" in html                    # gates → the exact card
    assert "/projetos/'+encodeURIComponent(p.project_id)" in html          # projects → workbench
    assert "Decisões pendentes" in html and "O que trocámos" in html       # para-ti + purpose sections
    assert 'id="_rename"' in html                                          # editable display name


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
        team=["Diogo"])
    assert "const LABELS" in html                         # PT label dict embedded for the pickers
    assert "Pedido de orçamento" in html                  # a PT purpose label present
    assert 'data-act="reclassPur"' in html and 'data-act="reclassCp"' in html
    assert "function reclassify(" in html and "/api/reclassify" in html


# ── Live freshness: periodic sync + uncached decision queue (ADR-023) ────────

def test_para_ti_routes_forbid_http_caching(tmp_path):
    """The queue is rebuilt per request, so the only way to serve a stale one is an HTTP cache in
    front of us (browser revisit / bfcache). Both the page and its API must opt out explicitly."""
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict(cp="LEAD"))]))
    for route in ("/para-ti", "/api/para-ti"):
        cc = cl.get(route).headers.get("cache-control", "")
        assert "no-store" in cc, f"{route} may be cached: {cc!r}"


def test_api_para_ti_carries_badges_and_freshness(tmp_path):
    """One round-trip feeds the refresh poll: items + nav badges + how old the mail actually is."""
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict(cp="LEAD"))]))
    d = cl.get("/api/para-ti").json()
    assert isinstance(d["items"], list) and isinstance(d["nav_counts"], dict)
    assert "synced_at" in d and "syncing" in d and d["served_at"]


def test_para_ti_page_refreshes_itself_without_reloading(tmp_path):
    """An open tab must pull new items on its own — and swap them in place, not location.reload()
    (a reload mid-decision throws away scroll position and the focused card)."""
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict(cp="LEAD"))]))
    html = cl.get("/para-ti").text
    assert "async function refresh(" in html and "'/api/para-ti'" in html
    assert "setInterval" in html and "visibilitychange" in html      # polls, and catches up on focus
    assert "function setNavCounts(" in html                          # badges update too
    assert "location.reload" not in html.split("function refresh(")[1][:1200]


def test_para_ti_dismissals_survive_a_refresh(tmp_path):
    """Dismissed/focused items are remembered by CONTENT key, never by list index — a refresh
    reorders the queue, so an index would silently re-point at a different decision."""
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict(cp="LEAD"))]))
    html = cl.get("/para-ti").text
    assert "function itemKey(" in html
    assert "dismissed.add(itemKey(item))" in html
    assert "items.indexOf(item)" not in html          # the index-based form is gone for good


# -- the schedule itself ------------------------------------------------------

def test_sync_interval_defaults_to_15_minutes():
    from email2data.webapp import resolve_sync_interval
    assert resolve_sync_interval({}) == 15 * 60
    assert resolve_sync_interval({"sync": {"interval_minutes": 5}}) == 5 * 60


def test_sync_interval_zero_or_garbage_disables_the_loop():
    from email2data.webapp import resolve_sync_interval
    assert resolve_sync_interval({"sync": {"interval_minutes": 0}}) == 0.0
    assert resolve_sync_interval({"sync": {"interval_minutes": -3}}) == 0.0
    assert resolve_sync_interval({"sync": {"interval_minutes": "nunca"}}) == 0.0


def test_periodic_loop_ticks_repeatedly_then_stops_promptly():
    """The whole point: it keeps pulling mail after startup. Also proves stop() doesn't have to wait
    out a full interval (a 60s shutdown hang on Ctrl-C would be a real regression)."""
    import threading as _t
    import time as _time
    from email2data.webapp import periodic_sync_loop
    calls, stop = [], _t.Event()

    def _run():
        calls.append(1)
        if len(calls) >= 3:
            stop.set()          # third tick ends it
        return {}

    th = _t.Thread(target=periodic_sync_loop, args=(_run, 0.01, stop), daemon=True)
    t0 = _time.monotonic()
    th.start()
    th.join(timeout=5)
    assert not th.is_alive(), "loop did not exit when stopped"
    assert len(calls) == 3                       # ticked repeatedly, not once
    assert _time.monotonic() - t0 < 5


def test_periodic_loop_survives_a_failing_sync():
    """A raising sync must not kill the thread — that would stop ingestion silently for the session,
    which is exactly the staleness this feature exists to prevent."""
    import threading as _t
    from email2data.webapp import periodic_sync_loop
    calls, stop, logged = [], _t.Event(), []

    def _run():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("IMAP down")
        if len(calls) == 2:
            return {"error": "no password"}
        stop.set()
        return {}

    th = _t.Thread(target=periodic_sync_loop,
                   args=(_run, 0.01, stop), kwargs={"log": logged.append}, daemon=True)
    th.start()
    th.join(timeout=5)
    assert not th.is_alive() and len(calls) == 3          # kept ticking through both failures
    assert any("IMAP down" in m for m in logged) and any("no password" in m for m in logged)


def test_injected_app_does_not_start_background_threads(tmp_path):
    """Tests inject state; a periodic sync there would hit the real IMAP/LLM from a unit test."""
    import threading as _t
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    with cl:                                  # enters lifespan
        cl.get("/para-ti")
        running = {t.name for t in _t.enumerate()}
        assert "email2data-periodic-sync" not in running
        assert "email2data-startup-sync" not in running


# ── Para ti detail panel: expand-in-place evidence (ADR-024) ────────────────

def _spec_for(mid: str) -> dict:
    """A job spec with 3 line items: material known for ONE of them, quantity for none."""
    from email2data import jobspec as js
    spec = js.JobSpec(message_id=mid, subject="Orçamento 50 placas", counterparty="CLIENT",
                      purpose="ESTIMATE_REQUEST_FROM_CLIENT")
    spec.job_fields = {"deadline": js.SpecField("2026-12-24", "llm", False),
                       "design_ready": js.SpecField("ficheiro em anexo", "offline", False)}
    spec.items = [{k: js.SpecField() for k in js.ITEM_KEYS} for _ in range(3)]
    spec.items[0]["item"] = js.SpecField("placa", "llm", False)
    spec.items[0]["material"] = js.SpecField("inox", "llm", False)
    return spec.to_dict()


def _client_with_spec(tmp_path, crm, mid="t1"):
    ws = Workspace(tmp_path / "w.db").connect()
    app = create_app({"team": ["Diogo", "Bruno"]}, workspace=ws,
                     jobspecs={mid: _spec_for(mid)},
                     prepared=([], [], {}), reply_pb="", crm_store=crm)
    return TestClient(app), ws


def test_thread_endpoint_carries_the_job_spec_for_the_detail_panel(tmp_path):
    """Expanding a decision must be ONE round-trip: the thread and what we already extracted from it.
    A second endpoint would mean a second spinner on the thing the user is trying to judge."""
    cl, _ = _client_with_spec(tmp_path, _crm_with([(_env("t1", 3), _verdict(cp="LEAD"))]))
    d = cl.get("/api/thread/mid:t1").json()
    spec = d["spec"]
    assert spec and spec["message_id"] == "t1"
    assert spec["job_fields"]["deadline"]["value"] == "2026-12-24"
    assert len(spec["items"]) == 3
    assert spec["readiness"]["estimable"] is False       # must-haves still missing → never claimed


def test_thread_endpoint_spec_is_null_when_nothing_was_extracted(tmp_path):
    """No jobspec must render as an honest absence, not an empty-but-present panel."""
    cl, _ = _client_with_spec(tmp_path, _crm_with([(_env("t9", 3), _verdict(cp="LEAD"))]))
    assert cl.get("/api/thread/mid:t9").json()["spec"] is None   # jobspec is keyed to t1, not t9


def test_para_ti_items_carry_the_handles_inline_actions_need(tmp_path):
    """Reclassification is keyed by MESSAGE, not thread — without message_id the card can only send
    the user elsewhere, which is the round-trip this change removes."""
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict(cp="LEAD"))]))
    it = cl.get("/api/para-ti").json()["items"][0]
    ctx = it["context"]
    assert ctx["message_id"] == "t1"
    assert isinstance(ctx["auto"], dict) and isinstance(ctx["owners"], list)
    assert "project" in ctx


def test_para_ti_page_ships_the_expandable_detail(tmp_path):
    """The gate opens in place into the evidence: the shared thread renderer + the spec panel."""
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict(cp="LEAD"))]))
    html = cl.get("/para-ti").text
    assert "async function toggleExpand(" in html and "'/api/thread/'" in html
    assert "msgThreadHTML(" in html                      # the shared kit, not a private copy
    assert "function specHTML(" in html and "function _specFold(" in html
    assert "const FIELDS =" in html                      # jobspec registry for the field labels


def test_para_ti_page_uses_canonical_labels_not_a_private_copy(tmp_path):
    """labels.py exists precisely so the lenses stop drifting apart on enum wording."""
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict(cp="LEAD"))]))
    html = cl.get("/para-ti").text
    assert "const LABELS =" in html and "Pedido de orçamento" in html
    # the old hardcoded JS dict is gone
    assert "'ESTIMATE_REQUEST_FROM_CLIENT':'pedido orçamento'" not in html


def test_para_ti_page_ships_grouping_filter_and_inline_actions(tmp_path):
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict(cp="LEAD"))]))
    html = cl.get("/para-ti").text
    assert "function renderChips(" in html and "function setFilter(" in html
    assert "function groupHTML(" in html and "const KIND_HEADING =" in html
    assert "function reclassMenu(" in html and "'/api/reclassify'" in html
    assert "function ownerMenu(" in html and "'/api/thread/owner'" in html
    assert "async function markHandled(" in html and "'/api/thread/handled'" in html
    assert "const ROSTER =" in html                      # owner picker needs the real roster


# ── Ordering: newest-first timeline + selectable queue order ─────────────────

def _thread_html_js() -> str:
    """The SHIPPED _threadHTML source, sliced out of the Fila lens JS."""
    return fila_page._LENS_JS.split("function _threadHTML(")[1].split("\n/*")[0]


def test_fila_timeline_renders_newest_message_first_non_destructively():
    """(a) The thread timeline reads newest→oldest. The .slice() is load-bearing: ``msgs`` IS the
    array cached in _threadCache, so an in-place ``msgs.reverse()`` would flip it again on every
    re-render — open a thread, hit any re-render, and the order silently swaps back."""
    fn = _thread_html_js()
    assert "msgs.slice().reverse()" in fn
    assert "msgs.reverse()" not in fn                  # the destructive form must never appear
    assert "ordered.map(m=>msgHTML(m))" in fn          # the reversed COPY is what renders
    assert "msgThreadSummary(msgs)" in fn              # summary keeps the chronological range


def test_fila_timeline_reversal_is_idempotent_across_renders():
    """Executes the shipped _threadHTML in node, twice, over one message array. The two renders must
    be byte-identical and the caller's array must come back untouched — precisely what an in-place
    reverse fails (2nd render flips back to oldest-first). Guarded on node like the JS date tests in
    test_webapp.py; the assertions above pin the same invariant without it."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available — the shipped JS cannot be executed")
    harness = (
        "function esc(s){return String(s);}\n"
        "function msgHTML(m){return '['+m.message_id+']';}\n"
        "function msgThreadSummary(msgs){return msgs.map(m=>m.message_id).join('>');}\n"
        "function _projHTML(r){return '';}\n"
        "function _threadHTML(" + _thread_html_js() + "\n"
        "const msgs=[{message_id:'m1'},{message_id:'m2'},{message_id:'m3'}];\n"
        "const r={_open:true,_threadMsgs:msgs};\n"
        "const a=_threadHTML(r), b=_threadHTML(r);\n"
        "console.log(JSON.stringify({a:a,b:b,cached:msgs.map(m=>m.message_id)}));\n"
    )
    out = subprocess.run([node, "-e", harness], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    d = json.loads(out.stdout)
    assert d["a"].index("[m3]") < d["a"].index("[m1]")   # newest message first
    assert d["a"] == d["b"]                              # re-render identical (the .slice() pin)
    assert d["cached"] == ["m1", "m2", "m3"]             # cached array not mutated
    assert "m1>m2>m3" in d["a"]                          # summary line still chronological


def test_fila_page_offers_selectable_order_defaulting_to_risk(tmp_path):
    """ADR-033: the queue order is user-selectable and DEFAULTS to response risk — the highest-stakes
    thread is on top at load ('the next move is never a question'). `Mais recentes` stays one click
    away, and the choice is still carried in the URL (the default stays out of the address bar)."""
    html = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))[0].get("/").text
    assert 'id="_order"' in html
    assert 'value="recent">Mais recentes' in html            # pt-PT labels
    assert 'value="risk">Risco de resposta' in html          # both orders reachable
    assert "let order = ORDER_RISK" in html                  # risk is the default
    assert "order!==ORDER_RISK" in html                      # …and stays out of the URL
    assert "function setOrder(" in html and "function sortRows(" in html
    assert "p.set('order'" in html and "p.get('order')" in html    # shareable / survives refresh
    assert "(r2.order_keys||{})[order]" in html              # sorts on the server-supplied keys…
    assert "_STATE_RANK" not in html                         # …never a JS copy of the risk tuple


def test_fila_order_toggle_reorders_and_is_reversible():
    """Executes the shipped cmpOrderKey/sortRows in node: flipping to 'risco de resposta' really does
    reproduce the risk order, flipping back restores the recent order exactly, and re-sorting in the
    same order twice is a no-op (the list must not drift under the user between renders)."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available — the shipped JS cannot be executed")
    js = fila_page._LENS_JS
    fns = ("function cmpOrderKey(" + js.split("function cmpOrderKey(")[1].split("\nfunction sortRows")[0]
           + "\nfunction sortRows(" + js.split("function sortRows(")[1].split("\nfunction setOrder")[0])
    harness = fns + """
let order='recent';
let rows=[{s:'antigo',order_keys:{recent:1000,risk:[3,1,72]}},
          {s:'novo',  order_keys:{recent:3000,risk:[3,1,2]}},
          {s:'ontem', order_keys:{recent:2000,risk:[3,1,20]}},
          {s:'espera',order_keys:{recent:2500,risk:[2,1,5]}}];
sortRows(); const r1=rows.map(r=>r.s); sortRows(); const r2=rows.map(r=>r.s);
order='risk'; sortRows(); const k1=rows.map(r=>r.s); sortRows(); const k2=rows.map(r=>r.s);
order='recent'; sortRows(); const back=rows.map(r=>r.s);
console.log(JSON.stringify({r1:r1,r2:r2,k1:k1,k2:k2,back:back}));
"""
    out = subprocess.run([node, "-e", harness], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    d = json.loads(out.stdout)
    assert d["r1"] == ["novo", "espera", "ontem", "antigo"]        # newest activity first
    assert d["k1"] == ["antigo", "ontem", "novo", "espera"]        # who owes, and for how long
    assert d["r1"] == d["r2"] and d["k1"] == d["k2"]               # re-sorting is a no-op
    assert d["back"] == d["r1"]                                    # the flip is reversible


def test_api_fila_defaults_to_risk_first_and_ships_both_keys(tmp_path):
    """End to end through the route the page actually loads (ADR-033): the oldest reply debt on top
    by default, and every row carries both order keys so the lens can flip without a round-trip."""
    crm = _crm_with([(_env("t1", 30), _verdict()),
                     (_env("t2", 2, subject="Pedido novo"), _verdict())])
    rows = _client(tmp_path, crm)[0].get("/api/fila").json()["rows"]
    assert [r["thread_root"] for r in rows] == ["mid:t1", "mid:t2"]
    assert all(set(r["order_keys"]) == {"recent", "risk"} for r in rows)
    assert all(r["last_date"] for r in rows)


# ── The five structural fixes (2026-07-20 UX review) ─────────────────────────

def test_api_fila_include_resolved_is_the_tratados_ledger(tmp_path):
    """A decision must stay reviewable after it is made: ``?include=resolved`` returns the handled
    thread (state HANDLED) that the default active view rightly drops. Before this parameter the
    only proof a decision ever happened was its absence."""
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    cl.post("/api/thread/handled", json={"thread_root": "mid:t1", "handled": True})
    active = [x["thread_root"] for x in cl.get("/api/fila").json()["rows"]]
    ledger = {x["thread_root"]: x for x in cl.get("/api/fila?include=resolved").json()["rows"]}
    assert "mid:t1" not in active
    assert ledger["mid:t1"]["clock"]["state"] == "HANDLED"


def test_api_para_ti_dismiss_survives_and_undismiss_restores(tmp_path):
    """'Ignorar' is a human decision: it must survive a reload (the JS Set didn't — every ignored
    proposal resurrected on the next GET). Dismiss → gone from /api/para-ti; undismiss (Z) → back."""
    cl, ws = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    items = cl.get("/api/para-ti").json()["items"]
    assert items, "expected at least one proposal from a CLIENT estimate thread"
    key = items[0]["key"]
    assert cl.post("/api/para-ti/dismiss", json={"key": key, "kind": items[0]["kind"]}).status_code == 200
    assert key not in {i["key"] for i in cl.get("/api/para-ti").json()["items"]}
    assert key in ws.para_ti_dismissed()                      # persisted, not in-memory
    assert cl.post("/api/para-ti/undismiss", json={"key": key}).status_code == 200
    assert key in {i["key"] for i in cl.get("/api/para-ti").json()["items"]}
    assert cl.post("/api/para-ti/dismiss", json={}).status_code == 400


def test_api_project_rename(tmp_path):
    """Projects are born titled with a raw email subject — the title must be editable to a human
    name, and a blank title is rejected."""
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    pid = cl.post("/api/projects", json={"title": "RE: AM-NCR-12376-LINDO"}).json()["project_id"]
    assert cl.post(f"/api/projects/{pid}/rename", json={"title": "Placas Cliente"}).status_code == 200
    assert cl.get(f"/api/projects/{pid}").json()["project"]["title"] == "Placas Cliente"
    assert cl.post(f"/api/projects/{pid}/rename", json={"title": "  "}).status_code == 400
    assert cl.post("/api/projects/p-9999/rename", json={"title": "x"}).status_code == 404


def test_api_contraparte_name_override(tmp_path):
    """A human display name set on a cluster key wins everywhere the cluster is rendered; an empty
    name resets to the automatic derivation. The key (machine identity) never changes."""
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    assert cl.post("/api/contrapartes/acme.pt/name", json={"name": "Acme Lda"}).status_code == 200
    named = {c["key"]: c for c in cl.get("/api/contrapartes").json()}
    assert named["acme.pt"]["display_name"] == "Acme Lda" and named["acme.pt"]["name_overridden"]
    cl.post("/api/contrapartes/acme.pt/name", json={"name": ""})
    reset = {c["key"]: c for c in cl.get("/api/contrapartes").json()}
    assert reset["acme.pt"]["display_name"] != "Acme Lda" and not reset["acme.pt"]["name_overridden"]


def test_fila_rows_carry_can_draft_flag(tmp_path):
    """The queue says who owes a reply — rows must also say whether the reply DRAFT path exists
    (message has a JobSpec), so the UI never offers a button that 404s."""
    crm = _crm_with([(_env("t1", 3), _verdict())])
    ws = Workspace(tmp_path / "w.db").connect()
    app = create_app({"team": []}, workspace=ws, jobspecs={"t1": {"message_id": "t1"}},
                     prepared=([], [], {}), reply_pb="", crm_store=crm)
    rows = {x["thread_root"]: x for x in TestClient(app).get("/api/fila").json()["rows"]}
    assert rows["mid:t1"]["can_draft"] is True
    cl2, _ = _client(tmp_path, _crm_with([(_env("t2", 3), _verdict())]))  # no jobspec → no draft
    assert all(r["can_draft"] is False for r in cl2.get("/api/fila").json()["rows"])


def test_fila_page_ships_the_review_fix_controls(tmp_path):
    """The Fila page carries: the clickable 'em risco' filter chip, the visible owner filter, the
    Tratados ledger toggle, the sticky-order storage, the critical-tier pulse (only ≥72 h red
    animates), and the reply-draft affordance."""
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    html = cl.get("/fila").text
    assert '<button id="_risk"' in html                      # the chip is a control, not a label
    assert "setFilter('band',filters.band==='risk'" in html  # …that toggles the risk filter
    assert 'id="_ownerf"' in html                            # visible owner filter
    # the decided ledger stays one action away — since ADR-033 P1 it lives in the vistas rail
    assert 'data-vista="tratados"' in html and "include=resolved" in html
    assert "localStorage.setItem('fila-order'" in html       # sticky ordering
    assert ">=72)?' crit':''" in html                        # only the critical tier pulses
    assert "rascunho de resposta" in html and "can_draft" in html    # reply path from the queue


# ── obligation grouping: "precisam de resposta" vs "à espera deles" ──────────

def _outbound(purpose="ESTIMATE_SENT_TO_CLIENT"):
    """A reply WE sent — flips the thread's clock to AWAITING (ball in their court)."""
    return {"direction": "outbound", "counterparty": "CLIENT", "purpose": purpose,
            "priority": "MEDIUM", "urgency": 40, "entities": {},
            "confidence": 0.9, "decided_by": "tier1:gemini-2.5-flash", "reason": "enviámos proposta"}


def _reply_env(mid, hours_ago, parent, subject="Orçamento 50 placas"):
    e = _env(mid, hours_ago, frm="orcamentos@lindoservico.pt", subject=subject)
    e["in_reply_to"] = parent
    e["references"] = [parent]
    e["to"] = [{"email": "maria@acme.pt", "name": "Maria"}]
    return e


def test_api_fila_distinguishes_we_owe_from_awaiting(tmp_path):
    """The grouping is only as good as the field it reads: every active row must carry a
    ``clock.state`` the UI can partition on.  If the API ever stopped emitting it (or emitted a
    state outside the known set) groupOf() would silently sweep the WHOLE queue into 'Internos' —
    a queue that looks calm while 34 threads wait on us.  Guards the data contract, not the CSS.
    """
    crm = _crm_with([
        (_env("owe1", 3), _verdict()),                                  # inbound, unanswered
        (_env("wait1", 9), _verdict()),                                 # inbound…
        (_reply_env("wait2", 2, "wait1"), _outbound()),                 # …then WE replied
    ])
    rows = _client(tmp_path, crm)[0].get("/api/fila").json()["rows"]
    states = {r["thread_root"]: r["clock"]["state"] for r in rows}
    assert states.get("mid:owe1") == "WE_OWE", states
    assert states.get("mid:wait1") == "AWAITING", states
    # No row may arrive without a state the UI knows how to group.
    assert all(r["clock"].get("state") in {"WE_OWE", "AWAITING", "HANDLED", "INTERNAL"}
               for r in rows), states


def test_fila_page_groups_queue_by_obligation(tmp_path):
    """WE_OWE and AWAITING are opposite obligations that the clock colour cannot separate (_band()
    encodes urgency only, so both render green when fresh).  The Fila must partition on the state:
    section headers with counts, group as the PRIMARY sort key, and a hollowed dot so a row read
    outside its section still says whose move it is."""
    html = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))[0].get("/fila").text
    # the partition function and its PT-PT section labels
    assert "function groupOf(" in html
    assert "'WE_OWE'" in html and "'AWAITING'" in html
    assert "Precisam de resposta" in html and "À espera deles" in html
    # group is the primary key; the chosen sort survives *within* a group (stable sort, no 2nd key).
    # Since ADR-033 groupOf() returns the TAB-AWARE RANK (semGroup() carries the semantic id), so
    # the same one-line stable partition also lets Fornecedores lead with «A cobrar».
    assert "out.sort((a,b)=>groupOf(a)-groupOf(b))" in html
    assert "function semGroup(" in html
    # the ledger stays one flat pile — grouping applies to the active queue only
    assert "if(mode==='tratados') return out;" in html
    # headers render with a count, and are sticky so they survive scrolling a long queue
    assert 'class="ghead ' in html and 'class="gh-n"' in html
    assert ".ghead{position:sticky" in html
    # obligation also encoded per-row: hollow dot whenever the ball is NOT ours (wait + chase)
    assert "semGroup(r)!==G_OWE?' wait':''" in html
    assert ".clock.wait .d{background:transparent" in html


# ── ADR-033 Phase 0 — «Mesa com Foco» quick relief ───────────────────────────

def _p0_page(tmp_path):
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    return cl.get("/").text


def test_fila_headline_counts_only_what_demands_us(tmp_path):
    """The old chip counted every red+amber row — including the passive AWAITING pile — so «88 em
    risco» inflated the workload. The headline now splits honestly: «a responder» = WE_OWE
    red+amber; «a cobrar» = AWAITING past the chase threshold; each half click-filters."""
    html = _p0_page(tmp_path)
    assert "a responder" in html
    assert 'id="_cobrar"' in html and "a cobrar" in html
    assert "c.state==='WE_OWE'" in html          # respondCount predicate
    assert "c.state==='AWAITING'" in html        # chaseCount predicate
    # both chips filter via pseudo-band values, like the old risk chip did
    assert "'chase'" in html
    assert "0 a responder" in html               # the zero state stays honest too


def test_fila_sections_collapse_and_awaiting_starts_collapsed(tmp_path):
    """«À espera deles» is a status report, not a to-do list (ADR-029) — it must be collapsible to a
    counted header, START collapsed, and remember the choice. Collapsed rows leave view() so J/K
    never walks invisible rows."""
    html = _p0_page(tmp_path)
    assert "DEFAULT_COLLAPSED" in html and "[G_WAIT]:true" in html
    assert "fila-collapsed" in html              # persisted choice
    assert "function toggleGroup(" in html and 'data-g="' in html
    assert "function viewAll(" in html           # counts come from the un-collapsed set
    # …and view() excludes collapsed groups, keyed SEMANTICALLY (folding «À espera» must fold the
    # same pile on every tab, whatever rank the tab gives it — ADR-033 P1)
    assert "isCollapsed(semGroup(r))" in html
    # the grouping contract of ADR-029 is untouched
    assert "out.sort((a,b)=>groupOf(a)-groupOf(b))" in html


def test_fila_trust_and_owner_chips_collapse_off_focus(tmp_path):
    """«sem dono» ×112 and «Gemini · 95%» ×~100 said nothing (a repeated label is not signal —
    cockpit-design §9). Off-focus rows carry a 2px trust dot and no owner chip; the focused row
    keeps the full chips and every action."""
    html = _p0_page(tmp_path)
    assert 'class="tdot' in html                 # dot rendering exists
    assert ".tdot.proposed" in html and ".tdot.committed" in html
    # both suppressions are focus-conditional, not deletions
    assert html.count("i===focus") >= 2


def test_fila_freshness_stamp_present(tmp_path):
    """The clocks demand trust they could not prove: the page now says how old the synced mail is
    («correio há N min»), turning amber when ingestion stalls (ADR-023's failure case)."""
    html = _p0_page(tmp_path)
    assert "const SYNCED_AT" in html and 'id="_fresh"' in html
    assert "function _agoLabel(" in html and "paintFreshness" in html
    assert "45*60" in html                       # the stale threshold


def test_build_fila_html_accepts_synced_at():
    html = fila_page.build_fila_html([], [], now_iso="2026-07-23T10:00:00",
                                     synced_at="2026-07-23T09:56:00+00:00")
    assert "SYNCED_AT" in html and "2026-07-23T09:56:00+00:00" in html


def test_slash_focuses_fila_search_via_shell_hook(tmp_path):
    """`/` must focus the visible search box on the Fila (the natural gesture), while every other
    lens keeps `/` = palette: the shell dispatches through an optional onSlash() hook."""
    html = _p0_page(tmp_path)
    assert "typeof onSlash==='function'" in html   # shell hook plumbing
    assert "function onSlash()" in html            # the Fila override…
    fn = html.split("function onSlash()")[1].split("\n")[0]
    assert "_search" in fn                         # …focuses the search input


def test_para_ti_keeps_palette_on_slash(tmp_path):
    """The hook must not change other lenses: Para ti defines no onSlash, so `/` still opens the
    palette there (the typeof check falls through)."""
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    html = cl.get("/para-ti").text
    assert "typeof onSlash==='function'" in html
    assert "function onSlash()" not in html


def test_fila_shift_jk_jumps_sections(tmp_path):
    """Reaching «À espera deles» cost ~5 screens of scrolling; Shift+J/K now jumps between group
    headers directly."""
    html = _p0_page(tmp_path)
    assert "e.shiftKey" in html and "(e.key==='J'||e.key==='K')" in html


def test_fila_row_background_click_opens_thread(tmp_path):
    """A row is one big target: clicking anywhere on it (outside buttons and the expanded thread)
    opens the conversation — a cursor:pointer that only focuses invites the click and discards it
    (cockpit-design §9)."""
    html = _p0_page(tmp_path)
    assert "dispatch('thread',i)" in html


# ── ADR-033 Phase 1 — the Mesa (split-pane + tabs + dossier + timeline) ──────

def test_fila_ships_counterparty_tabs_with_counts(tmp_path):
    """Counterparty fronts are structure, not a filter chip (hard owner requirement): first-class
    tabs Hoje · Clientes · Fornecedores · Leads with live counts, carried in the URL (?tab=) and
    cyclable from the keyboard (t / T)."""
    html = _p0_page(tmp_path)
    assert 'id="_tabs"' in html
    for label in ("Hoje", "Clientes", "Fornecedores", "Leads"):
        assert label in html
    assert "p.set('tab'" in html and "get('tab')" in html
    assert "function cycleTab(" in html and "e.key==='t'" in html


def test_fila_mesa_layout_split_pane(tmp_path):
    """The Mesa: full-width split pane — bounded queue left, dossier right — stacked below 1100px.
    The 1000px .wrap cap (a third of the screen was dead gutter) is gone on this lens."""
    html = _p0_page(tmp_path)
    assert 'class="mesa"' in html and 'id="_doss"' in html
    assert "mesa-body" in html and "@media (max-width:1100px)" in html
    assert 'id="_vrail"' in html            # the vistas rail
    for vista in ("Em risco", "Cobranças", "Tratados"):
        assert vista in html


def test_fila_group_order_is_tab_aware_chase_first_for_suppliers(tmp_path):
    """Inside the Fornecedores tab the queue leads with «A cobrar» (the actionable chase list),
    while Hoje/Clientes lead with «Precisam de resposta». One partition mechanism, per-tab rank."""
    html = _p0_page(tmp_path)
    assert "A cobrar" in html                              # the chase group label exists
    assert "G_CHASE" in html and "TAB_SEQ" in html
    assert "SUPPLIER:[G_CHASE" in html                     # suppliers: chase first


def test_fila_focus_is_keyed_by_thread_root(tmp_path):
    """Focus is content-keyed (ADR-023 prerequisite): a re-render or refresh can reorder the queue
    without re-pointing the caret at a different conversation."""
    html = _p0_page(tmp_path)
    assert "let focusRoot" in html
    assert "r.thread_root===focusRoot" in html             # focus derived from the id, not the index


def test_fila_dossier_mounts_focused_thread(tmp_path):
    """The dossier auto-mounts the focused (riskiest, at load) thread: identity strip, verb bar with
    PRINTED keys, the AI's reason always visible, the staged-draft slot («a app nunca envia»), and
    the conversation — rendered by the SAME _threadHTML/msgHTML kit as before (one render path:
    reading never inserts markup into the list)."""
    html = _p0_page(tmp_path)
    assert "function dossierHTML(" in html and "function renderDossier(" in html
    assert "<kbd>E</kbd>" in html and "<kbd>A</kbd>" in html    # verbs teach their keys
    assert "Responder" in html and "Adiar" in html              # P3 verbs visible, honestly disabled
    assert "a app nunca envia" in html
    # one render path: _threadHTML appears exactly twice — its definition and the dossier call
    assert html.count("_threadHTML(") == 2
    # the reclassify pickers moved to the dossier; row badges now FILTER (the natural gesture)
    assert 'data-act="fcp"' in html


def test_fila_vertical_timeline_marks_silences_and_now():
    """The dossier's vertical conversation timeline: one direction-coded marker per message
    (newest first, matching the message order), silences ≥24h rendered as LABELED gaps rather than
    compressed away, and the rail terminating at «agora» in the clock's band colour — the current
    open silence is the response debt, drawn as the growing tail of the thread."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available — the shipped JS cannot be executed")
    fn = "function timelineHTML(" + fila_page._LENS_JS.split("function timelineHTML(")[1].split("/* end-timeline */")[0]
    harness = (
        "function esc(s){return String(s==null?'':s);}\n" + fn +
        "const msgs=[{message_id:'m1',date:'2026-07-01T10:00:00+00:00',direction:'inbound'},\n"
        "            {message_id:'m2',date:'2026-07-05T10:00:00+00:00',direction:'outbound'},\n"
        "            {message_id:'m3',date:'2026-07-05T12:00:00+00:00',direction:'inbound'}];\n"
        "const clock={state:'WE_OWE',band:'red',label:'devemos resposta há 13 dias'};\n"
        "console.log(timelineHTML(msgs, clock));\n"
    )
    out = subprocess.run([node, "-e", harness], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    tl = out.stdout
    assert tl.count("data-tl=") == 3                       # one marker per message
    assert 'data-tl="0"' in tl                             # newest-first indexing (matches .tmsg order)
    assert "4 dias sem resposta" in tl                     # the m1→m2 silence is labeled, not hidden
    assert "agora" in tl and "vtl-now red" in tl           # the open tail carries the band colour
    assert "vtl-m in" in tl and "vtl-m out" in tl          # direction is the marker encoding


def test_api_fila_rows_carry_display_name_and_cluster(tmp_path):
    """Rows lead with the curated human name, never the raw address when a name exists: the v8
    counterparty_names override wins, and each row carries its cluster's rollup (the counterparty
    history card reads it without a second request)."""
    cl, ws = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    rows = {x["thread_root"]: x for x in cl.get("/api/fila").json()["rows"]}
    assert rows["mid:t1"]["display_name"]                  # always present (derived fallback)
    assert rows["mid:t1"]["cluster"]["key"] == "acme.pt"
    assert isinstance(rows["mid:t1"]["cluster"]["msg_count"], int)
    ws.set_counterparty_name("acme.pt", "ACME Metalomecânica")
    rows = {x["thread_root"]: x for x in cl.get("/api/fila").json()["rows"]}
    assert rows["mid:t1"]["display_name"] == "ACME Metalomecânica"   # the precious override wins


# ── ADR-033 Phase 2 — live + data joins + vistas + bulk ──────────────────────

def _verdict_ent(cp="CLIENT", purpose="ESTIMATE_REQUEST_FROM_CLIENT", **ents):
    v = _verdict(cp, purpose)
    v["entities"] = ents
    return v


def test_api_fila_rows_carry_entities_absent_when_unknown(tmp_path):
    """Extracted entities join the row — with a parsed money_value for the € vista — and a row whose
    verdict extracted nothing carries NO entities key: absence, never a placeholder (no-fake-numbers)."""
    crm = _crm_with([
        (_env("t1", 3), _verdict_ent(money="€ 1.200", deadline="2026-08-01",
                                     product_or_service="letras LED", action_requested="orçamento com montagem")),
        (_env("t2", 4, frm="joao@beira.pt", subject="Outro"), _verdict()),
    ])
    rows = {x["thread_root"]: x for x in _client(tmp_path, crm)[0].get("/api/fila").json()["rows"]}
    e = rows["mid:t1"]["entities"]
    assert e["money"] == "€ 1.200" and e["money_value"] == 1200.0
    assert e["deadline"] == "2026-08-01" and e["product_or_service"] == "letras LED"
    assert "entities" not in rows["mid:t2"]


def test_api_fila_rows_carry_chase_novo_momentum_and_related(tmp_path):
    """The joined decision signals: chase (AWAITING past 72h), novo (first contact ≤14d — flags the
    rarest, highest-value event), momentum, and the cross-thread related count (same contact or
    shared entity — the double-answer guard)."""
    real_now = datetime.now(timezone.utc)
    fresh = _env("t4", 0, frm="nova@boreal.pt", subject="Letras LED")
    fresh["date"] = (real_now - timedelta(hours=3)).isoformat()   # «novo» is a WALL-CLOCK concept
    crm = _crm_with([
        (_env("t1", 3), _verdict()),
        (_env("t2", 5, subject="Segundo pedido"), _verdict()),   # same contact, separate thread
        (_env("t3", 100, frm="forn@aco.pt", subject="Encomenda"),
         {**_verdict(cp="SUPPLIER", purpose="OUR_ORDER_TO_SUPPLIER"), "direction": "internal"}),
        (fresh, _verdict(cp="LEAD")),
    ])
    rows = {x["thread_root"]: x for x in _client(tmp_path, crm)[0].get("/api/fila").json()["rows"]}
    assert rows["mid:t3"]["chase"] is True                    # AWAITING amber = past the chase cutoff
    assert rows["mid:t1"]["chase"] is False
    assert rows["mid:t4"]["novo"] is True                     # first contact 3h ago (real clock)
    assert rows["mid:t1"]["novo"] is False                    # established contact (weeks old)
    assert rows["mid:t1"]["momentum"] in ("active", "slowing", "stalled")
    assert rows["mid:t1"]["related_count"] >= 1               # t2 shares the contact
    assert rows["mid:t3"]["related_count"] == 0


def test_api_fila_carries_freshness_badges_and_needs_review(tmp_path):
    """The poll updates everything in one round-trip (mirrors /api/para-ti): synced_at + syncing +
    nav_counts + the NEEDS_REVIEW count that finally gets a surface (the «rever N» chip)."""
    crm = _crm_with([(_env("t1", 3), _verdict()),
                     (_env("t2", 4, frm="x@spam.biz", subject="??"),
                      {**_verdict(), "priority": "NEEDS_REVIEW"})])
    d = _client(tmp_path, crm)[0].get("/api/fila").json()
    assert "synced_at" in d and "syncing" in d
    assert isinstance(d["nav_counts"], dict)
    assert d["needs_review"] == 1


def test_fila_routes_forbid_http_caching(tmp_path):
    """The queue is rebuilt per request; the only way to serve a stale one is an HTTP cache in front
    of us. Both the page and its API opt out (same rule Para ti already pins)."""
    cl, _ = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    assert cl.get("/").headers.get("cache-control") == "no-store"
    assert cl.get("/api/fila").headers.get("cache-control") == "no-store"


def test_fila_page_polls_itself_in_place(tmp_path):
    """ADR-023 reaches the hero: 30s poll, hidden-tab pause, signature diff (state|band|owners), the
    swap carries fetched threads/drafts across by root, never swaps under an open picker, and
    «Sincronizar» refreshes in place instead of location.reload() (the onSynced shell hook)."""
    html = _p0_page(tmp_path)
    assert "REFRESH_MS" in html and "'/api/fila'" in html and "no-store" in html
    assert "function _sig(" in html and "visibilitychange" in html
    assert "function onSynced()" in html                     # the Fila's no-reload sync hook
    assert "typeof onSynced==='function'" in html            # …dispatched by the shell
    assert "_menu" in html.split("async function refresh(")[1].split("setInterval")[0]


def test_fila_signal_tiles_and_entity_chips(tmp_path):
    """The dossier's signal tiles (Em jogo / Prazo / Resposta / Ritmo) and the row's dashed entity
    chips: an AI-extracted € renders dashed with a ? (proposed, never solid), deadlines as ⚑."""
    html = _p0_page(tmp_path)
    assert "Em jogo" in html and "Ritmo" in html and "A abrandar" in html
    assert "valor estimado (IA)" in html
    assert "money_value" in html and "⚑" in html
    assert 'class="rchip money"' in html


def test_fila_money_and_prazos_vistas(tmp_path):
    """€ em jogo (money desc, explicitly AI-estimated) and Prazos (days-left asc) on keys 2/3 —
    fixed vistas over the same queue, flat-rendered, honestly bannered."""
    html = _p0_page(tmp_path)
    assert 'data-vista="money"' in html and 'data-vista="prazos"' in html
    assert "e.key==='2'" in html and "e.key==='3'" in html
    assert "valores estimados pela IA" in html               # the € vista banner
    assert "p.set('vista'" in html and "get('vista')" in html


def test_fila_bulk_select_structurally_excludes_ignore(tmp_path):
    """X selects, Shift+X selects the group, and the bulk verbs are tratado/dono ONLY — a mass
    silent bin is the one unrecoverable triage mistake, so bulk IGNORE does not exist as a control."""
    html = _p0_page(tmp_path)
    assert "e.key==='x'" in html and "selecionadas" in html
    assert 'data-bulk="handled"' in html and 'data-bulk="owner"' in html
    assert 'data-bulk="ignore"' not in html and "bulkIgnore" not in html


def test_fila_rever_chip_surfaces_needs_review(tmp_path):
    """NEEDS_REVIEW finally gets a surface: a quiet chip in the strip, linking to Para ti."""
    html = _p0_page(tmp_path)
    assert 'id="_rever"' in html and "const NEEDS_REVIEW" in html
    assert "paintRever" in html and "/para-ti" in html


# ── ADR-033 Phase 3 — Adiar + contextual R + Tratar agora ────────────────────

def test_api_thread_snooze_defers_and_clear_restores(tmp_path):
    cl, ws = _client(tmp_path, _crm_with([(_env("t1", 3), _verdict())]))
    assert cl.post("/api/thread/snooze", json={}).status_code == 400
    until = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    assert cl.post("/api/thread/snooze",
                   json={"thread_root": "mid:t1", "until": until}).status_code == 200
    assert "mid:t1" not in [x["thread_root"] for x in cl.get("/api/fila").json()["rows"]]
    assert ws.thread_snoozes()["mid:t1"]["until_ts"] == until      # precious, survives re-runs
    assert cl.post("/api/thread/snooze",
                   json={"thread_root": "mid:t1", "until": None}).status_code == 200
    assert "mid:t1" in [x["thread_root"] for x in cl.get("/api/fila").json()["rows"]]


def test_api_reply_draft_maps_purpose_to_composer(tmp_path):
    """Contextual R (design §10, shipped mapping): a JobSpec thread points at the tested /api/reply;
    OUTBOUND_INVOICE gets the payment composer; everything else gets follow_up. Deterministic — no
    LLM in this route; never sends."""
    crm = _crm_with([
        (_env("t1", 3), _verdict()),                                          # plain client ask
        (_env("t2", 4, frm="fat@acme.pt", subject="Fatura 123"),
         _verdict(purpose="OUTBOUND_INVOICE")),
    ])
    ws = Workspace(tmp_path / "w.db").connect()
    app = create_app({"team": ["Diogo"]}, workspace=ws, jobspecs={"t1": {}},       # t1 has a JobSpec (keyed by message_id, not thread_root)
                     prepared=([], [], {}), reply_pb="", crm_store=crm)
    cl = TestClient(app)
    assert cl.post("/api/thread/reply-draft", json={}).status_code == 400
    assert cl.post("/api/thread/reply-draft",
                   json={"thread_root": "mid:nope"}).status_code == 404
    d1 = cl.post("/api/thread/reply-draft", json={"thread_root": "mid:t1"}).json()
    assert d1["kind"] == "jobspec_ask" and d1["redirect"] == "/api/reply"
    d2 = cl.post("/api/thread/reply-draft", json={"thread_root": "mid:t2"}).json()
    assert d2["kind"] == "payment" and d2["draft"]                 # deterministic template body


def test_fila_snooze_verb_menu_and_wake_copy(tmp_path):
    """H adiar: quick choices, the wake-on-reply promise in the copy, optimistic + undoable."""
    html = _p0_page(tmp_path)
    assert 'data-act="snooze"' in html and "function snoozeMenu(" in html
    assert "amanhã 09:00" in html and "2ª feira 09:00" in html
    assert "acorda antes se responderem" in html                   # the never-lose-a-client promise
    assert "'/api/thread/snooze'" in html
    assert "e.key==='h'||e.key==='H'" in html


def test_fila_contextual_r_wiring(tmp_path):
    html = _p0_page(tmp_path)
    assert 'data-act="reply"' in html and "function contextualReply(" in html
    assert "'/api/thread/reply-draft'" in html
    assert "d.redirect" in html                                    # JobSpec path stays on /api/reply
    assert "e.key==='r'||e.key==='R'" in html


def test_fila_tratar_agora_mode(tmp_path):
    """F walks the SAME filtered, risk-ordered queue one decision at a time — progress «N de M»,
    → skips free, Esc exits. No second queue, no separate order (ADR-033 rejected any)."""
    html = _p0_page(tmp_path)
    assert 'id="_foco"' in html and "Tratar agora" in html
    assert "e.key==='f'||e.key==='F'" in html
    assert "ArrowRight" in html


def test_novo_stays_silent_when_the_corpus_cannot_know(tmp_path):
    """«novo» honesty (live-data finding): a corpus whose OLDEST mail is itself recent cannot
    distinguish a new contact from an old client it only started reading — so the badge must stay
    absent rather than flag everyone. It appears only when mail exists from ≥7 days before the
    contact's first appearance."""
    real_now = datetime.now(timezone.utc)
    def at(hours_ago, mid, frm):
        e = _env(mid, 0, frm=frm)
        e["date"] = (real_now - timedelta(hours=hours_ago)).isoformat()
        return e
    # Shallow corpus: everything within 3 days → no depth → nobody is «novo».
    shallow = _crm_with([(at(3, "s1", "a@x.pt"), _verdict()),
                         (at(60, "s2", "b@y.pt"), _verdict())])
    rows = {x["thread_root"]: x for x in _client(tmp_path, shallow)[0].get("/api/fila").json()["rows"]}
    assert all(r["novo"] is False for r in rows.values())
    # Deep corpus: months of history, then a first-time sender 3h ago → that one IS «novo».
    deep = _crm_with([(at(24 * 60, "d1", "velho@x.pt"), _verdict()),
                      (at(3, "d2", "novo@z.pt"), _verdict())])
    rows = {x["thread_root"]: x for x in _client(tmp_path, deep)[0].get("/api/fila").json()["rows"]}
    assert rows["mid:d2"]["novo"] is True
    assert rows["mid:d1"]["novo"] is False
