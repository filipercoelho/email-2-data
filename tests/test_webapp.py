"""Webapp smoke: live report renders, confirm recomputes readiness, reply route (LLM monkeypatched)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from email2data import jobspec as js
from email2data.workspace import Workspace

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from email2data import webapp  # noqa: E402
from conftest import TEST_ADMIN, signed_in_client

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
    return signed_in_client(TestClient(app), ws)


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
    c = signed_in_client(TestClient(app), ws)
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
    assert r.status_code == 200
    # The model's text opens the reply; the signature of the signed-in person closes it (ADR-047).
    # Asserting equality here would pin the closing to this route, where it does not belong.
    assert r.json()["reply"].startswith("RASCUNHO GERADO")
    assert r.json()["reply"].rstrip().endswith(TEST_ADMIN + "\nLindo Serviço")


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
    # Chunks reassembled in order, then closed with the signature (ADR-047). The tail is held back
    # until the generator ends so a model-written sign-off can still be stripped — a stream cannot
    # retract what it has already sent, which is the whole reason that hold exists.
    assert r.text.startswith("Olá, obrigado pelo pedido.")
    assert r.text.rstrip().endswith(TEST_ADMIN + "\nLindo Serviço")
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


def test_projetos_page_wires_the_descritivo_composer(tmp_path):
    """GET /projetos ships the descritivo composer JS (ADR-030): the tab, the loader, the render and
    the AI-polish handlers. A template break (renamed/dropped function, missing button id) is caught
    without a browser."""
    html = _client(tmp_path).get("/projetos").text
    assert 'data-tab="descritivo"' in html and "function loadDescription(" in html
    assert "function descriptionHTML(" in html and "function polishDescription(" in html
    assert "/description/polish" in html and "_descaibtn" in html and "_desccopy" in html


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


def test_description_composer_builds_average_prose_from_confirmed_facts(tmp_path):
    """GET /description assembles the deterministic average-style descritivo (ADR-030) from the
    project's CONFIRMED item fields. An unconfirmed field is a visible gap, not a guess; the title is
    the project name passed through as typed (not upper-cased)."""
    c = _client(tmp_path)
    pid = c.post("/api/projects", json={"title": "Troféus EDP", "from_message": "m1"}).json()["project_id"]

    # Nothing confirmed yet → the body is gap-marked, never invented.
    d0 = c.get(f"/api/projects/{pid}/description").json()
    assert d0["complete"] is False
    assert "[[MATERIAL?]]" in d0["body"] and "MATERIAL" in d0["gaps"]

    # Confirm the per-item facts through the same route the workbench uses (source=user → confirmed).
    for addr, val in [("item#0", "troféu"), ("material#0", "acrílico"), ("thickness#0", "3mm"),
                      ("dimensions#0", "L 300 x A 200 mm"), ("colour_finish#0", "gravação a baixo relevo")]:
        assert c.post(f"/api/projects/{pid}/field", json={"field": addr, "value": val}).status_code == 200

    d = c.get(f"/api/projects/{pid}/description").json()
    assert d["complete"] is True and d["gaps"] == []
    assert d["body"] == (
        "Troféus EDP\n\n"
        "Produção de troféu em acrílico 3mm, c/ L 300 x A 200 mm, gravação a baixo relevo."
    )
    assert d["body"].splitlines()[0] == "Troféus EDP"          # passed through, not "TROFÉUS EDP"
    assert c.get("/api/projects/zzz/description").status_code == 404


def test_description_polish_route_checks_and_returns_both_texts(tmp_path, monkeypatch):
    """POST /description/polish mirrors the client-email polish: both texts back, the facts are
    re-checked, and a fact the model dropped is reported (ADR-030)."""
    from email2data import classifier
    settings = {**SETTINGS, "__settings_path__": str(tmp_path / "config" / "settings.json")}
    ws = Workspace(tmp_path / "w.db").connect()
    app = webapp.create_app(settings, workspace=ws, jobspecs={"m1": JOB}, reply_pb="pb",
                            prepared=([EMAIL], [], {}))
    c = signed_in_client(TestClient(app), ws)
    pid = c.post("/api/projects", json={"title": "Troféus", "from_message": "m1"}).json()["project_id"]
    for addr, val in [("item#0", "troféu"), ("material#0", "acrílico"), ("thickness#0", "3mm"),
                      ("dimensions#0", "L 300 x A 200 mm"), ("colour_finish#0", "gravação")]:
        c.post(f"/api/projects/{pid}/field", json={"field": addr, "value": val})
    monkeypatch.setattr(classifier, "make_client", lambda s: object())

    # A faithful polish keeps every fact (incl. the title on line 1) → missing is empty.
    good = "Troféus\n\nProdução de um belo troféu em acrílico 3mm, c/ L 300 x A 200 mm, com gravação."
    monkeypatch.setattr(webapp.descdraft, "polish_description", lambda *a, **k: good)
    r = c.post(f"/api/projects/{pid}/description/polish", json={"tier": "standard"}).json()
    assert r["body"] == good and r["base"].startswith("Troféus")
    assert r["missing"] == [] and r["dropped_gaps"] == 0 and r["tier"] == "standard"

    # A polish that alters a measurement is caught, not trusted.
    bad = "Troféus\n\nProdução de troféu em acrílico 3mm, c/ L 300 x A 250 mm, com gravação."
    monkeypatch.setattr(webapp.descdraft, "polish_description", lambda *a, **k: bad)
    r = c.post(f"/api/projects/{pid}/description/polish", json={}).json()
    assert "L 300 x A 200 mm" in r["missing"]

    # Loud failure — never dress the unpolished draft up as a success.
    from email2data import llm as _llm
    def _boom(*a, **k):
        raise _llm.LLMError("no creds")
    monkeypatch.setattr(webapp.descdraft, "polish_description", _boom)
    assert c.post(f"/api/projects/{pid}/description/polish", json={}).status_code == 502
    assert c.post("/api/projects/zzz/description/polish", json={}).status_code == 404


def test_description_polish_503_without_settings_path(tmp_path):
    """No settings.json → no paid LLM action, reported honestly (mirrors the email polish)."""
    c = _client(tmp_path)
    pid = c.post("/api/projects", json={"title": "T", "from_message": "m1"}).json()["project_id"]
    assert c.post(f"/api/projects/{pid}/description/polish", json={}).status_code == 503


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
    c = signed_in_client(TestClient(app), ws)
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
    c = signed_in_client(TestClient(app), ws)

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
    c = signed_in_client(TestClient(app), ws)
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
                        "channel": "call", "asserted_by": "Diogo", "acquired_at": "2026-06-13"}).status_code == 200
    c.post(f"/api/projects/{pid}/event", json={"kind": "note", "text": "cliente sem pressa",
                                               "channel": "meeting", "acquired_at": "2026-06-05"})
    # bad kind / empty text rejected
    assert c.post(f"/api/projects/{pid}/event", json={"kind": "bogus", "text": "x"}).status_code == 400
    assert c.post(f"/api/projects/{pid}/event", json={"kind": "note", "text": ""}).status_code == 400
    tl = c.get(f"/api/projects/{pid}/timeline").json()["timeline"]
    # newest-first by acquired_at: decision(06-13) > note(06-05) > field set(06-01)
    assert [r["new_value"] for r in tl] == ["avançar em inox", "cliente sem pressa", "2026-07-01"]
    assert [r["op"] for r in tl] == ["event", "event", "set"]
    assert tl[0]["field"] == "__decision__" and tl[0]["channel"] == "call" and tl[0]["asserted_by"] == "Diogo"
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


def test_deadline_field_renders_as_a_native_date_input(tmp_path):
    """`prazo` (deadline) carries a date+time contract (schema.Entities.deadline), so the workbench
    must offer a native picker rather than a free-text box — and the registry is the single source of
    truth for that, so no other field silently grows a picker."""
    from email2data import jobspec as js
    from email2data import projetos_page as pp

    assert js.INPUT_TYPE == {"deadline": "datetime-local"}
    by_key = {f["key"]: f for f in pp._FIELDS}
    assert by_key["deadline"]["input"] == "datetime-local"
    assert {f["input"] for k, f in by_key.items() if k != "deadline"} == {"text"}

    html = _client(tmp_path).get("/projetos").text
    # registry reached the page (json.dumps spacing differs by embed path)
    assert '"input": "datetime-local"' in html or '"input":"datetime-local"' in html
    assert "function inputType(" in html and "function pickerValue(" in html


def _run_lens_js(body: str):
    """Execute the SHIPPED date helpers from projetos_page._LENS_JS in node, so these tests exercise
    the real code path rather than a re-implementation that could drift from it."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available — the JS date logic can't be executed")
    from email2data import projetos_page as pp
    fns = pp._LENS_JS[pp._LENS_JS.index("const _PICKERS="):pp._LENS_JS.index("function fieldRow(")]
    r = subprocess.run([node, "-e", fns + "\n" + body], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_date_input_degrades_to_text_for_a_non_iso_deadline():
    """The regression this guards: a native picker can only *hold* a value it can parse, so rendering
    a legacy/vague deadline ("meados de agosto") as type=datetime-local shows an EMPTY box — the
    stored value looks lost. inputType() must fall back to text for anything unparseable, while
    accepting BOTH stored shapes (date-only and date+time)."""
    cases = [
        # date-only stays first-class: it is what the extractor/LLM emit and what every pre-clock
        # deadline looks like — it must still get a picker, not the text fallback.
        ("2026-07-01", "datetime-local"),
        ("2026-07-01T14:30", "datetime-local"),
        ("", "datetime-local"),            # empty -> picker (nothing to lose)
        ("2026-02-30", "text"),            # calendar-invalid -> NOT silently rolled to 03-02
        ("2026-07-01T24:00", "text"),      # hour out of range
        ("2026-07-01T14:60", "text"),      # minute out of range
        ("meados de agosto", "text"),
        ("01/07/2026", "text"),            # PT-typed date, not ISO
        ("2026-7-1", "text"),
        ("2026-07-01 14:30", "text"),      # space instead of T
    ]
    body = "const out=[];\n" + "".join(
        f"out.push(inputType({{input:'datetime-local'}}, {json.dumps(v)}));\n" for v, _ in cases
    ) + ("out.push(inputType({input:'text'}, '2026-07-01'));\n"
         "out.push(inputType({input:'date'}, '2026-07-01T14:30'));\n"   # date-only field, dt value
         "console.log(JSON.stringify(out));")
    got = _run_lens_js(body)
    # a text-typed field is never upgraded to a picker just because its value looks like a date; a
    # plain date field never accepts a datetime it cannot display
    assert got == [t for _, t in cases] + ["text", "text"]


def test_date_only_deadline_is_widened_to_midnight_for_display_only():
    """A datetime-local input cannot hold a bare "2026-07-01", so pickerValue() widens it to
    T00:00 purely for rendering. The guard: it must NOT invent a time for anything else — a value
    that already has one is untouched, and a non-ISO value is passed through verbatim so the text
    fallback still shows it. (Nothing is written back: no change event fires without a user edit.)"""
    cases = [
        ("2026-07-01", "2026-07-01T00:00"),      # widened for display
        ("2026-07-01T14:30", "2026-07-01T14:30"),  # already has a time -> untouched
        ("meados de agosto", "meados de agosto"),  # text fallback -> verbatim, never blanked
        ("2026-02-30", "2026-02-30"),              # invalid -> verbatim (text fallback shows it)
        ("", ""),
    ]
    body = "const out=[];\n" + "".join(
        f"out.push(pickerValue({{input:'datetime-local'}}, {json.dumps(v)}));\n" for v, _ in cases
    ) + ("out.push(pickerValue({input:'date'}, '2026-07-01'));\n"     # date field: no widening
         "out.push(pickerValue({input:'text'}, '2026-07-01'));\n"     # text field: no widening
         "console.log(JSON.stringify(out));")
    got = _run_lens_js(body)
    assert got == [e for _, e in cases] + ["2026-07-01", "2026-07-01"]


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
    c = signed_in_client(TestClient(app))
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
    assert r1.json()["reply"].startswith("DRAFT 1") and r2.json()["reply"].startswith("DRAFT 1")
    assert r1.json()["reply"] == r2.json()["reply"]
    assert r2.json().get("cached") is True
    assert calls["n"] == 1                       # model called exactly once despite two requests


def test_the_reply_memo_holds_the_UNSIGNED_body_so_it_cannot_leak_a_signature(tmp_path, monkeypatch):
    """ADR-047: the cache key is the SPEC, which says nothing about who is signed in.

    Signing before caching would mean the second person to open the same thread reads a draft closed
    with the first person's name, function and phone number — a client email sent in the wrong name,
    served from a memo that looks like a pure optimisation. Two people, one unchanged spec, one model
    call: each must get their own closing.
    """
    from conftest import sign_in
    calls = {"n": 0}
    monkeypatch.setattr(webapp.classifier, "make_client", lambda s: object())

    def fake_draft(*a, **k):
        calls["n"] += 1
        return "CORPO DO RASCUNHO"
    monkeypatch.setattr(webapp.replydraft, "draft_reply", fake_draft)

    ws = Workspace(tmp_path / "w.db").connect()
    app = webapp.create_app(SETTINGS, workspace=ws, jobspecs={"m1": JOB}, reply_pb="pb",
                            prepared=([EMAIL], [], {}))
    first = signed_in_client(TestClient(app), ws)
    ws.set_person_profile(ws.person(TEST_ADMIN)["person_id"], signature="Abraço,\n{nome}")
    r1 = first.post("/api/reply", json={"message_id": "m1"}).json()["reply"]

    second = TestClient(app)
    other = sign_in(second, ws, name="Outra Pessoa", is_admin=True)
    ws.set_person_profile(other["person_id"], signature="Cumprimentos,\n{nome} · {cargo}",
                          job_title="Comercial")
    r2 = second.post("/api/reply", json={"message_id": "m1"})

    assert calls["n"] == 1, "the memo stopped working — the model was called twice"
    assert r2.json()["cached"] is True
    assert r1 == "CORPO DO RASCUNHO\n\nAbraço,\nTeste Admin"
    assert r2.json()["reply"] == "CORPO DO RASCUNHO\n\nCumprimentos,\nOutra Pessoa · Comercial"


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
    r1 = c.post("/api/reply/stream", json={"message_id": "m1"})
    assert r1.text.startswith("STREAMED DRAFT")
    r2 = c.post("/api/reply/stream", json={"message_id": "m1"})           # replay from cache
    assert r2.text == r1.text and calls["stream"] == 1                    # generator NOT re-run
    r3 = c.post("/api/reply", json={"message_id": "m1"})                  # cross-route: served cached
    assert r3.json() == {"reply": r1.text, "cached": True} and calls["draft"] == 0
    assert TEST_ADMIN in r1.text


def test_project_cancel_records_party_reason_and_clears_on_reopen(tmp_path):
    """Cancellation lifecycle (ADR-017): CANCELLED carries party (client/supplier/our) + reason;
    a bad party is rejected; reopening clears the close-out."""
    c = _client(tmp_path)
    pid = c.post("/api/projects", json={"title": "T", "from_message": "m1"}).json()["project_id"]
    r = c.post(f"/api/projects/{pid}/stage",
               json={"stage": "CANCELLED", "close_party": "client", "close_reason": "cliente desistiu"})
    assert r.status_code == 200
    proj = r.json()["project"]
    assert proj["stage"] == "CANCELLED" and proj["close_party"] == "client"
    assert proj["close_reason"] == "cliente desistiu" and proj["closed_at"]
    assert c.post(f"/api/projects/{pid}/stage", json={"stage": "LOST", "close_party": "bogus"}).status_code == 400
    reopened = c.post(f"/api/projects/{pid}/stage", json={"stage": "GATHERING"}).json()["project"]
    assert reopened["close_party"] is None and reopened["closed_at"] is None


def test_project_owners_endpoint_sets_and_lists(tmp_path):
    c = _client(tmp_path)
    pid = c.post("/api/projects", json={"title": "T", "from_message": "m1"}).json()["project_id"]
    r = c.post(f"/api/projects/{pid}/owners", json={"owners": ["Diogo", "Marta", "Diogo"]})
    assert r.status_code == 200 and r.json()["owners"] == ["Diogo", "Marta"]      # de-duped
    assert any(p["project_id"] == pid and p["owners"] == ["Diogo", "Marta"]
               for p in c.get("/api/projects").json())                          # surfaced in the list
    assert c.post(f"/api/projects/{pid}/owners", json={"owners": []}).json()["owners"] == []


def test_thread_multi_owner_endpoint_and_legacy_single(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/thread/owner", json={"thread_root": "mid:t1", "owners": ["Diogo", "Marta"]})
    assert r.status_code == 200 and r.json()["owners"] == ["Diogo", "Marta"]
    r2 = c.post("/api/thread/owner", json={"thread_root": "mid:t1", "owner": "Bruno"})   # legacy single
    assert r2.json()["owners"] == ["Bruno"] and r2.json()["owner"] == "Bruno"


def test_roster_add_remove_and_served_to_fila(tmp_path):
    """In-app 'define new owners': a name added via /api/roster augments the settings roster and is
    served to the Fila picker without a restart."""
    c = _client(tmp_path)
    assert c.get("/api/roster").json()["team"] == []          # SETTINGS has no team
    assert "Sofia" in c.post("/api/roster", json={"name": "Sofia"}).json()["roster"]
    assert "Sofia" in c.get("/api/fila").json()["team"]       # effective roster reaches the Fila
    assert c.post("/api/roster", json={"name": ""}).status_code == 400
    c.post("/api/roster/remove", json={"name": "Sofia"})
    assert "Sofia" not in c.get("/api/roster").json()["roster"]


def test_a_name_added_to_the_roster_is_a_real_person(tmp_path):
    """ADR-041/W8: one roster. Before this the picker held free text, so a name could be assignable
    and not be a person — you could give Rita work and could not grant her anything."""
    ws = Workspace(tmp_path / "w.db").connect()
    app = webapp.create_app(SETTINGS, workspace=ws, jobspecs={"m1": JOB}, reply_pb="pb",
                            prepared=([EMAIL], [], {}))
    c = signed_in_client(TestClient(app), ws)
    c.post("/api/roster", json={"name": "Sofia"})
    sofia = ws.person("Sofia")
    assert sofia is not None and sofia["can_login"] is False
    # …and accountable to whoever added her, not to nobody.
    assert ws.person_by_id(sofia["responsible_id"])["name"] == TEST_ADMIN


def test_the_picker_cannot_be_used_to_deactivate_someone_who_signs_in(tmp_path):
    """/api/roster/remove is open to every member. Post-W8 it acts on people, so without this guard a
    picker affordance would have become a way to switch off a colleague's — or an admin's — access."""
    ws = Workspace(tmp_path / "w.db").connect()
    app = webapp.create_app(SETTINGS, workspace=ws, jobspecs={"m1": JOB}, reply_pb="pb",
                            prepared=([EMAIL], [], {}))
    c = signed_in_client(TestClient(app), ws)
    r = c.post("/api/roster/remove", json={"name": TEST_ADMIN})
    assert r.status_code == 400 and "Administração" in r.json()["error"]
    assert ws.person(TEST_ADMIN)["active"] is True
    assert TEST_ADMIN in c.get("/api/roster").json()["protected"]


def test_retiring_an_assignable_owner_keeps_their_past_assignments(tmp_path):
    """Deactivation, not deletion: the thread Sofia owns still says Sofia."""
    ws = Workspace(tmp_path / "w.db").connect()
    app = webapp.create_app(SETTINGS, workspace=ws, jobspecs={"m1": JOB}, reply_pb="pb",
                            prepared=([EMAIL], [], {}))
    c = signed_in_client(TestClient(app), ws)
    c.post("/api/roster", json={"name": "Sofia"})
    ws.set_thread_owner("t1", "Sofia")
    c.post("/api/roster/remove", json={"name": "Sofia"})
    assert "Sofia" not in c.get("/api/roster").json()["roster"]
    assert ws.thread_owners()["t1"] == ["Sofia"]
    assert ws.person("Sofia") is not None


def test_deactivating_someone_in_administracao_drops_them_from_the_picker(tmp_path):
    """The other half of «one roster»: the two surfaces cannot disagree about who exists."""
    ws = Workspace(tmp_path / "w.db").connect()
    app = webapp.create_app(SETTINGS, workspace=ws, jobspecs={"m1": JOB}, reply_pb="pb",
                            prepared=([EMAIL], [], {}))
    c = signed_in_client(TestClient(app), ws)
    diogo = ws.create_person("Diogo", can_login=True)
    assert "Diogo" in c.get("/api/fila").json()["team"]
    c.post(f"/api/admin/people/{diogo['person_id']}", json={"active": False})
    assert "Diogo" not in c.get("/api/fila").json()["team"]


def test_project_participants_rolls_up_asserted_by(tmp_path):
    """Multi-participant surfacing (ADR-015): the people who fed the project (via the capture ledger's
    asserted_by) are rolled up into a per-person contributor list."""
    c = _client(tmp_path)
    pid = c.post("/api/projects", json={"title": "T", "from_message": "m1"}).json()["project_id"]
    c.post(f"/api/projects/{pid}/event", json={"kind": "decision", "text": "avançar",
           "asserted_by": "Diogo", "channel": "call", "acquired_at": "2026-06-13"})
    c.post(f"/api/projects/{pid}/event", json={"kind": "note", "text": "sem pressa",
           "asserted_by": "Marta", "channel": "meeting", "acquired_at": "2026-06-12"})
    parts = c.get(f"/api/projects/{pid}/participants").json()["participants"]
    assert {p["name"] for p in parts} == {"Diogo", "Marta"}
    diogo = next(p for p in parts if p["name"] == "Diogo")
    assert diogo["contributions"] >= 1 and "call" in diogo["channels"]
    assert parts[0]["name"] == "Diogo"                        # newest-first (2026-06-13 > 06-12)


def test_projetos_page_ships_owners_cancel_and_participants(tmp_path):
    """Phase C: the Projetos lens ships the multi-owner picker, the CANCELLED close-out form (party +
    reason) + banner, and the participants ('quem contribuiu') panel — wired to the v4 endpoints."""
    html = _client(tmp_path).get("/projetos").text
    # owners (multi) + in-app roster add
    assert "function ownerPicker(" in html and "/owners" in html and "+ novo dono" in html
    assert "function addRosterOwner(" in html and "/api/roster" in html
    # cancellation lifecycle
    assert "CANCELLED" in html and "function openCloseout(" in html and "Cancelar projeto" in html
    assert "close_party" in html and "function closeoutBannerHTML(" in html and "Cancelado" in html
    # participants surfacing (ADR-015)
    assert "function loadParticipants(" in html and "/participants" in html and "Contribuíram" in html


def test_fila_page_ships_multi_owner_picker(tmp_path):
    """Phase C: the Fila owner control is now a multi-select picker writing the owners list, with an
    in-app '+ novo dono' that adds to the roster."""
    html = _client(tmp_path).get("/fila").text
    assert "function setThreadOwners(" in html and "function toggleThreadOwner(" in html
    assert "function ownerLabel(" in html and "function addFilaOwner(" in html
    assert "+ novo dono" in html and "/api/roster" in html


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# Administração (/admin) + scoped re-extraction + targeted sync.
#
# The load-bearing invariant across this whole block is non-negotiable #5: a password never crosses
# the HTTP boundary in either direction. The first test is the one that must never be deleted.
# ─────────────────────────────────────────────────────────────────────────────────────────────────

SEKRET = "SEKRET-DO-NOT-LEAK"

ADMIN_SETTINGS = {
    "imap": {
        "host": "mail.example.com", "port": 993, "use_ssl": True, "mailbox": "INBOX",
        "accounts": [
            {"id": "diogo", "username": "diogo.costa@lindoservico.pt",
             "password_env": "E2D_TEST_DIOGO_PASSWORD",
             "mailboxes": ["INBOX", "INBOX.Trash.clientes.B&APw-rocratik",
                           "Violaine d'Harcourt - 1111", 'INBOX."aspas"']},
            {"id": "geral", "username": "geral@lindoservico.pt",
             "password_env": "E2D_TEST_GERAL_PASSWORD", "mailboxes": ["INBOX"]},
        ],
    },
    "llm": {"provider": "vertex_gemini", "model": "gemini-2.5-flash",
            "vertex_project": "example-gcp-project",
            "tiers": {"heavy": {"model": "gemini-2.5-pro"}}},
    "intake": {"enabled": False, "bot_token_env": "TELEGRAM_BOT_TOKEN",
               "allowlist": [{"telegram_user_id": 123456789, "display_name": "Diogo"}]},
    "paths": {"corpus_dir": "corpus", "out_dir": "out", "captures_dir": "captures"},
    "sync": {"on_startup": False, "interval_minutes": 0},
}


def _admin_app(tmp_path, settings=None, **kw):
    """A settings-FILE-backed app (admin routes need __settings_path__), with the render state
    injected so no real corpus/LLM is touched."""
    import copy
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    s = copy.deepcopy(settings if settings is not None else ADMIN_SETTINGS)
    (cfg / "settings.json").write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
    (cfg / "reply_playbook.md").write_text("be brief", encoding="utf-8")
    s["__settings_path__"] = str(cfg / "settings.json")
    ws = Workspace(tmp_path / "w.db").connect()
    app = webapp.create_app(s, workspace=ws, jobspecs=kw.pop("jobspecs", {"m1": JOB}),
                            reply_pb="pb", prepared=([EMAIL], [], {}), **kw)
    return signed_in_client(TestClient(app), ws), s


def test_admin_accounts_never_leaks_a_password(tmp_path, monkeypatch):
    """THE test of this feature. With a real credential in the environment, /api/admin/accounts and
    the rendered /admin page must report only that it RESOLVED — the value itself appears in neither.
    A bool is the ceiling: no length, no prefix, no hint."""
    monkeypatch.setenv("E2D_TEST_DIOGO_PASSWORD", SEKRET)
    monkeypatch.delenv("E2D_TEST_GERAL_PASSWORD", raising=False)
    c, _ = _admin_app(tmp_path)

    r = c.get("/api/admin/accounts")
    assert r.status_code == 200
    raw_json = r.text
    accs = {a["id"]: a for a in r.json()["accounts"]}
    assert accs["diogo"]["credential_present"] is True        # it DID resolve …
    assert accs["geral"]["credential_present"] is False       # … and a missing one says so
    assert SEKRET not in raw_json                             # … but the value is nowhere
    assert accs["diogo"]["password_env"] == "E2D_TEST_DIOGO_PASSWORD"   # the NAME is fine

    html = c.get("/admin").text
    assert html.startswith("<!doctype html>") and "Administração" in html
    assert SEKRET not in html
    assert "credential_present" in html and 'type="password"' not in html


def test_admin_page_does_not_embed_the_whole_settings_dict(tmp_path, monkeypatch):
    """The page gets an explicit per-account projection, never the settings object. A future key
    (an API project id, a bot token variable, a Telegram user id) must not ride along for free."""
    monkeypatch.setenv("E2D_TEST_DIOGO_PASSWORD", SEKRET)
    c, _ = _admin_app(tmp_path)
    html = c.get("/admin").text
    for leak in ("example-gcp-project", "TELEGRAM_BOT_TOKEN", "123456789", "gemini-2.5-pro"):
        assert leak not in html, f"settings leaked into /admin: {leak}"
    body = c.get("/api/admin/accounts").text
    for leak in ("example-gcp-project", "TELEGRAM_BOT_TOKEN", "123456789"):
        assert leak not in body, f"settings leaked into /api/admin/accounts: {leak}"


def test_admin_escapes_mailbox_names_with_ampersand_and_quotes(tmp_path, monkeypatch):
    """Real mailbox names in this deployment carry ``&`` (modified-UTF-7), ``'`` and ``"``. They are
    embedded as JSON in a <script>, so the raw name is legitimately present there — what must not
    happen is the page breaking out of its script or an attribute. Assert the JSON embed is intact
    and that the shell's ``esc()`` (which the lens runs over every name) is shipped."""
    monkeypatch.setenv("E2D_TEST_DIOGO_PASSWORD", SEKRET)
    c, _ = _admin_app(tmp_path)
    html = c.get("/admin").text
    assert "INBOX.Trash.clientes.B&APw-rocratik" in html      # survived the round-trip intact
    assert "Violaine d'Harcourt - 1111" in html
    assert '\\"aspas\\"' in html                              # the " is JSON-escaped, not raw
    assert "</script>" not in html.split("const ACCOUNTS =")[1].split("\n")[0]
    assert "&amp;" in html and "&lt;" in html                 # esc() helper is present
    # and the API returns them verbatim (no double-escaping in the data layer)
    mbs = c.get("/api/admin/accounts").json()["accounts"][0]["mailboxes"]
    assert "INBOX.Trash.clientes.B&APw-rocratik" in mbs and "Violaine d'Harcourt - 1111" in mbs


def test_admin_save_rejects_a_password_in_the_body(tmp_path, monkeypatch):
    """A body carrying a secret VALUE is rejected outright, not filtered — and settings.json is left
    byte-identical, so a rejected request can never half-apply."""
    monkeypatch.setenv("E2D_TEST_DIOGO_PASSWORD", SEKRET)
    c, s = _admin_app(tmp_path)
    sp = Path(s["__settings_path__"])
    before = sp.read_text(encoding="utf-8")
    good = {"id": "diogo", "username": "p@lindoservico.pt", "host": "mail.example.com",
            "port": 993, "password_env": "E2D_TEST_DIOGO_PASSWORD", "mailboxes": ["INBOX"]}
    r = c.post("/api/admin/accounts", json={"accounts": [{**good, "password": SEKRET}]})
    assert r.status_code == 400
    assert "password_env" in r.json()["error"]
    assert SEKRET not in r.text                     # the rejection does not echo the secret back
    assert sp.read_text(encoding="utf-8") == before
    # a pasted secret in the password_env slot fails the env-NAME rule too
    r2 = c.post("/api/admin/accounts",
                json={"accounts": [{**good, "password_env": "hunter2 not a var name"}]})
    assert r2.status_code == 400 and sp.read_text(encoding="utf-8") == before


def test_admin_save_rejects_duplicate_ids_and_bad_shapes(tmp_path, monkeypatch):
    monkeypatch.setenv("E2D_TEST_DIOGO_PASSWORD", SEKRET)
    c, s = _admin_app(tmp_path)
    sp = Path(s["__settings_path__"])
    before = sp.read_text(encoding="utf-8")
    a = {"id": "diogo", "username": "p@lindoservico.pt", "host": "mail.example.com",
         "port": 993, "password_env": "E2D_A", "mailboxes": ["INBOX"]}
    cases = [
        ({"accounts": [a, {**a, "password_env": "E2D_B"}]}, "duplicado"),   # duplicate id
        ({"accounts": []}, "pelo menos uma"),                               # empty
        ({"accounts": [{**a, "id": ""}]}, "id"),                            # missing id
        ({"accounts": [{**a, "username": ""}]}, "utilizador"),              # missing username
        ({"accounts": [{**a, "mailboxes": []}]}, "caixa"),                  # no mailboxes
        ({"accounts": [{**a, "mailboxes": ["INBOX", "  "]}]}, "vazia"),     # blank mailbox
        ({"accounts": [{**a, "port": 70000}]}, "porta"),                    # bad port
        ({"accounts": [a, {**a, "id": "geral", "password_env": "E2D_B",
                           "host": "other.example.pt"}]}, "mesmo servidor"),  # split host
    ]
    for body, needle in cases:
        r = c.post("/api/admin/accounts", json=body)
        assert r.status_code == 400, f"expected 400 for {body}"
        assert needle in r.json()["error"], f"{needle!r} not in {r.json()['error']!r}"
    assert sp.read_text(encoding="utf-8") == before      # nothing was written by any rejection


def test_admin_save_preserves_every_other_settings_key(tmp_path, monkeypatch):
    """The account editor owns imap.accounts and nothing else. llm / intake / paths / sync must
    round-trip unchanged — this file is also the LLM + intake + paths config, and losing a key here
    would silently reconfigure the whole app."""
    monkeypatch.setenv("E2D_TEST_DIOGO_PASSWORD", SEKRET)
    c, s = _admin_app(tmp_path)
    sp = Path(s["__settings_path__"])
    original = json.loads(sp.read_text(encoding="utf-8"))
    r = c.post("/api/admin/accounts", json={"accounts": [
        {"id": "novo", "username": "novo@lindoservico.pt", "host": "imap.example.pt", "port": 143,
         "password_env": "E2D_NOVO_PASSWORD", "mailboxes": ["INBOX", "INBOX.Sent"]}]})
    assert r.status_code == 200 and r.json()["ok"] is True
    saved = json.loads(sp.read_text(encoding="utf-8"))
    for key in ("llm", "intake", "paths", "sync"):
        assert saved[key] == original[key], f"{key} was mutated by the account save"
    assert [a["id"] for a in saved["imap"]["accounts"]] == ["novo"]
    assert saved["imap"]["accounts"][0]["mailboxes"] == ["INBOX", "INBOX.Sent"]
    assert saved["imap"]["host"] == "imap.example.pt" and saved["imap"]["port"] == 143
    assert saved["imap"]["use_ssl"] is True          # untouched imap sibling keys survive
    # only the env-var NAME is persisted; no bare `password` key anywhere in the written file
    assert set(saved["imap"]["accounts"][0]) == {"id", "username", "password_env", "mailboxes"}
    assert '"password"' not in json.dumps(saved)
    # the in-memory settings were reloaded in place, so the next fetch uses the new account list
    assert [a["id"] for a in s["imap"]["accounts"]] == ["novo"]
    assert s["__settings_path__"] == str(sp)         # the runtime-only key survives the reload
    assert "__settings_path__" not in saved          # …and is never written to disk
    # no temp file left behind by the atomic write
    assert not list(sp.parent.glob("*.writing"))


def test_admin_save_409s_while_a_sync_is_running(tmp_path, monkeypatch):
    """fetch_all reads imap.accounts mid-run; swapping the file under it would give one run two
    different account lists."""
    monkeypatch.setenv("E2D_TEST_DIOGO_PASSWORD", SEKRET)
    c, s = _admin_app(tmp_path)
    before = Path(s["__settings_path__"]).read_text(encoding="utf-8")
    assert c.app.state.sync_lock.acquire(blocking=False)
    try:
        r = c.post("/api/admin/accounts", json={"accounts": [
            {"id": "x", "username": "x@y.pt", "host": "h", "port": 993,
             "password_env": "E2D_X", "mailboxes": ["INBOX"]}]})
        assert r.status_code == 409 and r.json()["running"] is True
    finally:
        c.app.state.sync_lock.release()
    assert Path(s["__settings_path__"]).read_text(encoding="utf-8") == before


def test_admin_reports_cursors_and_recent_account_errors(tmp_path, monkeypatch):
    """The panel must explain a 0: the per-mailbox watermark says when we last looked, and a failed
    account carries the server's own reply (never the credential)."""
    from email2data import sync as syncmod
    monkeypatch.setenv("E2D_TEST_DIOGO_PASSWORD", SEKRET)
    c, s = _admin_app(tmp_path)
    out = Path(s["__settings_path__"]).parents[1] / "out"
    out.mkdir(parents=True, exist_ok=True)
    st = syncmod.SyncStore(out / "sync.db").connect()
    st.set_cursor("diogo", "INBOX", 42, 1234)
    st.close()
    monkeypatch.setattr(syncmod, "run_sync", lambda settings, **k: {
        "fetched": 0, "triaged_new": 0, "triaged_skipped": 0, "offline": 0, "llm": 0, "failed": 0,
        "crm_recorded": 0, "per_account": {"diogo": 0, "geral": 0},
        "account_failures": {"geral": "[AUTHENTICATIONFAILED] Authentication failed."},
        "stages": {"fetch": True, "triage": False, "crm": False}})
    monkeypatch.setattr(webapp.report, "prepare", lambda s: ([EMAIL], [], {}))
    monkeypatch.setattr(webapp, "_load_jobspecs", lambda o: {"m1": JOB})

    assert c.post("/api/sync", json={"do_fetch": True, "do_triage": False}).status_code == 200
    accs = {a["id"]: a for a in c.get("/api/admin/accounts").json()["accounts"]}
    assert accs["diogo"]["cursors"] == [
        {"mailbox": "INBOX", "uidvalidity": 42, "last_uid": 1234,
         "updated_ts": accs["diogo"]["cursors"][0]["updated_ts"]}]
    assert accs["diogo"]["last_sync"]                      # derived from the cursor
    assert accs["geral"]["errors"][0]["message"].startswith("[AUTHENTICATIONFAILED]")
    assert accs["diogo"]["errors"] == []                   # a healthy account stays clean
    status = c.get("/api/sync/status").json()
    assert status["per_account"] == {"diogo": 0, "geral": 0}
    assert "geral" in status["account_failures"]
    assert status["stages"] == {"fetch": True, "triage": False, "crm": False}
    assert SEKRET not in c.get("/api/sync/status").text


def test_sync_body_targets_an_account_and_can_skip_triage(tmp_path, monkeypatch):
    """'Só buscar' must actually spend nothing: do_triage=False has to reach run_sync AND suppress
    the jobspec rebuild, which drafts specs through the LLM. An empty body keeps the old behaviour."""
    from email2data import sync as syncmod
    from email2data import specbuild
    seen, rebuilds = [], []
    monkeypatch.setattr(syncmod, "run_sync", lambda settings, **k: seen.append(k) or {
        "fetched": 0, "triaged_new": 0, "per_account": {}, "account_failures": {},
        "stages": k.get("stages")})
    monkeypatch.setattr(specbuild, "rebuild_jobspecs",
                        lambda settings, **k: rebuilds.append(k) or {"built": 0})
    monkeypatch.setattr(webapp.report, "prepare", lambda s: ([EMAIL], [], {}))
    monkeypatch.setattr(webapp, "_load_jobspecs", lambda o: {"m1": JOB})
    c, _ = _admin_app(tmp_path)

    assert c.post("/api/sync", json={}).status_code == 200            # legacy: full run
    assert seen[-1]["do_fetch"] and seen[-1]["do_triage"] and seen[-1]["account_ids"] is None
    assert len(rebuilds) == 1                                          # …and it DID rebuild specs

    c.post("/api/sync", json={"account_id": "diogo", "do_fetch": True, "do_triage": False})
    assert seen[-1]["account_ids"] == ["diogo"]
    assert seen[-1]["do_triage"] is False and seen[-1]["do_crm"] is False
    assert len(rebuilds) == 1, "fetch-only sync must not pay for a jobspec rebuild"


def _reextract_app(tmp_path, monkeypatch):
    """A project with two real CRM-linked messages, ready for a scoped re-extract."""
    from email2data.crm import CrmStore
    crm = CrmStore(tmp_path / "crm.db").connect()
    verdict = {"counterparty": "CLIENT", "purpose": "ESTIMATE_REQUEST_FROM_CLIENT",
               "priority": "HIGH", "direction": "inbound", "entities": {}}
    crm.record({"message_id": "m1", "from": {"email": "c@acme.pt"}, "to": [], "cc": [],
                "subject": "Pedido", "date": "2026-01-01T09:00:00", "references": [],
                "attachments": []}, verdict)
    crm.record({"message_id": "m2", "from": {"email": "c@acme.pt"}, "to": [], "cc": [],
                "subject": "Re: Pedido", "date": "2026-01-02T09:00:00", "references": ["m1"],
                "attachments": []}, verdict)
    c, s = _admin_app(tmp_path, crm_store=crm, jobspecs={"m1": JOB})
    pid = c.post("/api/projects", json={"title": "Corten", "from_message": "m1"}).json()["project_id"]
    return c, s, pid


def test_reextract_409s_while_a_sync_is_running(tmp_path, monkeypatch):
    """Both write out/jobspecs.jsonl — they must never interleave. Mirrors the /api/sync 409."""
    c, _s, pid = _reextract_app(tmp_path, monkeypatch)
    assert c.app.state.sync_lock.acquire(blocking=False)
    try:
        r = c.post(f"/api/projects/{pid}/reextract", json={"tier": "heavy"})
        assert r.status_code == 409 and r.json()["running"] is True
    finally:
        c.app.state.sync_lock.release()


def test_reextract_scopes_the_rebuild_to_this_projects_messages(tmp_path, monkeypatch):
    """THE cost-containment pin: re-extracting one project must pass only=<that project's mids> and
    incremental=True. Passing the whole corpus (or incremental=False) would re-bill a Tier-1 pass for
    every job email in the archive — the exact failure this route exists to avoid."""
    from email2data import specbuild
    calls = []
    monkeypatch.setattr(specbuild, "rebuild_jobspecs",
                        lambda settings, **k: calls.append(k) or
                        {"built": 2, "kept": 20, "failed": 0, "drafted": 2, "total": 22})
    c, _s, pid = _reextract_app(tmp_path, monkeypatch)
    r = c.post(f"/api/projects/{pid}/reextract", json={"tier": "heavy"})
    assert r.status_code == 200, r.text
    assert len(calls) == 1
    assert calls[0]["only"] == {"m1", "m2"}          # scoped — NOT the corpus
    assert calls[0]["incremental"] is True           # what keeps everything outside scope intact
    assert calls[0]["tier"] == "heavy"
    b = r.json()
    assert b["ok"] is True and b["tier"] == "heavy"
    assert b["counts"]["kept"] == 20
    assert [m["message_id"] for m in b["messages"]] == ["m1", "m2"]
    assert b["project"]["project_id"] == pid
    # a bad tier never reaches the LLM layer
    assert c.post(f"/api/projects/{pid}/reextract", json={"tier": "turbo"}).status_code == 400
    assert c.post("/api/projects/p-nope/reextract", json={}).status_code == 404
    assert len(calls) == 1


def test_reextract_surfaces_spec_error_instead_of_hiding_it(tmp_path, monkeypatch):
    """A failed extraction must not look like a thin email. The per-message spec_error written by
    specbuild reaches the response, and ok goes False."""
    from email2data import specbuild
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    def _fake_rebuild(settings, **k):
        (out_dir / "jobspecs.jsonl").write_text(
            json.dumps({**JOB, "message_id": "m1", "spec_error": "RuntimeError: model refused"})
            + "\n", encoding="utf-8")
        return {"built": 1, "kept": 0, "failed": 1, "drafted": 0, "total": 1}

    monkeypatch.setattr(specbuild, "rebuild_jobspecs", _fake_rebuild)
    c, _s, pid = _reextract_app(tmp_path, monkeypatch)
    b = c.post(f"/api/projects/{pid}/reextract", json={}).json()
    assert b["ok"] is False
    m1 = next(m for m in b["messages"] if m["message_id"] == "m1")
    assert m1["spec_error"] == "RuntimeError: model refused" and m1["has_spec"] is True
    m2 = next(m for m in b["messages"] if m["message_id"] == "m2")
    assert m2["has_spec"] is False and m2["spec_error"] == ""


def test_projetos_page_ships_the_reextract_control(tmp_path):
    """The re-extract button + cost-tier selector live on the project detail page, and the result
    block renders spec_error — failures stop being invisible."""
    html = _client(tmp_path).get("/projetos").text
    assert "async function reextract(" in html and "/reextract" in html
    assert 'id="_rexbtn"' in html and 'id="_retier"' in html
    assert "Reprocessar tudo com IA" in html
    # the three tiers are labelled by COST, since that is what the choice actually is (ADR-026)
    for label in ("Leve · custo baixo", "Normal · custo médio", "Profundo · custo alto"):
        assert f"'{label}'" in html
    assert "function rexResultHTML(" in html and "spec_error" in html
    assert "gasta tokens" in html                 # the cost is stated before the click


def test_admin_routes_need_a_settings_file(tmp_path):
    """Pure-injection apps (no __settings_path__) have no file to read or write — say 503 rather than
    inventing a path and writing settings.json somewhere unexpected."""
    c = _client(tmp_path)
    assert c.post("/api/admin/accounts", json={"accounts": []}).status_code == 503
    pid = c.post("/api/projects", json={"title": "x"}).json()["project_id"]
    c.post(f"/api/projects/{pid}/attach", json={"ref": "root-x"})
    assert c.post(f"/api/projects/{pid}/reextract", json={}).status_code == 503


def test_reextract_updates_machine_fields_but_never_a_human_decision(tmp_path, monkeypatch):
    """The property the whole feature rests on, driven through HTTP end-to-end (real jobspecs.jsonl,
    real seed_items_from(force=True), real ProjectStore):

      * a richer re-extraction reaches the project even though it already has item fields — the bug
        that made ``force`` necessary in the first place;
      * a field a HUMAN confirmed is not overwritten by the model's competing value.

    A re-extract that destroyed a human decision would be a defect, not a feature."""
    from email2data import specbuild
    thin = js.build_jobspec(
        {"message_id": "m1", "subject": "Corten", "counterparty": "CLIENT",
         "purpose": "ESTIMATE_REQUEST_FROM_CLIENT",
         "entities": {"product_or_service": "estrutura"}}, {}).to_dict()
    c, s, pid = _reextract_app(tmp_path, monkeypatch)
    out_dir = Path(s["__settings_path__"]).parents[1] / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    # a human confirms the material — the model will disagree with it below
    c.post(f"/api/projects/{pid}/field",
           json={"field": "material#0", "value": "Corten 8 mm (confirmado ao telefone)",
                 "channel": "call", "asserted_by": "Diogo Costa"})
    before = c.get(f"/api/projects/{pid}").json()
    assert before["items"][0]["material"]["value"].startswith("Corten 8 mm")

    def _rich_rebuild(settings, **k):
        assert k["only"] == {"m1", "m2"}          # still scoped, even on the real path
        rich = json.loads(json.dumps(thin))
        rich["items"][0]["material"] = {"value": "Aço Corten", "source": "llm", "confirmed": False}
        rich["items"][0]["thickness"] = {"value": "entre 5 e 8 mm", "source": "llm", "confirmed": False}
        rich["items"][0]["colour_finish"] = {"value": "Natural (sem pintura)", "source": "llm",
                                             "confirmed": False}
        (out_dir / "jobspecs.jsonl").write_text(json.dumps(rich) + "\n", encoding="utf-8")
        return {"built": 1, "kept": 0, "failed": 0, "drafted": 1, "total": 1}

    monkeypatch.setattr(specbuild, "rebuild_jobspecs", _rich_rebuild)
    r = c.post(f"/api/projects/{pid}/reextract", json={"tier": "heavy"})
    assert r.status_code == 200, r.text
    item = r.json()["project"]["items"][0]

    # the model's NEW knowledge landed …
    assert item["thickness"]["value"] == "entre 5 e 8 mm"
    assert item["colour_finish"]["value"] == "Natural (sem pintura)"
    # … and is honestly labelled as machine-derived, never as human-confirmed (zero-hallucination)
    assert item["thickness"]["confirmed"] is False and item["thickness"]["source"] != "user"
    # … but the human's material survived the model's competing value, verbatim
    assert item["material"]["value"] == "Corten 8 mm (confirmado ao telefone)"
    assert item["material"]["value"] != "Aço Corten"
    # the same result is served on a plain re-read (it was persisted, not just returned)
    assert c.get(f"/api/projects/{pid}").json()["items"][0]["material"]["value"] == \
        "Corten 8 mm (confirmado ao telefone)"


# ---------------------------------------------------------------------------
# ADR-026 — re-extraction reads the timeline too, not just the emails
# ---------------------------------------------------------------------------

def _stub_specbuild(monkeypatch, calls=None):
    from email2data import specbuild
    monkeypatch.setattr(specbuild, "rebuild_jobspecs", lambda settings, **k:
                        (calls.append(k) if calls is not None else None) or
                        {"built": 1, "kept": 0, "failed": 0, "drafted": 1, "total": 1})


def _stub_llm_client(monkeypatch):
    from email2data import classifier
    monkeypatch.setattr(classifier, "make_client", lambda s: object())


def _note(c, pid, text, **prov):
    """Record a Registar note through the REAL route, not a store back door."""
    r = c.post(f"/api/projects/{pid}/event", json={"kind": "note", "text": text, **prov})
    assert r.status_code == 200, r.text


def test_reextract_reads_timeline_events_and_applies_what_it_finds(tmp_path, monkeypatch):
    """THE feature: a deadline agreed on a phone call and typed into Registar was invisible to the
    readiness gate, because no model ever read it. One button now re-reads emails AND notes."""
    from email2data import capture_infer
    _stub_specbuild(monkeypatch)
    _stub_llm_client(monkeypatch)
    c, _s, pid = _reextract_app(tmp_path, monkeypatch)
    _note(c, pid, "ficou combinado prazo 15 de março", channel="call", asserted_by="Diogo")
    assert c.get(f"/api/projects/{pid}").json()["n_events"] == 1   # what the UI shows before clicking

    seen = []
    monkeypatch.setattr(capture_infer, "extract_fields_strict",
                        lambda text, client, cfg: seen.append((text, cfg.get("model")))
                        or {"fields": {"deadline": "2026-03-15"}, "confidence": 0.9})

    b = c.post(f"/api/projects/{pid}/reextract", json={"tier": "heavy"}).json()
    assert b["ok"] is True
    assert b["events"]["read"] == 1 and b["events"]["applied"] == ["deadline"]
    assert seen[0][0] == "ficou combinado prazo 15 de março"
    assert seen[0][1] == "gemini-2.5-pro", "the chosen cost tier must reach the events pass too"

    view = c.get(f"/api/projects/{pid}").json()
    assert view["job_fields"]["deadline"]["value"] == "2026-03-15"
    # applied as an unconfirmed machine extraction, NOT as Diogo's signed-off decision
    assert view["field_provenance"]["deadline"]["source_mid"].startswith("event:")
    assert view["field_provenance"]["deadline"]["asserted_by"] == ""
    assert view["job_fields"]["deadline"]["confirmed"] is False
    assert view["job_fields"]["deadline"]["source"] == "llm"


def test_reextract_runs_on_a_project_that_has_only_timeline_knowledge(tmp_path, monkeypatch):
    """A project can be pure off-email knowledge (calls + WhatsApp, no thread). Before ADR-026 that
    400'd as "sem emails ligados" — there was nothing the button could do for it."""
    from email2data import capture_infer
    calls = []
    _stub_specbuild(monkeypatch, calls)
    _stub_llm_client(monkeypatch)
    c, _s = _admin_app(tmp_path, jobspecs={})
    pid = c.post("/api/projects", json={"title": "Só chamadas"}).json()["project_id"]

    assert c.post(f"/api/projects/{pid}/reextract", json={}).status_code == 400, \
        "no emails AND no notes is still nothing to do"

    _note(c, pid, "inox 4mm, 20 unidades", channel="call")
    monkeypatch.setattr(capture_infer, "extract_fields_strict", lambda t, cl, cf:
                        {"fields": {"material#0": "inox"}, "confidence": 0.8})
    b = c.post(f"/api/projects/{pid}/reextract", json={}).json()
    assert b["events"]["read"] == 1 and b["events"]["applied"] == ["material#0"]
    assert not calls, "with no linked emails the Tier-1 email pass must not be billed at all"


def test_reextract_surfaces_a_note_the_model_could_not_read(tmp_path, monkeypatch):
    """A failed note must not be indistinguishable from a note that simply held no spec values —
    the same honesty rule the per-message spec_error already enforces."""
    from email2data import capture_infer, llm as _llm
    _stub_specbuild(monkeypatch)
    _stub_llm_client(monkeypatch)
    c, _s, pid = _reextract_app(tmp_path, monkeypatch)
    _note(c, pid, "nota ilegível")

    monkeypatch.setattr(capture_infer, "extract_fields_strict",
                        lambda t, cl, cf: (_ for _ in ()).throw(_llm.LLMError("429 exhausted")))
    b = c.post(f"/api/projects/{pid}/reextract", json={}).json()
    assert b["ok"] is False
    assert b["events"]["read"] == 1 and b["events"]["applied"] == []
    assert "429 exhausted" in b["events"]["failed"][0]["error"]


def test_reextract_never_lets_a_note_overwrite_a_human_decision(tmp_path, monkeypatch):
    """The property the whole widening rests on, driven end-to-end through HTTP."""
    from email2data import capture_infer
    _stub_specbuild(monkeypatch)
    _stub_llm_client(monkeypatch)
    c, _s, pid = _reextract_app(tmp_path, monkeypatch)
    c.post(f"/api/projects/{pid}/field", json={"field": "deadline", "value": "2026-04-01"})
    _note(c, pid, "acho que era para março")

    monkeypatch.setattr(capture_infer, "extract_fields_strict", lambda t, cl, cf:
                        {"fields": {"deadline": "2026-03-15"}, "confidence": 0.9})
    b = c.post(f"/api/projects/{pid}/reextract", json={}).json()
    assert b["events"]["applied"] == []
    assert c.get(f"/api/projects/{pid}").json()["job_fields"]["deadline"]["value"] == "2026-04-01"


def test_reextract_over_unchanged_notes_is_idempotent(tmp_path, monkeypatch):
    """Re-running must not pile up history rows — the project ends byte-for-byte the same."""
    from email2data import capture_infer
    _stub_specbuild(monkeypatch)
    _stub_llm_client(monkeypatch)
    c, _s, pid = _reextract_app(tmp_path, monkeypatch)
    _note(c, pid, "material inox")
    monkeypatch.setattr(capture_infer, "extract_fields_strict", lambda t, cl, cf:
                        {"fields": {"material#0": "inox"}, "confidence": 0.9})

    first = c.post(f"/api/projects/{pid}/reextract", json={}).json()
    assert first["events"]["applied"] == ["material#0"]
    n_rows = len(c.get(f"/api/projects/{pid}/timeline").json()["timeline"])

    second = c.post(f"/api/projects/{pid}/reextract", json={}).json()
    assert second["events"]["applied"] == [], "an unchanged value must not be rewritten"
    assert len(c.get(f"/api/projects/{pid}/timeline").json()["timeline"]) == n_rows


def test_projetos_page_states_the_widened_scope_and_its_cost(tmp_path):
    """The button now spends tokens on notes as well as emails, so the page must say so BEFORE the
    click — and Registar can no longer promise a blanket "sem IA"."""
    html = _client(tmp_path).get("/projetos").text
    assert "registo" in html and "linha do tempo" in html
    assert "uma chamada por email + uma por registo" in html      # the cost model, stated
    assert "a IA só lê isto se pedires Reprocessar" in html       # the honest Registar placeholder
    assert "opinião… (guardado tal e qual, sem IA)" not in html   # the promise it replaced


# ---------------------------------------------------------------------------
# ADR-027 — the AI polish of the client email
# ---------------------------------------------------------------------------

def _polish_app(tmp_path, monkeypatch):
    c, s = _admin_app(tmp_path, jobspecs={"m1": JOB})
    (Path(s["__settings_path__"]).parent / "client_email_polish_playbook.md").write_text(
        "PLAYBOOK DO POLISH", encoding="utf-8")
    _stub_llm_client(monkeypatch)
    pid = c.post("/api/projects", json={"title": "Corten", "from_message": "m1"}).json()["project_id"]
    return c, s, pid


def test_polish_rewrites_the_deterministic_draft_and_confirms_the_questions_survived(
        tmp_path, monkeypatch):
    """ADR-027 on top of ADR-013: the server rebuilds the deterministic body from the ticked keys and
    hands THAT to the model, so the questions enter the prompt as a fixed list. Both texts come back —
    the polish is an offer, never a silent swap."""
    from email2data import clientdraft
    c, _s, pid = _polish_app(tmp_path, monkeypatch)
    asks = c.get(f"/api/projects/{pid}/draft").json()["askables"]
    keys = [a["key"] for a in asks if a["default"]][:2]
    questions = [a["question"] for a in asks if a["key"] in keys]

    seen = {}

    def fake_polish(base, qs, playbook, client, cfg, *, facts=None, thread=None):
        seen.update(base=base, qs=qs, playbook=playbook, model=cfg.get("model"), facts=facts)
        return "Bom dia Ana,\n\n" + "\n".join(f"{i}. {q}" for i, q in enumerate(qs, 1))

    monkeypatch.setattr(clientdraft, "polish_draft", fake_polish)
    r = c.post(f"/api/projects/{pid}/draft/polish",
               json={"selected": keys, "custom": ["Entregam ao sábado?"], "tier": "heavy"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["missing"] == [] and b["n_questions"] == len(questions) + 1
    assert b["base"] == clientdraft.build_draft(questions + ["Entregam ao sábado?"])
    assert b["body"].startswith("Bom dia Ana,")
    assert b["body"] != b["base"], "both versions are returned so the user chooses"
    assert seen["playbook"] == "PLAYBOOK DO POLISH"     # the editable config, not a hard-coded prompt
    assert seen["model"] == "gemini-2.5-pro"            # the chosen cost tier reached the call
    assert "Entregam ao sábado?" in seen["qs"]          # a custom question is part of the contract
    assert all(k not in dict(seen["facts"]) for k in ("cliente", "processo de fabrico")), \
        "internal flags are never facts we'd state to a client"


def test_polish_reports_a_question_the_model_dropped(tmp_path, monkeypatch):
    """The one failure that matters: the email exists to ask these questions. A silent drop would
    make ADR-013's "sits on top of" guarantee decorative."""
    from email2data import clientdraft
    c, _s, pid = _polish_app(tmp_path, monkeypatch)
    keys = [a["key"] for a in c.get(f"/api/projects/{pid}/draft").json()["askables"] if a["default"]]
    assert len(keys) > 1
    monkeypatch.setattr(clientdraft, "polish_draft",
                        lambda base, qs, *a, **k: "Bom dia,\n1. " + qs[0])

    b = c.post(f"/api/projects/{pid}/draft/polish", json={"selected": keys}).json()
    assert len(b["missing"]) == b["n_questions"] - 1 and b["missing"], "the dropped ones are named"
    assert b["body"].startswith("Bom dia,")            # still returned — the user decides


def test_polish_fails_loudly_instead_of_returning_the_unpolished_draft(tmp_path, monkeypatch):
    """The user paid for a call and must know whether they got one. Returning `base` with a 200 would
    read as a successful polish that happened to change nothing."""
    from email2data import clientdraft, llm as _llm
    c, _s, pid = _polish_app(tmp_path, monkeypatch)
    keys = [a["key"] for a in c.get(f"/api/projects/{pid}/draft").json()["askables"] if a["default"]]

    monkeypatch.setattr(clientdraft, "polish_draft", lambda *a, **k:
                        (_ for _ in ()).throw(_llm.LLMError("503 unavailable")))
    r = c.post(f"/api/projects/{pid}/draft/polish", json={"selected": keys})
    assert r.status_code == 502 and "503 unavailable" in r.json()["error"]

    monkeypatch.setattr(clientdraft, "polish_draft", lambda *a, **k:
                        (_ for _ in ()).throw(RuntimeError("no ADC")))
    assert c.post(f"/api/projects/{pid}/draft/polish", json={"selected": keys}).status_code == 503


def test_polish_guards_before_it_ever_reaches_the_model(tmp_path, monkeypatch):
    """Nothing selected, a bogus tier, or an unknown project must cost zero tokens."""
    from email2data import clientdraft
    c, _s, pid = _polish_app(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(clientdraft, "polish_draft", lambda *a, **k: calls.append(1) or "x")

    assert c.post(f"/api/projects/{pid}/draft/polish", json={"selected": []}).status_code == 400
    assert c.post(f"/api/projects/{pid}/draft/polish",
                  json={"selected": ["deadline"], "tier": "turbo"}).status_code == 400
    assert c.post("/api/projects/p-nope/draft/polish",
                  json={"selected": ["deadline"]}).status_code == 404
    assert not calls


def test_polish_is_unavailable_without_settings_json(tmp_path):
    """Same 503 contract as re-extract: no settings file → no editable playbook → no LLM action."""
    c = _client(tmp_path)
    pid = c.post("/api/projects", json={"title": "T", "from_message": "m1"}).json()["project_id"]
    assert c.post(f"/api/projects/{pid}/draft/polish",
                  json={"selected": ["deadline"]}).status_code == 503


def test_projetos_page_ships_the_ai_polish_control_as_a_button_only_path(tmp_path):
    """The feature is activated by click ONLY — no page-load, toggle or keystroke path may call it."""
    html = _client(tmp_path).get("/projetos").text
    assert "async function polishDraft(" in html and "/draft/polish" in html
    assert 'id="_aibtn"' in html and "Melhorar com IA" in html
    assert "'#_aibtn'" in html and "polishDraft()" in html
    assert "function aiResultHTML(" in html and "não uses esta versão" in html
    # the composer opens with NO ai state, and loadDraft never calls the polish route
    assert "ai:null" in html.replace(" ", "")
    load = html.split("async function loadDraft(")[1].split("function purposeKind(")[0]
    assert "/draft/polish" not in load and "polishDraft(" not in load


# ---------------------------------------------------------------------------
# ADR-031 — the purpose selector + the verbatim-fact guard, end to end
# ---------------------------------------------------------------------------

def test_draft_get_exposes_the_purposes_and_reject_reasons(tmp_path):
    """The composer GET advertises the purpose selector + the editable reject reasons; the historical
    ask keys (subject/askables/body) are unchanged."""
    c = _client(tmp_path)
    pid = c.post("/api/projects", json={"title": "Troféus", "from_message": "m1"}).json()["project_id"]
    d = c.get(f"/api/projects/{pid}/draft").json()
    assert d["purpose"] == "ask"
    assert [p["id"] for p in d["purposes"]] == \
        ["ask", "reject", "quote", "follow_up", "approval", "payment", "deadline", "ready"]
    assert all("input_kind" in p and "label" in p for p in d["purposes"])
    assert len(d["reject_reasons"]) == 8
    # the ask starting point is byte-for-byte the historical one
    assert d["subject"] == "Re: Troféus" and d["body"].startswith("Bom dia,") and d["askables"]


def test_draft_build_reject_splices_the_reason_and_note(tmp_path):
    c = _client(tmp_path)
    pid = c.post("/api/projects", json={"title": "T", "from_message": "m1"}).json()["project_id"]
    r = c.post(f"/api/projects/{pid}/draft",
               json={"purpose": "reject", "reason": "Prazo pedido não é exequível",
                     "reason_note": "Podemos rever em 30/09."}).json()
    assert "Prazo pedido não é exequível" in r["body"] and "Podemos rever em 30/09." in r["body"]
    assert "30/09" in r["facts"]              # a date in the note is surfaced as a protected value


def test_draft_build_quote_returns_content_and_protected_facts(tmp_path):
    c = _client(tmp_path)
    pid = c.post("/api/projects", json={"title": "T", "from_message": "m1"}).json()["project_id"]
    r = c.post(f"/api/projects/{pid}/draft",
               json={"purpose": "quote", "content": "Total 160€, prazo 10 dias"}).json()
    assert "Total 160€, prazo 10 dias" in r["body"]
    assert r["facts"] == ["160€", "10 dias"]


def test_draft_build_is_backward_compatible_without_a_purpose(tmp_path):
    """A request with no `purpose` still behaves exactly as `ask` — explicit no-regression."""
    c = _client(tmp_path)
    pid = c.post("/api/projects", json={"title": "T", "from_message": "m1"}).json()["project_id"]
    r = c.post(f"/api/projects/{pid}/draft",
               json={"selected": ["thickness"], "custom": ["Têm logótipo em vetor?"]}).json()
    assert "1. Que espessura?" in r["body"] and "2. Têm logótipo em vetor?" in r["body"]
    assert r["facts"] == []


def test_polish_money_purpose_blocks_an_altered_number(tmp_path, monkeypatch):
    """ADR-031 extends the ADR-027 check to money: a polish that changes a typed price is caught and
    named in `missing`, exactly like a dropped question — the model may never alter a commitment."""
    from email2data import clientdraft
    c, _s, pid = _polish_app(tmp_path, monkeypatch)

    monkeypatch.setattr(clientdraft, "polish_draft",
                        lambda base, qs, *a, **k: "Bom dia,\nfica em 170€ em 10 dias.")
    b = c.post(f"/api/projects/{pid}/draft/polish",
               json={"purpose": "quote", "content": "Total 160€, prazo 10 dias"}).json()
    assert b["n_questions"] == 0 and b["n_facts"] == 2
    assert "160€" in b["missing"] and "10 dias" not in b["missing"]   # only the altered one

    monkeypatch.setattr(clientdraft, "polish_draft",
                        lambda base, qs, *a, **k: "Bom dia,\nfica em 160€ em 10 dias.")
    b2 = c.post(f"/api/projects/{pid}/draft/polish",
                json={"purpose": "quote", "content": "Total 160€, prazo 10 dias"}).json()
    assert b2["missing"] == [] and b2["n_facts"] == 2


def test_polish_empty_content_costs_zero_tokens(tmp_path, monkeypatch):
    """The empty-guard generalises to every purpose: nothing to say/protect → 400, no model call."""
    from email2data import clientdraft
    c, _s, pid = _polish_app(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(clientdraft, "polish_draft", lambda *a, **k: calls.append(1) or "x")
    assert c.post(f"/api/projects/{pid}/draft/polish",
                  json={"purpose": "quote", "content": "   "}).status_code == 400
    assert not calls


def test_polish_runs_for_a_reject_whose_note_has_no_numbers(tmp_path, monkeypatch):
    """Regression: the empty-guard must key off 'is there input to polish', NOT 'are there numbers to
    protect'. A reject with a reason + a note that happens to carry no price/date still has content to
    rewrite — it must reach the model, not 400 with 'nada para escrever'."""
    from email2data import clientdraft
    c, _s, pid = _polish_app(tmp_path, monkeypatch)
    calls = []

    def fake_polish(base, qs, *a, **k):
        calls.append(1)
        return "Bom dia,\n" + base   # keeps everything; no numbers to keep here anyway

    monkeypatch.setattr(clientdraft, "polish_draft", fake_polish)
    r = c.post(f"/api/projects/{pid}/draft/polish",
               json={"purpose": "reject", "reason": "Sem capacidade / agenda no período pedido",
                     "reason_note": "Não é possível no prazo pedido. Esperamos trabalhar em breve."})
    assert r.status_code == 200, r.text
    b = r.json()
    assert calls, "the model must be called — there is content to polish"
    assert b["n_questions"] == 0 and b["n_facts"] == 0 and b["missing"] == []


def test_projetos_page_ships_the_purpose_selector(tmp_path):
    """The rendered page ships the purpose control + the per-kind inputs + the money-guard wording.
    (The purpose labels themselves are dynamic JSON from GET /draft, asserted in the GET test above.)"""
    html = _client(tmp_path).get("/projetos").text
    assert 'id="_purpose"' in html and "Tipo de email" in html
    assert "function purposeKind(" in html
    assert "function reasonInputHTML(" in html and "function contentInputHTML(" in html
    assert "Motivo da recusa" in html               # the reject input label (static JS)
    assert "valores protegidos" in html             # the protected-values chip
    assert "alterou ou removeu" in html             # the money-guard warning wording
    assert "nunca altera os números" in html        # the content box hint
    # the ask path is unchanged: the checklist input + custom-question button still ship
    assert "function askInputHTML(" in html and 'id="_addq"' in html


# ---------------------------------------------------------------------------
# ADR-032 — composer output language + translate-received-emails-to-English
# ---------------------------------------------------------------------------

def test_polish_translates_to_english_and_still_guards_the_numbers(tmp_path, monkeypatch):
    """A non-PT polish is a translate+polish pass: the verbatim QUESTION check is skipped (can't be
    checked across languages, so `translated:true` and the caller reviews), but the number/date guard
    is language-independent and still catches an altered price."""
    from email2data import clientdraft
    c, _s, pid = _polish_app(tmp_path, monkeypatch)

    # a model that translates AND changes 160€ → 170€
    monkeypatch.setattr(clientdraft, "polish_draft",
                        lambda base, qs, *a, **k: "Hello,\nit is 170€ within 10 dias.")
    b = c.post(f"/api/projects/{pid}/draft/polish",
               json={"purpose": "quote", "content": "Total 160€, prazo 10 dias", "lang": "en"}).json()
    assert b["translated"] is True and b["lang"] == "en"
    assert "160€" in b["missing"] and "10 dias" not in b["missing"]   # number guard survives translation

    # a faithful translation that keeps the numbers → clean
    monkeypatch.setattr(clientdraft, "polish_draft",
                        lambda base, qs, *a, **k: "Hello,\nit is 160€ within 10 dias.")
    b2 = c.post(f"/api/projects/{pid}/draft/polish",
                json={"purpose": "quote", "content": "Total 160€, prazo 10 dias", "lang": "en"}).json()
    assert b2["missing"] == [] and b2["translated"] is True


def test_polish_pt_default_is_not_translated_and_invalid_lang_is_400(tmp_path, monkeypatch):
    from email2data import clientdraft
    c, _s, pid = _polish_app(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(clientdraft, "polish_draft",
                        lambda base, qs, *a, **k: calls.append(k.get("language")) or "Bom dia,\n1. " + (qs[0] if qs else "x"))
    keys = [a["key"] for a in c.get(f"/api/projects/{pid}/draft").json()["askables"] if a["default"]]
    b = c.post(f"/api/projects/{pid}/draft/polish", json={"selected": keys}).json()
    assert b["translated"] is False and b["lang"] == "pt"    # default behaves exactly as before
    assert calls == [None], "the PT path passes no language kwarg (backward compat)"
    # an unknown language is rejected before any model call
    calls.clear()
    r = c.post(f"/api/projects/{pid}/draft/polish", json={"selected": keys, "lang": "de"})
    assert r.status_code == 400 and not calls


def _translate_app(tmp_path, monkeypatch):
    c, s = _admin_app(tmp_path)
    _stub_llm_client(monkeypatch)          # classifier.make_client → dummy object (no real Vertex)
    return c, s


def test_translate_endpoint_translates_and_caches(tmp_path, monkeypatch):
    from email2data import translate
    c, _s = _translate_app(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(translate, "translate_to_english",
                        lambda text, pb, client, cfg: calls.append(text) or ("EN: " + text))

    r = c.post("/api/translate", json={"message_id": "m1", "text": "Bom dia, 160€"})
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "EN: Bom dia, 160€" and not r.json().get("cached")
    assert calls == ["Bom dia, 160€"]

    # identical text again → served from the in-memory memo, no second model call
    r2 = c.post("/api/translate", json={"message_id": "m1", "text": "Bom dia, 160€"})
    assert r2.json() == {"text": "EN: Bom dia, 160€", "cached": True}
    assert calls == ["Bom dia, 160€"], "a cached translation must cost zero tokens"


def test_translate_endpoint_guards_and_fails_honestly(tmp_path, monkeypatch):
    from email2data import llm as _llm, translate
    c, _s = _translate_app(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(translate, "translate_to_english",
                        lambda *a, **k: calls.append(1) or "x")
    # empty text → 400, zero tokens
    assert c.post("/api/translate", json={"message_id": "m1", "text": "   "}).status_code == 400
    assert not calls
    # a model failure is reported (502), never a faked/echoed translation
    monkeypatch.setattr(translate, "translate_to_english",
                        lambda *a, **k: (_ for _ in ()).throw(_llm.LLMError("503 unavailable")))
    r = c.post("/api/translate", json={"message_id": "m1", "text": "olá"})
    assert r.status_code == 502 and "503 unavailable" in r.json()["error"]


def test_translate_is_unavailable_without_settings_json(tmp_path):
    """Same contract as the polish: no settings file → no editable playbook → no LLM action."""
    c = _client(tmp_path)
    assert c.post("/api/translate", json={"message_id": "m1", "text": "olá"}).status_code == 503


def test_cockpit_pages_ship_the_translate_button_and_handler(tmp_path):
    """The shared renderer + the delegated handler ship on every cockpit page (one hook, all thread
    views). Also the composer ships the language selector + the translated banner wording."""
    for path in ("/fila", "/para-ti", "/projetos"):
        html = _client(tmp_path).get(path).text
        assert "function translateMsg(" in html and "/api/translate" in html
        assert "traduzir (EN)" in html and "closest('.trbtn')" in html
    proj = _client(tmp_path).get("/projetos").text
    assert 'id="_ailang"' in proj and "traduzido para" in proj
