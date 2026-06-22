"""WP1 — the Caixa de Capturas page (/capturas). TestClient can't run JS, but it can assert the lens
contract is shipped: the cockpit_ui consts (CAPTURES/PROJECTS/LABELS), the render/paletteItems/onKey
contract functions, the pt-PT labels + apply/discard wiring, the nav entry + live pending badge, and
that a capture's photo renders as a thumbnail in the project timeline."""

from __future__ import annotations

import pytest

from email2data import project as _project
from email2data.captures import CaptureStore
from email2data.workspace import Workspace

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from email2data import captures_page, webapp  # noqa: E402

SETTINGS = {"llm": {"provider": "vertex_gemini", "model": "gemini-2.5-flash"}}


def _setup(tmp_path):
    ws = Workspace(tmp_path / "w.db").connect()
    cap = CaptureStore(ws._conn)
    proj = _project.ProjectStore(ws._conn)
    pid = proj.create("Estante Sousa", stage="LEAD")
    app = webapp.create_app(SETTINGS, workspace=ws, jobspecs={}, reply_pb="pb",
                            prepared=([], [], {}), captures_dir=tmp_path)
    return TestClient(app), ws, cap, proj, pid


def test_capturas_page_serves_and_ships_the_lens_contract(tmp_path):
    client, ws, cap, _proj, pid = _setup(tmp_path)
    cap.add(telegram_message_id=1, telegram_chat_id=2, raw_text="cliente confirmou prazo")
    r = client.get("/capturas")
    assert r.status_code == 200
    html = r.text
    # cockpit_ui embeds (uppercased consts) + the three lens contract functions
    for marker in ("const CAPTURES =", "const PROJECTS =", "const LABELS =",
                   "function render(", "function paletteItems(", "function onKey("):
        assert marker in html, marker
    # the row builder + apply/discard wiring against the M3 API
    for marker in ("function renderCard", "/api/captures/", "/apply", "/discard",
                   'data-act="apply"', 'data-act="discard"'):
        assert marker in html, marker
    # pt-PT user-facing strings (page title, action buttons, event-kind labels)
    for s in ("Capturas", "Aplicar", "Descartar", "Nota", "Decisão", "Opinião", "To-do"):
        assert s in html, s
    # the capture text is embedded (escaped) for the user to read before validating
    assert "cliente confirmou prazo" in html
    ws.close()


def test_capturas_nav_badge_reflects_pending_count(tmp_path):
    client, ws, cap, _proj, _pid = _setup(tmp_path)
    # no pending captures -> the nav link is present but carries no badge
    html0 = client.get("/capturas").text
    link0 = html0.split('href="/capturas"')[1].split("</a>")[0]
    assert "Capturas" in link0 and "nbadge" not in link0
    # two pending captures -> the badge shows 2 on the Capturas nav link (live count)
    cap.add(telegram_message_id=1, telegram_chat_id=2, raw_text="a")
    cap.add(telegram_message_id=3, telegram_chat_id=2, raw_text="b")
    link = client.get("/capturas").text.split('href="/capturas"')[1].split("</a>")[0]
    assert 'class="nbadge">2<' in link
    ws.close()


def test_capturas_page_shows_active_projects_only(tmp_path):
    client, ws, cap, proj, pid = _setup(tmp_path)
    won = proj.create("Fechada", stage="WON")          # terminal -> must NOT be offered
    cap.add(telegram_message_id=1, telegram_chat_id=2, raw_text="x")
    html = client.get("/capturas").text
    assert pid in html and "Estante Sousa" in html      # active LEAD is in the pick-list
    assert won not in html                              # the WON project is filtered out
    ws.close()


def test_build_html_neutralizes_untrusted_text_and_titles(tmp_path):
    # XSS lens (WP2): capture text + project titles are untrusted. They ride in as JS string DATA
    # (a <script> const), which is inert HTML — the two real guarantees are:
    #   (1) the embed escapes "</" so a </script> payload can't break out of the script tag, and
    #   (2) the lens wraps them in esc() before they ever touch innerHTML.
    caps = [{"capture_id": "c-2-1", "raw_text": "</script><script>alert(1)</script>",
             "media_paths": [], "content_class": "conversation", "inferred_project_id": None,
             "asserted_by": "", "channel": "manual", "acquired_at": "", "created_ts": "2026-06-21"}]
    projects = [{"project_id": "p-0001", "title": "<b>boom</b>", "stage": "LEAD"}]
    html = captures_page.build_html(caps, projects)
    # (1) the raw closing-script payload cannot break out of the embed ("</" -> "<\/")
    assert "</script><script>alert(1)</script>" not in html
    assert r"<\/script>" in html
    # (2) the render path escapes the untrusted capture text + project title before DOM insertion
    assert "esc(txt)" in html and "esc(p.title)" in html


def test_page_persists_picks_and_guards_double_fire(tmp_path):
    # M3 review fixes (MEDIUM + LOW): the project/kind picks must survive a re-render (persisted onto
    # the capture, not just the DOM), an apply/discard must not double-fire, and a card click re-renders
    # the focus highlight. Assert the wiring is shipped (TestClient can't run JS).
    html = _setup(tmp_path)[0].get("/capturas").text
    assert "addEventListener('change'" in html                  # picks are persisted on change
    assert "c._proj" in html and "c._kind" in html              # ...onto the capture object
    assert "chosenProj(c)" in html and "chosenKind(c)" in html  # ...and read back on re-render
    assert "_busy" in html                                       # in-flight guard blocks a double-fire
    assert "focus=i;render()" in html.replace(" ", "")           # click-to-focus re-renders the highlight


def test_capturas_in_every_palette_and_contrapartes_in_its_own(tmp_path):
    # M3 review (LOW): the new lens must be reachable from the ⌘K palette on every page, and its own
    # palette must match the house set (incl. Contrapartes).
    from email2data import fila_page, para_ti_page, projetos_page
    cap_html = _setup(tmp_path)[0].get("/capturas").text
    assert "label:'Contrapartes'" in cap_html                    # captures palette gained Contrapartes
    for mod, args in ((fila_page, ([], ["P"])), (para_ti_page, ([],)),
                      (projetos_page, ([],))):
        html = (mod.build_fila_html(*args) if mod is fila_page else mod.build_html(*args))
        assert "label:'Capturas'" in html, mod.__name__          # Capturas reachable from each page


def test_capture_photo_thumbnail_renders_in_project_timeline(tmp_path):
    # WP1 deliverable: "the photo in the project timeline" — the timeline JS renders a capture event's
    # media (source_mid='capture:<cid>') as a thumbnail off the media endpoint.
    html = _setup(tmp_path)[0].get("/projetos").text
    assert "tl-thumb" in html                                    # the thumbnail CSS class is shipped
    assert "'capture:'" in html and "/api/captures/" in html      # the source_mid->media wiring
    assert ".slice(8)" in html                                   # cid = source_mid.slice(8)
