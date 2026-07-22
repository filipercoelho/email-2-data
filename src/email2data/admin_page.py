"""Administração lens page (/admin) — IMAP account inventory, force-sync, and the account editor.

Thin wrapper over ``cockpit_ui.page()`` (mirrors ``captures_page``/``para_ti_page``). Three blocks:

  1. **Contas de email** — one card per configured IMAP account: identity, host/port, mailbox count,
     whether the credential resolved (a BOOLEAN), last sync, the per-mailbox fetch cursor, and the
     recent per-account fetch errors.
  2. **Sincronizar agora** — force a sync over all accounts or one, in "só buscar" (fetch only, zero
     Tier-1 token spend) or "buscar + classificar" mode, with live progress and the last result.
  3. **Editor de contas** — add/remove/edit accounts and their mailbox lists, POSTed to
     ``/api/admin/accounts``.

SECRETS — the load-bearing invariant of this page (README non-negotiable #5). Passwords live in
``.env`` and are referenced by the *name* of the environment variable (``password_env``). This module:

  * never renders, accepts, or round-trips a password — there is no password input anywhere, by
    design, and the UI copy says so;
  * never embeds the settings dict. ``build_html`` re-projects every account through an explicit
    allowlist (``_account_view``), so an over-generous API response (a resolved secret, a stray
    ``password`` key) is dropped here rather than shipped to the DOM;
  * suppresses a ``password_env`` that is not a plausible environment-variable identifier — a pasted
    secret value would not match ``_ENV_NAME_RE`` and is therefore never echoed back;
  * keeps sync counts to NUMERIC values only, so no string can ride into the page through the
    counts channel.

IMAP stays read-only (non-negotiable #1): this page has no "esvaziar lixo", no "marcar como lida",
no mutation of any kind. It also offers no reset/rebuild control for ``workspace.db`` (#6) — the only
write it performs is replacing the account list in the settings, via the API.
"""

from __future__ import annotations

import re
from typing import Any

from . import cockpit_ui

# A plausible POSIX environment-variable identifier. Used as a *suppression* filter, not just
# validation: a real password (spaces, punctuation, accents) fails this, so it can never be echoed
# back into the page even if one somehow reached the ``password_env`` slot.
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_MAX_ERRORS = 20     # a stuck account must not turn the page into a log dump
_MAX_COUNTS = 24

# pt-PT labels for the ``sync.run_sync`` return keys (see sync.py). A key with no label falls back to
# the raw key at render time, so a new counter shows up instead of silently disappearing.
_COUNT_LABELS = {
    "fetched": "mensagens novas",
    "triaged_new": "classificadas",
    "triaged_skipped": "já classificadas",
    "offline": "por regra (offline)",
    "llm": "via LLM",
    "failed": "falhadas",
    "crm_recorded": "relações CRM",
}


# ── redaction / projection ────────────────────────────────────────────────────

def _s(v: Any) -> str:
    return "" if v is None else str(v)


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _port(v: Any) -> int | None:
    n = _int(v)
    return n if n is not None and 0 < n < 65536 else None


def _cursor_view(c: Any) -> dict[str, Any]:
    """One per-mailbox fetch watermark (``sync.fetch_cursor``), allowlisted."""
    if not isinstance(c, dict):
        return {"mailbox": _s(c), "uidvalidity": None, "last_uid": None, "updated_ts": ""}
    return {
        "mailbox": _s(c.get("mailbox")),
        "uidvalidity": _int(c.get("uidvalidity")),
        "last_uid": _int(c.get("last_uid")),
        "updated_ts": _s(c.get("updated_ts")),
    }


def _error_view(e: Any) -> dict[str, str]:
    """One recent per-account fetch error. Accepts a bare string or a dict; only the message-ish
    keys survive, so an error object carrying extra context cannot smuggle a value into the page."""
    if not isinstance(e, dict):
        return {"ts": "", "mailbox": "", "message": _s(e)}
    msg = ""
    for k in ("error", "message", "detail", "reason"):
        if e.get(k):
            msg = _s(e[k])
            break
    return {"ts": _s(e.get("ts")), "mailbox": _s(e.get("mailbox")),
            "message": msg or "erro sem detalhe"}


