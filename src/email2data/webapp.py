"""FastAPI workspace — serves the rich report (email2data.report) LIVE, with editable job-spec fields.

One UI, not two: ``GET /`` renders the same report as the static file but with ``live=True`` (the
job-spec panel becomes editable + a Regenerate button). Confirmations persist to the precious
``Workspace`` and **overlay** the regenerable specs. ``POST /api/confirm`` and ``POST /api/reply`` are
keyed by ``message_id``. **NEVER sends** — copy/paste only. Single-user, localhost.

Note: no ``from __future__ import annotations`` here — FastAPI must see the real ``Request`` class on
the route signatures (the future-import would make it an unresolved string -> 422).
"""

import hashlib
import json
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import (accounts as _accounts, captures, captures_page, classifier, clientdraft, cockpit,
               contrapartes_page, crm as _crm, export as _export, fila_page, jobspec as js, para_ti,
               para_ti_page, project as _project, projetos_page, replydraft, report)
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
               prepared=None, crm_store=None, corpus_index=None, capture_store=None,
               captures_dir=None):
    """Injectable factory. Defaults wire to the real files; tests pass prepared/jobspecs/workspace.

    ``crm_store`` is an open ``CrmStore`` instance; when omitted the factory opens ``out/crm.db``
    if it exists, or leaves relation queries unavailable (503) if it doesn't.
    """
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse

    # Touch __settings_path__ only for args that aren't injected (tests inject everything).
    def _outdir():
        return paths(settings, settings["__settings_path__"])["out_dir"]

    def _capturesdir() -> Path:
        # intake media root (ADR-020 sole-copy); injectable for tests, else resolved from settings.
        if captures_dir is not None:
            return Path(captures_dir)
        return paths(settings, settings["__settings_path__"])["captures_dir"]
    ws = workspace or Workspace(_outdir() / "workspace.db").connect()
    jspecs = jobspecs if jobspecs is not None else _load_jobspecs(_outdir())
    rpb = (reply_pb if reply_pb is not None
           else replydraft.load_playbook(Path(settings["__settings_path__"]).parents[1] / "config" / "reply_playbook.md"))
    emails, contacts, cost = prepared if prepared is not None else report.prepare(settings)
    _team = list(settings.get("team", []) or [])  # base owner roster (settings.json); never removable in-app

    def _roster() -> list[str]:
        """Effective owner roster = settings.team (in its configured order) followed by the in-app-added
        names (workspace.db). Computed per request so a freshly-added owner shows up without a restart
        (v4: "define new owners")."""
        return list(_team) + [n for n in ws.roster() if n not in _team]

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

    # Reply-draft memo (regenerable, in-process): keyed by (message_id, hash of the EXACT reply
    # prompt). A re-open / page-reload / second client for an UNCHANGED spec is served from here and
    # costs 0 tokens; any spec/readiness change (sync, confirm, item edit) changes the prompt -> new
    # key -> regenerate. Cold on restart by design — it caches LLM output, not precious state.
    _reply_cache: dict[tuple[str, str], str] = {}

    def _reply_key(mid: str, spec_d: dict, rd: dict) -> tuple[str, str]:
        prompt = replydraft.build_reply_message(spec_d, rd)
        return (mid, hashlib.sha256(prompt.encode("utf-8")).hexdigest())

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
        # New mail can change a project's coverage/estimable → mark the denormalized summaries stale
        # so the next list view recomputes them lazily (F3). Cheap single UPDATE.
        try:
            pstore.invalidate_summaries()
        except Exception:  # noqa: BLE001 — summary upkeep must never break a sync
            pass

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

    @app.get("/healthz")
    def healthz():
        """Liveness probe for the Docker HEALTHCHECK / orchestrators. Cheap — no DB/LLM/IMAP, just
        proves the app constructed and is serving. A crash-looping boot (e.g. a missing volume) never
        reaches this, so the container is marked unhealthy instead of silently restart-looping."""
        return JSONResponse({"status": "ok"})

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
        spec_d = spec.to_dict()
        ck = _reply_key(mid, spec_d, rd)
        if ck in _reply_cache:  # unchanged spec since last draft — serve cached, spend 0 tokens
            return JSONResponse({"reply": _reply_cache[ck], "cached": True})
        # The Gemini round-trip is BLOCKING — dispatch it OFF the event loop (mirror /api/sync,
        # NOT a bare in-loop call) so a multi-second LLM call can't freeze the single worker for
        # every other request (nav badges, list fetches). Any future LLM endpoint must do the same.
        import anyio

        def _draft():
            if app.state.client is None:
                app.state.client = classifier.make_client(settings)
            return replydraft.draft_reply(spec_d, rd, rpb, app.state.client, settings)

        text = await anyio.to_thread.run_sync(_draft)
        _reply_cache[ck] = text
        return JSONResponse({"reply": text})

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
        spec_d = spec.to_dict()
        ck = _reply_key(mid, spec_d, rd)

        def gen():
            # Runs in Starlette's threadpool (sync generator), so the blocking client init +
            # token generation stay off the event loop — keep make_client INSIDE the generator.
            if ck in _reply_cache:  # cached (e.g. a prior reload/non-stream draft) — replay, 0 tokens
                yield _reply_cache[ck]
                return
            if app.state.client is None:
                app.state.client = classifier.make_client(settings)
            chunks: list[str] = []
            for piece in replydraft.draft_reply_stream(spec_d, rd, rpb, app.state.client, settings):
                chunks.append(piece)
                yield piece
            _reply_cache[ck] = "".join(chunks)  # populate so the next reload / non-stream call is free

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
        """Return the messages of one email thread with body text.

        Merges two sources:
        1. IMAP messages in the CRM thread index (directly received or sent).
        2. Embedded messages extracted from forwarded/reply chains — emails that were never
           separate IMAP messages but are only available as quoted blocks inside a received
           message (e.g. the original client inquiry inside an internal forward).

        Both are returned in chronological order. Embedded messages carry ``"embedded": true``
        so the UI can render them with a subtle visual distinction.
        """
        from .envelope import clean_email_body as _clean_body
        from .envelope import extract_embedded_messages as _extract_embedded
        from .envelope import parse_eml as _parse_eml
        from .signals import OUR_DOMAIN
        if _crmdb is None:
            return JSONResponse({"error": "CRM not available"}, status_code=503)
        interactions = _crmdb.thread(thread_root)
        if not interactions:
            return JSONResponse({"error": "thread not found"}, status_code=404)
        messages = []
        # Track (from_email, date_prefix) of real messages so we don't duplicate as embedded.
        real_keys: set[tuple[str, str]] = set()
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
                "embedded": False,
            }
            f = _file_for(mid)
            if f:
                try:
                    env = _parse_eml(Path(f).read_bytes())
                    body = env.get("body_text") or ""
                    msg["body"] = body[:3000]
                    msg["body_clean"] = _clean_body(body)[:3000]
                    msg["body_truncated"] = len(body) > 3000
                    msg["attachments"] = [
                        {"name": a.get("filename") or "(sem nome)", "type": a.get("content_type", "")}
                        for a in (env.get("attachments") or [])
                    ]
                    # MIME To: header
                    msg["to"] = [a.get("email") for a in (env.get("to") or []) if a.get("email")]
                    # Recover from_email/date when Outlook strips headers (e.g. Trash messages).
                    if not msg["from_email"]:
                        msg["from_email"] = (env.get("from") or {}).get("email") or ""
                    if not msg["date"]:
                        msg["date"] = env.get("date") or ""
                    # Recover missing To: from Outlook inline header in the body (Trash messages
                    # often have no MIME To: but the forwarded header block has Para:/To: lines).
                    if not msg["to"] and body:
                        import re as _re
                        to_m = _re.findall(
                            r"(?:^|\n)(?:To|Para)\s*:[^\n]*?([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})",
                            body[:800], _re.I)
                        if to_m:
                            msg["to"] = list(dict.fromkeys(to_m))[:3]
                    # Re-derive direction from the recovered from_email when the CRM stored
                    # 'inbound' because the From: header was blank at triage time. A message
                    # FROM our own domain is never inbound — it's internal (same-domain) or
                    # outbound (reply to an external address).
                    if msg["direction"] == "inbound" and msg["from_email"]:
                        from_domain = msg["from_email"].rsplit("@", 1)[-1].lower() if "@" in msg["from_email"] else ""
                        is_ours = from_domain == OUR_DOMAIN or from_domain.endswith("." + OUR_DOMAIN)
                        if is_ours:
                            has_external_to = any(
                                (e.rsplit("@", 1)[-1].lower() if "@" in e else "") not in (OUR_DOMAIN, "")
                                and not (e.rsplit("@", 1)[-1].lower()).endswith("." + OUR_DOMAIN)
                                for e in msg["to"]
                            )
                            msg["direction"] = "outbound" if has_external_to else "internal"
                except Exception:  # noqa: BLE001
                    pass
            real_keys.add((msg["from_email"].lower(), (msg["date"] or "")[:10]))
            messages.append(msg)

        import re as _re

        # ── Step 1: extract embedded from ALL IMAP messages before any dedup ─
        # Must run first — dedup may suppress the container message that holds the embedded email.
        embedded_seen: set[tuple[str, str]] = set(real_keys)
        embedded_msgs: list[dict] = []
        anchor_date = next(
            (m["date"][:10] for m in messages if m.get("date") and "T" in m.get("date", "")), "")
        for msg in messages:
            if not msg["body"]:
                continue
            for em in _extract_embedded(msg["body"]):
                key = (em["from_email"].lower(), em["date_raw"].lower()[:10])
                if key in embedded_seen or not em.get("body"):
                    continue
                embedded_seen.add(key)
                domain = em["from_email"].rsplit("@", 1)[-1].lower() if "@" in em["from_email"] else ""
                direction = ("internal" if domain == OUR_DOMAIN or domain.endswith("." + OUR_DOMAIN)
                             else "inbound")
                time_m = _re.search(r"(\d{1,2}:\d{2})", em["date_raw"])
                iso_date = (f"{anchor_date}T{time_m.group(1)}"
                            if anchor_date and time_m else em["date_raw"])
                embedded_msgs.append({
                    "message_id": f"embedded:{em['from_email']}:{em['date_raw'][:16]}",
                    "subject": em.get("subject") or "",
                    "from_email": em["from_email"],
                    "date": iso_date,
                    "direction": direction,
                    "counterparty": "",
                    "body": em["body"][:3000],
                    "body_clean": _clean_body(em["body"])[:3000],
                    "to": em.get("to_emails") or [],
                    "has_attachment": False,
                    "attachments": [],
                    "embedded": True,
                })

        # ── Step 2: dedup IMAP messages ───────────────────────────────────────
        # Outlook saves multiple Trash copies of the same forward. Keep the richest copy per
        # visible-body fingerprint (most attachments); suppress empty-body messages whose
        # attachments are already covered by another card.

        def _body_fingerprint(body: str) -> str:
            """First 120 chars of visible text (before any quoted block)."""
            pats = [r'(?m)^>.*', r'(?im)^No dia .+', r'(?im)^Em .+escreveu:',
                    r'(?ims)^\s*De:\s.+', r'(?ims)^\s*From:\s.+']
            best = -1
            for p in pats:
                mm = _re.search(p, body)
                if mm and (best < 0 or mm.start() < best):
                    best = mm.start()
            visible = body[:best].strip() if best >= 0 else body.strip()
            return visible[:120].lower()

        by_fp: dict[str, list[dict]] = {}
        no_fp: list[dict] = []
        for m in messages:
            fp = _body_fingerprint(m.get("body") or "")
            if fp:
                by_fp.setdefault(fp, []).append(m)
            else:
                no_fp.append(m)

        deduped: list[dict] = []
        for group in by_fp.values():
            deduped.append(max(group, key=lambda x: len(x.get("attachments") or [])))

        # Keep empty-body messages only if they carry attachments not seen elsewhere
        all_att_names = {a["name"] for m in deduped for a in (m.get("attachments") or [])}
        for m in no_fp:
            unique = {a["name"] for a in (m.get("attachments") or [])} - all_att_names
            if unique:
                deduped.append(m)
                all_att_names |= unique

        messages = deduped

        # Merge: real messages first, then embedded sorted by date string (best-effort).
        # Since embedded date_raw is a human string ("3 de junho de 2026 14:33"), sort by the
        # time part (HH:MM) which is locale-independent and appears at the end.
        all_msgs = messages + sorted(embedded_msgs,
                                     key=lambda m: (m["date"] or "")[-5:])
        # Re-sort the full list: real messages already have ISO dates; use ISO date when present,
        # fall back to time suffix for embedded. Simple stable sort keeps relative order of same-date.
        def _sort_key(m: dict) -> str:
            d = m.get("date") or ""
            if "T" in d:
                return d[:16]           # ISO: "2026-06-03T14:33"
            return "2026-06-03T" + d[-5:]  # embedded raw: best-effort time-of-day sort

        all_msgs.sort(key=_sort_key)
        return JSONResponse({"thread_root": thread_root, "messages": all_msgs})

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
    cstore = capture_store or captures.CaptureStore(ws._conn)

    def _project_view(pid: str) -> dict:
        """Canonical spec + readiness + provenance + conflicts + custom fields + threads for one
        project. (Timeline is a SEPARATE, lazily-fetched endpoint — keep this default payload light.)"""
        proj = pstore.get(pid)
        mids = _project.message_ids_for(pstore, pid, _crmdb)
        # reuse mids so build_canonical doesn't re-run the CRM thread-expansion (perf: one pass, not two)
        spec, rd, prov, conflicts = _project.build_canonical(pstore, ws, jspecs, pid, _crmdb, mids=mids)
        # keep the denormalized list summary fresh off the same compute we just did (F3)
        pstore.set_summary(pid, rd.get("coverage", 0.0), rd.get("estimable", False))
        d = spec.to_dict()
        return {
            "project_id": pid, "project": proj,
            "owners": pstore.owners_for(pid),       # multi-owner (v4)
            "job_fields": d["job_fields"], "items": d["items"], "readiness": rd,
            "custom_fields": d["custom_fields"], "field_provenance": pstore.field_provenance(pid),
            "provenance": prov, "conflicts": conflicts,
            "threads": pstore.threads_for(pid),
            "message_ids": mids,
            "dangling_threads": _project.dangling_threads(pstore, pid, _crmdb),
        }

    def _provenance(body: dict) -> dict:
        """Extract the optional provenance bundle (channel/asserted_by/acquired_at) from a write body."""
        return {"channel": str(body.get("channel", "") or ""),
                "asserted_by": str(body.get("asserted_by", "") or ""),
                "acquired_at": str(body.get("acquired_at", "") or "")}

    def _summary_for(pr: dict) -> tuple[float, bool]:
        """Cheap (coverage, estimable) for the LIST view: read the denormalized columns; only fall
        back to a full build_canonical when they're stale/NULL (post-migration or post-sync), then
        persist so subsequent list renders stay O(1) per row (F3)."""
        cov, est = pr.get("coverage"), pr.get("estimable")
        if cov is None or est is None:
            try:
                _s, rd, _p, _c = _project.build_canonical(pstore, ws, jspecs, pr["project_id"], _crmdb)
                cov, est = rd.get("coverage", 0.0), rd.get("estimable", False)
                pstore.set_summary(pr["project_id"], cov, est)
            except Exception:  # noqa: BLE001 — a broken project must not break the list
                cov, est = 0.0, False
        return float(cov or 0.0), bool(est)

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
            cov, est = _summary_for(pr)   # denormalized read (F3); no per-row build_canonical
            out.append({**pr, "n_threads": len(pstore.threads_for(pr["project_id"])),
                        "owners": pstore.owners_for(pr["project_id"]),
                        "coverage": cov, "estimable": est})
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
        # a registry address (the 14 fields, incl per-item #i) OR a custom:<label> field (ADR-015).
        if not (base in _keys or js.is_custom_addr(field)):
            return JSONResponse({"error": "bad field"}, status_code=400)
        if value:
            pstore.set_field(pid, field, value, **_provenance(body))
        else:
            pstore.clear_field(pid, field)
        return JSONResponse(_project_view(pid))

    @app.post("/api/projects/{pid}/custom-field")
    async def project_custom_field(pid: str, request: Request):
        """Add a per-project custom field (ADR-015): tier=context, rendered + audited but never part
        of the estimable gate. Stored at ``custom:<name>``; subsequent edits go through /field."""
        if pstore.get(pid) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        body = await request.json()
        name = str(body.get("name", "")).strip()
        value = str(body.get("value", "")).strip()
        if not name or not value:
            return JSONResponse({"error": "name and value required"}, status_code=400)
        pstore.set_field(pid, js.CUSTOM_PREFIX + name, value, **_provenance(body))
        return JSONResponse(_project_view(pid))

    @app.post("/api/projects/{pid}/event")
    async def project_event(pid: str, request: Request):
        """Capture an off-email knowledge event — note/decision/opinion/todo (ADR-015). Deterministic,
        no LLM: the text is stored verbatim with its provenance, append-only, in the timeline."""
        from .workspace import EVENT_KINDS
        if pstore.get(pid) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        body = await request.json()
        kind = str(body.get("kind", "")).strip().lower()
        text = str(body.get("text", "")).strip()
        if kind not in EVENT_KINDS or not text:
            return JSONResponse(
                {"error": "kind (note/decision/opinion/todo) and text required"}, status_code=400)
        pstore.add_event(pid, kind, text, **_provenance(body))
        return JSONResponse({"ok": True, "kind": kind})

    @app.get("/api/projects/{pid}/timeline")
    def project_timeline(pid: str):
        """The project's audit timeline — field edits (set/clear) + events, newest-first by
        acquired_at (ADR-015). Separate from the detail payload so the workbench stays light."""
        if pstore.get(pid) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse({"timeline": pstore.timeline(pid)})

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
        """Set the lifecycle stage. For a CLOSED stage (CANCELLED/LOST) the body may carry
        ``close_party`` (client|supplier|our) + ``close_reason`` (free text); they're recorded as the
        close-out and cleared automatically if the project later reopens."""
        from .workspace import CLOSE_PARTIES
        if pstore.get(pid) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        body = await request.json()
        stage = str(body.get("stage", ""))
        if stage not in _project.STAGES:
            return JSONResponse({"error": "bad stage"}, status_code=400)
        party = (str(body.get("close_party", "") or "").strip().lower() or None)
        if party is not None and party not in CLOSE_PARTIES:
            return JSONResponse({"error": "bad close_party"}, status_code=400)
        reason = str(body.get("close_reason", "") or "").strip() or None
        pstore.set_stage(pid, stage, close_party=party, close_reason=reason)
        return JSONResponse(_project_view(pid))

    @app.post("/api/projects/{pid}/owners")
    async def project_owners(pid: str, request: Request):
        """Assign owners (multi) to a project, from the roster. ``{owners: [...]}``; replaces the set."""
        if pstore.get(pid) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        body = await request.json()
        owners = [str(o).strip() for o in (body.get("owners") or []) if str(o).strip()]
        pstore.set_owners(pid, owners)
        return JSONResponse(_project_view(pid))

    @app.get("/api/projects/{pid}/participants")
    def project_participants(pid: str):
        """Who has fed knowledge into this project — the named people from the capture ledger's
        asserted_by, rolled up (ADR-015 surfacing of the multi-participant scenario)."""
        if pstore.get(pid) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse({"participants": pstore.participants(pid)})

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

    def _client_email_template() -> str:
        """The editable email skeleton (config/client_email_template.md), re-read per request so a
        playbook edit takes effect without a restart. Falls back to the built-in default."""
        sp = settings.get("__settings_path__")
        if not sp:
            return clientdraft.DEFAULT_TEMPLATE
        return clientdraft.load_template(Path(sp).parents[1] / "config" / "client_email_template.md")

    def _questions_for(keys: list[str]) -> list[str]:
        """Map selected base keys → their pt-PT clarifying questions, in registry order (so the
        rendered list matches the on-screen checklist regardless of click order). Unknown keys
        and keys without a question are dropped."""
        wanted = set(keys)
        return [q for k, _l, _t, q, _s in js.FIELDS if k in wanted and q]

    @app.get("/api/projects/{pid}/draft")
    def project_draft(pid: str):
        """The client-email composer state: recipient, default subject, the selectable prompts
        (jobspec.askables), and the body pre-assembled from the default-ticked (missing must) set —
        i.e. the historical auto-email, now a starting point the user can edit."""
        proj = pstore.get(pid)
        if proj is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        spec, _rd, _p, _c = _project.build_canonical(pstore, ws, jspecs, pid, _crmdb)
        asks = js.askables(spec)
        default_keys = [a["key"] for a in asks if a["default"]]
        body = clientdraft.build_draft(_questions_for(default_keys), _client_email_template())
        return JSONResponse({
            "to": proj.get("client_email") or "",
            "subject": "Re: " + (proj.get("title") or ""),
            "askables": asks,
            "body": body,
        })

    @app.post("/api/projects/{pid}/draft")
    async def project_draft_build(pid: str, request: Request):
        """Re-assemble the body for a given selection. Body: ``{selected: [keys], custom: [str]}``.
        Deterministic — splices the selected questions (+ any free-text custom ones) into the
        template. The user's manual edits live only in the browser textarea; this just rebuilds the
        generated baseline when they toggle a prompt or hit Regenerar."""
        if pstore.get(pid) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        body = await request.json()
        selected = [str(k) for k in (body.get("selected") or [])]
        custom = [str(c).strip() for c in (body.get("custom") or []) if str(c).strip()]
        questions = _questions_for(selected) + custom
        return JSONResponse({"body": clientdraft.build_draft(questions, _client_email_template())})

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
    # Caixa de Capturas — the conversational-intake validation queue (ADR-019 §5 / R9 no-auto-apply).
    # A capture lands here from the Telegram worker; the user validates it INTO a project — nothing is
    # applied automatically. The photo (sole copy once Telegram is scrubbed, ADR-020) is served inline.
    # -------------------------------------------------------------------------
    def _active_projects() -> list[dict[str, Any]]:
        """The active-project pick-list for the Caixa de Capturas (terminal stages filtered out, same
        as the Telegram worker's ``_offer_projects``). Newest-first by id so a fresh lead is on top."""
        active = [p for p in pstore.list() if p.get("stage") not in _project.TERMINAL_STAGES]
        active.sort(key=lambda p: p["project_id"], reverse=True)
        return [{"project_id": p["project_id"], "title": p.get("title") or p["project_id"],
                 "stage": p.get("stage") or ""} for p in active]

    @app.get("/capturas", response_class=HTMLResponse)
    def capturas_view():
        """The Caixa de Capturas validation queue (ADR-019 §5 / R9 no-auto-apply). The page is glue
        over the M3 API; nothing is applied without a deliberate click."""
        return HTMLResponse(captures_page.build_html(
            cstore.list_pending(), _active_projects(), nav_counts=_nav_counts()))

    @app.get("/api/captures")
    def list_captures():
        return JSONResponse({"captures": cstore.list_pending()})

    @app.post("/api/captures/{cid}/apply")
    async def apply_capture(cid: str, request: Request):
        """Validate a capture into a project: append it to the project's ADR-015 ledger carrying the
        capture's own provenance, then mark the capture applied. A photo stays linked via the event's
        source_mid (``capture:<cid>``) so the project timeline can show it."""
        from .workspace import EVENT_KINDS
        cap = cstore.get(cid)
        if cap is None:
            return JSONResponse({"error": "capture not found"}, status_code=404)
        # Idempotency + preserve-at-core (M3 review): a terminal capture (already applied, or discarded)
        # must NEVER re-append to a project ledger. set_project/mark_applied are guarded to pending, but
        # add_event below is NOT — so short-circuit here. Closes a double-click double-write and an
        # apply-after-discard leak of content the user chose to keep out (ADR-019 §5 / ADR-020).
        if cap.get("status") not in captures.PENDING_STATUSES:
            return JSONResponse({"error": "capture is no longer pending",
                                 "status": cap.get("status")}, status_code=409)
        body = await request.json()
        pid = str(body.get("project_id", "")).strip()
        proj = pstore.get(pid)
        if proj is None:
            return JSONResponse({"error": "project not found"}, status_code=404)
        # Match the picker (_active_projects): never file a capture into a closed/archived project.
        if proj.get("stage") in _project.TERMINAL_STAGES:
            return JSONResponse({"error": "project is closed", "stage": proj.get("stage")},
                                status_code=409)
        kind = str(body.get("kind", "note")).strip().lower() or "note"
        if kind not in EVENT_KINDS:
            return JSONResponse({"error": "bad kind (note/decision/opinion/todo)"}, status_code=400)
        text = (cap.get("raw_text") or "").strip() or "📎 captura sem texto"
        pstore.add_event(pid, kind, text,
                         channel=cap.get("channel") or "manual",
                         asserted_by=cap.get("asserted_by") or "",
                         acquired_at=cap.get("acquired_at") or "",
                         source_mid=f"capture:{cid}" if cap.get("media_paths") else "")
        cstore.set_project(cid, pid)
        cstore.mark_applied(cid)
        return JSONResponse({"ok": True, "project_id": pid})

    @app.post("/api/captures/{cid}/discard")
    async def discard_capture(cid: str):
        if cstore.get(cid) is None:
            return JSONResponse({"error": "capture not found"}, status_code=404)
        cstore.discard(cid)
        return JSONResponse({"ok": True})

    @app.get("/api/captures/{cid}/media/{index}")
    def get_capture_media(cid: str, index: int):
        """Serve a capture's photo bytes inline (read-only, local) — guarded against path traversal."""
        import mimetypes
        from fastapi.responses import Response
        cap = cstore.get(cid)
        if cap is None:
            return JSONResponse({"error": "capture not found"}, status_code=404)
        media = cap.get("media_paths") or []
        if index < 0 or index >= len(media):
            return JSONResponse({"error": "media not found"}, status_code=404)
        root = _capturesdir().resolve()
        full = (root / media[index]).resolve()
        if root not in full.parents or not full.is_file():
            return JSONResponse({"error": "media not found"}, status_code=404)
        ctype = mimetypes.guess_type(str(full))[0] or "application/octet-stream"
        return Response(content=full.read_bytes(), media_type=ctype,
                        headers={"Content-Disposition": "inline"})

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

    def _clusters_as_dicts(cls: list[_accounts.AccountCluster],
                           frows: list | None = None) -> list[dict[str, Any]]:
        """Serialize clusters + enrich with Fila response-risk for the UI. Accepts a prebuilt
        ``frows`` so the caller's Fila build is reused, not recomputed (F3)."""
        frows = _fila_rows() if frows is None else frows
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

    def _nav_counts(frows: list | None = None,
                    clusters: list | None = None) -> dict[str, int]:
        """Live counts for the nav badges (C5). Only shows non-zero. Accepts an already-built
        ``frows``/``clusters`` so a page that also renders them doesn't rebuild the whole Fila +
        cluster set a second time per request (F3)."""
        frows = _fila_rows() if frows is None else frows
        clusters = _clusters() if clusters is None else clusters
        active = len(frows)
        para_ti_count = len(para_ti.all_items(
            frows, clusters,
            {t for p in pstore.list() for t in pstore.threads_for(p["project_id"])},
        ))
        # Pending captures awaiting validation (ADR-019 §5 / R9) — the Caixa de Capturas badge.
        capturas_count = len(cstore.list_pending())
        return {k: v for k, v in {"fila": active, "para-ti": para_ti_count,
                                  "capturas": capturas_count}.items() if v}

    @app.get("/", response_class=HTMLResponse)
    @app.get("/fila", response_class=HTMLResponse)
    def fila():
        frows = _fila_rows()  # build once, share with the nav badges (F3)
        return HTMLResponse(fila_page.build_fila_html(
            frows, _roster(),
            now_iso=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            nav_counts=_nav_counts(frows=frows)))

    @app.get("/api/fila")
    def api_fila():
        return JSONResponse({"rows": _fila_rows(), "team": _roster()})

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
        """Assign owners to a Fila thread. Accepts ``{owners: [...]}`` (multi-owner, preferred) or the
        legacy ``{owner: "x"}`` (single; "" clears). The full set is replaced each call."""
        body = await request.json()
        root = str(body.get("thread_root", "")).strip()
        if not root:
            return JSONResponse({"error": "thread_root required"}, status_code=400)
        if "owners" in body:
            owners = [str(o).strip() for o in (body.get("owners") or []) if str(o).strip()]
        else:
            one = str(body.get("owner", "")).strip()
            owners = [one] if one else []
        ws.set_thread_owners(root, owners)
        return JSONResponse({"ok": True, "thread_root": root,
                             "owner": owners[0] if owners else "", "owners": owners})

    # -- in-app owner roster (v4): effective roster = settings.team ∪ ws.roster() ------------------
    @app.get("/api/roster")
    def get_roster():
        return JSONResponse({"roster": _roster(), "team": _team, "added": ws.roster()})

    @app.post("/api/roster")
    async def add_roster(request: Request):
        body = await request.json()
        name = str(body.get("name", "")).strip()
        if not name:
            return JSONResponse({"error": "name required"}, status_code=400)
        ws.roster_add(name)
        return JSONResponse({"ok": True, "roster": _roster()})

    @app.post("/api/roster/remove")
    async def remove_roster(request: Request):
        """Remove an in-app-added owner name. settings.team names live in config and are not removable
        here (returned in ``protected``)."""
        body = await request.json()
        name = str(body.get("name", "")).strip()
        ws.roster_remove(name)
        return JSONResponse({"ok": True, "roster": _roster(), "protected": _team})

    # -------------------------------------------------------------------------
    # C2 — Contrapartes lens
    # -------------------------------------------------------------------------
    @app.get("/contrapartes", response_class=HTMLResponse)
    def contrapartes_list():
        cls = _clusters()
        frows = _fila_rows()  # build Fila + clusters once, reuse for both the list and the badges (F3)
        return HTMLResponse(contrapartes_page.build_list_html(
            _clusters_as_dicts(cls, frows=frows), nav_counts=_nav_counts(frows=frows, clusters=cls)))

    @app.get("/api/contrapartes")
    def api_contrapartes():
        return JSONResponse(_clusters_as_dicts(_clusters()))

    def _contraparte_detail_data(key: str) -> dict[str, Any] | None:
        """Everything the Contrapartes detail hub needs: the cluster, a navigable timeline (each row
        carries its ``thread_root`` + direction so the UI can link into the Fila / inbox), server-side
        rollup ``stats``, the cluster's open Fila threads + projects, and the Para-ti decisions that
        belong to this contraparte. Returns ``None`` when the key is unknown."""
        from collections import Counter
        cluster_dict: dict[str, Any] | None = None
        for c in _clusters_as_dicts(_clusters()):
            if c["key"] == key:
                cluster_dict = c
                break
        if cluster_dict is None:
            return None
        emails = set(cluster_dict["emails"])
        # Timeline: every interaction touching any cluster email, deduped, oldest-first. Carries the
        # navigation handles (thread_root → Fila/inbox, message_id → inbox) + the insight fields.
        timeline: list[dict[str, Any]] = []
        from_counts: Counter = Counter()
        if _crmdb is not None:
            seen: set[str] = set()
            for email in cluster_dict["emails"]:
                for row in _crmdb.by_contact(email):
                    mid = row["message_id"]
                    if mid in seen:
                        continue
                    seen.add(mid)
                    fe = row.get("from_email") or ""
                    if fe in emails:
                        from_counts[fe] += 1
                    timeline.append({
                        "type": "interaction", "date": row.get("date", ""),
                        "subject": row.get("subject", ""), "purpose": row.get("purpose", ""),
                        "message_id": mid, "thread_root": row.get("thread_root") or mid,
                        "direction": row.get("direction") or "", "priority": row.get("priority") or "",
                        "has_attachment": bool(row.get("has_attach")), "from_email": fe,
                    })
            timeline.sort(key=lambda r: r.get("date") or "")
        thread_set = {t["thread_root"] for t in timeline if t["thread_root"]}
        dir_counts: Counter = Counter(t["direction"] for t in timeline)
        purpose_counts: Counter = Counter(t["purpose"] for t in timeline if t["purpose"])
        # Projects whose client_email matches a cluster email.
        cluster_projects = [p for p in pstore.list() if (p.get("client_email") or "") in emails]
        # Fila rows for this cluster (the still-open response queue).
        cluster_frows = [r for r in _fila_rows() if (r.get("contact") or "") in emails]
        # Para-ti decisions belonging to this contraparte (by thread, contact, or proposed cluster).
        gates = [
            it for it in _para_ti_items()
            if (it.get("thread_root") in thread_set
                or (it.get("context") or {}).get("contact") in emails
                or it.get("email") in emails
                or (it.get("context") or {}).get("proposed_cluster") == key)
        ]
        # Primary email = the cluster address we've heard from most (best target for the inbox jump).
        primary = (max(cluster_dict["emails"], key=lambda e: from_counts.get(e, 0))
                   if cluster_dict["emails"] else "")
        stats = {
            # Distinct messages actually on record with this contraparte — matches the timeline the
            # user sees. (The cluster's ``msg_count`` counts per-participant, so it over-counts any
            # message addressed to several people in the same domain; we don't surface that here.)
            "messages": len(timeline),
            "threads": len(thread_set),
            "inbound": dir_counts.get("inbound", 0), "outbound": dir_counts.get("outbound", 0),
            "internal": dir_counts.get("internal", 0),
            "with_attachments": sum(1 for t in timeline if t["has_attachment"]),
            "purposes": purpose_counts.most_common(6),
            "we_owe": cluster_dict.get("we_owe_count", 0),
            "response_risk": cluster_dict.get("response_risk", "none"),
            "open_projects": cluster_dict.get("open_projects", 0),
            "first_seen": timeline[0]["date"] if timeline else "",
            "last_seen": cluster_dict.get("last_seen") or (timeline[-1]["date"] if timeline else ""),
            "primary_email": primary,
        }
        return {"cluster": cluster_dict, "timeline": timeline, "projects": cluster_projects,
                "fila_rows": cluster_frows, "gates": gates, "stats": stats}

    @app.get("/contrapartes/{key:path}", response_class=HTMLResponse)
    def contrapartes_detail(key: str):
        data = _contraparte_detail_data(key)
        if data is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return HTMLResponse(contrapartes_page.build_detail_html(
            data["cluster"], data["timeline"], data["projects"], data["fila_rows"],
            stats=data["stats"], gates=data["gates"], nav_counts=_nav_counts()))

    @app.get("/api/contrapartes/{key:path}")
    def api_contrapartes_detail(key: str):
        data = _contraparte_detail_data(key)
        if data is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(data)   # {cluster, stats, timeline, projects, fila_rows, gates}

    # -------------------------------------------------------------------------
    # C3 — Para ti decision inbox
    # -------------------------------------------------------------------------
    def _para_ti_items(frows: list | None = None,
                       clusters: list | None = None) -> list[dict[str, Any]]:
        frows = _fila_rows() if frows is None else frows
        clusters = _clusters() if clusters is None else clusters
        all_threads = {t for p in pstore.list() for t in pstore.threads_for(p["project_id"])}
        return para_ti.all_items(frows, clusters, all_threads)

    @app.get("/para-ti", response_class=HTMLResponse)
    def para_ti_view():
        frows = _fila_rows()  # build once, reuse for items + badges (F3)
        clusters = _clusters()
        return HTMLResponse(para_ti_page.build_html(
            _para_ti_items(frows, clusters), nav_counts=_nav_counts(frows=frows, clusters=clusters)))

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
    def _projetos_html() -> str:
        # Cheap list: read the denormalized coverage/estimable off each project row (F3). Only a
        # stale/NULL summary (post-migration / post-sync) triggers a single build_canonical that then
        # persists — so this is no longer an O(projects×messages) recompute on every render.
        projects_summary = []
        for p in pstore.list():
            cov, est = _summary_for(p)
            projects_summary.append({**p, "coverage": cov, "estimable": est,
                                     "n_threads": len(pstore.threads_for(p["project_id"])),
                                     "owners": pstore.owners_for(p["project_id"])})
        return projetos_page.build_html(projects_summary, nav_counts=_nav_counts(), roster=_roster())

    @app.get("/projetos", response_class=HTMLResponse)
    def projetos_view():
        return HTMLResponse(_projetos_html())

    @app.get("/projetos/{pid}", response_class=HTMLResponse)
    def projetos_detail_view(pid: str):
        # REST deep-link: ``/projetos/<pid>`` is the detail *resource* URL (mirrors
        # ``/contrapartes/<key>``). The same lens HTML is served — the page JS reads the id from the
        # path and opens that project's workbench. 404 on an unknown id so a stale/shared link fails
        # honestly instead of opening an empty workbench.
        if pstore.get(pid) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return HTMLResponse(_projetos_html())

    return app


def from_settings(settings: dict[str, Any]):
    return create_app(settings)
