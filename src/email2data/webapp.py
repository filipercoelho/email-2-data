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
import os
import re
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import (accounts as _accounts, admin_page, capture_resolve, captures, captures_page,
               classifier, clientdraft, cockpit, contrapartes_page, crm as _crm, descdraft,
               export as _export, fila_page, jobspec as js, para_ti, para_ti_page,
               project as _project, projetos_page, replydraft, report, translate as _translate)
from .config import paths
from .workspace import Workspace, RECLASSIFY_FIELDS

# ── Admin: the account-editor contract (see /api/admin/accounts) ────────────────────────────────
# A plausible POSIX environment-variable NAME. ``password_env`` names the variable; the value never
# enters this process through HTTP.
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Keys whose PRESENCE in an account-editor body means the client tried to send a secret VALUE. The
# request is rejected outright rather than filtered: silently dropping it would teach the caller that
# posting a password is acceptable, and the next writer might not filter. ``*_env`` names are fine —
# they are identifiers, not secrets — so they are absent from this set by construction.
_SECRET_BODY_KEYS = frozenset({
    "password", "passwd", "pwd", "pass", "secret", "credential", "credentials",
    "token", "api_key", "apikey", "auth", "authorization",
})

# The cost tiers (llm.tiers in settings.json), pt-PT-labelled in the Projetos UI. Shared by the two
# explicitly-paid-for LLM actions on the Projetos page: re-extraction and the client-email polish.
_REEXTRACT_TIERS = ("light", "standard", "heavy")

# Internal flags, not facts we would ever confirm back to a client (mirrors replydraft._HIDE).
_POLISH_HIDE = {"client_identity", "design_ready", "process"}


def _find_secret_key(node: Any, depth: int = 0) -> str | None:
    """Walk a decoded JSON body and return the first key that looks like a secret VALUE slot.

    Depth-bounded so a deeply-nested body cannot blow the stack before it is rejected."""
    if depth > 6:
        return None
    if isinstance(node, dict):
        for k, v in node.items():
            if str(k).strip().lower() in _SECRET_BODY_KEYS:
                return str(k)
            found = _find_secret_key(v, depth + 1)
            if found:
                return found
    elif isinstance(node, list):
        for v in node[:200]:
            found = _find_secret_key(v, depth + 1)
            if found:
                return found
    return None


def validate_accounts(raw: Any) -> tuple[list[dict[str, Any]], str]:
    """Validate an account-editor payload BEFORE anything is written.

    Returns ``(accounts, "")`` on success or ``([], "<mensagem pt-PT>")`` on the first failure.
    Module-level (not a closure) so the rules are testable without standing up an app.

    Every account is rebuilt from an allowlist — id / username / host / port / password_env /
    mailboxes — so no extra key from the request body can reach ``settings.json``.
    """
    if not isinstance(raw, list) or not raw:
        return [], "É preciso pelo menos uma conta."
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, a in enumerate(raw, start=1):
        if not isinstance(a, dict):
            return [], f"Conta {i}: formato inválido."
        aid = str(a.get("id", "") or "").strip()
        if not aid:
            return [], f"Conta {i}: o id é obrigatório."
        if aid in seen:
            return [], f"Conta {i}: id duplicado ({aid})."
        seen.add(aid)
        username = str(a.get("username", "") or "").strip()
        if not username:
            return [], f"Conta {aid}: o utilizador é obrigatório."
        env = str(a.get("password_env", "") or "").strip()
        if not env:
            return [], f"Conta {aid}: falta o nome da variável de ambiente (password_env)."
        if not _ENV_NAME_RE.match(env):
            return [], (f"Conta {aid}: 'password_env' tem de ser um NOME de variável de ambiente "
                        "(letras, dígitos e _). Nunca a password.")
        host = str(a.get("host", "") or "").strip()
        if not host:
            return [], f"Conta {aid}: falta o servidor IMAP."
        try:
            port = int(a.get("port", 993))
        except (TypeError, ValueError):
            return [], f"Conta {aid}: porta inválida."
        if not 0 < port < 65536:
            return [], f"Conta {aid}: porta fora do intervalo (1–65535)."
        mbs_raw = a.get("mailboxes")
        if not isinstance(mbs_raw, list) or not mbs_raw:
            return [], f"Conta {aid}: indica pelo menos uma caixa de correio."
        mailboxes: list[str] = []
        for m in mbs_raw:
            name = str(m or "").strip()
            if not name:
                return [], f"Conta {aid}: caixa de correio vazia na lista."
            if name not in mailboxes:
                mailboxes.append(name)
        out.append({"id": aid, "username": username, "host": host, "port": port,
                    "password_env": env, "mailboxes": mailboxes})
    hosts = {(a["host"], a["port"]) for a in out}
    if len(hosts) > 1:
        # Honest limitation, not a stylistic rule: fetch._connect reads imap.host/imap.port ONCE for
        # every account. Accepting per-account servers here would persist a setting the fetcher
        # ignores — a silent lie on the page. Rejected instead.
        return [], "Todas as contas têm de usar o mesmo servidor e porta IMAP."
    return out, ""


def _load_jobspecs(out_dir: Path) -> dict[str, Any]:
    jp = out_dir / "jobspecs.jsonl"
    m: dict[str, Any] = {}
    if jp.exists():
        for line in jp.read_text().splitlines():
            if line.strip():
                j = json.loads(line)
                m[j["message_id"]] = j
    return m


# ── periodic sync (ADR-023) ──────────────────────────────────────────────────────────────────────
# Module-level so the schedule is testable without standing up a whole app: the loop takes the
# already-locked ``run_sync`` as a parameter rather than closing over it.

SYNC_INTERVAL_DEFAULT_MIN: float = 15.0


def resolve_sync_interval(settings: dict[str, Any]) -> float:
    """Seconds between background syncs. ``sync.interval_minutes`` <= 0 (or unparseable) disables the
    periodic loop, leaving startup + the manual button as the only ingestion points."""
    try:
        mins = float(settings.get("sync", {}).get("interval_minutes", SYNC_INTERVAL_DEFAULT_MIN))
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, mins) * 60.0