def _account_view(a: Any) -> dict[str, Any]:
    """Project ONE account onto the exact set of fields this page may show. Allowlist, not blocklist:
    anything the API adds later (including a resolved password) is dropped by construction."""
    if not isinstance(a, dict):
        a = {}
    env = _s(a.get("password_env")).strip()
    env_ok = bool(_ENV_NAME_RE.match(env))
    return {
        "id": _s(a.get("id")),
        "username": _s(a.get("username")),
        "host": _s(a.get("host")),
        "port": _port(a.get("port")),
        "mailboxes": [_s(m) for m in (a.get("mailboxes") or []) if _s(m)],
        # The NAME of the env var, never its value — and only when it *looks* like a name.
        "password_env": env if env_ok else "",
        "password_env_invalid": bool(env) and not env_ok,
        # Whether the credential resolved. A bool is the ceiling: no length, no prefix, no hint.
        "credential_present": bool(a.get("credential_present")),
        "last_sync": _s(a.get("last_sync")),
        "cursors": [_cursor_view(c) for c in (a.get("cursors") or [])],
        "errors": [_error_view(e) for e in (a.get("errors") or [])][:_MAX_ERRORS],
    }


def _sync_view(sync: Any) -> dict[str, Any]:
    """Normalise the two sync shapes the API speaks — ``{running, last:{...}}`` (from
    /api/admin/accounts) and ``{running, last_counts, last_error}`` (from /api/sync/status) — into
    one, keeping only numeric counts and the designated error strings."""
    if not isinstance(sync, dict):
        sync = {}
    last = sync.get("last") if isinstance(sync.get("last"), dict) else {}
    raw = last.get("counts") or sync.get("last_counts") or last
    if not isinstance(raw, dict):
        raw = {}
    # Counts are NUMBERS. Restricting the type here means no string — and therefore no secret — can
    # reach the page through the counts channel, whatever the server sends.
    counts: dict[str, float] = {}
    for k, v in raw.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        counts[str(k)] = v
        if len(counts) >= _MAX_COUNTS:
            break
    fails_raw = last.get("failures") or last.get("account_failures") or sync.get("account_failures")
    failures = {}
    if isinstance(fails_raw, dict):
        for k, v in list(fails_raw.items())[:_MAX_ERRORS]:
            failures[str(k)] = _s(v)
    return {
        "running": bool(sync.get("running")),
        "last": {
            "ts": _s(last.get("ts") or sync.get("last_ts")),
            "error": _s(last.get("error") or sync.get("last_error")),
            "counts": counts,
            "failures": failures,
        },
    }


# ── page ──────────────────────────────────────────────────────────────────────

_BODY = """
<div class="wrap">
  <div class="bar">
    <span id="_count"></span>
    <span class="cmdk"><kbd>⌘K</kbd> comandos</span>
  </div>
  <div id="_sync"></div>
  <div id="_accts"></div>
  <div class="hint">
    As credenciais vivem no <b>.env</b> e são referidas só pelo <b>nome</b> da variável —
    esta página nunca lê, mostra nem guarda passwords.
    O acesso IMAP é <b>só de leitura</b>: nada é apagado, movido ou marcado no servidor.
  </div>
</div>
"""

