"""Webapp smoke: live report renders, confirm recomputes readiness, reply route (LLM monkeypatched)."""

import pytest

from email2data import jobspec as js
from email2data.workspace import Workspace

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from email2data import webapp  # noqa: E402

JOB = js.build_jobspec(
    {"message_id": "m1", "subject": "Pedido troféus", "counterparty": "CLIENT",
     "purpose": "ESTIMATE_REQUEST_FROM_CLIENT", "entities": {"product_or_service": "troféus"}},
    {"attachments": [{"filename": "spec.pdf"}], "subject": "x", "body_text": "b"},
).to_dict()
JOB["draft_reply"] = "Olá, obrigado pelo pedido."
EMAIL = {"message_id": "m1", "subject": "Pedido troféus", "priority": "HIGH", "counterparty": "CLIENT", "_jobspec": JOB}
SETTINGS = {"llm": {"provider": "vertex_gemini", "model": "gemini-2.5-flash"}}


def _client(tmp_path):
    ws = Workspace(tmp_path / "w.db").connect()
    app = webapp.create_app(SETTINGS, workspace=ws, jobspecs={"m1": JOB}, reply_pb="pb", prepared=([EMAIL], [], {}))
    return TestClient(app)


def test_index_renders_the_live_report(tmp_path):
    r = _client(tmp_path).get("/inbox")
    assert r.status_code == 200
    assert "const LIVE=true" in r.text          # served in live mode (editable panel)
    assert "Especificação" in r.text and "Pedido troféus" in r.text


def test_faceted_filter_panel_wired(tmp_path):
    """The generic, tab-aware facet engine + a filter container per tab must be present in the rendered
    HTML, so a template break (renamed function, dropped facet, missing container) is caught without a
    browser."""
    html = _client(tmp_path).get("/inbox").text
    assert "const TABFILTERS=" in html and "function renderFilters(" in html
    assert "function applyFacets(" in html and "function facetCounts(" in html
    # one filter container per tab
    for cid in ('id="filters"', 'id="cfilters"', 'id="lfilters"', 'id="pfilters"'):
        assert cid in html
    # representative group labels across the four registries
    for group in ("Prioridade", "Entidades", "Atividade", "Espera", "Estágio", "Cobertura"):
        assert group in html


def test_sync_endpoint_refreshes_render_state(tmp_path, monkeypatch):
    """POST /api/sync runs the incremental sync then rebuilds the cached emails/jobspecs, so a newly
    triaged email shows on the next GET / without restarting the server."""
    from email2data import sync as syncmod
    NEW = {"message_id": "m2", "subject": "Novo lead", "priority": "HIGH", "counterparty": "LEAD"}
    monkeypatch.setattr(syncmod, "run_sync", lambda settings, **k:
                        {"fetched": 1, "triaged_new": 1, "triaged_skipped": 0,
                         "offline": 0, "llm": 1, "failed": 0})
    monkeypatch.setattr(webapp.report, "prepare", lambda s: ([EMAIL, NEW], [], {}))
    monkeypatch.setattr(webapp, "_load_jobspecs", lambda out: {"m1": JOB})

    # __settings_path__ so _rebuild_state's _outdir() resolves (sync=off so no startup thread fires).
    settings = {**SETTINGS, "__settings_path__": str(tmp_path / "config" / "settings.json"),
                "sync": {"on_startup": False}}
    ws = Workspace(tmp_path / "w.db").connect()
    app = webapp.create_app(settings, workspace=ws, jobspecs={"m1": JOB}, reply_pb="pb",
                            prepared=([EMAIL], [], {}))
    c = TestClient(app)
    assert "Novo lead" not in c.get("/inbox").text          # not yet present
    r = c.post("/api/sync", json={})
    assert r.status_code == 200 and r.json()["triaged_new"] == 1
    assert "Novo lead" in c.get("/inbox").text              # state rebuilt → new email rendered