def periodic_sync_loop(run_sync, interval_s: float, stop: threading.Event, log=print) -> None:
    """Tick ``run_sync`` every ``interval_s`` until ``stop`` is set.

    Uses ``Event.wait`` rather than ``sleep`` so shutdown is immediate instead of blocking a whole
    interval. A tick that finds a manual sync already holding the lock is skipped, not queued — the
    next tick covers it. ``run_sync`` never raises by contract, but we guard anyway: this runs on a
    daemon thread where an escaping exception would kill auto-refresh silently for the whole session.
    """
    while not stop.wait(interval_s):
        try:
            r = run_sync() or {}
        except Exception as exc:  # noqa: BLE001 — a dead thread means silent staleness; keep ticking
            log(f"  periodic sync failed — {type(exc).__name__}: {exc}")
            continue
        if r.get("error"):
            log(f"  periodic sync skipped — {r['error']}")


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
    _sync = {"running": False, "last_counts": None, "last_ts": None, "last_error": None,
             # Per-account detail (sync.run_sync's additive keys) so the Admin panel can tell
             # "0 fetched because idle" from "0 fetched because this account is down".
             "per_account": {}, "account_failures": {}, "stages": None}
    # Recent per-account failures, newest-first, for the Admin cards. Bounded — a permanently broken
    # account must not grow the process's memory or turn the page into a log dump.
    _account_errors: dict[str, list[dict[str, str]]] = {}
    _MAX_ACCOUNT_ERRORS = 10
    _sync_lock = threading.Lock()

    # Reply-draft memo (regenerable, in-process): keyed by (message_id, hash of the EXACT reply
    # prompt). A re-open / page-reload / second client for an UNCHANGED spec is served from here and
    # costs 0 tokens; any spec/readiness change (sync, confirm, item edit) changes the prompt -> new
    # key -> regenerate. Cold on restart by design — it caches LLM output, not precious state.
    _reply_cache: dict[tuple[str, str], str] = {}

    # Translate-to-English memo (ADR-032), same discipline as _reply_cache: keyed by (message_id, hash
    # of the exact text). Re-clicking the same message is served from here at 0 tokens; different text
    # -> different key -> re-translate. Cold on restart — it caches LLM output, not precious state, and
    # persisting derived personal data would be a new store we deliberately don't create.
    _translate_cache: dict[tuple[str, str], str] = {}

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

    def _record_account_errors(failures: dict[str, str], ts: str) -> None:
        """Remember the per-account failure detail so /admin can show WHY an account is at 0.

        ``failures`` values come from ``fetch._imap_detail`` — the server's own tagged reply, which
        never echoes the credential. Nothing else from the account is stored."""
        for acc, detail in (failures or {}).items():
            lst = _account_errors.setdefault(str(acc), [])
            lst.insert(0, {"ts": ts, "mailbox": "", "message": str(detail)})
            del lst[_MAX_ACCOUNT_ERRORS:]

    def _run_sync(full: bool = False, *, do_fetch: bool = True, do_triage: bool = True,
                  do_crm: bool = True, account_ids: list[str] | None = None) -> dict:
        """Fetch new mail + triage new emails, then rebuild render state. Returns counts, a
        ``{"running": True}`` marker if a sync is already in flight, or ``{"error": msg}`` on a clean
        failure (e.g. the IMAP password isn't set) — never raises, so a daemon thread can't crash and
        the button gets a tidy message instead of a 500.

        The ``do_*`` switches and ``account_ids`` are forwarded to ``sync.run_sync`` (see its
        docstring). ``do_fetch=True, do_triage=False`` is the "pull mail, spend zero Tier-1 tokens"
        mode the Admin panel defaults to — which is why the jobspec rebuild below is gated on
        ``do_triage`` too: it drafts specs through the LLM, so running it unconditionally would have
        billed tokens on exactly the run that promised not to."""
        from . import specbuild
        from . import sync as _syncmod
        if not _sync_lock.acquire(blocking=False):
            return {"running": True}
        _sync["running"] = True
        try:
            counts = _syncmod.run_sync(settings, full=full, do_fetch=do_fetch, do_triage=do_triage,
                                       do_crm=do_crm, account_ids=account_ids)
            # Keep the spec layer fresh: newly-triaged leads get jobspecs (incremental → only new
            # message_ids pay the LLM cost). Without this, jobspecs.jsonl silently goes stale and
            # projects created from new leads arrive empty. Degrades to offline if no LLM client.
            # Skipped entirely on a triage-less run — see the docstring.
            if do_triage:
                try:
                    counts["jobspecs"] = specbuild.rebuild_jobspecs(
                        settings, draft=True, incremental=True, log=lambda m: print(f"  {m}"))
                except Exception as exc:  # noqa: BLE001 — never let a spec-build error fail the sync
                    print(f"  jobspec rebuild skipped — {type(exc).__name__}: {exc}")
            _rebuild_state()
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            _sync["last_counts"] = counts
            _sync["last_ts"] = ts
            _sync["per_account"] = dict(counts.get("per_account") or {})
            _sync["account_failures"] = dict(counts.get("account_failures") or {})
            _sync["stages"] = counts.get("stages")
            _record_account_errors(_sync["account_failures"], ts)
            return counts
        except Exception as exc:  # noqa: BLE001 — surface a clean message, keep serving
            msg = f"{type(exc).__name__}: {exc}"
            _sync["last_error"] = msg
            return {"error": msg}
        finally:
            _sync["running"] = False
            _sync_lock.release()

    # Periodic background sync (ADR-023). Without it the startup sync is the ONLY ingestion point, so
    # a long-lived server serves mail as of boot time and the decision lenses silently go stale — the
    # page was never "cached", the data behind it just stopped moving. Interval from
    # settings.sync.interval_minutes (default 15); <= 0 disables. Re-uses _run_sync, so it inherits
    # the non-blocking lock (a tick that lands on a manual "Sincronizar" is skipped, not queued) and
    # the never-raises contract. Cost stays bounded: the fetch watermark means an idle tick spends no
    # Tier-1 tokens, only a read-only IMAP check.
    _stop_periodic = threading.Event()

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
        interval_s = resolve_sync_interval(settings)
        if not _injected and interval_s > 0:
            threading.Thread(target=periodic_sync_loop,
                             args=(_run_sync, interval_s, _stop_periodic),
                             name="email2data-periodic-sync", daemon=True).start()
            print(f"  auto-sync every {interval_s / 60:g} min")
        try:
            yield
        finally:
            _stop_periodic.set()

    app = FastAPI(title="email-2-data workspace", lifespan=_lifespan)
    app.state.client = None
    app.state.sync_lock = _sync_lock  # exposed for tests to force the "already running" path
    app.state.periodic_stop = _stop_periodic      # tests: assert the loop is wired + stoppable
    app.state.periodic_interval_s = resolve_sync_interval(settings)

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
        clicked and waits. A concurrent call (e.g. the startup thread still running) returns 409.

        An empty/absent body keeps the historical behaviour EXACTLY — full fetch+triage+crm over every
        account. The optional keys (Admin panel) narrow it: ``account_id`` (a single account, or a
        list via ``account_ids``), ``do_fetch``/``do_triage`` (``do_triage:false`` is the zero-token
        mode). ``do_crm`` follows ``do_triage`` unless given, because rebuilding the CRM off verdicts
        that did not move is pure work."""
        import anyio
        body: dict[str, Any] = {}
        try:
            parsed = await request.json()
            if isinstance(parsed, dict):
                body = parsed
        except Exception:  # noqa: BLE001 — empty/invalid body → defaults (legacy behaviour)
            pass
        full = bool(body.get("full", False))
        do_fetch = bool(body.get("do_fetch", True))
        do_triage = bool(body.get("do_triage", True))
        do_crm = bool(body.get("do_crm", do_triage))
        acc_ids: list[str] | None = None
        if body.get("account_ids"):
            acc_ids = [str(a) for a in body["account_ids"] if str(a).strip()]
        elif str(body.get("account_id", "") or "").strip():
            acc_ids = [str(body["account_id"]).strip()]
        counts = await anyio.to_thread.run_sync(
            lambda: _run_sync(full, do_fetch=do_fetch, do_triage=do_triage, do_crm=do_crm,
                              account_ids=acc_ids))
        if counts.get("running"):
            return JSONResponse({"running": True}, status_code=409)
        if counts.get("error"):
            return JSONResponse({"error": counts["error"]}, status_code=503)
        return JSONResponse(counts)

    @app.get("/api/sync/status")
    def api_sync_status():
        """Live sync state for the header button + the Admin panel poll. ``per_account`` and
        ``account_failures`` come straight from the last ``run_sync``; ``stages`` says which stages
        actually ran, so a 0 from a skipped stage is never misread as a measured 0."""
        return JSONResponse({"running": _sync["running"], "last_counts": _sync["last_counts"],
                             "last_ts": _sync["last_ts"], "last_error": _sync.get("last_error"),
                             "per_account": _sync.get("per_account") or {},
                             "account_failures": _sync.get("account_failures") or {},
                             "stages": _sync.get("stages")})

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

    def _translation_playbook() -> str:
        """The translation system prompt (config/translation_playbook.md), re-read per request so an
        edit is live without a restart — same contract as the other playbooks."""
        sp = settings.get("__settings_path__")
        if not sp:
            return _translate.DEFAULT_TRANSLATION_PLAYBOOK
        return _translate.load_playbook(Path(sp).parents[1] / "config" / "translation_playbook.md")

    @app.post("/api/translate")
    async def translate_message(request: Request):
        """Translate a received email body to English — a reading aid (ADR-032). Body:
        ``{message_id, text}`` → ``{text, cached}``. Button-only from the shared message renderer;
        nothing here runs on page load. To-English only, so the language is fixed and not in the key.

        The translation is shown on screen — never sent, never stored, never logged (only ids/counts).
        Sending the body to Vertex is the same egress the triage/spec passes already do (ADR-012/-020).
        """
        import anyio
        from . import llm as _llm
        if not settings.get("__settings_path__"):
            return JSONResponse({"error": "tradução indisponível sem settings.json"}, status_code=503)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 — empty body → nothing to translate, caught below
            body = {}
        mid = str((body or {}).get("message_id", "") or "")
        text = str((body or {}).get("text", "") or "").strip()
        if not text:
            return JSONResponse({"error": "nada para traduzir"}, status_code=400)
        ck = (mid, hashlib.sha256(text.encode("utf-8")).hexdigest())
        if ck in _translate_cache:  # same message text as before — serve cached, spend 0 tokens
            return JSONResponse({"text": _translate_cache[ck], "cached": True})
        playbook = _translation_playbook()

        def _work() -> str:
            if app.state.client is None:
                app.state.client = classifier.make_client(settings)
            return _translate.translate_to_english(text, playbook, app.state.client, settings["llm"])

        try:
            out = await anyio.to_thread.run_sync(_work)
        except _llm.LLMError as exc:
            return JSONResponse({"error": f"o modelo falhou: {exc}"}, status_code=502)
        except Exception as exc:  # noqa: BLE001 — e.g. no ADC/credentials; report, never fake it
            return JSONResponse({"error": f"tradução indisponível: {exc}"}, status_code=503)
        _translate_cache[ck] = out
        return JSONResponse({"text": out})

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
        # What the extraction layer already knows about this thread (ADR-024): the merged job spec +
        # Gate-1 readiness for whichever of its messages carries a jobspec. Lets a caller judge
        # estimability from the thread view alone, without opening the project. Lazy by
        # construction — this endpoint is only hit when a human expands a thread, never in a list
        # render, so it costs nothing on the polled queue. ``null`` when no message has a spec.
        # Keyed off the thread's CRM interactions, NOT the rendered ``all_msgs``: rendering drops
        # messages with no body and no attachments (e.g. a missing .eml), and the spec is a property
        # of the thread, not of which messages survived that filter.
        spec_block = None
        for row in interactions:
            mid = row.get("message_id") or ""
            if mid and mid in jspecs:
                spec_block = {"message_id": mid, **_spec_payload(mid)}
                break
        return JSONResponse({"thread_root": thread_root, "messages": all_msgs, "spec": spec_block})

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

    # Deterministic capture→project resolver inputs (Increment 1, R2 seed): the capture_playbook alias
    # table + the gazetteer, loaded once. Empty when settings aren't file-backed (pure-injection tests)
    # — the resolver then degrades to plain title/client matching, never crashes.
    _cap_aliases: dict[str, str] = {}
    _cap_gazetteer: dict[str, str] = {}
    if settings.get("__settings_path__"):
        _cfgdir = Path(settings["__settings_path__"]).parents[1] / "config"
        _cap_aliases = capture_resolve.load_aliases(_cfgdir / "capture_playbook.md")
        _cap_gazetteer = capture_resolve.load_gazetteer(_cfgdir / "gazetteer.csv")

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
            # How many Registar notes a re-extraction would re-read (ADR-026). Sent with the light
            # default payload on purpose: it is what makes the button's cost predictable BEFORE the
            # click, and it is one indexed read on a project that is already fully loaded here.
            "n_events": sum(1 for e in pstore.knowledge_events(pid) if (e["text"] or "").strip()),
            "dangling_threads": _project.dangling_threads(pstore, pid, _crmdb),
        }

    def _provenance(body: dict) -> dict:
        """Extract the provenance bundle from a write body: channel/asserted_by/acquired_at PLUS the
        ``source_mid`` reference (WP-A).

        The reference used to be dropped here, so every capture-confirmed field reached the ledger
        unlinked — the highest-stakes data (it feeds the estimable gate) carried the weakest
        provenance, and the value was then relabelled 'user'. The email path always passed it
        (``project.seed_items_from``), which is what settles that this was a bug, not a design choice.

        Raises ``ValueError`` on a ``capture:<cid>`` reference naming a capture that does not exist —
        a bad reference must fail loudly, never be silently discarded (ADR-022 §7 freezes the value
        space at ``<message-id> | capture:<cid> | 'user' | ''``).
        """
        ref = str(body.get("source_mid", "") or "")
        cid = captures.capture_id_from_ref(ref)
        if cid and cstore.get(cid) is None:
            raise ValueError(f"unknown capture reference: {ref}")
        return {"channel": str(body.get("channel", "") or ""),
                "asserted_by": str(body.get("asserted_by", "") or ""),
                "acquired_at": str(body.get("acquired_at", "") or ""),
                "source_mid": ref}

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
        try:
            prov = _provenance(body)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if value:
            pstore.set_field(pid, field, value, **prov)
        else:
            # A removal can flip estimability, so it is attributed like any other write (WP-A): it
            # used to log with no bundle at all, and participants() skips unattributed rows — so a
            # deletion was never attributed to anyone.
            pstore.clear_field(pid, field, **prov)
        return JSONResponse(_project_view(pid))

    @app.post("/api/projects/{pid}/rename")
    async def project_rename(pid: str, request: Request):
        """Give the project a human title (the raw email subject it was born with is identity for
        machines, not a name for a list). Blank titles are rejected, not silently ignored."""
        if pstore.get(pid) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        body = await request.json()
        title = str(body.get("title", "")).strip()
        if not title:
            return JSONResponse({"error": "title required"}, status_code=400)
        pstore.rename(pid, title)
        return JSONResponse({"ok": True, "project_id": pid, "title": title})

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
        try:
            prov = _provenance(body)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        pstore.set_field(pid, js.CUSTOM_PREFIX + name, value, **prov)
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
        try:
            prov = _provenance(body)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        pstore.add_event(pid, kind, text, **prov)
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

    def _config_dir() -> Path | None:
        """The bind-mounted config/ dir (from settings.json's location), or None when running
        without a settings file — the signal to use built-in defaults."""
        sp = settings.get("__settings_path__")
        return (Path(sp).parents[1] / "config") if sp else None

    def _client_email_template_for(purpose: str) -> str:
        """The editable per-purpose skeleton (config/client_email_<id>_template.md), re-read per
        request so an edit takes effect without a restart. Falls back to the built-in default."""
        return clientdraft.load_purpose_template(purpose, _config_dir())

    def _client_email_template() -> str:
        """The ``ask`` skeleton — kept as a thin alias so existing callers are unchanged."""
        return _client_email_template_for("ask")

    def _reject_reasons() -> list[str]:
        """The editable reject-reason list (config/client_email_reject_reasons.md), re-read per
        request. Falls back to the built-in defaults."""
        d = _config_dir()
        if d is None:
            return clientdraft.DEFAULT_REJECT_REASONS
        return clientdraft.load_reasons(d / "client_email_reject_reasons.md")

    def _assemble_draft(body: dict) -> tuple[str, list[str], list[str], bool]:
        """Deterministically build the base draft for the requested purpose (no LLM). Returns
        ``(body, questions, keep_values, has_input)`` where ``questions`` are the ticked prompts
        (question purposes only), ``keep_values`` are the money/number/date tokens the user typed that
        an AI polish must preserve verbatim (reason/text purposes), and ``has_input`` says whether the
        user actually supplied the substance for this purpose — the signal for "is there anything to
        polish". It is NOT the same as having ``keep_values``: a reject with a reason but no numbers
        still has input worth rewriting. Unknown purpose → ``ask``."""
        purpose = str(body.get("purpose") or clientdraft.DEFAULT_PURPOSE)
        p = clientdraft.PURPOSES_BY_ID.get(purpose, clientdraft.PURPOSES_BY_ID[clientdraft.DEFAULT_PURPOSE])
        tmpl = _client_email_template_for(p.id)
        if p.input_kind == "questions":
            selected = [str(k) for k in (body.get("selected") or [])]
            custom = [str(c).strip() for c in (body.get("custom") or []) if str(c).strip()]
            questions = _questions_for(selected) + custom
            out = clientdraft.build_purpose_draft(p.id, tmpl, questions=questions)
            return out, questions, [], bool(questions)
        if p.input_kind == "reason":
            reason = str(body.get("reason") or "").strip()
            note = str(body.get("reason_note") or "").strip()
            out = clientdraft.build_purpose_draft(p.id, tmpl, reason=reason, reason_note=note)
            return out, [], clientdraft.extract_values(note), bool(reason or note)
        content = str(body.get("content") or "").strip()
        out = clientdraft.build_purpose_draft(p.id, tmpl, content=content)
        return out, [], clientdraft.extract_values(content), bool(content)

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
            # ADR-031: the purpose selector. `ask` stays the default; `body`/`askables` above are the
            # `ask` starting point exactly as before. The JS mirrors this list (never hand-keeps it).
            "purpose": clientdraft.DEFAULT_PURPOSE,
            "purposes": [{"id": p.id, "label": p.label, "input_kind": p.input_kind}
                         for p in clientdraft.PURPOSES],
            "reject_reasons": _reject_reasons(),
        })

    @app.post("/api/projects/{pid}/draft")
    async def project_draft_build(pid: str, request: Request):
        """Re-assemble the body for a given purpose + inputs. Body:
        ``{purpose, selected, custom, reason, reason_note, content}`` (only the fields the purpose
        uses). Deterministic — no LLM. A request with no ``purpose`` behaves as ``ask`` exactly as
        before. Returns ``{body, facts}`` where ``facts`` are the money/number/date tokens the user
        typed (empty for the question purposes) so the UI can show what the AI polish must preserve.
        The user's manual edits live only in the browser textarea; this rebuilds the baseline."""
        if pstore.get(pid) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        body = await request.json()
        draft_body, _questions, keep, _has = _assemble_draft(body or {})
        return JSONResponse({"body": draft_body, "facts": keep})

    def _polish_playbook() -> str:
        """The polish system prompt (config/client_email_polish_playbook.md), re-read per request so a
        playbook edit takes effect without a restart — same contract as the other playbooks."""
        sp = settings.get("__settings_path__")
        if not sp:
            return clientdraft.DEFAULT_POLISH_PLAYBOOK
        return clientdraft.load_polish_playbook(
            Path(sp).parents[1] / "config" / "client_email_polish_playbook.md")

    def _confirmed_facts(spec) -> list[tuple[str, str]]:
        """What we actually know about this job, as (pt-PT label, value) — the ONLY facts the polish
        pass is allowed to restate. Internal flags are withheld: they are not things one says to a
        client. Item facts are numbered when there is more than one piece."""
        labels = {k: lbl for k, lbl, _t, _q, _s in js.FIELDS}
        out: list[tuple[str, str]] = []
        for k, f in (spec.job_fields or {}).items():
            if f and f.value and k not in _POLISH_HIDE:
                out.append((labels.get(k, k), f.value))
        multi = len(spec.items or []) > 1
        for i, item in enumerate(spec.items or [], 1):
            for k, f in item.items():
                if f and f.value and k not in _POLISH_HIDE:
                    out.append((f"{labels.get(k, k)} (peça {i})" if multi else labels.get(k, k),
                                f.value))
        return out

    def _thread_excerpts(mids: list[str]) -> list[dict[str, Any]]:
        """What the client actually wrote, oldest→newest, for tone and continuity. Read from the same
        on-disk .eml the spec pass reads; a message whose file is gone is skipped, not faked."""
        from .envelope import clean_email_body as _clean_body
        from .envelope import parse_eml as _parse_eml
        out: list[dict[str, Any]] = []
        for mid in mids:
            f = _file_for(mid)
            if not f:
                continue
            try:
                env = _parse_eml(Path(f).read_bytes())
            except (OSError, ValueError):
                continue
            body = _clean_body(env.get("body_text") or "").strip()
            if not body:
                continue
            out.append({"from_email": (env.get("from") or {}).get("email") or "",
                        "date": env.get("date") or "", "body": body})
        return out

    @app.post("/api/projects/{pid}/draft/polish")
    async def project_draft_polish(pid: str, request: Request):
        """AI polish of the client email — ADR-027 (extended by ADR-031 to every purpose).

        Body: ``{purpose, selected, custom, reason, reason_note, content, tier}`` (only the fields
        the purpose uses). Explicitly button-triggered: nothing here runs on page load, on a checkbox
        toggle, or on any other path.

        The deterministic draft is rebuilt server-side from the same inputs the composer sent — the
        same call ``POST /draft`` makes — and handed to the model as the thing to rewrite. That keeps
        ADR-013 intact: the questions enter the prompt as a fixed list rather than being re-derived by
        the model, and the output is re-checked — ``missing_questions`` for the questions AND
        ``missing_values`` for every price/number/date the user typed, so a model that alters a
        number is caught exactly like a dropped question. Both texts come back (``base`` and ``body``)
        because the user chooses; the polish never silently becomes the draft.

        Fails loudly (502) rather than returning the unpolished draft dressed up as a success — the
        user paid for a call and must know whether they got one.
        """
        import anyio
        from . import classifier, llm as _llm
        if pstore.get(pid) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if not settings.get("__settings_path__"):
            return JSONResponse({"error": "melhorar com IA indisponível sem settings.json"},
                                status_code=503)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 — empty body → nothing to say, caught below
            body = {}
        tier = str((body or {}).get("tier", "") or "").strip().lower() or None
        if tier is not None and tier not in _REEXTRACT_TIERS:
            return JSONResponse({"error": f"tier inválido: {tier}"}, status_code=400)
        lang = str((body or {}).get("lang", "") or "").strip().lower() or clientdraft.DEFAULT_LANGUAGE
        if lang not in clientdraft.LANGUAGES_BY_ID:
            return JSONResponse({"error": f"idioma inválido: {lang}"}, status_code=400)
        base, questions, keep, has_input = _assemble_draft(body or {})
        if not has_input:
            return JSONResponse({"error": "nada para escrever"}, status_code=400)

        mids = _project.message_ids_for(pstore, pid, _crmdb)
        spec, _rd, _p, _c = _project.build_canonical(pstore, ws, jspecs, pid, _crmdb, mids=mids)
        facts = _confirmed_facts(spec)
        thread = _thread_excerpts(mids)
        cfg = _llm.with_tier(settings["llm"], tier)
        playbook = _polish_playbook()
        translated = lang != clientdraft.DEFAULT_LANGUAGE
        # Pass keep_values ONLY when non-empty so the `ask`/questions path calls polish_draft with
        # exactly its historical argument set (backward compat with existing stubs and behaviour).
        # Likewise pass language only when non-default, so the PT path is byte-identical to before.
        extra = {}
        if keep:
            extra["keep_values"] = keep
        if translated:
            extra["language"] = lang

        def _work() -> str:
            client = classifier.make_client(settings)
            return clientdraft.polish_draft(base, questions, playbook, client, cfg,
                                            facts=facts, thread=thread, **extra)

        try:
            polished = await anyio.to_thread.run_sync(_work)
        except _llm.LLMError as exc:
            return JSONResponse({"error": f"o modelo falhou: {exc}"}, status_code=502)
        except Exception as exc:  # noqa: BLE001 — e.g. no ADC/credentials; report, never fake a draft
            return JSONResponse({"error": f"LLM indisponível: {exc}"}, status_code=503)
        # The number/date guard is language-independent, so it runs for every language. The verbatim
        # QUESTION check cannot survive translation, so it runs only for PT — a non-PT result is marked
        # `translated` and reviewed by hand rather than claiming a coverage we did not verify.
        missing = list(clientdraft.missing_values(polished, keep))
        if not translated:
            missing = clientdraft.missing_questions(polished, questions) + missing
        return JSONResponse({"body": polished, "base": base, "tier": tier or "",
                             "lang": lang, "translated": translated,
                             "missing": missing, "n_questions": len(questions),
                             "n_facts": len(keep),
                             "used_thread": len(thread), "used_facts": len(facts)})

    # ── DESCRIÇÃO composer (ADR-030) — the proposta/fatura product descritivo ───────────────────────
    def _description_template() -> str:
        """The editable descritivo skeleton (config/description_playbook.md), re-read per request so a
        playbook edit takes effect without a restart. Falls back to the built-in average template."""
        sp = settings.get("__settings_path__")
        if not sp:
            return descdraft.DEFAULT_TEMPLATE
        return descdraft.load_template(Path(sp).parents[1] / "config" / "description_playbook.md")

    def _description_polish_playbook() -> str:
        """The descritivo polish prompt (config/description_polish_playbook.md), re-read per request.
        Falls back to the module default — a missing file must not become a permissive prompt."""
        sp = settings.get("__settings_path__")
        if not sp:
            return descdraft.DEFAULT_POLISH_PLAYBOOK
        return descdraft.load_polish_playbook(
            Path(sp).parents[1] / "config" / "description_polish_playbook.md")

    def _description_for(pid: str):
        """Build the deterministic average-style descritivo for a project from its canonical spec.
        Returns ``(Description, project)`` or ``(None, None)`` when the project is unknown."""
        proj = pstore.get(pid)
        if proj is None:
            return None, None
        spec, _rd, _p, _c = _project.build_canonical(pstore, ws, jspecs, pid, _crmdb)
        desc = descdraft.build_description(spec, _description_template(),
                                           titulo=proj.get("title") or "")
        return desc, proj

    @app.get("/api/projects/{pid}/description")
    def project_description(pid: str):
        """The descritivo composer state: the deterministic average-style draft assembled from the
        project's CONFIRMED spec fields, plus the gaps/unconfirmed facts the UI surfaces beside it.
        Nothing here calls the model — the AI polish is a separate, explicit button (ADR-030/-027)."""
        desc, _proj = _description_for(pid)
        if desc is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse({
            "body": desc.text, "gaps": desc.gaps, "unconfirmed": desc.unconfirmed,
            "complete": desc.complete, "n_facts": len(desc.facts),
        })

    @app.post("/api/projects/{pid}/description/polish")
    async def project_description_polish(pid: str, request: Request):
        """AI polish of the descritivo — ADR-030, the same checked shape as the client-email polish.

        Body: ``{tier: light|standard|heavy}``. Button-triggered only. The server rebuilds the
        deterministic draft from the canonical spec and hands THAT to the model with the confirmed
        facts it must keep verbatim; ``missing_facts``/``dropped_gaps`` re-check the output. Both texts
        come back (``base`` and ``body``) — the polish never silently becomes the draft. Fails loudly
        (502) rather than dressing the unpolished draft up as a success."""
        import anyio
        from . import classifier, llm as _llm
        if pstore.get(pid) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if not settings.get("__settings_path__"):
            return JSONResponse({"error": "melhorar com IA indisponível sem settings.json"},
                                status_code=503)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 — empty body → default tier, handled below
            body = {}
        tier = str((body or {}).get("tier", "") or "").strip().lower() or None
        if tier is not None and tier not in _REEXTRACT_TIERS:
            return JSONResponse({"error": f"tier inválido: {tier}"}, status_code=400)

        desc, _proj = _description_for(pid)
        base, facts = desc.text, desc.facts
        if not facts:
            return JSONResponse({"error": "nada confirmado para redigir"}, status_code=400)
        cfg = _llm.with_tier(settings["llm"], tier)
        playbook = _description_polish_playbook()

        def _work() -> str:
            client = classifier.make_client(settings)
            return descdraft.polish_description(base, facts, playbook, client, cfg)

        try:
            polished = await anyio.to_thread.run_sync(_work)
        except _llm.LLMError as exc:
            return JSONResponse({"error": f"o modelo falhou: {exc}"}, status_code=502)
        except Exception as exc:  # noqa: BLE001 — no ADC/credentials etc.; report, never fake a draft
            return JSONResponse({"error": f"LLM indisponível: {exc}"}, status_code=503)
        missing = descdraft.missing_facts(polished, facts)
        return JSONResponse({"body": polished, "base": base, "tier": tier or "",
                             "missing": missing, "dropped_gaps": descdraft.dropped_gaps(polished, base),
                             "n_facts": len(facts)})

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
        pending = cstore.list_pending()
        active = _active_projects()
        # Deterministic pre-select (R2 seed): suggest an obra for captures the staffer didn't pick in
        # chat. Only a CONFIDENT, unambiguous match is suggested (best_project → None otherwise); the
        # human still confirms every capture (ADR-019 §5). Reorders nothing; just hints the <select>.
        for c in pending:
            if not c.get("inferred_project_id"):
                hay = " ".join(filter(None, [c.get("raw_text"), c.get("transcript")]))
                c["suggested_project_id"] = (capture_resolve.best_project(
                    hay, active, aliases=_cap_aliases, gazetteer=_cap_gazetteer)
                    if hay.strip() else None)
        return HTMLResponse(captures_page.build_html(pending, active, nav_counts=_nav_counts()))

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
        # A voice/audio capture carries its content in the transcript (raw_text is empty) — fall back to
        # it so the validated event holds the staffer's actual words, not a placeholder (Increment 1).
        text = ((cap.get("raw_text") or "").strip()
                or (cap.get("transcript") or "").strip()
                or "📎 captura sem texto")
        # ALWAYS cite the capture (WP-A). The old `if media_paths` ternary was written for a thumbnail
        # renderer, and conflated "has media to show" with "has an origin worth citing" — so a
        # text-only capture (the common case: a typed phone-call note) landed with source_mid="" and
        # permanently lost its link. There is no derivable join key to repair it afterwards.
        pstore.add_event(pid, kind, text,
                         channel=cap.get("channel") or "manual",
                         asserted_by=cap.get("asserted_by") or "",
                         acquired_at=cap.get("acquired_at") or "",
                         source_mid=captures.capture_ref(cid))
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
    def _fila_rows(*, include_resolved: bool = False) -> list[dict[str, Any]]:
        if _crmdb is None:
            return []
        ints = _crmdb.all_interactions()
        now = datetime.now(timezone.utc)
        rows = cockpit.build_fila(ints, ws.thread_states(),
                                  now=now,
                                  reclassified=ws.get_reclassifications(),
                                  snoozes=ws.thread_snoozes(),
                                  include_resolved=include_resolved)
        # Annotate each thread with the project it already belongs to (if any), so the Fila can show
        # "already in project X" and offer open-vs-create — preventing duplicate projects from one lead.
        # The denormalized Gate-1 columns (v3) ride along so the dossier's project line can say
        # «faltam N campos» / «pronto a orçamentar» without opening the project (ADR-033 P2).
        root2proj: dict[str, dict] = {}
        for pr in pstore.list(include_archived=True):
            info = {"project_id": pr["project_id"], "title": pr.get("title") or pr["project_id"],
                    "stage": pr.get("stage") or "",
                    "coverage": pr.get("coverage"), "estimable": pr.get("estimable")}
            for root in pstore.threads_for(pr["project_id"]):
                root2proj.setdefault(root, info)
        for r in rows:
            r["project"] = root2proj.get(r.get("thread_root"))
            # Reply path from the queue: a draft exists only for messages with a JobSpec, so tell
            # the Fila which rows can offer "rascunho de resposta" instead of 404-ing on click.
            r["can_draft"] = (r.get("message_id") or "") in jspecs
        # ADR-033 P1: rows lead with the curated human name, never a raw address when a name exists,
        # and carry their cluster's rollup so the dossier's counterparty card needs no second call.
        # Precedence mirrors _clusters_as_dicts: v8 override (precious) → derived name → the contact.
        by_email: dict[str, dict[str, Any]] = {}
        for cd in _clusters_as_dicts(_clusters(), frows=rows):
            for e in cd.get("emails") or []:
                by_email.setdefault(e, cd)
        for r in rows:
            cd = by_email.get(r.get("contact") or "")
            if cd:
                r["display_name"] = cd["display_name"]
                r["cluster"] = {k: cd[k] for k in
                                ("key", "kind", "msg_count", "we_owe_count",
                                 "response_risk", "open_projects")}
            else:
                r["display_name"] = r.get("contact") or ""
                r["cluster"] = None
        # ADR-033 P2: join what the pipeline already extracted and never showed. Every field is
        # ABSENT when unknown — a missing extraction renders as absence, never a placeholder.
        ent_by_mid: dict[str, dict[str, Any]] = {}
        # «novo» honesty (live-data finding, 2026-07-23): contacts.first_seen cannot be trusted for
        # this — a corpus read from a sync watermark makes long-standing clients look brand new (the
        # live probe flagged EVERY contact as novo). Derive first-seen from the interactions we
        # actually hold, and only claim «novo» when the corpus is DEEP enough to know: a first
        # appearance is meaningful only if mail exists from well before it. Otherwise say nothing —
        # absence of the badge, never a fake fact.
        first_by_email: dict[str, datetime] = {}
        corpus_min: datetime | None = None
        for it in ints:
            raw = it.get("entities")
            if raw:
                try:
                    parsed = json.loads(raw) or {}
                    if isinstance(parsed, dict):
                        ent_by_mid[it.get("message_id") or ""] = parsed
                except (TypeError, ValueError):
                    pass
            dt = cockpit._parse_dt(it.get("date"))
            if dt:
                corpus_min = dt if corpus_min is None or dt < corpus_min else corpus_min
                em = it.get("from_email") or ""
                if em and (em not in first_by_email or dt < first_by_email[em]):
                    first_by_email[em] = dt
        for r in rows:
            ents = ent_by_mid.get(r.get("message_id") or "") or {}
            keep = {k: ents[k] for k in ("money", "deadline", "product_or_service",
                                         "action_requested") if ents.get(k)}
            if keep.get("money"):
                mv = cockpit.money_value(keep["money"])
                if mv is not None:
                    keep["money_value"] = mv   # the € vista's proposed ordering key — never the default sort
            if keep:
                r["entities"] = keep
            c = r.get("clock") or {}
            # chase: AWAITING past the 72h cutoff — _band() only ambers AWAITING at that threshold.
            r["chase"] = bool(c.get("state") == "AWAITING" and c.get("band") == "amber")
            # novo: first appearance ≤14d AND the corpus reaches ≥7d further back (else we can't know).
            fs = first_by_email.get(r.get("contact") or "")
            r["novo"] = bool(fs and (now - fs).days <= 14
                             and corpus_min and (fs - corpus_min).days >= 7)
            # cross-thread relations (same contact or shared entity), deduped by thread_root — the
            # double-answer guard. Bounded: one related() call per active thread on a local SQLite.
            n_rel = 0
            if r.get("message_id"):
                rel = _crmdb.related(r["message_id"])
                roots = {x.get("thread_root") for grp in ("by_contact", "by_entity")
                         for x in rel.get(grp, [])}
                roots.discard(r.get("thread_root"))
                roots.discard("")
                roots.discard(None)
                n_rel = len(roots)
            r["related_count"] = n_rel
        return rows

    def _needs_review_count() -> int:
        """Verdicts the cascade could not decide (tier-1 failure → NEEDS_REVIEW, ADR-016) — the
        «rever N» strip chip finally gives them a surface."""
        if _crmdb is None:
            return 0
        return sum(1 for it in _crmdb.all_interactions()
                   if (it.get("priority") or "") == "NEEDS_REVIEW")

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
        # Human display-name overrides (v8): a person manages "Tempus Lda", not "nif:274023911".
        name_overrides = ws.counterparty_names()
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
                # Precedence: human override (precious) → derived display name → the raw key.
                "display_name": name_overrides.get(cl.key) or cl.display_name or cl.key,
                "name_overridden": cl.key in name_overrides,
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
            # The freshness stamp (ADR-033 P0): same source as /api/para-ti's synced_at, so the
            # hero page can say how old the mail behind its clocks actually is.
            synced_at=_sync["last_ts"] or "",
            needs_review=_needs_review_count(),
            nav_counts=_nav_counts(frows=frows)),
            # Rebuilt per request; the only stale path is an HTTP cache in front of us (ADR-023).
            headers={"Cache-Control": "no-store"})

    @app.get("/api/fila")
    def api_fila(include: str = ""):
        """The active queue. ``?include=resolved`` adds HANDLED/INTERNAL rows — the "Tratados"
        ledger: what was already decided, so a decision can be reviewed (and reopened) instead of
        vanishing without a trace the moment it is made.

        Carries ``synced_at``/``syncing``/``nav_counts``/``needs_review`` alongside the rows so the
        Fila's ADR-023 poll updates the whole page in one round-trip (mirrors /api/para-ti)."""
        frows = _fila_rows(include_resolved=(include == "resolved"))
        # The fila badge must count the ACTIVE queue even when the ledger view asked for resolved.
        active = ([r for r in frows if (r.get("clock") or {}).get("state")
                   in (cockpit.WE_OWE, cockpit.AWAITING)] if include else frows)
        return JSONResponse({"rows": frows, "team": _roster(),
                             "synced_at": _sync["last_ts"], "syncing": _sync["running"],
                             "nav_counts": _nav_counts(frows=active),
                             "needs_review": _needs_review_count()},
                            headers={"Cache-Control": "no-store"})

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

    @app.post("/api/thread/snooze")
    async def thread_snooze(request: Request):
        """Adiar (ADR-033 P3): defer a thread to ``until`` (UTC ISO); ``until: null`` clears (the
        undo path). The wake rule — date OR new inbound, whichever first — lives in build_fila."""
        body = await request.json()
        root = str(body.get("thread_root", "")).strip()
        if not root:
            return JSONResponse({"error": "thread_root required"}, status_code=400)
        until = body.get("until")
        if until:
            ws.set_thread_snooze(root, str(until))
        else:
            ws.clear_thread_snooze(root)
        return JSONResponse({"ok": True, "thread_root": root, "until": until or None})

    @app.post("/api/thread/reply-draft")
    async def thread_reply_draft(request: Request):
        """Contextual R (ADR-033 §10, shipped mapping): route the queue's reply intent to the right
        composer. Deterministic — no LLM in this route, and it NEVER sends. A JobSpec thread points
        at the tested /api/reply (the honest-conditional ask draft keeps its own route); an
        OUTBOUND_INVOICE gets the ADR-031 payment template; everything else gets follow_up. The
        project-aware ask/quote variants stay on the Projetos composer where their inputs live."""
        body = await request.json()
        root = str(body.get("thread_root", "")).strip()
        if not root:
            return JSONResponse({"error": "thread_root required"}, status_code=400)
        row = next((x for x in _fila_rows(include_resolved=True)
                    if x.get("thread_root") == root), None)
        if row is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if row.get("can_draft"):
            return JSONResponse({"kind": "jobspec_ask", "redirect": "/api/reply"})
        kind = "payment" if (row.get("purpose") or "") == "OUTBOUND_INVOICE" else "follow_up"
        tmpl = clientdraft.load_purpose_template(kind, _config_dir())
        p = clientdraft.PURPOSES_BY_ID[kind]
        draft = (clientdraft.build_purpose_draft(kind, tmpl, questions=[])
                 if p.input_kind == "questions"
                 else clientdraft.build_purpose_draft(kind, tmpl, content=""))
        return JSONResponse({"kind": kind, "draft": draft})

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

    @app.post("/api/contrapartes/{key:path}/name")
    async def contraparte_set_name(key: str, request: Request):
        """Set (empty = reset to automatic) the human display name for a counterparty cluster (v8).
        The clustering key stays the identity; only what a person SEES changes."""
        body = await request.json()
        ws.set_counterparty_name(key, str(body.get("name", "")))
        return JSONResponse({"ok": True, "key": key,
                             "name": ws.counterparty_names().get(key, "")})

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
        # Persisted "Ignorar" (v8): a dismissed decision stays dismissed across reloads/restarts.
        return para_ti.all_items(frows, clusters, all_threads,
                                 dismissed=set(ws.para_ti_dismissed()))

    # The decision lenses are rebuilt from the stores on every request, so the only thing that can
    # serve a stale queue is an HTTP cache in front of us (browser bfcache / revisit). Opt out
    # explicitly rather than relying on the absence of a validator (ADR-023).
    _NO_STORE = {"Cache-Control": "no-store, must-revalidate", "Pragma": "no-cache"}

    @app.get("/para-ti", response_class=HTMLResponse)
    def para_ti_view():
        frows = _fila_rows()  # build once, reuse for items + badges (F3)
        clusters = _clusters()
        return HTMLResponse(para_ti_page.build_html(
            _para_ti_items(frows, clusters), nav_counts=_nav_counts(frows=frows, clusters=clusters),
            roster=_roster()), headers=_NO_STORE)

    @app.get("/api/para-ti")
    def api_para_ti():
        """Live decision queue. Carries ``nav_counts`` + sync state alongside the items so the page's
        refresh poll updates the badges and the freshness stamp in a single round-trip."""
        frows = _fila_rows()
        clusters = _clusters()
        return JSONResponse({
            "items": _para_ti_items(frows, clusters),
            "nav_counts": _nav_counts(frows=frows, clusters=clusters),
            "synced_at": _sync["last_ts"],
            "syncing": _sync["running"],
            "served_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }, headers=_NO_STORE)

    @app.post("/api/para-ti/dismiss")
    async def para_ti_dismiss(request: Request):
        """Persist an "Ignorar" on a decision card (v8). The toast used to say "ignorado" while
        keeping nothing — the same proposals resurrected on every load. Now the promise is kept."""
        body = await request.json()
        key = str(body.get("key", "")).strip()
        if not key:
            return JSONResponse({"error": "key required"}, status_code=400)
        ws.dismiss_para_ti(key, kind=str(body.get("kind", "")).strip())
        return JSONResponse({"ok": True, "key": key})

    @app.post("/api/para-ti/undismiss")
    async def para_ti_undismiss(request: Request):
        """Reverse a dismissal — the Z/undo path of /api/para-ti/dismiss."""
        body = await request.json()
        key = str(body.get("key", "")).strip()
        if not key:
            return JSONResponse({"error": "key required"}, status_code=400)
        ws.undismiss_para_ti(key)
        return JSONResponse({"ok": True, "key": key})

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

    # -------------------------------------------------------------------------
    # Scoped re-extraction (ADR-025 §4) — the sanctioned bypass of the idempotency rule.
    # -------------------------------------------------------------------------
    @app.post("/api/projects/{pid}/reextract")
    async def project_reextract(pid: str, request: Request):
        """Re-run extraction over ALL of THIS project's knowledge, then re-seed the project.

        Two sources, one button (ADR-026): the linked **emails** (Tier-1 spec pass) and the **timeline
        knowledge events** — the notes/decisions/opinions/todos a person recorded in Registar, which
        until now no model ever read, so a deadline agreed on a phone call stayed invisible to the
        readiness gate.

        This deliberately breaks the "never re-spend Tier-1 tokens on processed mail" default, so it
        is gated three ways: an explicit human POST, ``only=<this project's message_ids>`` (never the
        whole corpus — the cost-containment pin), and the sync lock (409 while a sync is in flight,
        because both write ``out/jobspecs.jsonl``).

        Human decisions survive on both paths: ``seed_items_from(force=True)`` and
        ``apply_event_fields`` each skip every address in the project's human-touched ledger and never
        delete a field (project.py). Failures are returned, not swallowed — a per-message
        ``spec_error``, and ``events_failed`` for notes the model could not read: a failed re-extract
        must not look like a thin email or an empty note.

        Cost is proportional and visible: the events pass is one call per note at the chosen tier, and
        the response reports ``events_read`` so the spend is never hidden behind a single click.
        """
        import anyio
        from . import capture_infer, classifier, llm as _llm, specbuild
        if pstore.get(pid) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if not settings.get("__settings_path__"):
            return JSONResponse({"error": "re-extração indisponível sem settings.json"},
                                status_code=503)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 — empty body → default tier
            body = {}
        tier = str((body or {}).get("tier", "") or "").strip().lower() or None
        if tier is not None and tier not in _REEXTRACT_TIERS:
            return JSONResponse({"error": f"tier inválido: {tier}"}, status_code=400)
        mids = _project.message_ids_for(pstore, pid, _crmdb)
        # Blank-text events are dropped here rather than inside the loop: they can yield nothing, so
        # sending them would be pure spend. No other length heuristic — a short note like "prazo:
        # 15 março" is exactly the knowledge this pass exists to recover.
        events = [e for e in pstore.knowledge_events(pid) if (e["text"] or "").strip()]
        if not mids and not events:
            return JSONResponse({"error": "projeto sem emails ligados nem registos na linha do tempo"},
                                status_code=400)

        cfg = _llm.with_tier(settings["llm"], tier)
        client = None
        if events:
            try:
                client = classifier.make_client(settings)
            except Exception as exc:  # noqa: BLE001 — no client means no events pass; say so, don't guess
                return JSONResponse({"error": f"LLM indisponível: {exc}"}, status_code=503)

        def _work() -> dict:
            nonlocal jspecs
            if not _sync_lock.acquire(blocking=False):
                return {"running": True}
            try:
                counts = {"built": 0, "drafted": 0, "kept": 0, "failed": 0, "total": 0}
                if mids:
                    counts = specbuild.rebuild_jobspecs(
                        settings, draft=True, reply=False,
                        incremental=True,      # REQUIRED: what keeps everything outside scope intact
                        only=set(mids), tier=tier,
                        log=lambda m: print(f"  {m}"))
                    # rebuild_jobspecs rewrote the file on disk — the in-memory map MUST be reloaded
                    # or the re-seed below would replay the stale specs it just replaced.
                    jspecs = _load_jobspecs(_outdir())
                    for mid in mids:  # oldest → newest, so the newest message wins a conflict
                        _project.seed_items_from(pstore, ws, jspecs, pid, mid, force=True)
                # Timeline pass (ADR-026) — deliberately AFTER the messages, so a recorded note beats
                # a value parsed out of an email: someone chose to write it down, which is the
                # stronger signal. Read once, after the re-seed, so both reflect the same state.
                protected = pstore.human_touched_fields(pid)
                current = {a: v for a, (v, _s) in pstore.fields_for(pid).items()}
                applied: list[str] = []
                failed: list[dict] = []
                for e in events:
                    try:
                        got = capture_infer.extract_fields_strict(e["text"], client, cfg)
                    except _llm.LLMError as exc:
                        # Surfaced, never swallowed: a note the model could not read must not be
                        # indistinguishable from a note that simply held no spec values.
                        failed.append({"rowid": e["rowid"], "kind": e["kind"], "error": str(exc)})
                        continue
                    applied.extend(_project.apply_event_fields(
                        pstore, pid, e["rowid"], got.get("fields") or {},
                        protected=protected, current=current))
                pstore.invalidate_summaries()
                return {"counts": counts, "applied": applied, "failed": failed}
            finally:
                _sync_lock.release()

        result = await anyio.to_thread.run_sync(_work)
        if result.get("running"):
            return JSONResponse({"running": True}, status_code=409)
        messages = [{"message_id": m,
                     "has_spec": m in jspecs,
                     "spec_error": (jspecs.get(m) or {}).get("spec_error") or ""}
                    for m in mids]
        counts = result["counts"]
        ev_failed = result["failed"]
        return JSONResponse({"ok": not counts.get("failed") and not ev_failed, "tier": tier or "",
                             "counts": counts, "messages": messages,
                             "events": {"read": len(events), "applied": result["applied"],
                                        "failed": ev_failed},
                             "project": _project_view(pid)})

    # -------------------------------------------------------------------------
    # Administração (/admin) — IMAP account inventory, force-sync, account editor.
    #
    # SECRETS (non-negotiable #5): a password NEVER crosses this boundary in either direction. The
    # GET reports only ``credential_present`` — a bool derived from testing os.environ, so not even
    # the length of the secret is observable — and the POST rejects any body carrying a secret-shaped
    # key. ``password_env`` is a variable NAME, which is configuration, not a credential.
    # -------------------------------------------------------------------------
    def _settings_file() -> Path:
        return Path(settings["__settings_path__"])

    def _cursors_for(account_id: str) -> list[dict[str, Any]]:
        """Per-mailbox fetch watermarks from ``out/sync.db``. Opened READ-ONLY and only when the file
        already exists, so merely viewing /admin never creates or migrates a store."""
        if not settings.get("__settings_path__"):
            return []
        db = _outdir() / "sync.db"
        if not db.exists():
            return []
        import sqlite3
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                rows = conn.execute(
                    "SELECT mailbox, uidvalidity, last_uid, updated_ts FROM fetch_cursor "
                    "WHERE account_id=? ORDER BY mailbox", (account_id,)).fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            return []       # a missing table (pre-migration db) is not an admin-page failure
        return [{"mailbox": r[0], "uidvalidity": r[1], "last_uid": r[2], "updated_ts": r[3]}
                for r in rows]

    def _admin_accounts() -> list[dict[str, Any]]:
        """The redacted account inventory. Every field is derived; the settings dict is never
        forwarded wholesale, and the only thing said about the credential is whether it resolved."""
        imap = settings.get("imap", {}) or {}
        rows = []
        for a in (imap.get("accounts") or []):
            if not isinstance(a, dict):
                continue
            aid = str(a.get("id", "") or "")
            env = str(a.get("password_env", "") or "").strip()
            # Truthiness only. The value is never bound to a name, logged, or returned.
            present = bool(_ENV_NAME_RE.match(env)) and bool(os.environ.get(env))
            mailboxes = ([str(m) for m in a["mailboxes"]] if isinstance(a.get("mailboxes"), list)
                         else [str(imap.get("mailbox", "INBOX"))])
            cursors = _cursors_for(aid)
            last_sync = max((str(c.get("updated_ts") or "") for c in cursors), default="")
            rows.append({
                "id": aid,
                "username": str(a.get("username", "") or ""),
                "host": str(a.get("host") or imap.get("host", "") or ""),
                "port": int(a.get("port") or imap.get("port", 993) or 993),
                "mailboxes": mailboxes,
                "password_env": env if _ENV_NAME_RE.match(env) else "",
                "credential_present": present,
                "last_sync": last_sync,
                "cursors": cursors,
                "errors": list(_account_errors.get(aid, [])),
            })
        return rows

    def _admin_sync_state() -> dict[str, Any]:
        return {"running": _sync["running"],
                "last": {"ts": _sync["last_ts"] or "",
                         "error": _sync.get("last_error") or "",
                         "counts": {k: v for k, v in (_sync["last_counts"] or {}).items()
                                    if isinstance(v, (int, float)) and not isinstance(v, bool)},
                         "failures": dict(_sync.get("account_failures") or {})}}

    @app.get("/admin", response_class=HTMLResponse)
    def admin_view():
        return HTMLResponse(admin_page.build_html(
            _admin_accounts(), sync=_admin_sync_state(), nav_counts=_nav_counts()),
            headers=_NO_STORE)

    @app.get("/api/admin/accounts")
    def api_admin_accounts():
        return JSONResponse({"accounts": _admin_accounts(), "sync": _admin_sync_state()},
                            headers=_NO_STORE)

    @app.post("/api/admin/accounts")
    async def api_admin_accounts_save(request: Request):
        """Replace ``imap.accounts`` in ``config/settings.json``. Validate-then-write, never both.

        Every other key in the file (``llm``, ``intake``, ``paths``, ``sync``, …) is round-tripped
        from the on-disk copy — this endpoint owns the account list and nothing else. The write is
        atomic (temp file + ``os.replace``), so a crash mid-write cannot leave a truncated
        settings.json that would make the app unbootable.
        """
        if not settings.get("__settings_path__"):
            return JSONResponse({"error": "edição indisponível sem settings.json"}, status_code=503)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "corpo inválido (JSON esperado)."}, status_code=400)
        leaked = _find_secret_key(body)
        if leaked:
            return JSONResponse(
                {"error": f"o campo '{leaked}' não é aceite: as passwords vivem no .env e esta "
                          "página guarda apenas o NOME da variável (password_env)."},
                status_code=400)
        accounts, err = validate_accounts((body or {}).get("accounts"))
        if err:
            return JSONResponse({"error": err}, status_code=400)
        # Guard the write against a concurrent sync: fetch_all reads imap.accounts mid-run, and
        # swapping the file under it would give one run two different account lists.
        if not _sync_lock.acquire(blocking=False):
            return JSONResponse({"running": True}, status_code=409)
        try:
            sp = _settings_file()
            disk = json.loads(sp.read_text(encoding="utf-8"))
            if not isinstance(disk, dict):
                return JSONResponse({"error": "settings.json não é um objeto JSON."},
                                    status_code=500)
            imap = dict(disk.get("imap") or {})
            host, port = accounts[0]["host"], accounts[0]["port"]
            imap["host"], imap["port"] = host, port
            # The per-account host/port were validated as identical above and live at the imap level
            # (that is where fetch._connect reads them), so they are not duplicated onto each account.
            imap["accounts"] = [{"id": a["id"], "username": a["username"],
                                 "password_env": a["password_env"], "mailboxes": a["mailboxes"]}
                                for a in accounts]
            disk["imap"] = imap
            tmp = sp.with_name(sp.name + ".writing")
            tmp.write_text(json.dumps(disk, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            os.replace(tmp, sp)
            # Reload the in-memory settings IN PLACE so every closure above (fetch, paths, llm) sees
            # the new accounts without a restart. __settings_path__ is runtime-only and never on disk.
            keep = settings["__settings_path__"]
            settings.clear()
            settings.update(disk)
            settings["__settings_path__"] = keep
        except OSError as exc:
            return JSONResponse({"error": f"não foi possível gravar: {type(exc).__name__}"},
                                status_code=500)
        finally:
            _sync_lock.release()
        return JSONResponse({"ok": True, "accounts": _admin_accounts()})

    return app


def from_settings(settings: dict[str, Any]):
    return create_app(settings)