_EXTRA_CSS = """
  .asec{margin-bottom:22px}
  .ahead{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);
    font-weight:700;margin:0 2px 9px;display:flex;align-items:center;gap:9px;flex-wrap:wrap}
  .ahead .sh{text-transform:none;letter-spacing:0;color:var(--mut2);font-weight:500}
  .acard{background:var(--card);border:1px solid var(--bd);border-radius:14px;
    padding:14px 16px;margin-bottom:10px;box-shadow:var(--shadow)}
  .acard.bad{border-color:#f3c9c9}
  .ahdr{display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin-bottom:6px}
  .aid{font-weight:680;font-size:15px;letter-spacing:-.01em}
  .auser{font-size:12.5px;color:var(--mut)}
  .ahdr .grow{margin-left:auto}
  .ab{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;
    border-radius:20px;padding:2px 9px;white-space:nowrap}
  .ab.ok{background:#e7f6ee;color:var(--green);border:1px solid #bfe6cf}
  .ab.bad{background:#fbeaea;color:var(--red);border:1px solid #f3c9c9}
  .ameta{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 2px}
  .chip{font-size:11.5px;color:var(--tx);background:var(--bg);border:1px solid var(--bd);
    border-radius:20px;padding:2px 10px;white-space:nowrap}
  .chip b{font-variant-numeric:tabular-nums}
  .chip code{font-family:ui-monospace,monospace;font-size:11px;color:var(--mut)}
  .apanel{margin-top:9px;border:1px solid var(--bd);border-radius:10px;background:#fbfcfe;overflow:hidden}
  .atog{width:100%;text-align:left;background:none;border:0;cursor:pointer;padding:8px 11px;
    font-size:12px;font-weight:650;color:var(--mut);font-family:inherit}
  .atog:hover{color:var(--ac)}
  .abody{padding:0 11px 10px;max-height:290px;overflow:auto}
  .atbl{width:100%;border-collapse:collapse;font-size:11.5px;font-variant-numeric:tabular-nums}
  .atbl th{text-align:left;color:var(--mut2);font-weight:650;padding:3px 8px 5px;
    border-bottom:1px solid var(--bd);position:sticky;top:0;background:#fbfcfe}
  .atbl td{padding:3px 8px;border-bottom:1px solid var(--bd2);word-break:break-all}
  .atbl td.mb{font-family:ui-monospace,monospace;font-size:11px}
  .alist{margin:0;padding:0;list-style:none;font-size:11.5px}
  .alist li{padding:4px 0;border-bottom:1px solid var(--bd2);word-break:break-word}
  .alist li:last-child{border-bottom:none}
  .alist code{font-family:ui-monospace,monospace;font-size:11px;color:var(--tx);word-break:break-all}
  .aerr{margin-top:9px;border:1px solid #f3c9c9;background:#fbeaea;border-radius:10px;padding:8px 11px}
  .aerr .et{font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:var(--red);margin-bottom:4px}
  .aerr li{font-size:11.5px;color:#7a2a2a;border-bottom-color:#f3c9c9}
  /* sync card */
  .sctl{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  .sctl select{border:1px solid var(--bd);border-radius:8px;padding:5px 9px;font-size:12.5px;
    color:var(--tx);background:var(--card);font-family:inherit;max-width:280px}
  .sctl select:focus{border-color:var(--ac);outline:none}
  .sctl button[disabled]{opacity:.5;cursor:default}
  .sctl button[disabled]:hover{border-color:var(--bd);color:var(--mut);background:#fff}
  .sres{margin-top:10px}
  .smut{font-size:11.5px;color:var(--mut)}
  .chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}
  .sprog{margin-top:10px;font-size:12.5px;color:var(--ac);font-weight:600}
  .serr{margin-top:6px;font-size:12px;color:var(--red);word-break:break-word}
  .snote{margin-top:8px;font-size:11.5px;color:var(--mut2)}
  /* editor */
  .frow{display:flex;align-items:center;gap:9px;margin:6px 0}
  .flab{flex:0 0 210px;font-size:12px;color:var(--mut);font-weight:600}
  .fin{flex:1;min-width:90px;border:1px solid var(--bd);border-radius:7px;padding:5px 9px;
    font-size:12.5px;color:var(--tx);background:var(--card);font-family:inherit;outline:none}
  .fin:focus{border-color:var(--ac)}
  .fin.ta{width:100%;font-family:ui-monospace,monospace;font-size:11.5px;line-height:1.5;resize:vertical}
  .fhint{font-size:11.5px;color:var(--mut2);margin:2px 0 8px 219px;line-height:1.45}
  .fhint code{font-family:ui-monospace,monospace;background:var(--bg);border:1px solid var(--bd);
    border-radius:5px;padding:0 4px}
  .act-btn.danger{border-color:#f3c9c9;color:var(--red)}
  .act-btn.danger:hover{background:#fbeaea;border-color:var(--red);color:var(--red)}
  .savebar{position:sticky;bottom:0;background:var(--card);border:1px solid var(--bd);
    border-radius:12px;padding:11px 14px;margin-top:12px;display:flex;align-items:center;gap:9px;
    flex-wrap:wrap;box-shadow:0 -2px 10px rgba(20,24,28,.07)}
  .savebar .smut{flex:1;min-width:180px}
"""

