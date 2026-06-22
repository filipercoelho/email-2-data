"""M3 — the Caixa de Capturas validation API: list pending, apply a capture into a project's ledger
(ADR-019 §5 / R9 no-auto-apply), discard (preserve-at-core), and serve capture media inline (the sole
copy once Telegram is scrubbed, ADR-020) with a path-traversal guard."""

from __future__ import annotations

import pytest

from email2data import project as _project
from email2data.captures import CaptureStore
from email2data.workspace import Workspace

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from email2data import webapp  # noqa: E402

SETTINGS = {"llm": {"provider": "vertex_gemini", "model": "gemini-2.5-flash"}}


def _setup(tmp_path):
    ws = Workspace(tmp_path / "w.db").connect()
    cap = CaptureStore(ws._conn)
    proj = _project.ProjectStore(ws._conn)
    pid = proj.create("Estante Sousa", stage="LEAD")
    app = webapp.create_app(SETTINGS, workspace=ws, jobspecs={}, reply_pb="pb",
                            prepared=([], [], {}), captures_dir=tmp_path)
    return TestClient(app), ws, cap, proj, pid


def test_pending_capture_is_listed(tmp_path):
    client, ws, cap, _proj, _pid = _setup(tmp_path)
    cid, _ = cap.add(telegram_message_id=1, telegram_chat_id=2, raw_text="prazo 30 jun")
    rows = client.get("/api/captures").json()["captures"]
    assert [c["capture_id"] for c in rows] == [cid]
    ws.close()


def test_apply_appends_an_event_with_provenance_and_marks_applied(tmp_path):
    client, ws, cap, proj, pid = _setup(tmp_path)
    cid, _ = cap.add(telegram_message_id=1, telegram_chat_id=2, raw_text="cliente confirmou prazo",
                     channel="call", asserted_by="Pedro Ferreira")
    r = client.post(f"/api/captures/{cid}/apply", json={"project_id": pid, "kind": "decision"})
    assert r.status_code == 200 and r.json()["ok"] is True
    # left the pending queue (applied), and the project timeline carries the event WITH provenance
    assert cap.get(cid)["status"] == "applied" and cap.list_pending() == []
    tl = proj.timeline(pid)
    assert tl[0]["op"] == "event" and tl[0]["field"] == "__decision__"
    assert tl[0]["new_value"] == "cliente confirmou prazo"
    assert tl[0]["channel"] == "call" and tl[0]["asserted_by"] == "Pedro Ferreira"
    ws.close()


def test_apply_links_media_via_source_mid(tmp_path):
    client, ws, cap, proj, pid = _setup(tmp_path)
    cid, _ = cap.add(telegram_message_id=1, telegram_chat_id=2, content_class="artifact",
                     media_paths=["c-2-1/photo.jpg"])
    client.post(f"/api/captures/{cid}/apply", json={"project_id": pid})
    # the timeline event references the capture so the project surface can render its photo
    assert proj.timeline(pid)[0]["source_mid"] == f"capture:{cid}"
    ws.close()


def test_apply_unknown_capture_or_project_is_404(tmp_path):
    client, ws, cap, _proj, pid = _setup(tmp_path)
    cid, _ = cap.add(telegram_message_id=1, telegram_chat_id=2, raw_text="x")
    assert client.post("/api/captures/nope/apply", json={"project_id": pid}).status_code == 404
    assert client.post(f"/api/captures/{cid}/apply", json={"project_id": "p-9999"}).status_code == 404
    ws.close()


def test_discard_keeps_the_row_but_removes_it_from_the_queue(tmp_path):
    client, ws, cap, _proj, _pid = _setup(tmp_path)
    cid, _ = cap.add(telegram_message_id=1, telegram_chat_id=2, raw_text="x")
    assert client.post(f"/api/captures/{cid}/discard").status_code == 200
    assert cap.list_pending() == []
    assert cap.get(cid)["status"] == "discarded"     # retained for audit (preserve-at-core)
    ws.close()


def test_double_apply_appends_exactly_one_event_and_409s_the_second(tmp_path):
    # M3 review (HIGH): apply must be idempotent — a double-click / two in-flight POSTs must NOT
    # double-append the off-email knowledge event. The 2nd hit sees a terminal capture and 409s.
    client, ws, cap, proj, pid = _setup(tmp_path)
    cid, _ = cap.add(telegram_message_id=1, telegram_chat_id=2, raw_text="cliente confirmou prazo")
    r1 = client.post(f"/api/captures/{cid}/apply", json={"project_id": pid, "kind": "decision"})
    r2 = client.post(f"/api/captures/{cid}/apply", json={"project_id": pid, "kind": "decision"})
    assert r1.status_code == 200 and r2.status_code == 409   # second is rejected, not re-applied
    events = [r for r in proj.timeline(pid) if r["op"] == "event"]
    assert len(events) == 1                                  # the ledger event landed exactly once
    ws.close()


def test_apply_after_discard_never_leaks_into_the_ledger(tmp_path):
    # M3 review (HIGH): a capture the user DISCARDED must never end up written into a project — the
    # unguarded add_event used to leak it. preserve-at-core / terminal immutability (ADR-020).
    client, ws, cap, proj, pid = _setup(tmp_path)
    cid, _ = cap.add(telegram_message_id=1, telegram_chat_id=2, raw_text="engano")
    assert client.post(f"/api/captures/{cid}/discard").status_code == 200
    r = client.post(f"/api/captures/{cid}/apply", json={"project_id": pid})
    assert r.status_code == 409                              # terminal capture: rejected
    assert cap.get(cid)["status"] == "discarded"            # stays discarded
    assert proj.timeline(pid) == []                          # nothing leaked into the project
    ws.close()


def test_apply_to_a_closed_project_is_rejected(tmp_path):
    # M3 review (LOW): the picker only offers active projects; the apply endpoint must agree and refuse
    # a terminal-stage (WON/LOST/CANCELLED/ARCHIVED) target instead of appending to a closed project.
    client, ws, cap, proj, _pid = _setup(tmp_path)
    won = proj.create("Fechada", stage="WON")
    cid, _ = cap.add(telegram_message_id=1, telegram_chat_id=2, raw_text="x")
    r = client.post(f"/api/captures/{cid}/apply", json={"project_id": won})
    assert r.status_code == 409                              # closed project: rejected
    assert cap.get(cid)["status"] == "stored"               # capture untouched, still pending
    assert proj.timeline(won) == []
    ws.close()


def test_media_is_served_inline_and_guards_index_range(tmp_path):
    client, ws, cap, _proj, _pid = _setup(tmp_path)
    (tmp_path / "c-2-1").mkdir()
    (tmp_path / "c-2-1" / "photo.jpg").write_bytes(b"JPEGDATA")
    cid, _ = cap.add(telegram_message_id=1, telegram_chat_id=2, content_class="artifact",
                     media_paths=["c-2-1/photo.jpg"])
    r = client.get(f"/api/captures/{cid}/media/0")
    assert r.status_code == 200 and r.content == b"JPEGDATA"
    assert r.headers["content-type"].startswith("image/")
    assert client.get(f"/api/captures/{cid}/media/5").status_code == 404   # out of range
    ws.close()
