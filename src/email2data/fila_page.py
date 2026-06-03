"""Fila page (D3 + B1 + B2) — the response cockpit's hero screen, served at ``GET /fila``.

A self-contained HTML page (component-kit CSS + vanilla JS) rendered from ``cockpit.build_fila`` rows.
Deliberately a SEPARATE render path from ``report.py`` (the inbox UI) so it doesn't collide with that
file's in-flight work; once stable it can be promoted to ``/``.

Design tokens mirror ``report.py`` so it looks native. The JS implements:
  * B1 — a command bus (``dispatch``) wired to BOTH keyboard (J/K/E/A/Z/?) and on-row buttons.
  * B2 — optimistic mutations with a global undo stack (``Z``); a failed POST auto-reverts + toasts.
NEVER sends mail; the only writes are thread owner/handled (the precious workspace overlay).
"""

from __future__ import annotations

import json
from typing import Any


def _embed(obj: Any) -> str:
    """JSON for safe inlining in a <script> (escape ``</`` so a value can't close the tag)."""
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def build_fila_html(rows: list[dict[str, Any]], team: list[str] | None = None,
                    *, now_iso: str = "") -> str:
    return (TEMPLATE
            .replace("__ROWS__", _embed(rows))
            .replace("__TEAM__", _embed(list(team or [])))
            .replace("__NOW__", _embed(now_iso)))