_LENS_JS = r"""
/* ── Administração lens ──────────────────────────────────────────────────────
   Everything rendered here comes from the server-side redacted projection
   (admin_page._account_view). There is NO password input on this page, by design:
   credentials stay in .env and are named, never shown. Every value goes through
   esc() before it touches innerHTML — real mailbox names carry & " ' and <. */

let accounts = ACCOUNTS.slice();
let sync = normSync(SYNC);
let draft = null;                 /* non-null while the account editor is open */
const open_ = new Set();          /* expanded detail panels, keyed "<idx>|mb" / "<idx>|cur" */
let _scope = '';                  /* '' = all accounts */
let _mode = 'fetch';              /* 'fetch' = só buscar (no LLM spend) | 'full' = + classificar */
let _busy = false;                /* a POST is in flight — blocks a double-fire */
let _pollT = null, _startedAt = 0;

const MODES = [['fetch', 'Só buscar (sem gastar LLM)'], ['full', 'Buscar + classificar']];
const ENVRE = /^[A-Za-z_][A-Za-z0-9_]*$/;

function normSync(d){
  d = d || {};
  const last = (d.last && typeof d.last === 'object') ? d.last : {};
  let counts = last.counts || d.last_counts || {};
  if(!counts || typeof counts !== 'object') counts = {};
  const numeric = {};   /* mirror of the server rule: counts are numbers, never strings */
  Object.keys(counts).forEach(k => { if(typeof counts[k] === 'number') numeric[k] = counts[k]; });
  const fails = last.failures || last.account_failures || d.account_failures || {};
  return {running: !!d.running,
          last: {ts: last.ts || d.last_ts || '',
                 error: last.error || d.last_error || '',
                 counts: numeric,
                 failures: (fails && typeof fails === 'object') ? fails : {}}};
}

function fmtTs(v){ const s = String(v || ''); return s ? s.slice(0,16).replace('T',' ') : '—'; }
function countLabel(k){ return COUNT_LABELS[k] || k; }
function mbList(text){
  const seen = new Set(), out = [];
  String(text == null ? '' : text).split('\n').forEach(l => {
    const v = l.trim();
    if(v && !seen.has(v)){ seen.add(v); out.push(v); }
  });
  return out;
}

/* ── sync card ─────────────────────────────────────────────────────────────── */

function elapsedLabel(){
  if(!_startedAt) return '';
  const s = Math.max(0, Math.round((Date.now() - _startedAt) / 1000));
  return s < 60 ? (s + 's') : (Math.floor(s/60) + 'm ' + (s%60) + 's');
}

function lastResultHTML(){
  const l = sync.last || {};
  const keys = Object.keys(l.counts || {});
  const fk = Object.keys(l.failures || {});
  if(!l.ts && !l.error && !keys.length && !fk.length)
    return '<div class="smut">Ainda não houve sincronização registada.</div>';
  const chips = keys.map(k =>
    '<span class="chip"><b>' + esc(l.counts[k]) + '</b> ' + esc(countLabel(k)) + '</span>').join('');
  const err = l.error ? '<div class="serr">Erro: ' + esc(l.error) + '</div>' : '';
  const fails = fk.map(k =>
    '<div class="serr">' + esc(k) + ': ' + esc(l.failures[k]) + '</div>').join('');
  return '<div class="sres"><div class="smut">Última sync: ' + esc(fmtTs(l.ts)) + '</div>'
    + (chips ? '<div class="chips">' + chips + '</div>' : '') + err + fails + '</div>';
}

function renderSync(){
  const el = $('#_sync'); if(!el) return;
  const running = sync.running || _busy;
  const accOpts = '<option value="">Todas as contas</option>'
    + accounts.map(a => '<option value="' + esc(a.id) + '"' + (a.id === _scope ? ' selected' : '')
        + '>' + esc(a.id || '(sem id)') + '</option>').join('');
  const modeOpts = MODES.map(m =>
    '<option value="' + m[0] + '"' + (m[0] === _mode ? ' selected' : '') + '>' + esc(m[1]) + '</option>').join('');
  const dis = running ? ' disabled' : '';
  el.innerHTML =
      '<div class="asec"><div class="ahead">Sincronizar <span class="sh">buscar correio novo por IMAP (só leitura)</span></div>'
    + '<div class="acard">'
    + '<div class="sctl">'
    +   '<select id="_scope" aria-label="Contas a sincronizar"' + dis + '>' + accOpts + '</select>'
    +   '<select id="_mode" aria-label="Modo de sincronização"' + dis + '>' + modeOpts + '</select>'
    +   '<button class="act-btn accept" id="_run"' + dis + '>Sincronizar agora</button>'
    + '</div>'
    + (running
        ? '<div class="sprog">A sincronizar… <span id="_elapsed">' + esc(elapsedLabel()) + '</span></div>'
        : lastResultHTML())
    + '<div class="snote">«Só buscar» não chama o LLM — nenhum token é gasto. '
    + '«Buscar + classificar» só classifica mensagens ainda não classificadas.</div>'
    + '</div></div>';
}

function startPoll(){ if(!_pollT) _pollT = setInterval(pollSync, 2000); }
function stopPoll(){ if(_pollT){ clearInterval(_pollT); _pollT = null; } }

async function pollSync(){
  try{
    const r = await fetch('/api/sync/status', {cache:'no-store'});
    if(!r.ok) return;
    const was = sync.running || _busy;
    sync = normSync(await r.json());
    if(_busy) sync.running = true;          /* our own POST is still in flight */
    renderSync();
    if(was && !sync.running && !_busy){ stopPoll(); toast(S.sincronizado); loadAccounts(); }
  }catch(e){ /* server restarting — keep what we have, retry next tick */ }
}

async function runSync(){
  if(sync.running || _busy) return;         /* disabled while a sync is running */
  _busy = true; _startedAt = Date.now();
  sync.running = true; renderSync(); startPoll();
  announce('sincronização iniciada');
  const body = {do_fetch: true, do_triage: (_mode === 'full')};
  if(_scope) body.account_id = _scope;
  try{
    const r = await fetch('/api/sync', {method:'POST', headers:{'Content-Type':'application/json'},
                                        body: JSON.stringify(body)});
    if(r.status === 409){ toast(S.syncEmCurso); return; }   /* someone else is syncing — keep polling */
    const d = r.ok ? await r.json().catch(()=>({})) : {};
    if(!r.ok){
      sync = normSync({running:false, last:{ts:new Date().toISOString(), error:'HTTP ' + r.status}});
      toast(S.syncFalhou); stopPoll(); return;
    }
    sync = normSync({running:false, last:{ts: new Date().toISOString(), counts: d,
                                          error: d.error || '', failures: d.account_failures || {}}});
    stopPoll(); toast(S.sincronizado); announce('sincronização concluída');
    await loadAccounts();
  }catch(e){
    sync = normSync({running:false, last:{ts:new Date().toISOString(), error:'falhou'}});
    toast(S.syncFalhou); stopPoll();
  }finally{
    _busy = false; _startedAt = 0;
    if(!_pollT) sync.running = false;
    renderSync();
  }
}

/* ── account cards (read-only view) ────────────────────────────────────────── */

function panel(key, label, bodyHTML){
  const on = open_.has(key);
  return '<div class="apanel"><button class="atog" data-act="tog" data-key="' + esc(key) + '">'
    + (on ? '▾ ' : '▸ ') + esc(label) + '</button>'
    + '<div class="abody' + (on ? '' : ' hidden') + '">' + bodyHTML + '</div></div>';
}

function mailboxesHTML(a){
  const mb = a.mailboxes || [];
  if(!mb.length) return '<div class="smut">Sem caixas configuradas.</div>';
  return '<ul class="alist">' + mb.map(m =>
    '<li><code class="mb">' + esc(m) + '</code></li>').join('') + '</ul>';
}

function cursorsHTML(a){
  const cs = a.cursors || [];
  if(!cs.length) return '<div class="smut">Ainda sem cursor — esta conta nunca foi buscada.</div>';
  return '<table class="atbl"><thead><tr><th>Caixa</th><th>last_uid</th><th>uidvalidity</th>'
    + '<th>Atualizado</th></tr></thead><tbody>'
    + cs.map(c => '<tr><td class="mb">' + esc(c.mailbox) + '</td>'
        + '<td>' + esc(c.last_uid == null ? '—' : c.last_uid) + '</td>'
        + '<td>' + esc(c.uidvalidity == null ? '—' : c.uidvalidity) + '</td>'
        + '<td>' + esc(fmtTs(c.updated_ts)) + '</td></tr>').join('')
    + '</tbody></table>';
}

function errorsHTML(a){
  const es = a.errors || [];
  if(!es.length) return '';
  return '<div class="aerr"><div class="et">Erros recentes (' + es.length + ')</div>'
    + '<ul class="alist">' + es.map(e => '<li>'
        + (e.ts ? '<b>' + esc(fmtTs(e.ts)) + '</b> · ' : '')
        + (e.mailbox ? esc(e.mailbox) + ' · ' : '')
        + esc(e.message) + '</li>').join('') + '</ul></div>';
}

function accountCard(a, i){
  /* credential_present is a BOOLEAN from the server — the page shows only whether the env var
     resolved, plus its NAME. The value never leaves the process. */
  const cred = a.credential_present
    ? '<span class="ab ok">✓ credencial presente</span>'
    : '<span class="ab bad">✗ credencial em falta</span>';
  const envChip = a.password_env
    ? '<span class="chip">variável <code>' + esc(a.password_env) + '</code></span>'
    : '<span class="chip">variável <code>' + (a.password_env_invalid ? 'nome inválido' : 'por definir') + '</code></span>';
  const mb = (a.mailboxes || []).length;
  const meta = '<div class="ameta">'
    + '<span class="chip">' + esc(a.host || '—') + ':' + esc(a.port == null ? '—' : a.port) + '</span>'
    + '<span class="chip"><b>' + mb + '</b> caixa' + (mb === 1 ? '' : 's') + '</span>'
    + envChip
    + '<span class="chip">Última sync: ' + esc(fmtTs(a.last_sync)) + '</span>'
    + '</div>';
  return '<div class="acard' + (a.credential_present ? '' : ' bad') + '">'
    + '<div class="ahdr"><span class="aid">' + esc(a.id || '(sem id)') + '</span>'
    + '<span class="auser">' + esc(a.username || '—') + '</span>'
    + '<span class="grow"></span>' + cred + '</div>'
    + meta
    + panel(i + '|mb', 'Caixas de correio (' + mb + ')', mailboxesHTML(a))
    + panel(i + '|cur', 'Cursores de leitura (' + (a.cursors || []).length + ')', cursorsHTML(a))
    + errorsHTML(a)
    + '</div>';
}

/* ── account editor ────────────────────────────────────────────────────────── */

function field(i, k, label, val, type){
  return '<div class="frow"><label class="flab" for="f' + i + '_' + k + '">' + esc(label) + '</label>'
    + '<input class="fin" id="f' + i + '_' + k + '" type="' + (type || 'text') + '" data-i="' + i + '"'
    + ' data-k="' + k + '" value="' + esc(val == null ? '' : val) + '" spellcheck="false"'
    + ' autocomplete="off"></div>';
}

function editorCard(a, i){
  const inv = a.password_env_invalid
    ? '<div class="serr">O nome de variável configurado não é válido e foi ocultado — escreve o nome correto.</div>'
    : '';
  return '<div class="acard" data-i="' + i + '">'
    + '<div class="ahdr"><span class="aid">Conta ' + (i + 1) + '</span><span class="grow"></span>'
    + '<button class="act-btn danger" data-act="rm" data-i="' + i + '">Remover conta</button></div>'
    + field(i, 'id', 'Identificador', a.id)
    + field(i, 'username', 'Utilizador (email)', a.username)
    + field(i, 'host', 'Servidor IMAP', a.host)
    + field(i, 'port', 'Porta', a.port == null ? 993 : a.port, 'number')
    + field(i, 'password_env', 'Variável da password (.env)', a.password_env)
    + '<div class="fhint">Só o <b>nome</b> da variável definida no <code>.env</code> '
    + '(ex.: <code>EMAIL2DATA_ORCAMENTOS_PASSWORD</code>). '
    + 'A password nunca é escrita, lida nem guardada nesta página.</div>' + inv
    + '<div class="frow" style="align-items:flex-start">'
    + '<label class="flab" for="f' + i + '_mb">Caixas de correio<br><span style="font-weight:500;color:var(--mut2)">uma por linha</span></label>'
    + '<textarea class="fin ta" id="f' + i + '_mb" data-i="' + i + '" data-k="mailboxes" rows="7"'
    + ' spellcheck="false">' + esc(a.mailboxes) + '</textarea></div>'
    + '</div>';
}

function validate(list){
  const errs = [], seen = new Set();
  if(!list.length) errs.push('Adiciona pelo menos uma conta');
  list.forEach((a, i) => {
    const n = 'Conta ' + (i + 1) + ': ';
    const id = String(a.id || '').trim();
    if(!id) errs.push(n + 'identificador em falta');
    else if(seen.has(id)) errs.push(n + 'identificador repetido (' + id + ')');
    else seen.add(id);
    if(!String(a.username || '').trim()) errs.push(n + 'utilizador em falta');
    if(!String(a.host || '').trim()) errs.push(n + 'servidor em falta');
    const p = parseInt(a.port, 10);
    if(!(p > 0 && p < 65536)) errs.push(n + 'porta inválida');
    const pe = String(a.password_env || '').trim();
    /* Also the guard against a pasted SECRET: a real password fails ENVRE, so it is refused at the
       door instead of being POSTed into settings.json. */
    if(!pe) errs.push(n + 'nome da variável de ambiente em falta');
    else if(!ENVRE.test(pe)) errs.push(n + 'nome de variável inválido (só letras, dígitos e _)');
    if(!mbList(a.mailboxes).length) errs.push(n + 'sem caixas de correio');
  });
  return errs;
}

function openEditor(){
  draft = accounts.map(a => ({id: a.id, username: a.username, host: a.host,
    port: a.port == null ? 993 : a.port, password_env: a.password_env,
    password_env_invalid: !!a.password_env_invalid,
    mailboxes: (a.mailboxes || []).join('\n')}));
  renderAccounts();
}
function closeEditor(){ draft = null; renderAccounts(); }

function addAccount(){
  draft.push({id: '', username: '', host: (accounts[0] || {}).host || '', port: 993,
              password_env: '', password_env_invalid: false, mailboxes: 'INBOX'});
  renderAccounts();
}
function removeAccount(i){
  if(!draft || !(i >= 0 && i < draft.length)) return;
  draft.splice(i, 1);
  renderAccounts();
  toast('conta removida do rascunho — «Guardar contas» para confirmar');
}

async function saveAccounts(){
  if(!draft || _busy) return;
  if(sync.running){ toast('sync em curso — espera que termine'); return; }
  const errs = validate(draft);
  if(errs.length){ toast(errs[0]); announce(errs[0]); return; }
  const payload = draft.map(a => ({
    id: String(a.id).trim(), username: String(a.username).trim(), host: String(a.host).trim(),
    port: parseInt(a.port, 10), password_env: String(a.password_env).trim(),
    mailboxes: mbList(a.mailboxes)}));
  _busy = true;
  try{
    await post('/api/admin/accounts', {accounts: payload});
    draft = null;
    await loadAccounts();
    toast('contas guardadas'); announce('contas guardadas');
  }catch(e){ toast(S.revertido); }
  finally{ _busy = false; }
}

/* ── accounts section ──────────────────────────────────────────────────────── */

function renderAccounts(){
  const el = $('#_accts'); if(!el) return;
  const cnt = $('#_count');
  if(cnt) cnt.textContent = accounts.length + (accounts.length === 1 ? ' conta' : ' contas');
  if(draft){
    el.innerHTML =
        '<div class="asec"><div class="ahead">Contas de email '
      + '<span class="sh">editor — nada é aplicado até «Guardar contas»</span></div>'
      + draft.map((a, i) => editorCard(a, i)).join('')
      + '<button class="act-btn" data-act="add">+ Adicionar conta</button>'
      + '<div class="savebar"><span class="smut">As passwords ficam no <b>.env</b>. '
      + 'Aqui guarda-se apenas o <b>nome</b> da variável.</span>'
      + '<button class="act-btn" data-act="cancel">Cancelar</button>'
      + '<button class="act-btn accept" data-act="save">Guardar contas</button></div>'
      + '</div>';
    return;
  }
  el.innerHTML =
      '<div class="asec"><div class="ahead">Contas de email '
    + '<span class="sh">' + esc(accounts.length) + ' configurada' + (accounts.length === 1 ? '' : 's')
    + ' · credenciais no .env</span></div>'
    + (accounts.length
        ? accounts.map((a, i) => accountCard(a, i)).join('')
        : '<div class="acard"><div class="smut">Nenhuma conta configurada.</div></div>')
    + '<button class="act-btn" data-act="edit">Editar contas</button>'
    + '</div>';
}

async function loadAccounts(){
  try{
    const r = await fetch('/api/admin/accounts', {cache:'no-store'});
    if(!r.ok) return;
    const d = await r.json();
    if(Array.isArray(d.accounts)) accounts = d.accounts;
    if(d.sync && !_busy && !sync.running) sync = normSync(d.sync);
    if(!draft) renderAccounts();   /* never clobber an open editor mid-typing */
    renderSync();
  }catch(e){ /* offline — keep showing what we have */ }
}

/* ── lens contract ─────────────────────────────────────────────────────────── */

function render(){ renderSync(); renderAccounts(); }

function onKey(e){ /* no page-level shortcuts: this page is a form, typing must stay literal */ }

function paletteItems(q){
  q = (q || '').toLowerCase().trim();
  const base = [
    {kind:'ação', label:'Sincronizar agora (só buscar)', run:()=>{ _mode='fetch'; renderSync(); runSync(); }},
    {kind:'ação', label:'Buscar + classificar', run:()=>{ _mode='full'; renderSync(); runSync(); }},
    {kind:'ação', label:'Editar contas', run:openEditor},
    {kind:'ação', label:'Fila', run:()=>{ location.href='/'; }},
    {kind:'ação', label:'Contrapartes', run:()=>{ location.href='/contrapartes'; }},
    {kind:'ação', label:'Projetos', run:()=>{ location.href='/projetos'; }},
    {kind:'ação', label:'Para ti', run:()=>{ location.href='/para-ti'; }},
    {kind:'ação', label:'Capturas', run:()=>{ location.href='/capturas'; }},
  ];
  accounts.forEach(a => base.push({kind:'conta', label:a.id || a.username,
    sub:(a.mailboxes || []).length + ' caixas',
    run:()=>{ _scope = a.id; _mode = 'fetch'; renderSync(); }}));
  return q ? base.filter(it => (it.label + ' ' + (it.sub || '') + ' ' + it.kind).toLowerCase().includes(q)) : base;
}

/* ── wiring ────────────────────────────────────────────────────────────────── */

$('#_sync').addEventListener('change', e => {
  if(e.target.id === '_scope') _scope = e.target.value;
  else if(e.target.id === '_mode') _mode = e.target.value;
});
$('#_sync').addEventListener('click', e => {
  if(e.target.closest('#_run')) runSync();
});

/* Edits land on the draft object on every keystroke but do NOT re-render — a re-render mid-typing
   would eat the caret. Only add/remove/save re-render. */
$('#_accts').addEventListener('input', e => {
  const t = e.target;
  if(!draft || !t.dataset || t.dataset.i === undefined) return;
  const i = parseInt(t.dataset.i, 10);
  if(!(i >= 0 && i < draft.length)) return;
  draft[i][t.dataset.k] = t.value;
});
$('#_accts').addEventListener('click', e => {
  const btn = e.target.closest('[data-act]'); if(!btn) return;
  const act = btn.dataset.act;
  if(act === 'tog'){
    const k = btn.dataset.key;
    if(open_.has(k)) open_.delete(k); else open_.add(k);
    renderAccounts(); return;
  }
  if(act === 'edit') openEditor();
  else if(act === 'cancel') closeEditor();
  else if(act === 'add') addAccount();
  else if(act === 'rm') removeAccount(parseInt(btn.dataset.i, 10));
  else if(act === 'save') saveAccounts();
});

/* Tick the elapsed counter while a sync runs (cheap: one text node, no re-render). */
setInterval(() => { const el = $('#_elapsed'); if(el) el.textContent = elapsedLabel(); }, 1000);
/* If a sync was already running when the page loaded (startup thread, another tab), follow it. */
if(sync.running){ _startedAt = Date.now(); startPoll(); }
"""


def build_html(accounts: list[dict[str, Any]],
               sync: dict[str, Any] | None = None,
               nav_counts: dict[str, int] | None = None) -> str:
    """Render the Administração page (/admin).

    ``accounts`` — the account rows from ``GET /api/admin/accounts``. They are re-projected through
    ``_account_view`` here, so this function is the last line of defence: whatever the caller hands
    over, only the allowlisted, non-secret fields reach the page.
    ``sync``      — the sync state (``{running, last:{...}}`` or ``{running, last_counts, ...}``).
    """
    return cockpit_ui.page(
        "Administração", "admin", _BODY,
        embeds={"accounts": [_account_view(a) for a in (accounts or [])],
                "sync": _sync_view(sync),
                "count_labels": _COUNT_LABELS},
        lens_js=_LENS_JS,
        nav_counts=nav_counts,
        extra_css=_EXTRA_CSS,
    )
