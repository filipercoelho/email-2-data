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

from . import (accounts as _accounts, classifier, cockpit, contrapartes_page, crm as _crm,
               export as _export, fila_page, jobspec as js, para_ti, para_ti_page,
               project as _project, projetos_page, replydraft, report)
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
        from . import specbuild
        from . import sync as _syncmod
        if not _sync_lock.acquire(blocking=False):
            return {"running": True}
        _sync["running"] = True
        try:
            counts = _syncmod.run_sync(settings, full=full)
            # Keep the spec layer fresh: newly-triaged leads get jobspecs (incremental → only new
            # message_ids pay the LLM cost). Without this, jobspecs.jsonl silently goes stale and
            # projects created from new leads arrive empty. Degrades to offline if no LLM client.
            try:
                counts["jobspecs"] = specbuild.rebuild_jobspecs(
                    settings, draft=True, incremental=True, log=lambda m: print(f"  {m}"))
            except Exception as exc:  # noqa: BLE001 — never let a spec-build error fail the sync
                print(f"  jobspec rebuild skipped — {type(exc).__name__}: {exc}")
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

    @app.get("/api/thread/{thread_root:path}")
    def api_thread(thread_root: str):
        """Return the messages of one email thread with body text, for the Fila inline preview.

        Uses the CRM thread index + the corpus .eml files. Each message carries:
        subject, from_email, date, direction, counterparty, body (capped at 3000 chars),
        has_attachment, and attachment names.  Returns 503 when CRM is unavailable.
        """
        from .envelope import parse_eml as _parse_eml
        if _crmdb is None:
            return JSONResponse({"error": "CRM not available"}, status_code=503)
        interactions = _crmdb.thread(thread_root)
        if not interactions:
            return JSONResponse({"error": "thread not found"}, status_code=404)
        messages = []
        for row in interactions:
            mid = row.get("message_id", "")
            msg: dict = {
                "message_id": mid,
                "subject": row.get("subject", ""),
                "from_email": row.get("from_email", ""),
                "date": row.get("date", ""),
                "direction": row.get("direction", ""),
                "counterparty": row.get("counterparty", ""),
                "body": "",
                "to": [],
                "has_attachment": bool(row.get("has_attach")),
                "attachments": [],
            }
            f = _file_for(mid)
            if f:
                try:
                    env = _parse_eml(Path(f).read_bytes())
                    body = env.get("body_text") or ""
                    msg["body"] = body[:3000]
                    msg["body_truncated"] = len(body) > 3000
                    # recipients so the UI can show "sent to whom", not just "→ enviado"
                    msg["to"] = [a.get("email") for a in (env.get("to") or []) if a.get("email")]
                    msg["attachments"] = [
                        {"name": a.get("filename") or "(sem nome)", "type": a.get("content_type", "")}
                        for a in (env.get("attachments") or [])
                    ]
                except Exception:  # noqa: BLE001
                    pass
            messages.append(msg)
        return JSONResponse({"thread_root": thread_root, "messages": messages})

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

    def _resolve_ref(ref: str) -> tuple[str, str, list[dict]]:
        """Resolve a project source reference → (thread_root, seed_message_id, thread_rows).

        ``ref`` may be a message_id OR a thread_root — Para-ti suggestions send the *root*, the
        report UI sends a message_id. We resolve the canonical root and pick a ``seed_message_id``
        that actually has a jobspec (so line items can be seeded), preferring ``ref`` itself when it
        is one. Without this, a suggested project whose root message wasn't triaged attaches nothing
        and arrives empty. ``seed_message_id`` is "" when no message in the thread has a spec."""
        root = _project.resolve_thread_root(_crmdb, ref)
        rows = _crmdb.thread(root) if _crmdb is not None else []
        mids = [r["message_id"] for r in rows] or [ref]
        seed_mid = ref if ref in jspecs else next((m for m in mids if m in jspecs), "")
        return root, seed_mid, rows

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
        # `from_message` may be a message_id OR a thread_root (suggestions send the root). Resolve it
        # so the project ALWAYS arrives with its source thread attached, line items seeded, and client
        # identity filled — the whole point of creating a project from a lead.
        ref = str(body.get("from_message", "") or "")
        client_name = client
        root = seed_mid = ""
        rows: list[dict] = []
        if ref:
            root, seed_mid, rows = _resolve_ref(ref)
            if seed_mid and seed_mid in jspecs:
                client_name = jspecs[seed_mid].get("counterparty") or client_name
            if not client:  # best-effort client email: first inbound sender in the thread
                client = next((r.get("from_email") for r in rows
                               if r.get("direction") == "inbound" and r.get("from_email")), None)
        pid = pstore.create(title, client_email=client, client_name=client_name)
        if root:
            pstore.attach_thread(pid, root)
            if seed_mid:
                _project.seed_items_from(pstore, ws, jspecs, pid, seed_mid)
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
        # Accept a message_id OR a thread_root and seed from a message that has a spec (see _resolve_ref).
        root, seed_mid, _rows = _resolve_ref(ref)
        pstore.attach_thread(pid, root)
        if seed_mid:
            _project.seed_items_from(pstore, ws, jspecs, pid, seed_mid)
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
        rows = cockpit.build_fila(_crmdb.all_interactions(), ws.thread_states(),
                                  now=datetime.now(timezone.utc),
                                  reclassified=ws.get_reclassifications())
        # Annotate each thread with the project it already belongs to (if any), so the Fila can show
        # "already in project X" and offer open-vs-create — preventing duplicate projects from one lead.
        root2proj: dict[str, dict] = {}
        for pr in pstore.list(include_archived=True):
            info = {"project_id": pr["project_id"], "title": pr.get("title") or pr["project_id"],
                    "stage": pr.get("stage") or ""}
            for root in pstore.threads_for(pr["project_id"]):
                root2proj.setdefault(root, info)
        for r in rows:
            r["project"] = root2proj.get(r.get("thread_root"))
        return rows

    # -------------------------------------------------------------------------
    # Shared cluster builder (C1a/C1b) — assembled per-request; cheap (in-memory).
    # -------------------------------------------------------------------------
    def _clusters() -> list[_accounts.AccountCluster]:
        if _crmdb is None:
            return []
        return _accounts.cluster(
            _crmdb.all_contacts(),
            nif_refs=_crmdb.contacts_by_nif(),
            identity_links=ws.identity_links(),
        )

    def _clusters_as_dicts(cls: list[_accounts.AccountCluster]) -> list[dict[str, Any]]:
        """Serialize clusters + enrich with Fila response-risk for the UI."""
        frows = _fila_rows()
        # Index fila rows by each email that appears in them
        risk_by_email: dict[str, str] = {}
        owe_by_email: dict[str, int] = {}
        for r in frows:
            contact = r.get("contact") or ""
            if contact:
                band = (r.get("clock") or {}).get("band", "none")
                risk_by_email[contact] = max(
                    risk_by_email.get(contact, "none"), band,
                    key=lambda b: {"red": 3, "amber": 2, "green": 1, "none": 0}.get(b, 0)
                )
                if band in ("red", "amber"):
                    owe_by_email[contact] = owe_by_email.get(contact, 0) + 1
        out = []
        for cl in cls:
            we_owe = sum(owe_by_email.get(e, 0) for e in cl.emails)
            risk = "none"
            for e in cl.emails:
                r_band = risk_by_email.get(e, "none")
                if {"red": 3, "amber": 2, "green": 1, "none": 0}.get(r_band, 0) > \
                   {"red": 3, "amber": 2, "green": 1, "none": 0}.get(risk, 0):
                    risk = r_band
            # Find open projects for this cluster
            open_proj = 0
            if _crmdb is not None:
                for e in cl.emails:
                    for p in pstore.list():
                        if (p.get("client_email") or "") == e and p.get("stage") not in ("WON", "LOST", "ARCHIVED"):
                            open_proj += 1
            out.append({
                "key": cl.key, "kind": cl.kind, "emails": cl.emails,
                "display_name": cl.display_name or cl.key,
                "nif": cl.nif, "last_counterparty": cl.last_counterparty,
                "last_seen": cl.last_seen, "msg_count": cl.msg_count,
                "we_owe_count": we_owe, "response_risk": risk, "open_projects": open_proj,
            })
        return out

    def _nav_counts() -> dict[str, int]:
        """Live counts for the nav badges (C5). Only shows non-zero."""
        frows = _fila_rows()
        active = len(frows)
        para_ti_count = len(para_ti.all_items(
            frows, _clusters(),
            {t for p in pstore.list() for t in pstore.threads_for(p["project_id"])},
        ))
        return {k: v for k, v in {"fila": active, "para-ti": para_ti_count}.items() if v}

    @app.get("/", response_class=HTMLResponse)
    @app.get("/fila", response_class=HTMLResponse)
    def fila():
        return HTMLResponse(fila_page.build_fila_html(
            _fila_rows(), _team,
            now_iso=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            nav_counts=_nav_counts()))

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

    # -------------------------------------------------------------------------
    # C2 — Contrapartes lens
    # -------------------------------------------------------------------------
    @app.get("/contrapartes", response_class=HTMLResponse)
    def contrapartes_list():
        cls = _clusters()
        return HTMLResponse(contrapartes_page.build_list_html(
            _clusters_as_dicts(cls), nav_counts=_nav_counts()))

    @app.get("/api/contrapartes")
    def api_contrapartes():
        return JSONResponse(_clusters_as_dicts(_clusters()))

    @app.get("/contrapartes/{key:path}", response_class=HTMLResponse)
    def contrapartes_detail(key: str):
        cls = _clusters()
        cluster_dict: dict[str, Any] | None = None
        for c in _clusters_as_dicts(cls):
            if c["key"] == key:
                cluster_dict = c
                break
        if cluster_dict is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        # Build timeline: all interactions for any email in cluster, sorted oldest-first.
        timeline: list[dict[str, Any]] = []
        if _crmdb is not None:
            seen: set[str] = set()
            for email in cluster_dict["emails"]:
                for row in _crmdb.by_contact(email):
                    mid = row["message_id"]
                    if mid not in seen:
                        seen.add(mid)
                        timeline.append({
                            "type": "interaction",
                            "date": row.get("date", ""),
                            "subject": row.get("subject", ""),
                            "purpose": row.get("purpose", ""),
                            "message_id": mid,
                        })
            timeline.sort(key=lambda r: r.get("date") or "")
        # Projects whose client_email matches a cluster email.
        cluster_projects = [
            p for p in pstore.list()
            if (p.get("client_email") or "") in cluster_dict["emails"]
        ]
        # Fila rows for this cluster
        cluster_frows = [
            r for r in _fila_rows()
            if (r.get("contact") or "") in cluster_dict["emails"]
        ]
        return HTMLResponse(contrapartes_page.build_detail_html(
            cluster_dict, timeline, cluster_projects, cluster_frows,
            nav_counts=_nav_counts()))

    @app.get("/api/contrapartes/{key:path}")
    def api_contrapartes_detail(key: str):
        cls = _clusters()
        cluster_dict: dict[str, Any] | None = None
        for c in _clusters_as_dicts(cls):
            if c["key"] == key:
                cluster_dict = c
                break
        if cluster_dict is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse({"cluster": cluster_dict})

    # -------------------------------------------------------------------------
    # C3 — Para ti decision inbox
    # -------------------------------------------------------------------------
    def _para_ti_items() -> list[dict[str, Any]]:
        frows = _fila_rows()
        all_threads = {t for p in pstore.list() for t in pstore.threads_for(p["project_id"])}
        return para_ti.all_items(frows, _clusters(), all_threads)

    @app.get("/para-ti", response_class=HTMLResponse)
    def para_ti_view():
        return HTMLResponse(para_ti_page.build_html(_para_ti_items(), nav_counts=_nav_counts()))

    @app.get("/api/para-ti")
    def api_para_ti():
        return JSONResponse({"items": _para_ti_items()})

    @app.post("/api/identity/confirm")
    async def identity_confirm(request: Request):
        body = await request.json()
        email = str(body.get("email", "")).strip().lower()
        key = str(body.get("account_key", "")).strip()
        if not email or not key:
            return JSONResponse({"error": "email and account_key required"}, status_code=400)
        ws.set_identity_link(email, key)
        return JSONResponse({"ok": True, "email": email, "account_key": key})

    # -------------------------------------------------------------------------
    # C4 — Projetos lens (sidesteps report.py WIP; reuses existing /api/projects*)
    # -------------------------------------------------------------------------
    @app.get("/projetos", response_class=HTMLResponse)
    def projetos_view():
        # Build a trimmed summary list (avoid the O(projects×messages) build_canonical per row).
        projects_summary = []
        for p in pstore.list():
            try:
                _, rd, _, _ = _project.build_canonical(pstore, ws, jspecs, p["project_id"], _crmdb)
                projects_summary.append({**p, "coverage": rd.get("coverage", 0.0),
                                         "estimable": rd.get("estimable", False),
                                         "n_threads": len(pstore.threads_for(p["project_id"]))})
            except Exception:  # noqa: BLE001 — degrade gracefully on a broken project
                projects_summary.append({**p, "coverage": 0.0, "estimable": False, "n_threads": 0})
        return HTMLResponse(projetos_page.build_html(projects_summary, nav_counts=_nav_counts()))

    return app


def from_settings(settings: dict[str, Any]):
    return create_app(settings)
