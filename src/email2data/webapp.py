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
import logging
import os
import re
import threading
import unicodedata
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from . import (accounts as _accounts, admin_page, capture_resolve, captures, captures_page,
               classifier, clientdraft, cockpit, cockpit_ui, contrapartes_page, crm as _crm, descdraft,
               export as _export, fila_page, home_page, jobspec as js, para_ti, para_ti_page,
               project as _project, projetos_page, replydraft, report, translate as _translate)
from . import (auth as _authmod, auth_page as _auth_page, mailer as _mailer,
               scopes as _scopesmod, signature as _signature)
from .config import paths
from .workspace import Workspace, RECLASSIFY_FIELDS

logger = logging.getLogger(__name__)

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

# Fila related-list (ADR-037): minimum by_entity slots reserved before by_contact backfills the
# rest, up to the 8-item cap in _fila_rows — otherwise a prolific counterparty's routine traffic
# (by_contact, any topic) always fills all 8 slots first and the rarer, more specific entity match
# never surfaces. Measured on the real corpus: 15% of threads with a genuine entity match had it
# fully crowded out under the old fill-by_contact-first order.
_RELATED_ENTITY_RESERVE = 3


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


def _load_sidecars(out_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """The two ADR-054 sidecars: ``(evidence by message_id, narratives by thread_root)``.

    Both loaders are deliberately TOLERANT where ``_load_jobspecs`` above is not. This runs inside
    ``create_app``, i.e. before the lifespan — an unhandled ``JSONDecodeError`` here does not degrade
    a feature, it makes the app unconstructable, so ``/healthz`` never answers and the container
    crash-loops. These two files are written by LLM passes and carry free text; they are the last
    files that should be able to do that.
    """
    from .locate import load_evidence
    from .narrate import load_narratives
    return load_evidence(out_dir), load_narratives(out_dir)


def _thread_decision_lines(states: dict[str, Any]) -> Callable[[str], list[str]]:
    """``thread_root -> the human decisions on it, as short PT lines`` for the narrative prompt.

    Module-level and closed over a plain dict so the narrative pass never touches a store, and so
    this is testable without standing up an app. Only decisions that are TRUE of the thread as a
    whole go in — a per-message reclassification carries no message_id and no timestamp in the
    ledger, so feeding it here would give the model "corrigido counterparty → CLIENT" with no way to
    place it in the conversation, and a chronicler that cannot place an event will invent a place
    for it.
    """
    def _for(root: str) -> list[str]:
        st = states.get(root) or {}
        out: list[str] = []
        if st.get("owners"):
            out.append(f"dono da conversa: {', '.join(st['owners'])}")
        if st.get("handled"):
            out.append(f"marcada como tratada em {(st.get('handled_ts') or '')[:10]}")
        return out
    return _for


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
               captures_dir=None, auth_store=None, evidence=None, narratives=None):
    """Injectable factory. Defaults wire to the real files; tests pass prepared/jobspecs/workspace.

    ``crm_store`` is an open ``CrmStore`` instance; when omitted the factory opens ``out/crm.db``
    if it exists, or leaves relation queries unavailable (503) if it doesn't.

    ``evidence`` / ``narratives`` are the ADR-054 sidecars (``out/evidence.jsonl`` keyed by
    message_id, ``out/narratives.jsonl`` keyed by thread_root); when omitted they are read from
    ``out/`` and both degrade to ``{}`` — absent evidence renders exactly as it did before Phase 4.
    """
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

    # Touch __settings_path__ only for args that aren't injected (tests inject everything).
    def _outdir():
        return paths(settings, settings["__settings_path__"])["out_dir"]

    def _capturesdir() -> Path:
        # intake media root (ADR-020 sole-copy); injectable for tests, else resolved from settings.
        if captures_dir is not None:
            return Path(captures_dir)
        return paths(settings, settings["__settings_path__"])["captures_dir"]
    ws = workspace or Workspace(_outdir() / "workspace.db").connect()
    # ADR-039 auth. Injectable for tests; otherwise out/auth.db beside the other stores. When the
    # caller injected everything and there is no settings path (pure-fixture tests), fall back to an
    # in-memory store so the gate is STILL ON -- a test must never silently run unauthenticated.
    if auth_store is not None:
        _auth = auth_store
    elif settings.get("__settings_path__"):
        _auth = _authmod.AuthStore(_outdir() / "auth.db").connect()
    else:
        _auth = _authmod.AuthStore(":memory:").connect()
    jspecs = jobspecs if jobspecs is not None else _load_jobspecs(_outdir())
    # ADR-054 sidecars. Injectable for tests like `jobspecs`, but NOT folded into `_injected` below:
    # having evidence must not switch off the startup sync, and an app given only evidence is still a
    # real app. Read here and rebound in `_rebuild_state`, the same two touch points jobspecs needs —
    # miss the rebind and the store is correct on disk and frozen at boot in the running app.
    _evid, _narr = ((evidence or {}), (narratives or {})) if (evidence is not None or narratives is not None) \
        else (_load_sidecars(_outdir()) if settings.get("__settings_path__") else ({}, {}))
    rpb = (reply_pb if reply_pb is not None
           else replydraft.load_playbook(Path(settings["__settings_path__"]).parents[1] / "config" / "reply_playbook.md"))
    emails, contacts, cost = prepared if prepared is not None else report.prepare(settings)
    _team = list(settings.get("team", []) or [])  # legacy seed roster (settings.json); see the backfill

    # ONE roster (ADR-041 / W8). The owner picker used to read settings.team ∪ the in-app `roster`
    # table while permissions read `people` — two vocabularies for one question, so a name could be
    # assignable and not be a person: you could give Rita work and could not grant her anything.
    # Folded here, once, at construction. Idempotent, and a no-op until an admin exists (there would
    # be nobody to be accountable for the backfilled names), so a virgin install just runs it again
    # on the boot after /setup. Adds rows; never rewrites or removes one — the precious store's rule.
    def _fold_roster_into_people() -> None:
        seeded = ws.backfill_people_from_roster(_team)
        if seeded:
            print(f"  roster → people: {len(seeded)} nome(s) migrado(s) ({', '.join(seeded)})")

    # Called again right after /setup: on a real first boot there IS no admin yet — that is what
    # /setup is for — so the construction-time attempt is always the no-op, and without the second
    # call the configured team would sit unmigrated until somebody happened to restart.
    _fold_roster_into_people()

    def _roster() -> list[str]:
        """The effective owner roster: every ACTIVE person, name-ordered.

        Read per request, so someone added or deactivated in Administração appears (or stops
        appearing) in the picker without a restart.
        """
        return [p["name"] for p in ws.people()]

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

    # Corpus index (message_id -> .eml path) for serving attachment BYTES on demand (no parsing).
    # ADR-053: filename is safe_filename(mid), so a lookup is normally an O(1) compute + stat rather
    # than the O(N) glob+parse the old cold-index path did (measured 9 s on a 1094-file corpus). The
    # dict stays as a small cache for injected fixtures + successful computes + the one-time fallback
    # scan that covers the ~1-in-1000 legacy case where an .eml's derived canonical id no longer
    # matches its filename. `scanned` is a one-way latch so a miss never re-scans on the next click.
    _idx: dict[str, Path] = dict(corpus_index) if corpus_index else {}
    _idx_state = {"scanned": corpus_index is not None}

    def _file_for(mid: str):
        hit = _idx.get(mid)
        if hit is not None:
            return hit
        # If a fixture injected corpus_index, don't touch disk (the injection is authoritative).
        # Same latch guards the one-time fallback scan below on real deployments.
        if _idx_state["scanned"] or not settings.get("__settings_path__"):
            return None
        from .identity import safe_filename
        corpus_dir = paths(settings, settings["__settings_path__"])["corpus_dir"]
        cand = corpus_dir / safe_filename(mid)
        if cand.exists():
            _idx[mid] = cand
            return cand
        # Pathological: the .eml lives under a different derived name (e.g. an older canonical_id
        # form). One-time full scan; latch it so an unknown mid never re-scans.
        from .envelope import parse_eml
        for f in corpus_dir.glob("*.eml"):
            try:
                _idx.setdefault(parse_eml(f.read_bytes())["message_id"], f)
            except Exception:  # noqa: BLE001
                pass
        _idx_state["scanned"] = True
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
        nonlocal emails, contacts, cost, jspecs, _crmdb, _evid, _narr
        emails, contacts, cost = report.prepare(settings)
        jspecs = _load_jobspecs(_outdir())
        # ADR-054: the locate/narrate passes just rewrote these files (see _run_sync). Without this
        # rebind the sidecars would be correct on disk and frozen at boot in the running app — the
        # exact failure mode the jobspecs precedent names, and invisible to every grep.
        if settings.get("__settings_path__"):
            _evid, _narr = _load_sidecars(_outdir())
        # ADR-053: DO NOT clear _idx. Corpus files are content-addressed via safe_filename(mid), so
        # a cached entry can never be stale — a mid uniquely determines a filename. New files land
        # at their own computed path and are indexed lazily on first access. The old clear() threw
        # away a warm index and rearmed the 9-second click, once every 15 minutes on the periodic
        # sync alone.
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
        from . import locate, narrate, specbuild
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
                # ADR-054 — the evidence and narrative passes. Both incremental, both gated on
                # do_triage for the same reason the spec build is: they spend LLM tokens, so running
                # them on the "pull mail, spend zero Tier-1 tokens" run would bill exactly the sync
                # that promised not to. Wrapped SEPARATELY, so a locate failure cannot cost the
                # narrative and neither can cost the sync. Both are top-level calls placed AFTER
                # rebuild_jobspecs and never inside it — test_specbuild.py spies os.replace
                # process-wide and asserts exactly one atomic write per jobspec rebuild.
                try:
                    counts["evidence"] = locate.rebuild_evidence(
                        settings, incremental=True, log=lambda m: print(f"  {m}"))
                except Exception as exc:  # noqa: BLE001 — never let the locate pass fail the sync
                    print(f"  locate pass skipped — {type(exc).__name__}: {exc}")
                try:
                    counts["narratives"] = narrate.rebuild_narratives(
                        settings, incremental=True, log=lambda m: print(f"  {m}"),
                        decisions_for=_thread_decision_lines(ws.thread_states()))
                except Exception as exc:  # noqa: BLE001 — never let the narrative pass fail the sync
                    print(f"  narrate pass skipped — {type(exc).__name__}: {exc}")
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
        # Housekeeping: drop long-dead session rows (ADR-039). Purely cosmetic for security — expiry
        # is enforced in the SELECT, so a row that outlives this sweep is already unusable — but
        # without a caller the sessions table only ever grows, and `auth list`/"where am I signed in"
        # get slower and noisier forever. Boot is the right hook: one cheap DELETE per deploy, no
        # timer to own. Never fatal — a locked DB must not stop the app from serving.
        try:
            purged = _auth.purge_expired()
            if purged:
                print(f"  auth: {purged} dead session row(s) purged")
        except Exception as exc:  # noqa: BLE001 — housekeeping must never block startup
            print(f"  auth: session purge skipped — {type(exc).__name__}: {exc}")

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
    app.state.auth = _auth
    app.state.workspace = ws
    # Outbound transactional mail (ADR-042). Built once at construction so a misconfigured `mail`
    # block fails at boot with a readable error rather than at 2am when someone is locked out.
    # None is legitimate -- it means recovery-by-email is off and /recuperar says so honestly.
    # Tests replace this with a fake; nothing else in the app sends mail.
    try:
        app.state.mailer = _mailer.from_settings(settings)
    except Exception as exc:  # noqa: BLE001 — a bad mail block must not make the whole app unbootable
        logger.error("mail is configured but unusable, recovery-by-email is OFF: %s", exc)
        app.state.mailer = None

    # ── auth gate (ADR-039) ──────────────────────────────────────────────────────────────────────
    #
    # DEFAULT-DENY: every path is gated unless it is explicitly public. The allowlist is a closed
    # set checked here, not a decorator each route must remember -- the sibling app's own U5a notes
    # record 67 hand-copied inline checks and the bugs that came from forgetting one.
    #
    # /healthz MUST stay public: the image HEALTHCHECK probes it, and a 401 there would mark the
    # container unhealthy, which would stop intake-bot too (it depends_on: email2data healthy).
    #
    # /recuperar is public by necessity (ADR-042): the whole point is that the visitor cannot sign
    # in. The exact entry covers GET+POST /recuperar; the prefix covers /recuperar/{token} and
    # /recuperar/definir. The gate matches on path only, never method, so one entry does both verbs
    # -- intended here, and the reason the POST must be safe to reach unauthenticated (it is: it
    # answers identically whatever it is given, and mints nothing an attacker can read).
    _PUBLIC_EXACT = {"/healthz", "/login", "/logout", "/setup", "/aceitar-convite", "/recuperar"}
    _PUBLIC_PREFIX = ("/static/", "/aceitar-convite/", "/recuperar/")
    _COOKIE = "e2d_session"

    # ── authorization (ADR-040) ──────────────────────────────────────────────────────────────────
    #
    # ADR-039 built authentication and explicitly deferred authorization, which left `is_admin` as a
    # column nothing read: every signed-in person could reach /admin and POST /api/admin/accounts --
    # the route that rewrites imap.accounts in settings.json. Anyone with a login could repoint the
    # mail accounts.
    #
    # Expressed as a closed path set in the SAME middleware as the auth gate, deliberately, and not
    # as an @admin_required decorator: default-deny only holds if a rule cannot be forgotten, and a
    # decorator is forgettable exactly once per new route. `test_admin_paths_are_admin_only` walks the
    # real route tree against this set, so both halves stay honest.
    #
    # /inbox is the legacy full report (ADR-045). It renders EVERY message body from a closure bound
    # at startup, joins no crm data, and `report.build_html` takes no `person` at all — so it cannot
    # be scoped without rebuilding a 1500-line module around a filtered list. Owner decision
    # 2026-07-26: make it admin-only. That is honest and total, where a half-filtered report would
    # look filtered and not be — the failure mode this whole phase exists to prevent. The static
    # `out/report.html` is unaffected; it was always a full-corpus artefact.
    _ADMIN_EXACT = {"/admin", "/inbox"}
    _ADMIN_PREFIX = ("/api/admin/",)

    # The person's own surface (ADR-041). Authenticated like everything else -- but exempt from the
    # forced-password-change funnel below, because it is where that change happens.
    _ACCOUNT = "/a-minha-conta"

    def _project_id_in_path(path: str) -> str:
        """The project id an id-bearing project path names, or '' (ADR-045).

        Both shapes the app serves: the JSON surface ``/api/projects/{pid}[/...]`` and the HTML page
        ``/projetos/{pid}``. The bare collection paths (``/api/projects``, ``/projetos``) return ''
        because they are filtered by content, not refused.
        """
        for prefix in ("/api/projects/", "/projetos/"):
            if path.startswith(prefix):
                rest = path[len(prefix):]
                return rest.split("/", 1)[0]
        return ""

    def _is_admin_path(path: str) -> bool:
        return path in _ADMIN_EXACT or path.startswith(_ADMIN_PREFIX)

    def _who(request: Request) -> dict[str, Any] | None:
        """The signed-in person, for the shell to render identity from (ADR-041).

        Reads what `_auth_gate` already resolved — never re-queries, so a page can never disagree
        with the gate that let it through. ``None`` only where the gate does not run (an unguarded
        render path), and the shell treats that as default-deny.
        """
        return getattr(request.state, "person", None)

    def _form(body: bytes) -> dict[str, str]:
        """Parse an urlencoded form body with the stdlib.

        Starlette's request.form() would pull in python-multipart; HTML forms post urlencoded by
        default, so parsing it here keeps the dependency count where it is (see auth.py).
        """
        from urllib.parse import parse_qs
        return {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items() if v}

    def _current_person(request) -> dict[str, Any] | None:
        """The signed-in person, re-read from workspace.db on EVERY request.

        Never cached in the cookie: deactivating or demoting someone must take effect on their next
        request, not whenever their session happens to expire.
        """
        person_id = _auth.session_person(request.cookies.get(_COOKIE, ""))
        if not person_id:
            return None
        person = ws.person_by_id(person_id)
        if person is None or not person["active"] or not person["can_login"]:
            return None
        return person

    def _safe_next(raw: str) -> str:
        """Open-redirect guard: only same-site absolute paths survive.

        "//evil.example" is a protocol-relative URL that browsers treat as another origin, so the
        second character has to be checked too -- a bare startswith("/") is the classic hole.
        """
        if not raw or not raw.startswith("/") or raw.startswith("//"):
            return "/"
        return raw

    def _set_session_cookie(response, token: str, request) -> None:
        # secure is derived from the live scheme rather than hard-coded, so the flag is correct under
        # opt-in TLS without a second setting to keep in sync (and is not set on plain-HTTP loopback,
        # where it would silently drop the cookie).
        response.set_cookie(
            _COOKIE, token, httponly=True, samesite="strict",
            secure=(request.url.scheme == "https"), path="/", max_age=_authmod.SESSION_TTL_HOURS * 3600)

    @app.middleware("http")
    async def _auth_gate(request, call_next):
        path = request.url.path
        if path in _PUBLIC_EXACT or path.startswith(_PUBLIC_PREFIX):
            return await call_next(request)
        person = _current_person(request)
        if person is None:
            if not _auth.has_any_credentials():
                # Virgin install: funnel to first-run setup rather than a login nobody can pass.
                # Checked AFTER the session so an authenticated user is never bounced to /setup.
                # /api/* still gets a status it can act on -- an API client cannot follow a 303 to an
                # HTML setup form and would otherwise read "not configured" as "endpoint moved".
                if path.startswith("/api/"):
                    return JSONResponse(
                        {"error": "instalação por configurar — abre /setup"}, status_code=401)
                return RedirectResponse("/setup", status_code=303)
            if path.startswith("/api/"):
                return JSONResponse({"error": "autenticação necessária"}, status_code=401)
            from urllib.parse import quote
            # safe="" so the whole path is encoded: quote()'s default leaves "/" bare, which is fine
            # for a plain path but truncates one carrying reserved characters.
            #
            # The QUERY rides along, not just the path. `build_login_html` already promises that
            # "next_url round-trips where the visitor was heading so a bookmarked deep link survives
            # a login" -- and it could not, because this line sent only request.url.path. A signed-out
            # click on /fila?thread=<root> came back as /login?next=%2Ffila and landed the person on
            # an unfiltered queue, which reads as "the link is broken" rather than "you were logged
            # out". _safe_next still re-validates the whole string server-side before any redirect,
            # and the guard is unchanged by the addition: the value still has to start with a single
            # "/", which a path+query always does.
            nxt = path + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse(f"/login?next={quote(nxt, safe='')}", status_code=303)
        # Authenticated -- now authorized? (ADR-040). 403, never a 303 to /login: bouncing a signed-in
        # person to a login page they would pass instantly is a loop that reads as a broken app, and
        # it misnames the problem ("who are you?" when the answer is "not enough").
        if not person["is_admin"] and _is_admin_path(path):
            if path.startswith("/api/"):
                return JSONResponse({"error": "acesso reservado a administradores"}, status_code=403)
            return HTMLResponse(
                # `person` is passed EXPLICITLY: the gate has not assigned
                # request.state.person yet (that happens below, after authorisation), so
                # `_who(request)` would be None here and `_nav_counts` would fail closed to
                # zero — or, before ADR-045, leak unfiltered demand to the person being refused.
                cockpit_ui.forbidden_page(nav_counts=_nav_counts(person=person), person=person),
                status_code=403, headers=_NO_STORE)
        # Per-person project visibility (ADR-045), in the SAME middleware as authentication and
        # authorization and for the same reason: there are 23 id-bearing project routes, and a
        # per-route guard is a guard the 24th route forgets. Every one of them is
        # /api/projects/{pid}[/...] or /projetos/{pid}, so one path rule covers the whole surface by
        # construction — ADR-040 §1's argument applied to data instead of to admin rights.
        #
        # 404, not 403: a 403 would confirm the project exists, which is most of what an
        # unauthorised caller wanted to know.
        _pid = _project_id_in_path(path)
        if _pid and not _may_open_project(person, _pid):
            if path.startswith("/api/"):
                return JSONResponse({"error": "não encontrado"}, status_code=404)
            return HTMLResponse(
                cockpit_ui.forbidden_page(nav_counts=_nav_counts(person=person), person=person),
                status_code=404, headers=_NO_STORE)
        # A password an admin chose is temporary by definition (ADR-041). The flag has existed since
        # ADR-039 and nothing read it, so "temporário" was a promise the app never kept. Held here,
        # in the same middleware, for the same reason authorization is: a funnel that each route has
        # to remember is a funnel with holes. /a-minha-conta is the way out and /logout is public, so
        # this refuses without trapping.
        if not path.startswith(_ACCOUNT) and _auth.must_change_password(person["person_id"]):
            if path.startswith("/api/"):
                return JSONResponse({"error": "tens de definir uma nova palavra-passe"},
                                    status_code=403)
            return RedirectResponse(_ACCOUNT, status_code=303)
        request.state.person = person
        return await call_next(request)

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        if _current_person(request) is not None:
            return RedirectResponse(_safe_next(request.query_params.get("next", "/")), status_code=303)
        return HTMLResponse(_auth_page.build_login_html(
            next_url=_safe_next(request.query_params.get("next", "/"))))

    @app.post("/login")
    async def login_submit(request: Request):
        form = _form(await request.body())
        target = _safe_next(form.get("next", "/"))
        person = ws.person(form.get("name", ""))
        # One message for every failure mode (unknown / not a login account / inactive / wrong
        # password) so the page never becomes a "who works here" oracle. AuthStore.check_password
        # burns an equivalent scrypt for an unknown person so the timing does not leak it either.
        if (person is None or not person["can_login"] or not person["active"]
                or not _auth.check_password(person["person_id"], form.get("password", ""))):
            return HTMLResponse(_auth_page.build_login_html(
                error="Nome ou palavra-passe incorretos.", next_url=target), status_code=401)
        token = _auth.start_session(person["person_id"],
                                    user_agent=request.headers.get("user-agent", ""))
        response = RedirectResponse(target, status_code=303)
        _set_session_cookie(response, token, request)
        return response

    @app.post("/logout")
    def logout(request: Request):
        # Revoke the ROW, not just the cookie: clearing a cookie leaves a copied token usable, which
        # is exactly the gap the review found in the sibling app.
        _auth.revoke_session(request.cookies.get(_COOKIE, ""))
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(_COOKIE, path="/")
        return response

    @app.get("/setup", response_class=HTMLResponse)
    def setup_form():
        if _auth.has_any_credentials():
            raise HTTPException(status_code=404)
        return HTMLResponse(_auth_page.build_setup_html())

    @app.post("/setup")
    async def setup_submit(request: Request):
        # 404 once any credential exists, so this can never mint a second admin.
        if _auth.has_any_credentials():
            raise HTTPException(status_code=404)
        form = _form(await request.body())
        name, pw, confirm = form.get("name", ""), form.get("password", ""), form.get("confirm", "")
        problem = ""
        if not name.strip():
            problem = "Indica um nome."
        elif len(pw) < 8:
            problem = "A palavra-passe precisa de pelo menos 8 caracteres."
        elif pw != confirm:
            problem = "As palavras-passe não coincidem."
        if problem:
            return HTMLResponse(_auth_page.build_setup_html(error=problem), status_code=400)
        person = ws.person(name) or ws.create_person(name, can_login=True, is_admin=True)
        if not (person["can_login"] and person["is_admin"]):
            return HTMLResponse(_auth_page.build_setup_html(
                error=f"Já existe alguém com o nome {person['name']!r}."), status_code=400)
        _auth.set_password(person["person_id"], pw)
        # There is now an admin, so the legacy roster finally has someone to be accountable for it
        # (ADR-041/W8). This is the only moment on a real install where that becomes true without a
        # restart, and the picker would otherwise open empty on the very first session.
        _fold_roster_into_people()
        token = _auth.start_session(person["person_id"],
                                    user_agent=request.headers.get("user-agent", ""))
        response = RedirectResponse("/", status_code=303)
        _set_session_cookie(response, token, request)
        return response

    @app.get("/aceitar-convite/{token}", response_class=HTMLResponse)
    def invite_form(token: str):
        person_id = _auth.invite_person(token)
        person = ws.person_by_id(person_id) if person_id else None
        if person is None:
            return HTMLResponse(_auth_page.build_invite_expired_html(), status_code=404)
        return HTMLResponse(_auth_page.build_invite_html(person_name=person["name"], token=token))

    @app.post("/aceitar-convite")
    async def invite_submit(request: Request):
        form = _form(await request.body())
        token = form.get("token", "")
        person_id = _auth.invite_person(token)
        person = ws.person_by_id(person_id) if person_id else None
        if person is None:
            return HTMLResponse(_auth_page.build_invite_expired_html(), status_code=404)
        pw, confirm = form.get("password", ""), form.get("confirm", "")
        problem = ""
        if len(pw) < 8:
            problem = "A palavra-passe precisa de pelo menos 8 caracteres."
        elif pw != confirm:
            problem = "As palavras-passe não coincidem."
        if problem:
            return HTMLResponse(_auth_page.build_invite_html(
                person_name=person["name"], token=token, error=problem), status_code=400)
        if _auth.redeem_invite(token, pw) is None:      # atomic single-use gate
            return HTMLResponse(_auth_page.build_invite_expired_html(), status_code=409)
        session = _auth.start_session(person["person_id"],
                                      user_agent=request.headers.get("user-agent", ""))
        response = RedirectResponse("/", status_code=303)
        _set_session_cookie(response, session, request)
        return response

    # ── password recovery (ADR-042) ──────────────────────────────────────────
    #
    # The one self-service way back in. Public by necessity, and therefore written to the rule that
    # an unauthenticated caller learns NOTHING from it: every POST /recuperar answers with the same
    # page and the same status whether the name matched a person, matched nobody, matched someone
    # with no address on file, or matched someone whose mail then failed to send. The roster is
    # people's names -- a distinguishable "no such person" would make it enumerable by anyone who
    # can reach the port.
    #
    # This does NOT repair a zero-admin install, and must not be mistaken for that: it needs a
    # person row with an address, which a bricked install has no way to create (ADR-041 §10).

    def _reset_base_url() -> str:
        """Absolute base for the link that goes in the mail.

        Read from settings, never from the request's Host header. A reset link built from an
        attacker-controlled Host is the classic reset-poisoning bug: the victim receives a genuine
        token pointing at the attacker's server. Configuration is the only trustworthy source here.
        """
        return str(((settings or {}).get("mail") or {}).get("base_url", "")).rstrip("/")

    @app.get("/recuperar", response_class=HTMLResponse)
    def forgot_form(request: Request):
        if _current_person(request) is not None:
            return RedirectResponse("/", status_code=303)
        if app.state.mailer is None or not _reset_base_url():
            return HTMLResponse(_auth_page.build_recovery_unavailable_html(), status_code=503,
                                headers=_NO_STORE)
        return HTMLResponse(_auth_page.build_forgot_html(), headers=_NO_STORE)

    @app.post("/recuperar")
    async def forgot_submit(request: Request):
        mailer = app.state.mailer
        base = _reset_base_url()
        if mailer is None or not base:
            return HTMLResponse(_auth_page.build_recovery_unavailable_html(), status_code=503,
                                headers=_NO_STORE)
        form = _form(await request.body())
        name = form.get("name", "").strip()
        if not name:
            return HTMLResponse(
                _auth_page.build_forgot_html(error="Indica o teu nome."),
                status_code=400, headers=_NO_STORE)

        # From here to the return there is exactly ONE response, built once. Every branch below is a
        # reason to send nothing -- and none of them may change what the visitor sees.
        neutral = HTMLResponse(_auth_page.build_forgot_html(sent=True), headers=_NO_STORE)
        person = ws.person(name)
        if person is None or not person["active"] or not person["can_login"]:
            logger.info("password-reset requested for an unknown or non-login name")
            return neutral
        if not person.get("email"):
            logger.info("password-reset requested for a person with no address on file")
            return neutral
        cap = int(((settings or {}).get("mail") or {}).get("reset_max_per_hour", 5) or 5)
        if _auth.recent_reset_count(person["person_id"]) >= cap:
            # Refusing to MAIL, never refusing to reset: this cannot lock anyone out, it only bounds
            # how much mail one name can cause. The person's existing password stays valid and an
            # admin reset still works.
            logger.warning("password-reset throttled for one person (cap %s/hour)", cap)
            return neutral
        token = _auth.create_reset(person["person_id"],
                                   user_agent=request.headers.get("user-agent", ""))
        try:
            mailer.send_password_reset(
                to_address=person["email"], person_name=person["name"],
                reset_url=f"{base}/recuperar/{token}",
                ttl_minutes=_authmod.RESET_TTL_MINUTES)
        except _mailer.MailError as exc:
            # The token stays live and unused; the visitor is told nothing different. A send failure
            # is an operator problem, visible in the log, not a signal handed to whoever asked.
            logger.error("password-reset mail failed to send: %s", exc)
        return neutral

    @app.get("/recuperar/{token}", response_class=HTMLResponse)
    def reset_form(token: str):
        person_id = _auth.reset_person(token)
        person = ws.person_by_id(person_id) if person_id else None
        if person is None or not person["active"] or not person["can_login"]:
            return HTMLResponse(_auth_page.build_reset_expired_html(), status_code=404,
                                headers=_NO_STORE)
        return HTMLResponse(
            _auth_page.build_reset_html(person_name=person["name"], token=token),
            headers=_NO_STORE)

    @app.post("/recuperar/definir")
    async def reset_submit(request: Request):
        form = _form(await request.body())
        token = form.get("token", "")
        person_id = _auth.reset_person(token)
        person = ws.person_by_id(person_id) if person_id else None
        if person is None or not person["active"] or not person["can_login"]:
            return HTMLResponse(_auth_page.build_reset_expired_html(), status_code=404,
                                headers=_NO_STORE)
        pw, confirm = form.get("password", ""), form.get("confirm", "")
        problem = ""
        if len(pw) < 8:
            problem = "A palavra-passe precisa de pelo menos 8 caracteres."
        elif pw != confirm:
            problem = "As palavras-passe não coincidem."
        if problem:
            return HTMLResponse(
                _auth_page.build_reset_html(person_name=person["name"], token=token, error=problem),
                status_code=400, headers=_NO_STORE)
        if _auth.redeem_reset(token, pw) is None:      # atomic single-use gate
            return HTMLResponse(_auth_page.build_reset_expired_html(), status_code=409,
                                headers=_NO_STORE)
        # redeem_reset -> set_password already revoked every prior session, so the person is signed
        # out everywhere and then signed in here, on this device only.
        session = _auth.start_session(person["person_id"],
                                      user_agent=request.headers.get("user-agent", ""))
        response = RedirectResponse("/", status_code=303)
        _set_session_cookie(response, session, request)
        return response

    # ── «A minha conta» (ADR-041) ────────────────────────────────────────────
    #
    # Every route here acts on the SIGNED-IN person and takes no id: there is nothing in the URL or
    # the form to tamper with, so "can I edit someone else's account?" is not a question this surface
    # can be asked. The admin-side equivalent lives behind /admin.

    def _account_html(request: Request, *, error: str = "", ok: str = "",
                      person: dict | None = None, signature: str | None = None) -> str:
        person = person or _who(request)
        return cockpit_ui.account_page(
            person,
            sessions=_auth.live_sessions(person["person_id"]),
            scopes=list(person.get("scopes") or []),
            must_change=_auth.must_change_password(person["person_id"]),
            # What the person would actually send, rendered with their own values — not the raw
            # template. A preview of the template is a preview of nothing: the whole point of the
            # empty-line rule is that you cannot tell what the block looks like by reading it.
            signature_preview=_signature.for_person(person, _config_dir()),
            # On a rejected save, echo back WHAT THEY TYPED rather than the stored value, or the
            # error message arrives beside a textarea that no longer contains the mistake.
            signature=person.get("signature") or "" if signature is None else signature,
            error=error, ok=ok, nav_counts=_nav_counts(person=person))

    @app.get(_ACCOUNT, response_class=HTMLResponse)
    def account_view(request: Request):
        ok = {
            "pw": "Palavra-passe alterada.",
            "s": "As outras sessões foram terminadas.",
            "sig": "Assinatura guardada.",
            "sightml": ("Reconhecemos uma assinatura em HTML (colada do Outlook/Gmail) e convertámos"
                        " para texto — os rascunhos de resposta são texto simples, e o «Abrir no"
                        " mail» também. Confirma em baixo e ajusta se precisares."),
        }.get(request.query_params.get("ok", ""), "")
        return HTMLResponse(_account_html(request, ok=ok), headers=_NO_STORE)

    @app.post(_ACCOUNT + "/assinatura")
    async def account_signature(request: Request):
        """The person's own closing + the profile fields that fill it (ADR-047).

        Deliberately reachable during the forced-password-change funnel, like every other route under
        this prefix — the funnel exempts the whole of «A minha conta», and narrowing that here would
        put a second, differently-shaped rule beside the one in the gate.

        ``email`` is NOT editable here even though ``{email}`` renders it: it is where a password-reset
        link is sent (ADR-042), so a stolen session that could rewrite it becomes a permanent takeover
        rather than a walk-up. An admin sets it in Administração → Pessoas.
        """
        person = _current_person(request)
        if person is None:                       # mirrors account_password: the funnel path has no
            return RedirectResponse("/login", status_code=303)   # request.state.person to read
        form = _form(await request.body())
        sig = form.get("signature", "")
        try:
            _row, converted = ws.set_person_profile(
                person["person_id"], signature=sig,
                job_title=form.get("job_title", ""), phone=form.get("phone", ""))
        except ValueError as exc:
            # Nothing was written — set_person_profile validates before it touches the row — so the
            # re-render carries the TYPED values over the stored ones. Handing back the old job title
            # beside "fix your signature" makes the person retype work they did not get wrong.
            person = {**(ws.person_by_id(person["person_id"]) or person),
                      "job_title": form.get("job_title", ""), "phone": form.get("phone", "")}
            return HTMLResponse(
                _account_html(request, error=str(exc), person=person, signature=sig),
                status_code=400, headers=_NO_STORE)
        # A pasted Outlook/Gmail signature is HTML and was just flattened to text. Saying so is not
        # politeness: the textarea now holds something different from what they pasted, and a person
        # who is not told that reads it as the app having mangled their signature.
        return RedirectResponse(f"{_ACCOUNT}?ok={'sightml' if converted else 'sig'}", status_code=303)

    @app.post(_ACCOUNT + "/palavra-passe")
    async def account_password(request: Request):
        # The gate exempts /a-minha-conta from the forced-change funnel, so `person` is read here the
        # same way the funnel does -- from the session, never from the form.
        person = _current_person(request)
        if person is None:                       # the funnel path skips request.state.person
            return RedirectResponse("/login", status_code=303)
        form = _form(await request.body())
        current, new, confirm = form.get("current", ""), form.get("new", ""), form.get("confirm", "")
        problem = ""
        # Re-authenticate before changing. A session left open on a shared workshop machine is
        # otherwise a permanent takeover rather than a walk-up: change the password and the owner is
        # locked out of their own account.
        if not _auth.check_password(person["person_id"], current):
            problem = "A palavra-passe atual não está correta."
        elif len(new) < 8:
            problem = "A nova palavra-passe precisa de pelo menos 8 caracteres."
        elif new != confirm:
            problem = "As palavras-passe não coincidem."
        elif new == current:
            problem = "A nova palavra-passe tem de ser diferente da atual."
        if problem:
            return HTMLResponse(_account_html(request, error=problem, person=person),
                                status_code=400, headers=_NO_STORE)
        _auth.set_password(person["person_id"], new)     # …which revokes every session, incl. this one
        # So mint a fresh one: without it the success case logs you out, which reads as a failure and
        # invites a retry with the old password.
        token = _auth.start_session(person["person_id"],
                                    user_agent=request.headers.get("user-agent", ""))
        response = RedirectResponse(f"{_ACCOUNT}?ok=pw", status_code=303)
        _set_session_cookie(response, token, request)
        return response

    @app.post(_ACCOUNT + "/sessoes")
    def account_end_other_sessions(request: Request):
        """Sign out everywhere else, without changing the password — the answer to a laptop left
        signed in at a client's office."""
        person = _who(request)
        _auth.revoke_all_sessions(person["person_id"])
        token = _auth.start_session(person["person_id"],
                                    user_agent=request.headers.get("user-agent", ""))
        response = RedirectResponse(f"{_ACCOUNT}?ok=s", status_code=303)
        _set_session_cookie(response, token, request)
        return response

    @app.get("/api/me")
    def api_me(request: Request):
        """Who am I — the seam Phase C/D permission checks will read."""
        person = _current_person(request)
        if person is None:
            return JSONResponse({"error": "autenticação necessária"}, status_code=401)
        return JSONResponse({"name": person["name"], "person_id": person["person_id"],
                             "is_admin": person["is_admin"], "scopes": person["scopes"]})


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
        # The signature is applied AFTER the cache, never baked into it (ADR-047): the cache key is
        # the spec, which says nothing about who is signed in, so a cached signed draft would hand
        # the next reader the previous reader's name and phone number.
        if ck in _reply_cache:  # unchanged spec since last draft — serve cached, spend 0 tokens
            return JSONResponse({"reply": _sign_for(request, _reply_cache[ck]), "cached": True})
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
        return JSONResponse({"reply": _sign_for(request, text)})

    @app.post("/api/reply/stream")
    async def reply_stream(request: Request):
        """Stream the clarifying-reply draft token-by-token (text/plain). NEVER sends.

        The non-streaming ``/api/reply`` above stays as the tested fallback the UI uses when the
        browser can't read a streaming body or this route errors before the first chunk.

        Signing a STREAM (ADR-047) needs one extra move: text already yielded cannot be retracted, so
        a model-written sign-off would be on the reader's screen before there was anything to strip
        it with. The tail — the last few lines, where a sign-off can only be — is therefore held back
        until the generator ends, and `signature.sign` runs on that. Both reply paths then produce
        byte-identical text, instead of the streaming one quietly closing twice.
        """
        from fastapi.responses import StreamingResponse
        body = await request.json()
        mid = str(body.get("message_id", ""))
        if mid not in jspecs:
            return JSONResponse({"error": "not found"}, status_code=404)
        spec, rd = ws.merge(jspecs[mid])
        spec_d = spec.to_dict()
        ck = _reply_key(mid, spec_d, rd)
        person = _who(request)          # read OUT here: the generator runs off the event loop
        cfg = _config_dir()
        # +2, not +1: `strip_closing` ignores a match on line 0 (a body that IS only a sign-off must
        # survive), so the held window needs a line of margin above the furthest-back closing it can
        # legitimately cut, or a sign-off landing exactly on the boundary is missed.
        hold = _signature._CLOSING_TAIL_LINES + 2

        def gen():
            # Runs in Starlette's threadpool (sync generator), so the blocking client init +
            # token generation stay off the event loop — keep make_client INSIDE the generator.
            if ck in _reply_cache:  # cached (e.g. a prior reload/non-stream draft) — replay, 0 tokens
                yield _signature.sign(_reply_cache[ck], person, cfg)
                return
            if app.state.client is None:
                app.state.client = classifier.make_client(settings)
            chunks: list[str] = []
            pending, emitted = "", False
            for piece in replydraft.draft_reply_stream(spec_d, rd, rpb, app.state.client, settings):
                chunks.append(piece)
                pending += piece
                lines = pending.split("\n")
                if len(lines) > hold:
                    yield "\n".join(lines[:-hold]) + "\n"
                    emitted = True
                    pending = "\n".join(lines[-hold:])
            _reply_cache[ck] = "".join(chunks)  # populate so the next reload / non-stream call is free
            signed = _signature.sign(pending, person, cfg)
            # A tail that is pure whitespace makes `sign` return the block alone — which would weld
            # itself onto the last emitted line. The blank line has to come from here, not from sign.
            yield signed if (pending.strip() or not emitted) else "\n" + signed

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
    def api_thread(thread_root: str, request: Request):
        """Return the messages of one email thread with body text.

        Merges two sources:
        1. IMAP messages in the CRM thread index (directly received or sent).
        2. Embedded messages extracted from forwarded/reply chains — emails that were never
           separate IMAP messages but are only available as quoted blocks inside a received
           message (e.g. the original client inquiry inside an internal forward).

        Both are returned in chronological order. Embedded messages carry ``"embedded": true``
        so the UI can render them with a subtle visual distinction.
        """
        from . import attachments as _att
        from .envelope import clean_email_body_parts as _clean_body_parts
        from .envelope import extract_embedded_messages as _extract_embedded
        from .envelope import parse_eml as _parse_eml
        from .signals import OUR_DOMAIN
        if _crmdb is None:
            return JSONResponse({"error": "CRM not available"}, status_code=503)
        # ADR-045. 404 rather than 403, deliberately: a 403 here would confirm that the thread
        # exists, which is most of what an unauthorised caller wanted to learn. The honest-refusal
        # rule (ADR-040) applies to a person's OWN surfaces, not to someone else's mail.
        if not _may_open_thread(_who(request), thread_root):
            return JSONResponse({"error": "não encontrado"}, status_code=404)
        interactions = _crmdb.thread(thread_root)
        if not interactions:
            return JSONResponse({"error": "thread not found"}, status_code=404)
        messages = []
        # The attachment funnel's raw input (ADR-046). Collected from EVERY interaction, in the
        # CRM's oldest-first order, and folded *before* the message dedup below — a file whose
        # only carrier is a suppressed Trash copy must still reach the funnel.
        thread_parts: list[dict[str, Any]] = []
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
                    _raw = Path(f).read_bytes()
                    env = _parse_eml(_raw)
                    body = env.get("body_text") or ""
                    # body_clean stays EXACTLY what it was; the signature rides alongside it in its
                    # own field so the client can collapse it without any of the three regressions
                    # folding it inline would cause (see msgHTML). fila-evidence §Phase 2.
                    _clean, _sig = _clean_body_parts(body)
                    msg["body"] = body[:3000]
                    msg["body_clean"] = _clean[:3000]
                    msg["body_sig"] = _sig[:1500]
                    msg["body_truncated"] = len(body) > 3000
                    # ADR-046: the same parts _attachments() yields, in the same index order that
                    # attachment_part re-walks, plus the band + content hash. Additive metadata —
                    # the per-message chip list is still index-aligned with /api/attachment/…/{i}.
                    _parts = _att.message_parts(_raw)
                    msg["attachments"] = [
                        {"name": p["name"] or "(sem nome)", "type": p["type"],
                         "sha": p["sha"], "band": p["band"]}
                        for p in _parts
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
                    # Feed the funnel LAST, so ``first_seen``/``from_email`` carry the values
                    # recovered above — a Trash copy with stripped headers would otherwise
                    # attribute its files to "" on a blank date.
                    thread_parts.append({"message_id": mid, "date": msg["date"],
                                         "from_email": msg["from_email"], "parts": _parts})
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
                _em_clean, _em_sig = _clean_body_parts(em["body"])
                embedded_msgs.append({
                    "message_id": f"embedded:{em['from_email']}:{em['date_raw'][:16]}",
                    "subject": em.get("subject") or "",
                    "from_email": em["from_email"],
                    "date": iso_date,
                    "direction": direction,
                    "counterparty": "",
                    "body": em["body"][:3000],
                    "body_clean": _em_clean[:3000],
                    "body_sig": _em_sig[:1500],
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

        # Keep empty-body messages only if they carry attachments not seen elsewhere.
        # Keyed by CONTENT HASH, not by filename (ADR-046). Measured on the corpus: 220 pairs
        # share a filename while differing in bytes — one thread carries composition.pdf at both
        # 154 KB and 152 KB. A name key silently dropped the second, different document; it also
        # missed 181 byte-identical duplicates that happened to be renamed. Parts with no bytes
        # have no hash, so they fall back to the name rather than collapsing into one another.
        def _att_keys(m: dict) -> set[str]:
            return {(a.get("sha") or ("name:" + a.get("name", ""))) for a in (m.get("attachments") or [])}

        all_att_keys = {k for m in deduped for k in _att_keys(m)}
        for m in no_fp:
            unique = _att_keys(m) - all_att_keys
            if unique:
                deduped.append(m)
                all_att_keys |= unique

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
        # ── The thread LEDGER (ADR-033 P4a, owner request): one place where everything the
        # pipeline extracted from this thread and every human decision on it accumulate —
        # deterministic, with provenance. NIF/IBAN are checksum FACTs (ADR-007); the rest is
        # LLM-extracted and renders dashed until a human commits it.
        facts: list[dict[str, Any]] = []
        for row in interactions:
            raw = row.get("entities")
            if not raw:
                continue
            try:
                parsed = json.loads(raw) or {}
            except (TypeError, ValueError):
                continue
            if not isinstance(parsed, dict):
                continue
            _ev = (_evid.get(row.get("message_id") or "") or {}).get("quotes") or {}
            for key in ("money", "deadline", "product_or_service", "action_requested",
                        "client_name", "nif", "iban"):
                val = parsed.get(key)
                if val:
                    entry = {"key": key, "value": val,
                             "message_id": row.get("message_id") or "",
                             "date": (row.get("date") or "")[:10],
                             "fact": key in ("nif", "iban")}
                    # ADR-054 Phase 4: the justifying sentence rides INSIDE the fact entry rather
                    # than as a sibling key, and that is load-bearing rather than tidy. `facts` is
                    # already carried through all three client places (the thread cache's write and
                    # its read, plus refresh()'s hand-written carry list); a new top-level key would
                    # render on first expand and vanish on the next poll — the defect that left Para
                    # Ti's attachment funnel unrendered for weeks. Absent for most keys, by design.
                    q = _ev.get(key)
                    if q:
                        entry["quote"] = q
                    facts.append(entry)
        recl = ws.get_reclassifications()
        decisions: list[dict[str, Any]] = []
        for row in interactions:
            for field, val in (recl.get(row.get("message_id") or "") or {}).items():
                decisions.append({"kind": "reclass", "field": field, "value": val})
        st = ws.thread_states().get(thread_root) or {}
        if st.get("owners"):
            decisions.append({"kind": "owners", "value": ", ".join(st["owners"])})
        if st.get("handled"):
            decisions.append({"kind": "handled", "value": (st.get("handled_ts") or "")[:10]})
        sn = ws.thread_snoozes().get(thread_root)
        if sn:
            decisions.append({"kind": "snooze", "value": (sn.get("until_ts") or "")[:10]})
        proj_block = None
        for pr in pstore.list(include_archived=True):
            if thread_root in pstore.threads_for(pr["project_id"]):
                nf = pstore._conn.execute(
                    "SELECT COUNT(*) FROM project_fields WHERE project_id=?",
                    (pr["project_id"],)).fetchone()[0]
                proj_block = {"project_id": pr["project_id"],
                              "title": pr.get("title") or pr["project_id"],
                              "stage": pr.get("stage") or "", "fields_confirmed": int(nf)}
                break
        # ── The attachment funnel (ADR-046) ──────────────────────────────────────────────────
        # Deduped by content hash across the WHOLE thread and sorted into three bands. Built from
        # ``thread_parts``, which was collected from every interaction above — before the message
        # dedup — so a file carried only by a suppressed Trash copy still appears here.
        #
        # ADR-048: recurring branding art is then omitted entirely. Read per request rather than
        # cached — it is one indexed query over a table of a few hundred rows, and a cache here
        # would go stale against `Sync now` rebuilding crm.db underneath it. A missing or pre-v6
        # crm.db yields an empty set, so the funnel degrades to plain ADR-046 behaviour.
        _branding = _att.branding_shas(_crmdb.asset_spread()) if _crmdb is not None else set()
        att_items = _att.fold_thread(thread_parts, branding=_branding)
        # ADR-054 Phase 5 — «Evolução da conversa». `null` when this thread has no narrative, which
        # is the established honest-absence convention here (see the spec block above): a thread
        # with one message never gets one, and an empty list would read as "we looked and there was
        # no story" rather than "we never asked".
        _nrow = _narr.get(thread_root) or {}
        narrative = None
        if _nrow.get("steps"):
            narrative = {"steps": _nrow["steps"], "state": _nrow.get("state")}
        return JSONResponse({"thread_root": thread_root, "messages": all_msgs, "spec": spec_block,
                             "facts": facts, "decisions": decisions,
                             "ledger_project": proj_block, "narrative": narrative,
                             "attachments": {"items": att_items,
                                             "counts": _att.band_counts(att_items),
                                             "bands": list(_att.BANDS)}})

    @app.get("/api/relations/{message_id}")
    def get_relations(message_id: str, request: Request):
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
        person = _who(request)
        if not _may_open_thread(person, _root_for_message(message_id)):
            return JSONResponse({"error": "não encontrado"}, status_code=404)
        result = _crmdb.related(message_id)
        if not any(result.values()):
            # Check whether the message_id is simply unknown vs genuinely no relations.
            known = _crmdb._conn.execute(  # type: ignore[union-attr]
                "SELECT 1 FROM interactions WHERE message_id=?", (message_id,)
            ).fetchone()
            if known is None:
                return JSONResponse({"error": "message_id not found in CRM"}, status_code=404)
        # Being allowed to ask about THIS message does not make every relation it returns readable:
        # `related()` reaches across threads by contact and by entity, which is exactly how a
        # scoped reader would otherwise pull back subjects from inboxes they were never granted
        # (ADR-045). Each bucket is filtered by the same rule the Fila's related-list uses.
        allowed = _visible_roots(person)
        if allowed is not None:
            result = {
                bucket: [x for x in rows
                         if (x.get("thread_root") or x.get("message_id") or "") in allowed]
                for bucket, rows in result.items()
            }
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
    def list_projects(request: Request, archived: bool = False):
        out = []
        # Filtered by CONTENT, not refused: a collection endpoint answers "here is what is yours",
        # which for someone with no matching grants is legitimately an empty list (ADR-045).
        for pr in _visible_projects(_who(request), pstore.list(include_archived=archived)):
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

    @app.get("/api/projects/{pid}/captures")
    def project_capture_files(pid: str):
        """The project's intake-capture media, as an ADR-046 funnel block (ADR-052).

        "All the files of this project" means email **and** intake — a drawing photographed on the
        shop floor is a project file by every measure a human uses. The payload is deliberately the
        same shape ``/api/thread`` returns for ``attachments``, so the client folds both through the
        one ``attMerge`` and the same file arriving by both routes is ONE row.

        **Why this may be a server-side collection when the email half deliberately is not** (ADR-052
        §Scoping): ``_may_open_project`` is ANY-thread, so a server-built list of a project's *email*
        attachments would hand a member filenames from mail they were never granted. Captures carry
        no per-thread scope at all — they are project knowledge, and the timeline endpoint beside
        this one has served their thumbnails to exactly this audience since ADR-019. So this adds no
        reachable byte: the same middleware rule that gates ``/api/projects/{pid}`` gates this by
        construction (``_project_id_in_path``), which is why it is not a route that has to remember
        a decorator.

        Reads and hashes the bytes on every call — see ``capture_media_items``. Never cached: the
        sole-copy store is the truth, and a stale list of a *precious* store is worse than a slow one.
        """
        from . import attachments as _att
        if pstore.get(pid) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        # Which captures were applied to this project: the ledger cites them as `capture:<cid>`
        # (ADR-020/-022 §7 freeze that value space), so this is a read of existing provenance, not a
        # new join. dict.fromkeys keeps first-seen order and drops the repeats a multi-field apply
        # would produce.
        cids = list(dict.fromkeys(
            cid for row in pstore.timeline(pid)
            if (cid := captures.capture_id_from_ref(row.get("source_mid") or ""))))
        caps = [c for c in (cstore.get(cid) for cid in cids) if c is not None]
        # Resolve the media root only when there is something to read. 10 of the 13 live projects
        # have no capture at all, and this endpoint is called on every project open — it must not
        # touch the filesystem, or fail on an install whose captures_dir is unresolvable, to answer
        # "none". The client merges this block unconditionally: an error here blanks the file list.
        items = _att.capture_media_items(caps, media_root=_capturesdir()) if caps else []
        return JSONResponse({"items": items, "counts": _att.band_counts(items),
                             "bands": list(_att.BANDS), "n_captures": len(caps)})

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

    def _sign_for(request: Request, body: str) -> str:
        """``body`` closed with the SIGNED-IN person's signature (ADR-047).

        The single call site shape for every reply draft the email detail panel serves. The person is
        read from the gate's resolution, never from the request body: a signature is identity, and an
        endpoint that let a caller name whose signature to use would be a way to send mail as someone
        else. `person=None` (an unguarded render path) falls through to the install default, which
        names nobody — the honest output when we do not know who is asking.
        """
        return _signature.sign(body, _who(request), _config_dir())

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

    @app.get("/api/attachment/{ref:path}")
    def get_attachment(ref: str, request: Request):
        """Serve one attachment's raw bytes for view/download. Read-only, local, NO parsing.
        Previewable types (PDF, images) open inline; everything else downloads.

        ``ref`` is ``<message_id>/<index>`` and is a ``:path`` split on the **last** slash, because
        an Outlook ``Message-ID`` routinely contains ``/`` (they are base64-ish blobs). With a plain
        ``{message_id}/{index}`` pair those never matched: the ASGI server percent-decodes before
        routing, so the client's ``%2F`` became a real separator and the extra segment 404'd. That
        silently broke **201 of 1039** attachment links on the current corpus — one in five 📎 — and
        it looked like missing data rather than a routing bug. Verified by fetching every funnel
        item's bytes back and comparing sha256 (see ADR-046).
        """
        from fastapi.responses import Response
        from .envelope import attachment_part
        message_id, _, _index = ref.rpartition("/")
        if not message_id or not _index.isdigit():
            return JSONResponse({"error": "attachment not found"}, status_code=404)
        index = int(_index)
        # This route reads the corpus file DIRECTLY — no crm join, no thread fold — so it is the one
        # escape that hands over real bytes (a client's PDF quote) with nothing else in the way.
        if not _may_open_thread(_who(request), _root_for_message(message_id)):
            return JSONResponse({"error": "não encontrado"}, status_code=404)
        f = _file_for(message_id)
        if f is None:
            return JSONResponse({"error": "message not found"}, status_code=404)
        part = attachment_part(Path(f).read_bytes(), index)
        if part is None:
            return JSONResponse({"error": "attachment not found"}, status_code=404)
        name, ctype, data = part
        disp = "inline" if (ctype.startswith("image/") or ctype == "application/pdf") else "attachment"
        # RFC 6266/5987. A raw non-ASCII filename here is not a cosmetic issue: HTTP header values
        # are latin-1, so `Comprovativo Pag. Lindo Serviço.pdf` emitted a byte no UTF-8 reader
        # accepts — and in a pt-PT shop the accented filename is the common case, not the edge. Send
        # a transliterated ASCII `filename=` for old clients plus the percent-encoded `filename*=`
        # that every current browser prefers, so the name survives the download intact.
        ascii_name = (unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
                      or "anexo")
        cd = (f'{disp}; filename="{ascii_name.replace(chr(34), chr(39))}"; '
              f"filename*=UTF-8''{quote(name, safe='')}")
        return Response(content=data, media_type=ctype, headers={"Content-Disposition": cd})

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
    def capturas_view(request: Request):
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
        return HTMLResponse(captures_page.build_html(pending, active,
                                                     nav_counts=_nav_counts(person=_who(request)),
                                                     person=_who(request)))

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
    # ── per-person visibility (Phase D, ADR-045) ─────────────────────────────
    #
    # ADR-038 recorded WHICH of our inboxes each message reached and said plainly that it contained
    # no policy — `scopes.visible()` was "the seam Phase D still owes a caller". This is that caller.
    #
    # The filter is applied to `ints` inside `_fila_rows`, BEFORE `cockpit.build_fila`, and not to
    # `rows` afterwards. Everything else in that function is recomputed from `ints` rather than from
    # `rows`: the thread summaries, the outbound-only contact fallback, the entity join, the
    # novo/first_seen derivation, and the «relacionados» list. Filtering `rows` would leave
    # «↻ 5 relacionadas» pointing at threads the reader cannot open — a filter that hides the row and
    # keeps the pointer is not a filter, it is a leak with extra steps.

    _scope_cache: dict[str, Any] = {"key": None, "map": {}}

    def _scope_map() -> dict[str, set[str]]:
        """``{thread_root: {scope, ...}}``, cached until crm.db or sync.db changes.

        Recomputed by mtime rather than per request because `scopes.thread_scopes` scans all of
        `message_scope` plus all of `interactions`, and `_fila_rows` runs up to four times on a
        single `/contrapartes/{key}` render. Both stores are rebuilt only by a sync, so mtime is a
        sufficient key — and a stale map can only ever be *narrower* than the truth for a moment,
        never wider, because a new message starts unattributed and unattributed fails closed.
        """
        if _crmdb is None or not settings.get("__settings_path__"):
            return {}
        crm_path = _outdir() / "crm.db"
        sync_path = _outdir() / "sync.db"
        try:
            key = (crm_path.stat().st_mtime_ns if crm_path.exists() else 0,
                   sync_path.stat().st_mtime_ns if sync_path.exists() else 0)
        except OSError:
            key = None
        if key is not None and _scope_cache["key"] == key:
            return _scope_cache["map"]
        if not sync_path.exists():
            return {}
        from . import sync as _syncmod
        try:
            store = _syncmod.SyncStore(sync_path).connect()
        except Exception:  # noqa: BLE001 — no attribution store yet is not a render failure
            return {}
        try:
            mapping = _scopesmod.thread_scopes(store, crm_path)
        except Exception:  # noqa: BLE001
            mapping = {}
        finally:
            store.close()
        _scope_cache["key"], _scope_cache["map"] = key, mapping
        return mapping

    def _visible_roots(person: dict[str, Any] | None) -> set[str] | None:
        """Thread roots this person may see, or ``None`` meaning "no restriction" (an admin).

        ``person is None`` returns an EMPTY set, not None — fail closed. The gate assigns
        `request.state.person` only after it has authorised the request, so a render path that has
        no person is either unguarded or being called from inside the gate itself, and neither is a
        reason to hand over the queue. Same rule as `cockpit_ui.page(person=None)` (ADR-041).
        """
        if person is None:
            return set()
        if person.get("is_admin"):
            return None
        granted = set(person.get("scopes") or [])
        return {root for root, scope in _scope_map().items()
                if _scopesmod.visible(scope, granted, is_admin=False)}

    def _person_sees_everything(person: dict[str, Any] | None) -> bool:
        return bool(person and person.get("is_admin"))

    def _fila_rows(*, person: dict[str, Any] | None,
                   include_resolved: bool = False) -> list[dict[str, Any]]:
        """The Fila rows this PERSON may see.

        ``person`` is a REQUIRED keyword with no default, deliberately. A default of ``None`` would
        fail closed and therefore be safe, but a default of any kind means a new call site can omit
        it and silently get someone else's idea of visibility; with no default, a forgotten call site
        is a TypeError the suite raises immediately. Default-deny expressed in the signature rather
        than in a convention someone has to remember (ADR-040 §1's argument, one layer down).
        """
        if _crmdb is None:
            return []
        ints = _crmdb.all_interactions()
        allowed = _visible_roots(person)
        if allowed is not None:
            ints = [i for i in ints
                    if (i.get("thread_root") or i.get("message_id") or "") in allowed]
        now = datetime.now(timezone.utc)
        rows = cockpit.build_fila(ints, ws.thread_states(),
                                  now=now,
                                  reclassified=ws.get_reclassifications(),
                                  snoozes=ws.thread_snoozes(),
                                  include_resolved=include_resolved)
        # Momentum-by-root for the related-list badges below (ADR-037) — recomputed from every
        # interaction, not just `rows`, because a related thread can be HANDLED/closed (dropped
        # from `rows` when include_resolved is False) and that's exactly the status worth showing.
        thread_summaries = {t.thread_root: t for t in cockpit.fold_threads(ints)}
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
        # «(sem contacto)» fix (ADR-033 P4a): an outbound-only thread has no inbound sender, but the
        # counterparty — who WE wrote to — is in crm.participants (role='to'). Fall back to the
        # first external recipient across the thread's messages BEFORE the display-name join, so
        # the cluster lookup gets a key to work with. Critical detail, never a vague placeholder.
        from .signals import NO_REPLY_RE, OUR_DOMAIN

        def _external_addr(e: str) -> bool:
            d = e.rsplit("@", 1)[-1].lower() if "@" in e else ""
            return bool(d) and d != OUR_DOMAIN and not d.endswith("." + OUR_DOMAIN)
        if any(not r.get("contact") for r in rows):
            mids_by_root: dict[str, list[str]] = {}
            for it in ints:
                mids_by_root.setdefault(it.get("thread_root") or "", []).append(
                    it.get("message_id") or "")
            tos = _crmdb.tos_by_message()
            for r in rows:
                if r.get("contact"):
                    continue
                for mid in mids_by_root.get(r.get("thread_root") or "", []):
                    ext = [e for e in tos.get(mid, []) if _external_addr(e)]
                    if ext:
                        r["contact"] = ext[0]
                        break
        # ADR-033 P1: rows lead with the curated human name, never a raw address when a name exists,
        # and carry their cluster's rollup so the dossier's counterparty card needs no second call.
        # Precedence mirrors _clusters_as_dicts: v8 override (precious) → derived name → the contact.
        by_email: dict[str, dict[str, Any]] = {}
        for cd in _clusters_as_dicts(_clusters(), frows=rows, person=person):
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
            # novo: first appearance ≤14d AND the corpus reaches ≥7d further back (else we can't
            # know) AND never for automated senders — a mailer-daemon wearing «novo» as a Cliente
            # (seen live 2026-07-23) is a fake fact twice over (same gate ADR-028 pinned for Para ti).
            fs = first_by_email.get(r.get("contact") or "")
            r["novo"] = bool(fs and (now - fs).days <= 14
                             and corpus_min and (fs - corpus_min).days >= 7
                             and not NO_REPLY_RE.search(r.get("contact") or ""))
            # cross-thread relations (same contact or shared entity), deduped by thread_root — the
            # double-answer guard. Bounded: one related() call per active thread on a local SQLite.
            # Carry a small labelled list (ADR-034) with the match REASON + the related thread's own
            # momentum (ADR-037), so the dossier shows why two threads are linked and whether the
            # other one is still live — not just a bare subject line.
            #
            # A prolific counterparty can have hundreds of by_contact hits (any topic, same person),
            # which would otherwise fill all 8 slots before the rarer, more specific by_entity match
            # (shared NIF/name/product) ever gets a look-in — measured on the real corpus (ADR-037):
            # 15% of threads with a genuine entity match had it fully crowded out.
            # _RELATED_ENTITY_RESERVE guarantees entity matches a shot regardless of contact volume.
            related: list[dict[str, Any]] = []
            if r.get("message_id"):
                # contact_email: use the row's own resolved EXTERNAL contact (already computed above,
                # including the ADR-033 P4a outbound-only fallback) — never the dominant message's raw
                # from_email, which is an internal @lindoservico.pt mailbox whenever that message was
                # OUTBOUND (ADR-037).
                rel = _crmdb.related(r["message_id"], contact_email=r.get("contact") or "")
                seen_roots = {r.get("thread_root"), "", None}

                def _add(x: dict, reason: str) -> None:
                    root = x.get("thread_root")
                    if root in seen_roots:
                        return
                    # `_crmdb.related()` queries the store DIRECTLY, so it does NOT inherit the
                    # `ints` filter above — this is the second gate, and it is load-bearing.
                    # Measured on the real corpus before it existed: a member scoped to one inbox
                    # saw **26** «relacionados» entries pointing at threads they could not open,
                    # each leaking a real client subject line and a jump-link that 404s. A filter
                    # that hides the row and keeps the pointer is a leak with extra steps.
                    if allowed is not None and root not in allowed:
                        return
                    seen_roots.add(root)
                    t = thread_summaries.get(root)
                    related.append({
                        "thread_root": root,
                        "subject": (x.get("subject") or "").strip() or "(sem assunto)",
                        "reason": reason,
                        "momentum": cockpit.momentum(t.dates, now) if t else None,
                    })

                entity_hits = rel.get("by_entity", [])
                for x in entity_hits[:_RELATED_ENTITY_RESERVE]:
                    if len(related) >= 8:
                        break
                    _add(x, x.get("_matched_entity") or "entidade")
                for x in rel.get("by_contact", []):
                    if len(related) >= 8:
                        break
                    _add(x, "contacto")
                for x in entity_hits[_RELATED_ENTITY_RESERVE:]:
                    if len(related) >= 8:
                        break
                    _add(x, x.get("_matched_entity") or "entidade")
            r["related"] = related
            r["related_count"] = len(related)
        return rows

    def _may_open_thread(person: dict[str, Any] | None, thread_root: str) -> bool:
        """Whether this person may open ONE thread by root (ADR-045).

        The row-level filter in `_fila_rows` decides what a person is SHOWN. This decides what they
        may FETCH — and the two are different questions, because every id-bearing route below can be
        called directly with a root the caller guessed, copied from a colleague, or kept from before
        their grants changed. Hiding a row while leaving its `/api/thread/{root}` open would protect
        the index and publish the contents.
        """
        allowed = _visible_roots(person)
        return allowed is None or (thread_root or "") in allowed

    def _root_for_message(message_id: str) -> str:
        """The canonical thread_root a message belongs to, or the message id itself.

        Attachment and relation routes are keyed by MESSAGE, while scopes are keyed by THREAD, so
        the join has to happen somewhere; here, once, rather than in three call sites.
        """
        if _crmdb is None or not message_id:
            return message_id or ""
        try:
            row = _crmdb._conn.execute(
                "SELECT thread_root FROM interactions WHERE message_id=? LIMIT 1",
                (message_id,)).fetchone()
        except Exception:  # noqa: BLE001 — a missing/locked crm must not 500 an auth check
            return message_id
        return (row["thread_root"] if row and row["thread_root"] else message_id) or message_id

    def _project_roots(project_id: str) -> list[str]:
        try:
            return pstore.threads_for(project_id)
        except Exception:  # noqa: BLE001 — a missing project is "no roots", not a 500
            return []

    def _may_open_project(person: dict[str, Any] | None, project_id: str) -> bool:
        """Whether this person may open ONE project (ADR-045, owner decision 2026-07-26).

        Projects have **no scope column and never touch crm.db**, so visibility here is *derived*:
        you may see a project if you may see any of its threads — the same union rule
        `scopes.thread_scopes` already uses, and the same safe direction (a union can only widen).

        A project with **no threads yet** is admin-only, deliberately. It is the fail-closed reading,
        and it is the honest one: nothing has been attached, so there is no evidence anybody should
        see it. The alternative — visible-to-all until its first thread — would make every new
        project briefly public, which is precisely backwards.

        The rejected alternative was `project_owners`: that column is a free-text NAME rather than a
        `person_id`, so it cannot be validated without a name join, and existing rows carry owners
        that may match no person at all — projects would have silently vanished for everyone.
        """
        allowed = _visible_roots(person)
        if allowed is None:
            return True
        return any(root in allowed for root in _project_roots(project_id))

    def _visible_projects(person: dict[str, Any] | None,
                          projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
        allowed = _visible_roots(person)
        if allowed is None:
            return projects
        return [pr for pr in projects
                if any(root in allowed for root in _project_roots(pr.get("project_id") or ""))]

    def _has_no_grants(person: dict[str, Any] | None) -> bool:
        """True when this person's queue is empty because nothing is GRANTED (ADR-045).

        Not the same question as "are there zero rows": a person with grants and a clear queue has
        genuinely finished, and deserves «Tudo tratado». Only a non-admin with no scopes at all is
        looking at a queue they were never given.
        """
        return bool(person) and not person.get("is_admin") and not (person.get("scopes") or [])

    def _needs_review_count(*, person: dict[str, Any] | None,
                            frows: list[dict[str, Any]] | None = None) -> int:
        """What «Rever classificação N» on the Fila rail promises — and it is counted from the
        **same builder that renders the destination**, so the chip cannot promise a number the click
        does not deliver.

        It used to count NEEDS_REVIEW-priority interactions (tier-1 failure, ADR-016) while the
        group it links to — Para ti's «Classificações a rever» — lists rows under the confidence
        floor. Two different populations, and measurably disjoint: 2 NEEDS_REVIEW rows at 0.95
        confidence plus 1 HIGH row at 0.20 gave a chip of 2 pointing at a group of 1, sharing no
        member. The docstring here already named that failure ("clicking through would show a
        shorter list than the chip promised") and closed only its scoping half; this closes the
        other. NEEDS_REVIEW remains a priority value in the data — it just no longer has a rail
        chip claiming to be something else.

        Scoping is inherited rather than re-derived: ``frows`` is the caller's already-visible queue
        (ADR-045), so this cannot drift from it the way a second corpus read did.
        """
        rows = _fila_rows(person=person) if frows is None else frows
        return len(para_ti.low_confidence_items(rows))

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
                           frows: list | None = None, *,
                           person: dict[str, Any] | None) -> list[dict[str, Any]]:
        """Serialize clusters + enrich with Fila response-risk for the UI. Accepts a prebuilt
        ``frows`` so the caller's Fila build is reused, not recomputed (F3).

        ``person`` is required for the same reason `_fila_rows` requires it: the risk bands and
        «a responder» counts below are computed FROM the rows, so an unfiltered build here would
        put another reader's demand on this reader's counterparty cards (ADR-045)."""
        frows = _fila_rows(person=person) if frows is None else frows
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
                visible_projects = _visible_projects(person, pstore.list())
                for e in cl.emails:
                    for p in visible_projects:
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
                    clusters: list | None = None, *,
                    person: dict[str, Any] | None) -> dict[str, int]:
        """Live counts for the nav badges (C5). Only shows non-zero. Accepts an already-built
        ``frows``/``clusters`` so a page that also renders them doesn't rebuild the whole Fila +
        cluster set a second time per request (F3).

        A badge is a claim about work waiting for YOU. Counted from unfiltered rows it would say
        «7 a responder» over a queue showing three — and the number the operator trusts is the one
        in the nav, so the disagreement resolves in favour of the lie (ADR-045).

        NOTE for the caller inside `_auth_gate`: `request.state.person` is not assigned until the
        gate has finished authorising, so `_who(request)` returns None there. The 403 render passes
        its local `person` explicitly — otherwise the refusal page would leak aggregate demand to
        exactly the person being refused."""
        frows = _fila_rows(person=person) if frows is None else frows
        clusters = _clusters() if clusters is None else clusters
        # The Fila badge carries DEMAND, not inventory (ADR-034): what actually needs a reply
        # (WE_OWE red+amber) — the same number the «Hoje» front shows as «N a responder» — never the
        # total active count, which reads as N fires when far fewer demand the operator. Defined in
        # cockpit.respond_demand since ADR-044, so the badge, the Início headline and the Fila's own
        # front card cannot drift apart while sitting in the same viewport.
        fila_demand = cockpit.respond_demand(frows)
        para_ti_count = len(para_ti.all_items(
            frows, clusters,
            {t for p in pstore.list() for t in pstore.threads_for(p["project_id"])},
        ))
        # Pending captures awaiting validation (ADR-019 §5 / R9) — the Caixa de Capturas badge.
        capturas_count = len(cstore.list_pending())
        return {k: v for k, v in {"fila": fila_demand, "para-ti": para_ti_count,
                                  "capturas": capturas_count}.items() if v}

    @app.get("/", response_class=HTMLResponse)
    def inicio(request: Request):
        """Início (ADR-044) — the landing page: the day's demand and four big buttons, no rows.

        Shares ``_fila_rows()`` with the nav badges the same way the Fila does (F3), so arriving
        costs exactly one queue build — the same one the next click would have paid for anyway."""
        person = _who(request)
        frows = _fila_rows(person=person)
        return HTMLResponse(home_page.build_home_html(
            cockpit.home_summary(frows),
            synced_at=_sync["last_ts"] or "",
            nav_counts=_nav_counts(frows=frows, person=person), person=person),
            headers={"Cache-Control": "no-store"})

    @app.get("/api/inicio")
    def api_inicio(request: Request):
        """Início's numbers, for the in-place repaint after a sync (ADR-023 §7) — same shape the page
        was rendered from, so the refresh path and the first paint cannot disagree."""
        person = _who(request)
        frows = _fila_rows(person=person)
        return JSONResponse({"summary": cockpit.home_summary(frows),
                             "synced_at": _sync["last_ts"], "syncing": _sync["running"],
                             "nav_counts": _nav_counts(frows=frows, person=person)},
                            headers={"Cache-Control": "no-store"})

    @app.get("/fila", response_class=HTMLResponse)
    def fila(request: Request):
        person = _who(request)
        frows = _fila_rows(person=person)  # build once, share with the nav badges (F3)
        return HTMLResponse(fila_page.build_fila_html(
            frows, _roster(),
            now_iso=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            # The freshness stamp (ADR-033 P0): same source as /api/para-ti's synced_at, so the
            # hero page can say how old the mail behind its clocks actually is.
            synced_at=_sync["last_ts"] or "",
            needs_review=_needs_review_count(person=person, frows=frows),
            no_scopes=_has_no_grants(person),
            nav_counts=_nav_counts(frows=frows, person=person), person=person),
            # Rebuilt per request; the only stale path is an HTTP cache in front of us (ADR-023).
            headers={"Cache-Control": "no-store"})

    @app.get("/api/fila")
    def api_fila(request: Request, include: str = ""):
        """The active queue. ``?include=resolved`` adds HANDLED/INTERNAL rows — the "Tratados"
        ledger: what was already decided, so a decision can be reviewed (and reopened) instead of
        vanishing without a trace the moment it is made.

        Carries ``synced_at``/``syncing``/``nav_counts``/``needs_review`` alongside the rows so the
        Fila's ADR-023 poll updates the whole page in one round-trip (mirrors /api/para-ti)."""
        person = _who(request)
        frows = _fila_rows(person=person, include_resolved=(include == "resolved"))
        # The fila badge must count the ACTIVE queue even when the ledger view asked for resolved.
        active = ([r for r in frows if (r.get("clock") or {}).get("state")
                   in (cockpit.WE_OWE, cockpit.AWAITING)] if include else frows)
        return JSONResponse({"rows": frows, "team": _roster(),
                             "synced_at": _sync["last_ts"], "syncing": _sync["running"],
                             "nav_counts": _nav_counts(frows=active, person=person),
                             # `active`, not `frows` — same reason nav_counts uses it: asking for the
                             # Tratados ledger must not inflate a chip about the ACTIVE queue.
                             "needs_review": _needs_review_count(person=person, frows=active)},
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
        row = next((x for x in _fila_rows(person=_who(request), include_resolved=True)
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
        # Signed here and NOT in clientdraft (ADR-047): the same templates feed the Projetos
        # composer, whose draft goes through an AI polish that is allowed to reword the prose. A
        # signature that passed through the polish would come back reworded — a contact block the
        # model paraphrased is exactly the kind of confident wrongness this project refuses.
        return JSONResponse({"kind": kind, "draft": _sign_for(request, draft)})

    # -- the owner roster (v4 "define new owners", now people-backed: ADR-041 / W8) ----------------
    #
    # These stay reachable by any signed-in person, deliberately: naming a new owner is a decision
    # made mid-flow from the Fila/Projetos picker, and a member could always do it. What changes is
    # what it creates — a real person, assignable-only, accountable to whoever added them — instead
    # of free text that no permission could ever attach to.
    @app.get("/api/roster")
    def get_roster():
        people = ws.people()
        return JSONResponse({"roster": [p["name"] for p in people], "team": _team,
                             # Who a member may retire from the picker here: assignable-only people.
                             # Anyone who can sign in is managed in Administração (ADR-040/-041), and
                             # is reported as protected so the UI can say so before the click.
                             "added": [p["name"] for p in people if not p["can_login"]],
                             "protected": _protected_owners()})

    @app.post("/api/roster")
    async def add_roster(request: Request):
        body = await request.json()
        name = str(body.get("name", "")).strip()
        if not name:
            return JSONResponse({"error": "name required"}, status_code=400)
        if ws.person(name) is not None:
            return JSONResponse({"ok": True, "roster": _roster()})      # idempotent, as it always was
        me = _who(request) or {}
        try:
            # Accountable to whoever added them: they are signed in, so there is a real answer, and
            # the alternative (nobody) is the queue-that-nobody-opens the CHECK exists to prevent.
            ws.create_person(name, responsible=me.get("name", ""))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True, "roster": _roster()})

    @app.post("/api/roster/remove")
    async def remove_roster(request: Request):
        """Retire an assignable-only owner from the picker (deactivate, never delete — their past
        assignments stay attributed to them).

        Anyone who can SIGN IN is refused here: this endpoint is open to every member, and before W8
        it could only ever remove an in-app-added name. Letting it deactivate a colleague's login —
        or an admin's — would turn a picker affordance into a permission change.
        """
        body = await request.json()
        name = str(body.get("name", "")).strip()
        person = ws.person(name)
        if person is None:
            return JSONResponse({"ok": True, "roster": _roster(), "protected": _protected_owners()})
        if person["can_login"]:
            return JSONResponse(
                {"error": f"{person['name']} entra na plataforma — quem tem acesso é gerido em "
                          f"Administração, não a partir do seletor de donos."}, status_code=400)
        try:
            ws.set_person_active(person["person_id"], False)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True, "roster": _roster(), "protected": _protected_owners()})

    def _protected_owners() -> list[str]:
        """Names this endpoint will not retire — everyone who can sign in."""
        return [p["name"] for p in ws.people() if p["can_login"]]

    # -------------------------------------------------------------------------
    # C2 — Contrapartes lens
    # -------------------------------------------------------------------------
    @app.get("/contrapartes", response_class=HTMLResponse)
    def contrapartes_list(request: Request):
        cls = _clusters()
        person = _who(request)
        frows = _fila_rows(person=person)  # build Fila + clusters once, reuse for list + badges (F3)
        return HTMLResponse(contrapartes_page.build_list_html(
            _clusters_as_dicts(cls, frows=frows, person=person),
            nav_counts=_nav_counts(frows=frows, clusters=cls, person=person),
            person=_who(request)))

    @app.get("/api/contrapartes")
    def api_contrapartes(request: Request):
        return JSONResponse(_clusters_as_dicts(_clusters(), person=_who(request)))

    def _contraparte_detail_data(key: str, *,
                                 person: dict[str, Any] | None) -> dict[str, Any] | None:
        """Everything the Contrapartes detail hub needs: the cluster, a navigable timeline (each row
        carries its ``thread_root`` + direction so the UI can link into the Fila / inbox), server-side
        rollup ``stats``, the cluster's open Fila threads + projects, and the Para-ti decisions that
        belong to this contraparte. Returns ``None`` when the key is unknown."""
        from collections import Counter
        cluster_dict: dict[str, Any] | None = None
        for c in _clusters_as_dicts(_clusters(), person=person):
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
            # `by_contact` queries the store directly, so it does NOT inherit the Fila filter
            # (ADR-045). Both the timeline AND the `stats` rollup below are computed from these rows,
            # so filtering here fixes both at once — and not filtering here would print an honest
            # `we_owe: 0` beside a `messages: 87` counted over mail the reader cannot open, which is
            # the worst of the two states: a number that contradicts the page it sits on.
            _allowed_roots = _visible_roots(person)
            for email in cluster_dict["emails"]:
                for row in _crmdb.by_contact(email):
                    mid = row["message_id"]
                    if mid in seen:
                        continue
                    if _allowed_roots is not None and (
                            row.get("thread_root") or mid or "") not in _allowed_roots:
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
        cluster_frows = [r for r in _fila_rows(person=person) if (r.get("contact") or "") in emails]
        # Para-ti decisions belonging to this contraparte (by thread, contact, or proposed cluster).
        gates = [
            it for it in _para_ti_items(person=person)
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
    def contrapartes_detail(key: str, request: Request):
        data = _contraparte_detail_data(key, person=_who(request))
        if data is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return HTMLResponse(contrapartes_page.build_detail_html(
            data["cluster"], data["timeline"], data["projects"], data["fila_rows"],
            stats=data["stats"], gates=data["gates"],
            nav_counts=_nav_counts(person=_who(request)),
            person=_who(request)))

    @app.post("/api/contrapartes/{key:path}/name")
    async def contraparte_set_name(key: str, request: Request):
        """Set (empty = reset to automatic) the human display name for a counterparty cluster (v8).
        The clustering key stays the identity; only what a person SEES changes."""
        body = await request.json()
        ws.set_counterparty_name(key, str(body.get("name", "")))
        return JSONResponse({"ok": True, "key": key,
                             "name": ws.counterparty_names().get(key, "")})

    @app.get("/api/contrapartes/{key:path}")
    def api_contrapartes_detail(key: str, request: Request):
        data = _contraparte_detail_data(key, person=_who(request))
        if data is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(data)   # {cluster, stats, timeline, projects, fila_rows, gates}

    # -------------------------------------------------------------------------
    # C3 — Para ti decision inbox
    # -------------------------------------------------------------------------
    def _para_ti_items(frows: list | None = None,
                       clusters: list | None = None, *,
                       person: dict[str, Any] | None) -> list[dict[str, Any]]:
        frows = _fila_rows(person=person) if frows is None else frows
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
    def para_ti_view(request: Request):
        person = _who(request)
        frows = _fila_rows(person=person)  # build once, reuse for items + badges (F3)
        clusters = _clusters()
        return HTMLResponse(para_ti_page.build_html(
            _para_ti_items(frows, clusters, person=person),
            nav_counts=_nav_counts(frows=frows, clusters=clusters, person=person),
            roster=_roster(), person=_who(request)), headers=_NO_STORE)

    @app.get("/api/para-ti")
    def api_para_ti(request: Request):
        """Live decision queue. Carries ``nav_counts`` + sync state alongside the items so the page's
        refresh poll updates the badges and the freshness stamp in a single round-trip."""
        person = _who(request)
        frows = _fila_rows(person=person)
        clusters = _clusters()
        return JSONResponse({
            "items": _para_ti_items(frows, clusters, person=person),
            "nav_counts": _nav_counts(frows=frows, clusters=clusters, person=person),
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
    def _projetos_html(request: Request) -> str:
        # Cheap list: read the denormalized coverage/estimable off each project row (F3). Only a
        # stale/NULL summary (post-migration / post-sync) triggers a single build_canonical that then
        # persists — so this is no longer an O(projects×messages) recompute on every render.
        projects_summary = []
        for p in _visible_projects(_who(request), pstore.list()):
            cov, est = _summary_for(p)
            projects_summary.append({**p, "coverage": cov, "estimable": est,
                                     "n_threads": len(pstore.threads_for(p["project_id"])),
                                     "owners": pstore.owners_for(p["project_id"])})
        return projetos_page.build_html(projects_summary,
                                        nav_counts=_nav_counts(person=_who(request)),
                                        roster=_roster(), person=_who(request))

    @app.get("/projetos", response_class=HTMLResponse)
    def projetos_view(request: Request):
        return HTMLResponse(_projetos_html(request))

    @app.get("/projetos/{pid}", response_class=HTMLResponse)
    def projetos_detail_view(pid: str, request: Request):
        # REST deep-link: ``/projetos/<pid>`` is the detail *resource* URL (mirrors
        # ``/contrapartes/<key>``). The same lens HTML is served — the page JS reads the id from the
        # path and opens that project's workbench. 404 on an unknown id so a stale/shared link fails
        # honestly instead of opening an empty workbench.
        if pstore.get(pid) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return HTMLResponse(_projetos_html(request))

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
    def admin_view(request: Request):
        return HTMLResponse(admin_page.build_html(
            _admin_accounts(), sync=_admin_sync_state(),
            nav_counts=_nav_counts(person=_who(request)),
            person=_who(request)),
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

    # ── «Pessoas»: the roster surface (ADR-041) ──────────────────────────────
    #
    # Everything past `create_person` used to be hand-written SQL against workspace.db — the PRECIOUS
    # store, the one with no rebuild path. Promoting someone, marking a leaver inactive, fixing a
    # typo: a sqlite3 prompt each time. Every rule these routes enforce lives in workspace.py, not
    # here; this layer only turns a ValueError into a 400 a person can read.

    def _attributed_addresses() -> list[str]:
        """Every address that real mail was actually attributed to, from ``sync.message_scope``.

        Read-only, and only when the file exists — merely viewing /admin must never create or
        migrate a store (same rule as ``_cursors_for``). A missing table degrades to ``[]``.
        """
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
                    "SELECT DISTINCT address FROM message_scope WHERE address != ''").fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            return []
        return [str(r[0]).strip().lower() for r in rows if str(r[0] or "").strip()]

    def _known_scopes() -> list[str]:
        """The inbox tokens that can actually be granted: every address real mail reached, our
        configured mailbox addresses, plus the unattributed bucket (a real, grantable token by
        ADR-038's design).

        The vocabulary exists so a grant can be VALIDATED. `set_person_scopes` stores any string, so
        a mistyped address would round-trip through the UI and read as a permission — while matching
        no mail at all. A permission that looks granted and is not is worse than no permission.

        **Configured accounts are not the vocabulary** (ADR-045). ADR-038 deliberately made the scope
        token the ADDRESS a message reached, not a configured account id, precisely so the inboxes we
        never fetch are still grantable — mail reaches them by Cc, by forward, by delivery to an
        alias. Measured on the real corpus 2026-07-26: `message_scope` held **10** addresses while
        `imap.accounts[]` named **4**, and **22 of 374 threads (5.9%)** carried none of those 4.
        Only **one** thread carried `sem-atribuicao`, so the admin bucket did not reach them either.
        Validating grants against the configured 4 would therefore have made those 22 threads
        ungrantable — invisible to every non-admin with no way to fix it from the UI. That is the
        "never silently bin a client" non-negotiable, reached through a permission vocabulary
        instead of through a classifier.
        """
        imap = settings.get("imap", {}) or {}
        addresses = list(_attributed_addresses())
        for a in (imap.get("accounts") or []):
            if isinstance(a, dict):
                username = str(a.get("username", "") or "").strip().lower()
                if username:
                    addresses.append(username)
        return sorted(dict.fromkeys(addresses)) + [_scopesmod.SCOPE_UNATTRIBUTED]

    def _people_view() -> list[dict[str, Any]]:
        """The roster as the panel shows it. Nothing secret: whether a password EXISTS, never a hash,
        and a session COUNT, never a token."""
        by_id = {p["person_id"]: p for p in ws.people(include_inactive=True)}
        rows = []
        for p in by_id.values():
            responsible = by_id.get(p.get("responsible_id") or "")
            rows.append({
                "person_id": p["person_id"], "name": p["name"],
                # Contact data, not password material (ADR-042) — the same class of fact as `name`,
                # so it travels with the roster the panel already renders.
                "email": p.get("email", ""),
                "can_login": bool(p["can_login"]), "is_admin": bool(p["is_admin"]),
                "active": bool(p["active"]), "scopes": list(p["scopes"]),
                "responsible": (responsible or {}).get("name", ""),
                "has_credential": _auth.has_credential(p["person_id"]),
                "sessions": len(_auth.live_sessions(p["person_id"])),
                "must_change": _auth.must_change_password(p["person_id"]),
            })
        return rows

    def _people_payload() -> dict[str, Any]:
        return {"people": _people_view(), "known_scopes": _known_scopes()}

    @app.get("/api/admin/people")
    def api_admin_people():
        return JSONResponse(_people_payload(), headers=_NO_STORE)

    @app.post("/api/admin/people")
    async def api_admin_people_add(request: Request):
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "corpo inválido (JSON esperado)."}, status_code=400)
        access = str((body or {}).get("access", "assign"))
        scopes, err = _validated_scopes((body or {}).get("scopes"))
        if err:
            return JSONResponse({"error": err}, status_code=400)
        name = " ".join(str((body or {}).get("name", "")).split())
        responsible = str((body or {}).get("responsible", "") or "").strip()
        can_login = access in ("login", "admin")
        # The form states its own requirements in pt-PT. Workspace still enforces every one of them
        # (and stays the only enforcer -- these are reads, not a second copy of the rule); its
        # messages are the English developer contract eight tests in test_people.py pin by text.
        if not name:
            problem = "Indica um nome."
        elif ws.person(name) is not None:
            problem = f"Já existe alguém com o nome {name!r}."
        elif not can_login and not responsible:
            problem = ("Quem não entra na plataforma precisa de um responsável — sem ele, o trabalho "
                       "que lhe for atribuído não aparece na vista de ninguém.")
        elif responsible and ws.person(responsible) is None:
            problem = f"O responsável {responsible!r} não existe."
        else:
            problem = ""
        if problem:
            return JSONResponse({"error": problem}, status_code=400)
        try:
            person = ws.create_person(name, can_login=can_login, is_admin=(access == "admin"),
                                      responsible=responsible)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if scopes:
            ws.set_person_scopes(person["person_id"], scopes)
        return JSONResponse({"ok": True, **_people_payload()})

    def _validated_scopes(raw: Any) -> tuple[list[str] | None, str]:
        """``(scopes, error)``. ``None`` scopes = the caller did not ask to change them."""
        if raw is None:
            return None, ""
        if not isinstance(raw, list):
            return None, "as caixas têm de vir numa lista."
        wanted = [str(s).strip().lower() for s in raw if str(s).strip()]
        known = set(_known_scopes())
        unknown = [s for s in wanted if s not in known]
        if unknown:
            return None, (f"{', '.join(unknown)} não é uma caixa desta instalação — uma caixa "
                          f"escrita ao lado fica guardada e não dá acesso a correio nenhum. "
                          f"Conhecidas: {', '.join(sorted(known))}.")
        return list(dict.fromkeys(wanted)), ""

    @app.post("/api/admin/people/{person_id}")
    async def api_admin_people_update(person_id: str, request: Request):
        """Promote/demote, activate/deactivate, re-grant scopes. Each field is optional; absent means
        unchanged, so the panel can send one intent at a time."""
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "corpo inválido (JSON esperado)."}, status_code=400)
        body = body or {}
        me = _who(request)
        # Self-demotion and self-deactivation are refused even when another admin exists. The store's
        # last-admin invariant already prevents the unrecoverable case; this prevents the ordinary
        # misclick, which has no undo from inside the app either — you would be locked out of the
        # screen you would need to fix it.
        if person_id == (me or {}).get("person_id"):
            if body.get("is_admin") is False:
                return JSONResponse(
                    {"error": "não te podes despromover a ti próprio — pede a outro administrador."},
                    status_code=400)
            if body.get("active") is False:
                return JSONResponse(
                    {"error": "não te podes desativar a ti próprio — ficarias de fora do ecrã que "
                              "precisas para o desfazer."}, status_code=400)
        scopes, err = _validated_scopes(body.get("scopes"))
        if err:
            return JSONResponse({"error": err}, status_code=400)
        try:
            if "is_admin" in body:
                ws.set_person_admin(person_id, bool(body["is_admin"]))
            if "active" in body:
                ws.set_person_active(person_id, bool(body["active"]))
                if not body["active"]:
                    # Deactivation has to end the live sessions too, or the person keeps the app open
                    # until their cookie expires -- `_current_person` re-reads workspace.db every
                    # request, but only the NEXT request.
                    _auth.revoke_all_sessions(person_id)
            if scopes is not None:
                ws.set_person_scopes(person_id, scopes)
            if "email" in body:
                # Validation (shape + one-address-one-person) lives in the store, so the CLI gets it
                # too and the refusal text reaches the panel verbatim via the ValueError below.
                ws.set_person_email(person_id, str(body["email"] or ""))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True, **_people_payload()})

    @app.post("/api/admin/people/{person_id}/convite")
    def api_admin_people_invite(person_id: str, request: Request):
        """Mint a single-use invite link, in the browser.

        `email2data auth invite` printed the token to a terminal, where it stayed — in shell history,
        in a scrollback buffer, and then in whatever chat it was pasted into. Minted here it can be
        copied straight from the panel and the terminal never sees it.
        """
        person = ws.person_by_id(person_id)
        if person is None:
            return JSONResponse({"error": "pessoa desconhecida."}, status_code=404)
        if not (person["can_login"] and person["active"]):
            return JSONResponse(
                {"error": f"{person['name']} não tem acesso à plataforma — um convite para quem não "
                          f"pode entrar não leva a lado nenhum."}, status_code=400)
        token = _auth.create_invite(person_id, created_by=(_who(request) or {}).get("name", ""))
        return JSONResponse({"ok": True, "url": f"/aceitar-convite/{token}",
                             "expires_hours": _authmod.INVITE_TTL_HOURS,
                             "name": person["name"]}, headers=_NO_STORE)

    @app.delete("/api/admin/people/{person_id}")
    def api_admin_people_delete(person_id: str, request: Request):
        if person_id == (_who(request) or {}).get("person_id"):
            return JSONResponse({"error": "não te podes remover a ti próprio."}, status_code=400)
        try:
            ws.delete_person(person_id)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        # workspace.db and auth.db are joined by person_id with NO foreign key -- SQLite cannot
        # enforce one across files. Leaving the credential behind is exactly the orphan drift
        # `auth list` reports as a warning.
        _auth.purge_person(person_id)
        return JSONResponse({"ok": True, **_people_payload()})

    return app


def from_settings(settings: dict[str, Any]):
    return create_app(settings)