TEMPLATE = r"""<!doctype html>
<html lang="pt">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Fila · email-2-data</title>
<style>
  :root{--bg:#eef0f3;--card:#fff;--bd:#e3e6ea;--bd2:#eef0f3;--tx:#15181c;--mut:#6b7280;--mut2:#9aa1ab;
    --ac:#3358d4;--int:#0d9488;--ext:#64748b;--red:#cf3a3a;--amber:#b9791c;--green:#1f9d57;
    --shadow:0 1px 2px rgba(20,24,28,.05),0 1px 3px rgba(20,24,28,.04);}
  *{box-sizing:border-box} html,body{margin:0}
  body{font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:var(--tx);background:var(--bg)}
  header{background:var(--card);border-bottom:1px solid var(--bd);padding:15px 26px;position:sticky;top:0;z-index:20;box-shadow:var(--shadow)}
  .htop{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
  h1{margin:0;font-size:17px;font-weight:680;letter-spacing:-.01em}
  .sub{color:var(--mut);font-size:12.5px}
  .nav{color:var(--ac);text-decoration:none;font-weight:600;font-size:12.5px}
  .nav:hover{text-decoration:underline}
  .risk{margin-left:auto;font-size:12.5px;font-weight:680;font-variant-numeric:tabular-nums;
    color:var(--red);background:#fbeaea;border:1px solid #f3c9c9;border-radius:20px;padding:3px 12px}
  .risk.clear{color:var(--green);background:#e7f6ee;border-color:#bfe6cf}
  .wrap{max-width:980px;margin:0 auto;padding:18px 26px 60px}
  .count{color:var(--mut);font-size:12px;margin:2px 0 12px}
  .list{background:var(--card);border:1px solid var(--bd);border-radius:14px;overflow:hidden;box-shadow:var(--shadow)}
  .row{display:flex;align-items:center;gap:12px;padding:12px 15px;border-bottom:1px solid var(--bd2);border-left:3px solid transparent;cursor:pointer}
  .row:last-child{border-bottom:none}
  .row:hover{background:#f8f9fb}
  .row.on{background:#eef2ff;border-left-color:var(--ac)}
  .cp{flex:0 0 auto;display:inline-block;padding:2px 9px;border-radius:20px;font-size:10px;font-weight:700;letter-spacing:.03em;min-width:62px;text-align:center}
  .cp.CLIENT{background:#e7f6ee;color:var(--green)} .cp.LEAD{background:#efeafb;color:#6b4fd1}
  .cp.SUPPLIER{background:#e8eefc;color:var(--ac)} .cp.INTERNAL,.cp.OTHER,.cp.BULK{background:#eef0f3;color:var(--mut)}
  .rmain{flex:1;min-width:0}
  .subj{font-weight:620;font-size:13.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .rmeta{color:var(--mut);font-size:11.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px}
  .clock{flex:0 0 auto;font-size:12px;font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap}
  .clock::before{content:"";display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;vertical-align:middle;background:currentColor}
  .clock.red{color:var(--red)} .clock.amber{color:var(--amber)} .clock.green{color:var(--green)} .clock.none{color:var(--mut2)}
  .owner{flex:0 0 auto;font-size:12px;color:var(--int);background:#f0fdfa;border:1px solid #bfe6e0;border-radius:20px;padding:2px 10px;cursor:pointer;white-space:nowrap}
  .owner .nodon{color:var(--mut2)} .owner.empty{background:#f6f7f9;border-color:var(--bd);color:var(--mut2)}
  .acts{flex:0 0 auto;display:flex;gap:5px}
  .acts button{border:1px solid var(--bd);background:#fff;border-radius:8px;width:30px;height:30px;cursor:pointer;font-size:14px;color:var(--mut);line-height:1}
  .acts button:hover{border-color:var(--ac);color:var(--ac);background:#f1f5ff}
  .zero{text-align:center;padding:70px 20px;color:var(--green);font-size:18px;font-weight:650}
  .zero .s{display:block;color:var(--mut2);font-size:13px;font-weight:400;margin-top:8px}
  .hint{margin-top:14px;color:var(--mut2);font-size:11.5px;text-align:center}
  .hint b{color:var(--mut);font-weight:680}
  .hidden{display:none!important}  /* utility: must beat .overlay's display:flex regardless of source order */
  .toast{position:fixed;bottom:22px;left:50%;transform:translateX(-50%);background:var(--tx);color:#fff;
    padding:9px 16px;border-radius:9px;font-size:13px;box-shadow:var(--shadow);z-index:60}
  .menu{position:absolute;background:#fff;border:1px solid var(--bd);border-radius:10px;box-shadow:0 4px 16px rgba(20,24,28,.14);z-index:50;min-width:170px;overflow:hidden;padding:4px}
  .menu .mi{padding:7px 11px;border-radius:7px;cursor:pointer;font-size:13px} .menu .mi:hover{background:#eef2ff;color:var(--ac)}
  .overlay{position:fixed;inset:0;background:rgba(20,24,28,.32);display:flex;align-items:center;justify-content:center;z-index:70}
  .card{background:#fff;border-radius:14px;padding:22px 26px;box-shadow:var(--shadow);max-width:340px}
  .card h3{margin:0 0 12px;font-size:14px} .card kbd{background:var(--bg);border:1px solid var(--bd);border-radius:5px;padding:1px 6px;font-family:ui-monospace,monospace;font-size:12px}
  .card .kr{display:flex;justify-content:space-between;padding:5px 0;font-size:13px;border-top:1px solid var(--bd2)} .card .kr:first-of-type{border-top:none}
</style>
</head>
<body>
<header>
  <div class="htop">
    <h1>Fila</h1><span class="sub">resposta · por risco</span>
    <a class="nav" href="/">← workspace</a>
    <span id="risk" class="risk"></span>
  </div>
</header>
<div class="wrap">
  <div id="count" class="count"></div>
  <div id="list" class="list"></div>
  <div id="zero" class="zero hidden">✓ Tudo tratado<span class="s">nada está a cair · 0 em risco</span></div>
  <div class="hint"><b>J/K</b> mover · <b>E</b> tratado · <b>A</b> dono · <b>Z</b> desfazer · <b>?</b> ajuda</div>
</div>
<div id="toast" class="toast hidden"></div>
<div id="menu" class="menu hidden"></div>
<div id="help" class="overlay hidden"><div class="card">
  <h3>Atalhos</h3>
  <div class="kr"><span>Mover</span><span><kbd>J</kbd> <kbd>K</kbd></span></div>
  <div class="kr"><span>Marcar tratado</span><kbd>E</kbd></div>
  <div class="kr"><span>Atribuir dono</span><kbd>A</kbd></div>
  <div class="kr"><span>Desfazer</span><kbd>Z</kbd></div>
  <div class="kr"><span>Fechar</span><kbd>Esc</kbd></div>
</div></div>
<script>
const ROWS = __ROWS__, TEAM = __TEAM__, NOW = __NOW__;
let rows = ROWS.slice(), focus = 0;
const undo = [];
const $ = s => document.querySelector(s);
const esc = s => String(s==null?'':s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function riskCount(){ return rows.filter(r => r.clock.band==='red' || r.clock.band==='amber').length; }

function render(){
  const n = riskCount();
  const risk = $('#risk');
  risk.textContent = rows.length ? (n + ' em risco') : '0 em risco';
  risk.classList.toggle('clear', n===0);
  $('#count').textContent = rows.length ? (rows.length + (rows.length===1 ? ' thread' : ' threads')) : '';
  $('#zero').classList.toggle('hidden', rows.length>0);
  if(focus >= rows.length) focus = Math.max(0, rows.length-1);
  $('#list').innerHTML = rows.map((r,i) => {
    const c = r.clock || {};
    const owner = r.owner ? ('@'+esc(r.owner)) : '<span class="nodon">sem dono</span>';
    const meta = [esc(r.contact||''),
                  r.n_messages>1 ? (r.n_messages+' msgs') : '',
                  r.has_attachment ? '📎' : '',
                  r.purpose ? esc(String(r.purpose).toLowerCase()) : ''].filter(Boolean).join(' · ');
    return '<div class="row'+(i===focus?' on':'')+'" data-i="'+i+'">'
      + '<span class="cp '+esc(r.counterparty||'OTHER')+'">'+esc(r.counterparty||'—')+'</span>'
      + '<div class="rmain"><div class="subj">'+esc(r.subject||'(sem assunto)')+'</div><div class="rmeta">'+meta+'</div></div>'
      + '<span class="clock '+esc(c.band||'none')+'">'+esc(c.label||'')+'</span>'
      + '<span class="owner'+(r.owner?'':' empty')+'" data-act="owner">'+owner+'</span>'
      + '<div class="acts"><button data-act="handled" title="tratado (E)">✓</button>'
      + '<button data-act="owner" title="dono (A)">@</button></div></div>';
  }).join('');
}

function toast(msg){ const t=$('#toast'); t.textContent=msg; t.classList.remove('hidden');
  clearTimeout(t._h); t._h=setTimeout(()=>t.classList.add('hidden'), 2600); }

async function post(url, body){
  const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  if(!r.ok) throw new Error('HTTP '+r.status);
  return r.json();
}

// B2 — optimistic mutations + undo. Each action updates the view first, then persists; a failed
// POST reverts the view and drops the undo entry.
function handle(i){
  const r = rows[i]; if(!r) return;
  const at = i;
  rows.splice(i,1); undo.push({type:'handled', row:r, at}); render();
  post('/api/thread/handled', {thread_root:r.thread_root, handled:true})
    .catch(() => { rows.splice(Math.min(at, rows.length), 0, r); undo.pop(); render(); toast('falhou — revertido'); });
}
function setOwner(i, owner){
  const r = rows[i]; if(!r) return;
  const prev = r.owner || '';
  r.owner = owner; undo.push({type:'owner', root:r.thread_root, prev}); render();
  post('/api/thread/owner', {thread_root:r.thread_root, owner})
    .catch(() => { r.owner = prev; undo.pop(); render(); toast('falhou — revertido'); });
}
function doUndo(){
  const u = undo.pop();
  if(!u){ toast('nada para desfazer'); return; }
  if(u.type==='handled'){
    rows.splice(Math.min(u.at, rows.length), 0, u.row); render();
    post('/api/thread/handled', {thread_root:u.row.thread_root, handled:false}).catch(()=>toast('falhou'));
  } else if(u.type==='owner'){
    const r = rows.find(x => x.thread_root===u.root); if(r){ r.owner = u.prev; render(); }
    post('/api/thread/owner', {thread_root:u.root, owner:u.prev}).catch(()=>toast('falhou'));
  }
  toast('desfeito');
}

function ownerMenu(i){
  const m = $('#menu');
  m.innerHTML = TEAM.map(n => '<div class="mi" data-n="'+esc(n)+'">@'+esc(n)+'</div>').join('')
              + '<div class="mi" data-n="">sem dono</div>';
  m.dataset.i = i; m.classList.remove('hidden');
  const row = document.querySelector('.row[data-i="'+i+'"]');
  if(row){ const b = row.getBoundingClientRect();
    m.style.top = (window.scrollY + b.bottom + 4) + 'px';
    m.style.left = (window.scrollX + Math.max(8, b.right - 180)) + 'px'; }
}

// B1 — one command bus, reached from the keyboard AND the on-row buttons (a third entry, ⌘K, is a
// later deliverable that would call the same dispatch).
function dispatch(action, i){
  if(action==='handled') handle(i);
  else if(action==='owner') ownerMenu(i);
  else if(action==='undo') doUndo();
}

function scrollFocus(){ const r = document.querySelector('.row.on'); if(r) r.scrollIntoView({block:'nearest'}); }

document.addEventListener('keydown', e => {
  const tag = (e.target.tagName||'').toLowerCase();
  if(tag==='input' || tag==='textarea'){ if(e.key==='Escape') e.target.blur(); return; }
  if(e.key==='?'){ $('#help').classList.toggle('hidden'); return; }
  if(e.key==='Escape'){ $('#help').classList.add('hidden'); $('#menu').classList.add('hidden'); return; }
  if(!$('#help').classList.contains('hidden')) return;  // help modal open — keys inert behind it
  if(!$('#menu').classList.contains('hidden')) return;  // menu open — let its own clicks drive
  if(!rows.length) return;
  if(e.key==='j' || e.key==='ArrowDown'){ focus = Math.min(rows.length-1, focus+1); render(); scrollFocus(); e.preventDefault(); }
  else if(e.key==='k' || e.key==='ArrowUp'){ focus = Math.max(0, focus-1); render(); scrollFocus(); e.preventDefault(); }
  else if(e.key==='e' || e.key==='E') dispatch('handled', focus);
  else if(e.key==='a' || e.key==='A') dispatch('owner', focus);
  else if(e.key==='z' || e.key==='Z') dispatch('undo', focus);
});

$('#list').addEventListener('click', e => {
  const row = e.target.closest('.row'); if(!row) return;
  const i = parseInt(row.dataset.i, 10);
  const act = e.target.closest('[data-act]');
  focus = i;
  if(act){ dispatch(act.dataset.act, i); e.stopPropagation(); }
  else render();
});
$('#menu').addEventListener('click', e => {
  const mi = e.target.closest('.mi'); if(!mi) return;
  const m = $('#menu'); setOwner(parseInt(m.dataset.i, 10), mi.dataset.n); m.classList.add('hidden');
});
$('#help').addEventListener('click', e => { if(e.target.id === 'help') $('#help').classList.add('hidden'); });
document.addEventListener('click', e => {
  if(!e.target.closest('#menu') && !e.target.closest('[data-act="owner"]')) $('#menu').classList.add('hidden');
});

render();
</script>
</body>
</html>
"""
