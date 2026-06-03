"""FastAPI workspace — serves the rich report (email2data.report) LIVE, with editable job-spec fields.

One UI, not two: ``GET /`` renders the same report as the static file but with ``live=True`` (the
job-spec panel becomes editable + a Regenerate button). Confirmations persist to the precious
``Workspace`` and **overlay** the regenerable specs. ``POST /api/confirm`` and ``POST /api/reply`` are
keyed by ``message_id``. **NEVER sends** — copy/paste only. Single-user, localhost.

Note: no ``from __future__ import annotations`` here — FastAPI must see the real ``Request`` class on
the route signatures (the future-import would make it an unresolved string -> 422).
"""

import json
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import (classifier, cockpit, crm as _crm, export as _export, fila_page, jobspec as js,
               project as _project, replydraft, report)
from .config import paths
from .workspace import Workspace, RECLASSIFY_FIELDS


def _load_jobspecs(out_dir: Path) -> dict[str, Any]:
    jp = out_dir / "jobspecs.jsonl"
    m: dict[str, Any] = {}
    if jp.exists():
        for line in jp.read_text().splitlines():
            if line.strip():
                j = json.loads(line)
                m[j["message_id"]] = j
    return m


def create_app(settings: dict[str, Any], *, workspace=None, jobspecs=None, reply_pb=None,
               prepared=None, crm_store=None, corpus_index=None):
    """Injectable factory. Defaults wire to the real files; tests pass prepared/jobspecs/workspace.

    ``crm_store`` is an open ``CrmStore`` instance; when omitted the factory opens ``out/crm.db``
    if it exists, or leaves relation queries unavailable (503) if it doesn't.
    """
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse

    # Touch __settings_path__ only for args that aren't injected (tests inject everything).
    def _outdir():
        return paths(settings, settings["__settings_path__"])["out_dir"]
    ws = workspace or Workspace(_outdir() / "workspace.db").connect()
    jspecs = jobspecs if jobspecs is not None else _load_jobspecs(_outdir())
    rpb = (reply_pb if reply_pb is not None
           else replydraft.load_playbook(Path(settings["__settings_path__"]).parents[1] / "config" / "reply_playbook.md"))
    emails, contacts, cost = prepared if prepared is not None else report.prepare(settings)
    _team = list(settings.get("team", []) or [])  # owner roster for the Fila (A5); editable in settings.json

    # When the caller injects state (tests), the data isn't backed by real files — disable the
    # rebuild/startup-sync machinery so we never try to re-read results.jsonl from a fixture.
    _injected = prepared is not None or jobspecs is not None

    # CRM store: caller may inject one (tests), or we open the real db when running from real settings.
    # When create_app is called without __settings_path__ (pure injection in tests) we skip the DB
    # lookup and leave relations unavailable — _outdir() would KeyError otherwise.
    _crmdb: _crm.CrmStore | None
    if crm_store is not None:
        _crmdb = crm_store
    elif settings.get("__settings_path__"):
        db_path = _outdir() / "crm.db"
        _crmdb = _crm.CrmStore(db_path).connect() if db_path.exists() else None
    else:
        _crmdb = None

    # Lazy corpus index (message_id -> .eml path) for serving attachment BYTES on demand (no parsing).
    _idx: dict[str, Path] = dict(corpus_index) if corpus_index else {}
    _idx_state = {"built": corpus_index is not None}

    def _file_for(mid: str):
        if not _idx_state["built"] and settings.get("__settings_path__"):
            from .envelope import parse_eml
            for f in paths(settings, settings["__settings_path__"])["corpus_dir"].glob("*.eml"):
                try:
                    _idx.setdefault(parse_eml(f.read_bytes())["message_id"], f)
                except Exception:  # noqa: BLE001
                    pass
            _idx_state["built"] = True
        return _idx.get(mid)

    _keys = {k for k, _, _, _, _ in js.FIELDS}

    # ── Incremental sync (button + on-deploy) ────────────────────────────────────────────────────
    # The four pieces of render state above are captured in this closure, so a fetch/triage after
    # startup must rebind them or the new emails never show. _rebuild_state re-reads the (now-larger)
    # results.jsonl/jobspecs.jsonl and resets the lazy corpus index. A lock serializes the startup
    # background thread against a "Sync now" click; single-user, but the race is real.
    _sync = {"running": False, "last_counts": None, "last_ts": None, "last_error": None}
    _sync_lock = threading.Lock()

    def _rebuild_state() -> None:
        nonlocal emails, contacts, cost, jspecs, _crmdb
        emails, contacts, cost = report.prepare(settings)
        jspecs = _load_jobspecs(_outdir())
        _idx.clear()
        _idx_state["built"] = False
        # run_sync rebuilt crm.db (a new inode) — reopen so the Fila reads fresh relations, not the
        # now-unlinked file the previous connection still points at.
        if settings.get("__settings_path__"):
            if _crmdb is not None:
                _crmdb.close()
            _db = _outdir() / "crm.db"
            _crmdb = _crm.CrmStore(_db).connect() if _db.exists() else None

    def _run_sync(full: bool = False) -> dict:
        """Fetch new mail + triage new emails, then rebuild render state. Returns counts, a
        ``{"running": True}`` marker if a sync is already in flight, or ``{"error": msg}`` on a clean
        failure (e.g. the IMAP password isn't set) — never raises, so a daemon thread can't crash and
        the button gets a tidy message instead of a 500."""
        from . import sync as _syncmod
        if not _sync_lock.acquire(blocking=False):
            return {"running": True}
        _sync["running"] = True
        try:
            counts = _syncmod.run_sync(settings, full=full)
            _rebuild_state()
            _sync["last_counts"] = counts
            _sync["last_ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            return counts
        except Exception as exc:  # noqa: BLE001 — surface a clean message, keep serving
            msg = f"{type(exc).__name__}: {exc}"
            _sync["last_error"] = msg
            return {"error": msg}
        finally:
            _sync["running"] = False
            _sync_lock.release()

    @asynccontextmanager
    async def _lifespan(_app):
        # Auto-sync on every deploy, in the background so the page serves immediately (the watermark
        # keeps token spend bounded to genuinely-new mail). Off for injected state (tests) or when
        # settings.sync.on_startup is false. A failure (e.g. no IMAP password) logs one line, never
        # a traceback — local deploys without creds are normal.
        def _bg():
            r = _run_sync()
            if r.get("error"):
                print(f"  startup sync skipped — {r['error']}")
        if not _injected and settings.get("sync", {}).get("on_startup", True):
            threading.Thread(target=_bg, name="email2data-startup-sync", daemon=True).start()
        yield

    app = FastAPI(title="email-2-data workspace", lifespan=_lifespan)
    app.state.client = None
    app.state.sync_lock = _sync_lock  # exposed for tests to force the "already running" path

    def _spec_payload(mid: str) -> dict:
        """Merged spec + readiness in the wire shape the report renders (job_fields + items[])."""
        spec, rd = ws.merge(jspecs[mid])
        d = spec.to_dict()
        return {"readiness": rd, "job_fields": d["job_fields"], "items": d["items"]}

    @app.get("/inbox", response_class=HTMLResponse)
    def inbox():
        # The inbox report (was "/"; the Fila is now home — A3). overlay decisions onto each job's
        # auto-spec (idempotent: always from the original).
        for e in emails:
            j = jspecs.get(e["message_id"])
            if j:
                e["_jobspec"] = {**j, **_spec_payload(e["message_id"])}
        return report.build_html(emails, contacts, cost,
                                 reclassifications=ws.get_reclassifications(), live=True)

    @app.post("/api/sync")
    async def api_sync(request: Request):
        """Pull new mail + triage new emails, then refresh the page state. Synchronous: the user
        clicked and waits. A concurrent call (e.g. the startup thread still running) returns 409."""
        import anyio
        full = False
        try:
            full = bool((await request.json()).get("full", False))
        except Exception:  # noqa: BLE001 — empty/invalid body → default
            pass
        counts = await anyio.to_thread.run_sync(lambda: _run_sync(full))
        if counts.get("running"):
            return JSONResponse({"running": True}, status_code=409)
        if counts.get("error"):
            return JSONResponse({"error": counts["error"]}, status_code=503)
        return JSONResponse(counts)

    @app.get("/api/sync/status")
    def api_sync_status():
        return JSONResponse({"running": _sync["running"], "last_counts": _sync["last_counts"],
                             "last_ts": _sync["last_ts"], "last_error": _sync.get("last_error")})

    @app.post("/api/confirm")
    async def confirm(request: Request):
        body = await request.json()
        mid, field = str(body.get("message_id", "")), str(body.get("field", ""))
        value = str(body.get("value", "")).strip()
        base, _idx = js.parse_address(field)
        if mid not in jspecs or base not in _keys:
            return JSONResponse({"error": "bad request"}, status_code=400)
        ws.confirm(mid, field, value) if value else ws.clear(mid, field)
        return JSONResponse(_spec_payload(mid))

    @app.post("/api/item/add")
    async def add_item(request: Request):
        body = await request.json()
        mid = str(body.get("message_id", ""))
        if mid not in jspecs:
            return JSONResponse({"error": "not found"}, status_code=404)
        spec, _ = ws.merge(jspecs[mid])
        ws.set_item_count(mid, len(spec.items) + 1)
        return JSONResponse(_spec_payload(mid))

    @app.post("/api/item/remove")
    async def remove_item(request: Request):
        body = await request.json()
        mid = str(body.get("message_id", ""))
        index = int(body.get("index", -1))
        if mid not in jspecs:
            return JSONResponse({"error": "not found"}, status_code=404)
        spec, _ = ws.merge(jspecs[mid])
        if not (0 <= index < len(spec.items)) or len(spec.items) <= 1:
            return JSONResponse({"error": "bad index"}, status_code=400)
        ws.remove_item(mid, index)
        return JSONResponse(_spec_payload(mid))

    @app.post("/api/reply")
    async def reply(request: Request):
        body = await request.json()
        mid = str(body.get("message_id", ""))
        if mid not in jspecs:
            return JSONResponse({"error": "not found"}, status_code=404)
        spec, rd = ws.merge(jspecs[mid])
        if app.state.client is None:
            app.state.client = classifier.make_client(settings)
        return JSONResponse({"reply": replydraft.draft_reply(spec.to_dict(), rd, rpb, app.state.client, settings)})

    @app.post("/api/reply/stream")
    async def reply_stream(request: Request):
        """Stream the clarifying-reply draft token-by-token (text/plain). NEVER sends.

        The non-streaming ``/api/reply`` above stays as the tested fallback the UI uses when the
        browser can't read a streaming body or this route errors before the first chunk.
        """
        from fastapi.responses import StreamingResponse
        body = await request.json()
        mid = str(body.get("message_id", ""))
        if mid not in jspecs:
            return JSONResponse({"error": "not found"}, status_code=404)
        spec, rd = ws.merge(jspecs[mid])
        if app.state.client is None:
            app.state.client = classifier.make_client(settings)

        def gen():
            yield from replydraft.draft_reply_stream(spec.to_dict(), rd, rpb, app.state.client, settings)

        return StreamingResponse(gen(), media_type="text/plain; charset=utf-8",
                                 headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})

    @app.post("/api/reclassify")
    async def reclassify_email(request: Request):
        """Save (or clear) a human correction to counterparty / purpose / priority.

        Body: {message_id, field, value_auto, value_human}
        Send value_human="" or null to reset to auto (deletes the override row).
        Both value_auto and value_human are stored for training-pair export later.
        """
        body = await request.json()
        mid = str(body.get("message_id", ""))
        field = str(body.get("field", ""))
        value_auto = body.get("value_auto") or None
        value_human = (body.get("value_human") or "").strip() or None
        if not mid or field not in RECLASSIFY_FIELDS:
            return JSONResponse({"error": "bad request"}, status_code=400)
        if value_human:
            ws.reclassify(mid, field, value_auto, value_human)
        else:
            ws.clear_reclassify(mid, field)
        return JSONResponse({"ok": True, "field": field, "value_human": value_human})

    @app.get("/api/reclassifications")
    def get_reclassifications():
        """Export all human corrections as {message_id: {field: {auto, human}}} — training data."""
        rows = ws._conn.execute(  # type: ignore[union-attr]
            "SELECT message_id, field, value_auto, value_human, ts FROM reclassifications ORDER BY ts DESC"
        ).fetchall()
        out: dict[str, list] = {}
        for r in rows:
            out.setdefault(r["message_id"], []).append(
                {"field": r["field"], "auto": r["value_auto"], "human": r["value_human"], "ts": r["ts"]}
            )
        return JSONResponse(out)

    @app.get("/api/relations/{message_id}")
    def get_relations(message_id: str):
        """Return thread siblings, same-contact history, and entity cross-refs for one message.

        Requires ``out/crm.db`` (run ``email2data crm`` first).  Returns 503 when the CRM is
        not available, 404 when the message_id is unknown, or a JSON object with three lists::

            {
              "thread":     [{interaction…}, …],
              "by_contact": [{interaction…}, …],
              "by_entity":  [{interaction…, "_matched_entity": "nif"}, …],
            }
        """
        if _crmdb is None:
            return JSONResponse(
                {"error": "CRM not available — run `email2data crm` first"}, status_code=503
            )
        result = _crmdb.related(message_id)
        if not any(result.values()):
            # Check whether the message_id is simply unknown vs genuinely no relations.
            known = _crmdb._conn.execute(  # type: ignore[union-attr]
                "SELECT 1 FROM interactions WHERE message_id=?", (message_id,)
            ).fetchone()
            if known is None:
                return JSONResponse({"error": "message_id not found in CRM"}, status_code=404)
        return JSONResponse(result)

    # -------------------------------------------------------------------------
    # Projects — cross-thread canonical spec + export. Shares the Workspace connection.
    # -------------------------------------------------------------------------
    pstore = _project.ProjectStore(ws._conn)

    def _project_view(pid: str) -> dict:
        """Canonical spec + readiness + provenance + conflicts + threads/messages for one project."""
        proj = pstore.get(pid)
        mids = _project.message_ids_for(pstore, pid, _crmdb)
        # reuse mids so build_canonical doesn't re-run the CRM thread-expansion (perf: one pass, not two)
        spec, rd, prov, conflicts = _project.build_canonical(pstore, ws, jspecs, pid, _crmdb, mids=mids)
        d = spec.to_dict()
        return {
            "project_id": pid, "project": proj,
            "job_fields": d["job_fields"], "items": d["items"], "readiness": rd,
            "provenance": prov, "conflicts": conflicts,
            "threads": pstore.threads_for(pid),
            "message_ids": mids,
            "dangling_threads": _project.dangling_threads(pstore, pid, _crmdb),
        }

    @app.get("/api/projects")
    def list_projects(archived: bool = False):
        out = []
        for pr in pstore.list(include_archived=archived):
            _spec, rd, _p, _c = _project.build_canonical(pstore, ws, jspecs, pr["project_id"], _crmdb)
            out.append({**pr, "n_threads": len(pstore.threads_for(pr["project_id"])),
                        "coverage": rd.get("coverage", 0.0), "estimable": rd.get("estimable", False)})
        return JSONResponse(out)

    @app.post("/api/projects")
    async def create_project(request: Request):
        body = await request.json()
        title = str(body.get("title", "")).strip()
        if not title:
            return JSONResponse({"error": "title required"}, status_code=400)
        client = (body.get("client_email") or None)
        mid = str(body.get("from_message", "") or "")
        client_name = client
        if mid and mid in jspecs:
            client_name = jspecs[mid].get("counterparty") or client
        pid = pstore.create(title, client_email=client, client_name=client_name)
        if mid and mid in jspecs:
            pstore.attach_thread(pid, _project.resolve_thread_root(_crmdb, mid))
            _project.seed_items_from(pstore, ws, jspecs, pid, mid)
        return JSONResponse({"project_id": pid})

    @app.get("/api/projects/{pid}")
    def get_project(pid: str):
        if pstore.get(pid) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(_project_view(pid))

    @app.delete("/api/projects/{pid}")
    def delete_project(pid: str):
        """Hard-delete a project (mistakes/duplicates). To soft-retire instead, set stage=ARCHIVED."""
        if not pstore.delete(pid):
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse({"ok": True, "deleted": pid})

    @app.post("/api/projects/{pid}/detach")
    async def detach(pid: str, request: Request):
        """Remove a thread from a project. Accepts a thread_root or any message_id in it (resolved)."""
        if pstore.get(pid) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        body = await request.json()
        ref = str(body.get("ref", "")).strip()
        if not ref:
            return JSONResponse({"error": "ref required"}, status_code=400)
        pstore.detach_thread(pid, _project.resolve_thread_root(_crmdb, ref))
        return JSONResponse(_project_view(pid))

    @app.post("/api/projects/{pid}/attach")
    async def attach(pid: str, request: Request):
        if pstore.get(pid) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        body = await request.json()
        ref = str(body.get("ref", "")).strip()
        if not ref:
            return JSONResponse({"error": "ref required"}, status_code=400)
        pstore.attach_thread(pid, _project.resolve_thread_root(_crmdb, ref))
        _project.seed_items_from(pstore, ws, jspecs, pid, ref)
        return JSONResponse(_project_view(pid))

    @app.post("/api/projects/{pid}/field")
    async def project_field(pid: str, request: Request):
        if pstore.get(pid) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        body = await request.json()
        field = str(body.get("field", ""))
        value = str(body.get("value", "")).strip()
        base, _i = js.parse_address(field)
        if base not in {k for k, _, _, _, _ in js.FIELDS}:
            return JSONResponse({"error": "bad field"}, status_code=400)
        pstore.set_field(pid, field, value) if value else pstore.clear_field(pid, field)
        return JSONResponse(_project_view(pid))

    @app.post("/api/projects/{pid}/item/add")
    async def project_item_add(pid: str):
        proj = pstore.get(pid)
        if proj is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        pstore.set_item_count(pid, (proj["n_items"] or 1) + 1)
        return JSONResponse(_project_view(pid))

    @app.post("/api/projects/{pid}/item/remove")
    async def project_item_remove(pid: str, request: Request):
        proj = pstore.get(pid)
        if proj is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        body = await request.json()
        index = int(body.get("index", -1))
        if not (0 <= index < (proj["n_items"] or 1)) or (proj["n_items"] or 1) <= 1:
            return JSONResponse({"error": "bad index"}, status_code=400)
        pstore.remove_item(pid, index)
        return JSONResponse(_project_view(pid))

    @app.post("/api/projects/{pid}/stage")
    async def project_stage(pid: str, request: Request):
        if pstore.get(pid) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        body = await request.json()
        stage = str(body.get("stage", ""))
        if stage not in _project.STAGES:
            return JSONResponse({"error": "bad stage"}, status_code=400)
        pstore.set_stage(pid, stage)
        return JSONResponse(_project_view(pid))

    @app.post("/api/projects/{pid}/export")
    async def project_export(pid: str, request: Request):
        if pstore.get(pid) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        body = await request.json()
        adapter_name = str(body.get("adapter", "json"))
        force = bool(body.get("force"))
        if adapter_name == "materials-costing":
            try:
                adapter = _export.MaterialsCostingAdapter.from_settings(settings)
            except ValueError as exc:
                return JSONResponse({"ok": False, "detail": str(exc)}, status_code=400)
        else:
            adapter = _export.JsonFileAdapter(_outdir() if settings.get("__settings_path__") else Path("out"))
        result = _export.export_project(pstore, ws, jspecs, adapter, pid, crm_store=_crmdb, force=force)
        return JSONResponse({"ok": result.ok, "external_id": result.external_id, "detail": result.detail})

    @app.get("/api/attachment/{message_id}/{index}")
    def get_attachment(message_id: str, index: int):
        """Serve one attachment's raw bytes for view/download. Read-only, local, NO parsing.
        Previewable types (PDF, images) open inline; everything else downloads."""
        from fastapi.responses import Response
        from .envelope import attachment_part
        f = _file_for(message_id)
        if f is None:
            return JSONResponse({"error": "message not found"}, status_code=404)
        part = attachment_part(Path(f).read_bytes(), index)
        if part is None:
            return JSONResponse({"error": "attachment not found"}, status_code=404)
        name, ctype, data = part
        disp = "inline" if (ctype.startswith("image/") or ctype == "application/pdf") else "attachment"
        return Response(content=data, media_type=ctype,
                        headers={"Content-Disposition": f'{disp}; filename="{name.replace(chr(34), chr(39))}"'})

    # -------------------------------------------------------------------------
    # Cockpit Fila — response queue (cockpit.build_fila over the CRM + thread_state overlay).
    # A SEPARATE render path from "/" (the inbox report) so it doesn't collide with that template.
    # -------------------------------------------------------------------------
    def _fila_rows() -> list[dict[str, Any]]:
        if _crmdb is None:
            return []
        return cockpit.build_fila(_crmdb.all_interactions(), ws.thread_states(),
                                  now=datetime.now(timezone.utc),
                                  reclassified=ws.get_reclassifications())

    @app.get("/", response_class=HTMLResponse)
    @app.get("/fila", response_class=HTMLResponse)
    def fila():
        return fila_page.build_fila_html(
            _fila_rows(), _team, now_iso=datetime.now(timezone.utc).isoformat(timespec="seconds"))

    @app.get("/api/fila")
    def api_fila():
        return JSONResponse({"rows": _fila_rows(), "team": _team})

    @app.post("/api/thread/handled")
    async def thread_handled(request: Request):
        body = await request.json()
        root = str(body.get("thread_root", "")).strip()
        if not root:
            return JSONResponse({"error": "thread_root required"}, status_code=400)
        ws.set_thread_handled(root, bool(body.get("handled", True)))
        return JSONResponse({"ok": True, "thread_root": root})

    @app.post("/api/thread/owner")
    async def thread_owner(request: Request):
        body = await request.json()
        root = str(body.get("thread_root", "")).strip()
        if not root:
            return JSONResponse({"error": "thread_root required"}, status_code=400)
        owner = str(body.get("owner", "")).strip()
        ws.set_thread_owner(root, owner)
        return JSONResponse({"ok": True, "thread_root": root, "owner": owner})

    return app


def from_settings(settings: dict[str, Any]):
    return create_app(settings)