def test_sync_endpoint_409_when_already_running(tmp_path):
    """A concurrent sync (e.g. the startup background thread still working) returns 409, not a
    second IMAP/LLM run."""
    c = _client(tmp_path)
    assert c.app.state.sync_lock.acquire(blocking=False)
    try:
        r = c.post("/api/sync", json={})
        assert r.status_code == 409 and r.json()["running"] is True
    finally:
        c.app.state.sync_lock.release()


def test_confirm_persists_and_recomputes(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/confirm", json={"message_id": "m1", "field": "material#0", "value": "acrílico"})
    assert r.status_code == 200
    b = r.json()
    assert b["items"][0]["material"]["value"] == "acrílico" and b["items"][0]["material"]["source"] == "user"
    assert "material#0" not in b["readiness"]["missing"]
    assert c.post("/api/confirm", json={"message_id": "m1", "field": "bogus", "value": "x"}).status_code == 400
    assert c.post("/api/confirm", json={"message_id": "zzz", "field": "material#0", "value": "x"}).status_code == 400


def test_add_and_remove_item(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/item/add", json={"message_id": "m1"})
    assert r.status_code == 200 and len(r.json()["items"]) == 2
    c.post("/api/confirm", json={"message_id": "m1", "field": "item#1", "value": "expositor"})
    r = c.post("/api/item/remove", json={"message_id": "m1", "index": 0})
    assert r.status_code == 200 and len(r.json()["items"]) == 1
    assert r.json()["items"][0]["item"]["value"] == "expositor"        # survivor renumbered to #0
    # cannot remove the last remaining item
    assert c.post("/api/item/remove", json={"message_id": "m1", "index": 0}).status_code == 400


def test_reply_route_uses_replydraft(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp.classifier, "make_client", lambda s: object())
    monkeypatch.setattr(webapp.replydraft, "draft_reply", lambda *a, **k: "RASCUNHO GERADO")
    r = _client(tmp_path).post("/api/reply", json={"message_id": "m1"})
    assert r.status_code == 200 and r.json()["reply"] == "RASCUNHO GERADO"


def test_reply_stream_route_streams_chunks(tmp_path, monkeypatch):
    """The streaming route assembles provider chunks into the same draft, and 404s on unknown id
    WITHOUT constructing a client (so a bad request never touches the LLM)."""
    monkeypatch.setattr(webapp.classifier, "make_client", lambda s: object())
    monkeypatch.setattr(webapp.replydraft, "draft_reply_stream",
                        lambda *a, **k: (c for c in ["Olá, ", "obrigado ", "pelo pedido."]))
    c = _client(tmp_path)
    r = c.post("/api/reply/stream", json={"message_id": "m1"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert r.text == "Olá, obrigado pelo pedido."          # chunks reassembled in order
    assert c.post("/api/reply/stream", json={"message_id": "zzz"}).status_code == 404


def test_call_stream_dispatches_per_provider(monkeypatch):
    """llm.call_stream yields whatever the provider stream yields (provider plumbing, mocked)."""
    from email2data import llm
    monkeypatch.setattr(llm, "_gemini_stream", lambda *a: iter(["ge", "mini"]))
    monkeypatch.setattr(llm, "_anthropic_stream", lambda *a: iter(["anth", "ropic"]))
    assert "".join(llm.call_stream(None, {"provider": "vertex_gemini", "model": "m"}, "s", "u")) == "gemini"
    assert "".join(llm.call_stream(None, {"provider": "anthropic", "model": "m"}, "s", "u")) == "anthropic"


def test_project_create_attach_field_and_export(tmp_path, monkeypatch):
    c = _client(tmp_path)
    # create
    r = c.post("/api/projects", json={"title": "Troféus", "from_message": "m1"})
    assert r.status_code == 200
    pid = r.json()["project_id"]
    # listed with coverage/estimable enrichment
    lst = c.get("/api/projects").json()
    assert any(x["project_id"] == pid and "coverage" in x for x in lst)
    # seeded the item from m1's spec
    view = c.get(f"/api/projects/{pid}").json()
    assert view["items"][0]["item"]["value"] == "troféus"
    # confirm a canonical job field
    r = c.post(f"/api/projects/{pid}/field", json={"field": "deadline", "value": "2026-07-01"})
    assert r.status_code == 200 and r.json()["job_fields"]["deadline"]["value"] == "2026-07-01"
    assert c.post(f"/api/projects/{pid}/field", json={"field": "bogus", "value": "x"}).status_code == 400
    # stage transition
    assert c.post(f"/api/projects/{pid}/stage", json={"stage": "WON"}).status_code == 200
    assert c.post(f"/api/projects/{pid}/stage", json={"stage": "NOPE"}).status_code == 400
    # export via monkeypatched orchestration (avoids needing __settings_path__ / network)
    monkeypatch.setattr(webapp._export, "export_project",
                        lambda *a, **k: webapp._export.ExportResult(ok=True, external_id="PRJ-1", detail="ok"))
    r = c.post(f"/api/projects/{pid}/export", json={"adapter": "json"})
    assert r.status_code == 200 and r.json() == {"ok": True, "external_id": "PRJ-1", "detail": "ok"}
    assert c.get("/api/projects/zzz").status_code == 404


def test_projetos_detail_route_serves_page_and_404s(tmp_path):
    """REST deep-link: GET /projetos/<pid> serves the lens for a real project (the page JS reads the
    id from the path and opens that workbench) and 404s on an unknown id — so a stale/shared link
    fails honestly, mirroring GET /contrapartes/<key>."""
    c = _client(tmp_path)
    pid = c.post("/api/projects", json={"title": "Troféus", "from_message": "m1"}).json()["project_id"]
    r = c.get(f"/projetos/{pid}")
    assert r.status_code == 200 and "Projetos" in r.text
    assert c.get("/projetos/p-9999").status_code == 404


def test_projetos_page_wires_the_composer(tmp_path):
    """GET /projetos renders and ships the composer JS (loadDraft + composerHTML + the draft
    endpoints), and the old hard-coded clientEmailText() builder is gone — a template break
    (renamed/dropped function) is caught without a browser."""
    html = _client(tmp_path).get("/projetos").text
    assert "function loadDraft(" in html and "function composerHTML(" in html
    assert "/draft" in html and "Email para o cliente" in html
    assert "clientEmailText" not in html          # the static JS builder was removed


def test_client_email_draft_compose_and_rebuild(tmp_path):
    """The Projetos composer: GET returns the selectable prompts + a body pre-built from the
    missing-must defaults; POST re-assembles for a chosen subset (+ a custom question). The
    internal 'process' prompt is flagged and excluded from the default body."""
    c = _client(tmp_path)
    pid = c.post("/api/projects", json={"title": "Troféus", "from_message": "m1"}).json()["project_id"]

    d = c.get(f"/api/projects/{pid}/draft").json()
    assert d["subject"] == "Re: Troféus"
    keys = {a["key"]: a for a in d["askables"]}
    assert keys["thickness"]["default"] is True              # a missing must → pre-ticked
    assert keys["process"]["internal"] is True               # internal note, surfaced...
    # ...and the default body asks the musts but NOT the internal process note
    assert "espessura" in d["body"].lower() and "(interno" not in d["body"]
    assert d["body"].startswith("Bom dia,")

    # rebuild for an explicit subset + a custom question
    r = c.post(f"/api/projects/{pid}/draft",
               json={"selected": ["thickness"], "custom": ["Têm logótipo em vetor?"]}).json()
    assert "1. Que espessura?" in r["body"] and "2. Têm logótipo em vetor?" in r["body"]
    assert "quantidade" not in r["body"].lower()             # quantity wasn't selected

    assert c.get("/api/projects/zzz/draft").status_code == 404
    assert c.post("/api/projects/zzz/draft", json={"selected": []}).status_code == 404


_EML = (b"From: a@x.pt\r\nSubject: s\r\nMIME-Version: 1.0\r\n"
        b'Content-Type: multipart/mixed; boundary="b"\r\n\r\n'
        b"--b\r\nContent-Type: text/plain\r\n\r\nhello\r\n"
        b'--b\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; filename="spec.pdf"\r\n\r\nPDFBYTES\r\n'
        b"--b--\r\n")


def test_envelope_attachment_part_bytes_only():
    from email2data.envelope import attachment_part
    name, ctype, data = attachment_part(_EML, 0)
    assert name == "spec.pdf" and ctype == "application/pdf" and b"PDFBYTES" in data
    assert attachment_part(_EML, 5) is None   # out of range


def test_attachment_endpoint_serves_and_404s(tmp_path):
    eml = tmp_path / "m.eml"
    eml.write_bytes(_EML)
    ws = Workspace(tmp_path / "w.db").connect()
    app = webapp.create_app(SETTINGS, workspace=ws, jobspecs={"m1": JOB}, reply_pb="pb",
                            prepared=([EMAIL], [], {}), corpus_index={"m1": eml})
    c = TestClient(app)
    r = c.get("/api/attachment/m1/0")
    assert r.status_code == 200 and b"PDFBYTES" in r.content
    assert "spec.pdf" in r.headers["content-disposition"] and "inline" in r.headers["content-disposition"]
    assert c.get("/api/attachment/m1/9").status_code == 404   # bad index
    assert c.get("/api/attachment/zzz/0").status_code == 404   # unknown message


def test_projects_work_with_a_real_crm_store(tmp_path):
    """Production wiring: when out/crm.db exists, create_app injects a CrmStore and project reads expand
    each attached thread_root into its sibling messages. The other webapp tests pass crm_store=None and
    so NEVER exercised this path — which is exactly where the thread-expansion + cross-thread bug lived.
    m1 is a thread root; m2 is a reply in the same thread carrying a deadline that must merge in."""
    from email2data.crm import CrmStore

    crm = CrmStore(tmp_path / "crm.db").connect()
    verdict = {"counterparty": "CLIENT", "purpose": "PO_FROM_CLIENT", "priority": "HIGH",
               "direction": "inbound", "entities": {}}
    env1 = {"message_id": "m1", "from": {"email": "cliente@acme.pt"}, "to": [], "cc": [],
            "subject": "Pedido", "date": "2026-01-01T09:00:00", "references": [],
            "attachments": [{"filename": "spec.pdf"}]}
    env2 = {"message_id": "m2", "from": {"email": "cliente@acme.pt"}, "to": [], "cc": [],
            "subject": "Re: Pedido", "date": "2026-01-02T09:00:00", "references": ["m1"], "attachments": []}
    crm.record(env1, verdict)
    crm.record(env2, verdict)

    j1 = js.build_jobspec({"message_id": "m1", "subject": "Pedido", "counterparty": "CLIENT",
                           "purpose": "PO_FROM_CLIENT", "entities": {"product_or_service": "troféus"}},
                          {"attachments": [{"filename": "spec.pdf"}]}).to_dict()
    j2 = js.build_jobspec({"message_id": "m2", "subject": "Re: Pedido", "counterparty": "CLIENT",
                           "purpose": "PO_FROM_CLIENT", "entities": {"deadline": "2026-07-01"}}, {}).to_dict()

    ws = Workspace(tmp_path / "w.db").connect()
    app = webapp.create_app(SETTINGS, workspace=ws, jobspecs={"m1": j1, "m2": j2}, reply_pb="pb",
                            prepared=([], [], {}), crm_store=crm)
    c = TestClient(app)

    r = c.post("/api/projects", json={"title": "Troféus", "from_message": "m1"})
    assert r.status_code == 200
    pid = r.json()["project_id"]
    # list endpoint must not 500 when a project owns a thread (the reported symptom)
    assert any(x["project_id"] == pid for x in c.get("/api/projects").json())
    view = c.get(f"/api/projects/{pid}").json()
    assert view["threads"] == ["mid:m1"]                   # attached by canonical thread_root
    assert view["message_ids"] == ["m1", "m2"]             # CRM expanded the root to its siblings
    assert view["job_fields"]["deadline"]["value"] == "2026-07-01"   # merged in from the reply m2


def test_project_delete_detach_and_archive_hide(tmp_path):
    """Maintenance routes (Phase 3): DELETE removes a project, /detach removes a thread, and the list
    hides ARCHIVED unless ?archived=1. Covers the 'stuck duplicates / mis-attached thread' gap."""
    c = _client(tmp_path)
    # two projects
    p1 = c.post("/api/projects", json={"title": "Keep"}).json()["project_id"]
    p2 = c.post("/api/projects", json={"title": "Dup"}).json()["project_id"]
    # detach: attach a thread (degraded mode: ref==root), then remove it
    c.post(f"/api/projects/{p1}/attach", json={"ref": "root-x"})
    assert c.get(f"/api/projects/{p1}").json()["threads"] == ["root-x"]
    r = c.post(f"/api/projects/{p1}/detach", json={"ref": "root-x"})
    assert r.status_code == 200 and r.json()["threads"] == []
    assert c.post(f"/api/projects/{p1}/detach", json={"ref": ""}).status_code == 400
    # archive p1 -> hidden by default, visible with ?archived=1
    c.post(f"/api/projects/{p1}/stage", json={"stage": "ARCHIVED"})
    ids = {x["project_id"] for x in c.get("/api/projects").json()}
    assert p1 not in ids and p2 in ids
    assert p1 in {x["project_id"] for x in c.get("/api/projects?archived=1").json()}
    # delete p2 (hard)
    assert c.request("DELETE", f"/api/projects/{p2}").status_code == 200
    assert c.get(f"/api/projects/{p2}").status_code == 404
    assert c.request("DELETE", f"/api/projects/{p2}").status_code == 404


def test_project_view_flags_dangling_threads(tmp_path):
    """Integrity (Phase 5): a thread_root attached to a project but absent from the CRM (e.g. crm.db
    was rebuilt and the root changed) is surfaced as dangling rather than silently dropped."""
    from email2data.crm import CrmStore
    crm = CrmStore(tmp_path / "crm.db").connect()
    crm.record({"message_id": "live", "from": {"email": "c@acme.pt"}, "to": [], "cc": [],
                "subject": "s", "date": "2026-01-01T09:00:00", "references": [], "attachments": []},
               {"counterparty": "CLIENT", "purpose": "PO_FROM_CLIENT", "priority": "HIGH",
                "direction": "inbound", "entities": {}})
    ws = Workspace(tmp_path / "w.db").connect()
    app = webapp.create_app(SETTINGS, workspace=ws, jobspecs={"live": JOB}, reply_pb="pb",
                            prepared=([], [], {}), crm_store=crm)
    c = TestClient(app)
    pid = c.post("/api/projects", json={"title": "X"}).json()["project_id"]
    c.post(f"/api/projects/{pid}/attach", json={"ref": "live"})
    c.post(f"/api/projects/{pid}/attach", json={"ref": "ghost-root"})   # not in CRM
    v = c.get(f"/api/projects/{pid}").json()
    assert v["dangling_threads"] == ["ghost-root"]
    assert "mid:live" in v["threads"]


# ── ADR-015: knowledge capture (custom fields, events, timeline, provenance) ───────────────────

def test_custom_field_renders_but_never_gates_estimability(tmp_path):
    """A custom field is stored + rendered (custom_fields channel) and carries provenance, but is
    tier=context: it must NOT appear in readiness.missing nor flip estimable (ADR-015 G1 fix)."""
    c = _client(tmp_path)
    pid = c.post("/api/projects", json={"title": "Custom"}).json()["project_id"]
    r = c.post(f"/api/projects/{pid}/custom-field",
               json={"name": "Acabamento especial", "value": "anodizado",
                     "channel": "call", "asserted_by": "João", "acquired_at": "2026-06-13"})
    assert r.status_code == 200
    v = r.json()
    addr = "custom:Acabamento especial"
    assert v["custom_fields"][addr]["value"] == "anodizado"          # rendered, not dropped
    assert addr not in v["readiness"]["missing"]                      # never a gate gap
    assert v["readiness"]["estimable"] is False                       # didn't fabricate estimability
    assert v["field_provenance"][addr]["channel"] == "call"
    assert v["field_provenance"][addr]["asserted_by"] == "João"
    # name+value required
    assert c.post(f"/api/projects/{pid}/custom-field", json={"name": "", "value": "x"}).status_code == 400
    # editing a custom field goes through /field (custom: address accepted); a non-registry,
    # non-custom address is still rejected (the zero-hallucination guard on field addresses).
    assert c.post(f"/api/projects/{pid}/field",
                  json={"field": addr, "value": "polido"}).json()["custom_fields"][addr]["value"] == "polido"
    assert c.post(f"/api/projects/{pid}/field", json={"field": "bogus", "value": "x"}).status_code == 400


def test_field_write_carries_provenance_bundle(tmp_path):
    """A normal field write threads the provenance bundle (channel/who/when) through to the store."""
    c = _client(tmp_path)
    pid = c.post("/api/projects", json={"title": "Prov"}).json()["project_id"]
    v = c.post(f"/api/projects/{pid}/field",
               json={"field": "deadline", "value": "2026-08-15",
                     "channel": "meeting", "asserted_by": "Cliente", "acquired_at": "2026-06-10"}).json()
    assert v["job_fields"]["deadline"]["value"] == "2026-08-15"
    assert v["field_provenance"]["deadline"] == {
        "source_mid": "", "channel": "meeting", "asserted_by": "Cliente", "acquired_at": "2026-06-10"}


def test_event_capture_and_timeline(tmp_path):
    """Events (note/decision/opinion/todo) are captured verbatim (no LLM) and surface in the
    timeline newest-first by acquired_at, alongside field edits — one read, no reconstruction."""
    c = _client(tmp_path)
    pid = c.post("/api/projects", json={"title": "Cap"}).json()["project_id"]
    c.post(f"/api/projects/{pid}/field", json={"field": "deadline", "value": "2026-07-01",
                                               "channel": "email", "acquired_at": "2026-06-01"})
    assert c.post(f"/api/projects/{pid}/event",
                  json={"kind": "decision", "text": "avançar em inox",
                        "channel": "call", "asserted_by": "Pedro", "acquired_at": "2026-06-13"}).status_code == 200
    c.post(f"/api/projects/{pid}/event", json={"kind": "note", "text": "cliente sem pressa",
                                               "channel": "meeting", "acquired_at": "2026-06-05"})
    # bad kind / empty text rejected
    assert c.post(f"/api/projects/{pid}/event", json={"kind": "bogus", "text": "x"}).status_code == 400
    assert c.post(f"/api/projects/{pid}/event", json={"kind": "note", "text": ""}).status_code == 400
    tl = c.get(f"/api/projects/{pid}/timeline").json()["timeline"]
    # newest-first by acquired_at: decision(06-13) > note(06-05) > field set(06-01)
    assert [r["new_value"] for r in tl] == ["avançar em inox", "cliente sem pressa", "2026-07-01"]
    assert [r["op"] for r in tl] == ["event", "event", "set"]
    assert tl[0]["field"] == "__decision__" and tl[0]["channel"] == "call" and tl[0]["asserted_by"] == "Pedro"
    assert c.get("/api/projects/zzz/timeline").status_code == 404


def test_projetos_page_ships_capture_ui(tmp_path):
    """The Projetos lens ships the ADR-015 capture UI (TestClient can't run JS, but it can assert
    the wiring is shipped): tab strip, capture surface, timeline, conflict banner, and the new
    project-scoped endpoints the JS calls."""
    html = _client(tmp_path).get("/projetos").text
    for marker in ('class="ptabs"', "function captureHTML", "function timelineHTML", "function showTab",
                   "function contestedBanner", "_registarFromURL", "/custom-field", "/event",
                   "/timeline", 'data-tab="registar"'):
        assert marker in html, marker


def test_from_settings_builds_on_fresh_out_dir(tmp_path):
    """Fresh-volume boot (the Docker first-run): from_settings must construct the app against an empty
    out/ — no results.jsonl/jobspecs/crm.db yet — without raising. Tests elsewhere inject prepared=...
    which short-circuits report.prepare(); this one exercises the REAL build path that bricked
    `docker compose up`. The lifespan boot-sync is disabled so no IMAP/LLM is touched."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "reply_playbook.md").write_text("be brief", encoding="utf-8")
    settings = {**SETTINGS, "__settings_path__": str(cfg_dir / "settings.json"),
                "sync": {"on_startup": False}}
    app = webapp.from_settings(settings)          # must not raise on empty out/
    c = TestClient(app)
    assert c.get("/inbox").status_code == 200      # renders an empty-but-valid report


def test_reply_is_memoized_across_calls(tmp_path, monkeypatch):
    """Reload-safe caching: a 2nd /api/reply for an UNCHANGED spec is served from the server-side memo
    and does NOT re-call the model — fixes the per-reload token re-bill the audit flagged."""
    calls = {"n": 0}
    monkeypatch.setattr(webapp.classifier, "make_client", lambda s: object())

    def fake_draft(*a, **k):
        calls["n"] += 1
        return f"DRAFT {calls['n']}"
    monkeypatch.setattr(webapp.replydraft, "draft_reply", fake_draft)

    c = _client(tmp_path)
    r1 = c.post("/api/reply", json={"message_id": "m1"})
    r2 = c.post("/api/reply", json={"message_id": "m1"})
    assert r1.json()["reply"] == "DRAFT 1" and r2.json()["reply"] == "DRAFT 1"
    assert r2.json().get("cached") is True
    assert calls["n"] == 1                       # model called exactly once despite two requests


def test_reply_cache_busts_when_spec_changes(tmp_path, monkeypatch):
    """A spec change (here: confirming a field) changes the reply prompt → new cache key → regenerate.
    Proves the memo keys on the actual prompt, not just the message_id."""
    calls = {"n": 0}
    monkeypatch.setattr(webapp.classifier, "make_client", lambda s: object())

    def fake_draft(*a, **k):
        calls["n"] += 1
        return f"DRAFT {calls['n']}"
    monkeypatch.setattr(webapp.replydraft, "draft_reply", fake_draft)

    c = _client(tmp_path)
    c.post("/api/reply", json={"message_id": "m1"})                       # call 1, cached
    c.post("/api/confirm", json={"message_id": "m1", "field": "material#0", "value": "inox"})
    c.post("/api/reply", json={"message_id": "m1"})                       # spec changed → call 2
    assert calls["n"] == 2


def test_healthz_liveness_probe(tmp_path):
    """The Docker HEALTHCHECK hits /healthz — it must answer 200 without any DB/LLM/IMAP work."""
    r = _client(tmp_path).get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_reply_stream_is_memoized_and_cross_route(tmp_path, monkeypatch):
    """Stream route caching: a 2nd stream for an unchanged spec replays from the memo (generator NOT
    re-run), and a prior stream populates the cache that the non-stream /api/reply then serves for 0
    tokens. Guards the documented cross-route reuse the audit found untested."""
    calls = {"stream": 0, "draft": 0}
    monkeypatch.setattr(webapp.classifier, "make_client", lambda s: object())

    def fake_stream(*a, **k):
        calls["stream"] += 1
        yield "STREAMED "
        yield "DRAFT"

    def fake_draft(*a, **k):
        calls["draft"] += 1
        return "NONSTREAM"
    monkeypatch.setattr(webapp.replydraft, "draft_reply_stream", fake_stream)
    monkeypatch.setattr(webapp.replydraft, "draft_reply", fake_draft)

    c = _client(tmp_path)
    assert c.post("/api/reply/stream", json={"message_id": "m1"}).text == "STREAMED DRAFT"
    r2 = c.post("/api/reply/stream", json={"message_id": "m1"})           # replay from cache
    assert r2.text == "STREAMED DRAFT" and calls["stream"] == 1           # generator NOT re-run
    r3 = c.post("/api/reply", json={"message_id": "m1"})                  # cross-route: served cached
    assert r3.json() == {"reply": "STREAMED DRAFT", "cached": True} and calls["draft"] == 0
