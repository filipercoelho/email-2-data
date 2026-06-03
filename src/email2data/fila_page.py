"""Fila page — the response cockpit's hero screen, served at ``/`` and ``/fila`` (A3: it is home).

A self-contained HTML page (component-kit CSS + vanilla JS) rendered from ``cockpit.build_fila`` rows.
A SEPARATE render path from ``report.py`` (the inbox, now at ``/inbox``) so it doesn't collide with that
file. NEVER sends mail; the only writes are thread owner/handled (the precious workspace overlay).

Sections below are labelled so the file stays navigable as it grows:
  CSS  — tokens · layout · component kit · B5 trust · B4 motion · B6 density/a11y · B3 palette
  JS   — strings(i18n) · state · render · command bus · mutations(+B4 anim) · B3 palette · B6 density
The JS is pure presentation: every datum (clock, trust, owner) is computed/persisted server-side and
unit-tested in cockpit/crm/webapp; nothing business-critical lives only here.
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
  /* ── tokens (shared with report.py) ─────────────────────────────────────── */
  :root{--bg:#eef0f3;--card:#fff;--bd:#e3e6ea;--bd2:#eef0f3;--tx:#15181c;--mut:#6b7280;--mut2:#9aa1ab;
    --ac:#3358d4;--int:#0d9488;--ext:#64748b;--red:#cf3a3a;--amber:#b9791c;--green:#1f9d57;--purple:#6b4fd1;
    --shadow:0 1px 2px rgba(20,24,28,.05),0 1px 3px rgba(20,24,28,.04);
    --rpad:12px;--rfont:13.5px;}
  body.compact{--rpad:7px;--rfont:13px}
  *{box-sizing:border-box} html,body{margin:0}
  body{font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:var(--tx);background:var(--bg)}
  .sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}
  :focus-visible{outline:2px solid var(--ac);outline-offset:2px;border-radius:6px}
  /* ── layout / header ─────────────────────────────────────────────────────── */
  header{background:var(--card);border-bottom:1px solid var(--bd);padding:15px 26px;position:sticky;top:0;z-index:20;box-shadow:var(--shadow)}
  .htop{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
  h1{margin:0;font-size:17px;font-weight:680;letter-spacing:-.01em}
  .sub{color:var(--mut);font-size:12.5px}
  .nav,.hbtn{color:var(--ac);text-decoration:none;font-weight:600;font-size:12.5px;background:none;border:0;cursor:pointer;padding:4px 6px;border-radius:7px}
  .hbtn{border:1px solid var(--bd);color:var(--mut)} .hbtn:hover{border-color:var(--ac);color:var(--ac)}
  .nav:hover{text-decoration:underline}
  .grow{margin-left:auto}
  .risk{font-size:12.5px;font-weight:680;font-variant-numeric:tabular-nums;color:var(--red);background:#fbeaea;border:1px solid #f3c9c9;border-radius:20px;padding:3px 12px}
  .risk.clear{color:var(--green);background:#e7f6ee;border-color:#bfe6cf}
  .risk.pulse{animation:pop .35s ease}
  .wrap{max-width:1000px;margin:0 auto;padding:16px 26px 60px}
  .bar{display:flex;align-items:center;gap:10px;color:var(--mut);font-size:12px;margin:2px 2px 12px}
  .fchip{display:inline-flex;align-items:center;gap:6px;background:#eef2ff;border:1px solid #cdd7ff;color:var(--ac);border-radius:20px;padding:2px 10px;font-weight:600;cursor:pointer}
  .cmdk{margin-left:auto;color:var(--mut2)}
  .cmdk kbd{background:var(--bg);border:1px solid var(--bd);border-radius:5px;padding:0 5px;font-family:ui-monospace,monospace}
  /* ── component kit: row · counterparty · clock · owner · acts ─────────────── */
  .list{background:var(--card);border:1px solid var(--bd);border-radius:14px;overflow:hidden;box-shadow:var(--shadow)}
  .row{display:flex;align-items:center;gap:12px;padding:var(--rpad) 15px;border-bottom:1px solid var(--bd2);border-left:3px solid transparent;cursor:pointer;transition:opacity .16s ease,transform .16s ease,background .12s}
  .row:last-child{border-bottom:none}
  .row:hover{background:#f8f9fb}
  .row.on{background:#eef2ff;border-left-color:var(--ac)}
  .row.leaving{opacity:0;transform:translateX(10px)}
  .cp{flex:0 0 auto;display:inline-block;padding:2px 9px;border-radius:20px;font-size:10px;font-weight:700;letter-spacing:.03em;min-width:62px;text-align:center}
  .cp.CLIENT{background:#e7f6ee;color:var(--green)} .cp.LEAD{background:#efeafb;color:var(--purple)}
  .cp.SUPPLIER{background:#e8eefc;color:var(--ac)} .cp.INTERNAL,.cp.OTHER,.cp.BULK{background:#eef0f3;color:var(--mut)}
  .rmain{flex:1;min-width:0}
  .subj{font-weight:620;font-size:var(--rfont);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .rmeta{color:var(--mut);font-size:11.5px;margin-top:2px;display:flex;align-items:center;gap:7px;flex-wrap:wrap}
  .why{margin-top:6px;font-size:12px;color:#4a4326;background:#fffdf3;border:1px solid #f0e6c0;border-radius:8px;padding:6px 10px;line-height:1.5;white-space:normal}
  .clock{flex:0 0 auto;font-size:12px;font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap}
  .clock .d{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;vertical-align:middle;background:currentColor}
  .clock.red{color:var(--red)} .clock.amber{color:var(--amber)} .clock.green{color:var(--green)} .clock.none{color:var(--mut2)}
  .clock.red .d{animation:beat 2s ease-in-out infinite}
  .owner{flex:0 0 auto;font-size:12px;color:var(--int);background:#f0fdfa;border:1px solid #bfe6e0;border-radius:20px;padding:2px 10px;cursor:pointer;white-space:nowrap;border-width:1px}
  .owner.empty{background:#f6f7f9;border-color:var(--bd);color:var(--mut2)}
  .acts{flex:0 0 auto;display:flex;gap:5px}
  .acts button{border:1px solid var(--bd);background:#fff;border-radius:8px;width:30px;height:30px;cursor:pointer;font-size:14px;color:var(--mut);line-height:1}
  .acts button:hover{border-color:var(--ac);color:var(--ac);background:#f1f5ff}
  /* ── B5 trust grammar: proposed (AI, dashed) vs committed (human, solid) ──── */
  .trust{font-size:10.5px;font-weight:650;border-radius:20px;padding:1px 8px;cursor:pointer;font-variant-numeric:tabular-nums;background:#fff}
  .trust.proposed{border:1px dashed var(--mut2);color:var(--mut)}
  .trust.committed{border:1px solid var(--int);color:var(--int);background:#f0fdfa}
  .trust.committed::before{content:"✓ ";font-weight:700}
  /* ── zero / hint ──────────────────────────────────────────────────────────── */
  .zero{text-align:center;padding:70px 20px;color:var(--green);font-size:18px;font-weight:650;animation:zin .3s ease}
  .zero .s{display:block;color:var(--mut2);font-size:13px;font-weight:400;margin-top:8px}
  .hint{margin-top:14px;color:var(--mut2);font-size:11.5px;text-align:center}
  .hint b{color:var(--mut);font-weight:680}
  .hidden{display:none!important}
  .toast{position:fixed;bottom:22px;left:50%;transform:translateX(-50%);background:var(--tx);color:#fff;padding:9px 16px;border-radius:9px;font-size:13px;box-shadow:var(--shadow);z-index:80}
  /* ── menus / overlays / palette ─────────────────────────────────────────── */
  .menu{position:absolute;background:#fff;border:1px solid var(--bd);border-radius:10px;box-shadow:0 4px 16px rgba(20,24,28,.14);z-index:60;min-width:170px;overflow:hidden;padding:4px}
  .menu .mi{padding:7px 11px;border-radius:7px;cursor:pointer;font-size:13px} .menu .mi:hover,.menu .mi.on{background:#eef2ff;color:var(--ac)}
  .overlay{position:fixed;inset:0;background:rgba(20,24,28,.32);display:flex;align-items:flex-start;justify-content:center;z-index:70}
  .help{align-items:center}
  .card{background:#fff;border-radius:14px;padding:22px 26px;box-shadow:var(--shadow);max-width:340px}
  .card h3{margin:0 0 12px;font-size:14px} .card kbd{background:var(--bg);border:1px solid var(--bd);border-radius:5px;padding:1px 6px;font-family:ui-monospace,monospace;font-size:12px}
  .card .kr{display:flex;justify-content:space-between;gap:24px;padding:5px 0;font-size:13px;border-top:1px solid var(--bd2)} .card .kr:first-of-type{border-top:none}
  .pcard{background:#fff;border-radius:14px;box-shadow:0 10px 40px rgba(20,24,28,.22);width:min(560px,92vw);margin-top:12vh;overflow:hidden}
  #pq{width:100%;border:0;border-bottom:1px solid var(--bd);padding:15px 18px;font-size:15px;outline:none}
  #presults{max-height:50vh;overflow:auto;padding:6px}
  .pi{display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:9px;cursor:pointer}
  .pi.on{background:#eef2ff} .pi .k{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:var(--mut2);min-width:64px}
  .pi .pl{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13.5px}
  .pi .ps{color:var(--mut2);font-size:11.5px}
  /* ── B4 motion keyframes + reduced-motion ─────────────────────────────────── */
  @keyframes zin{from{opacity:0;transform:scale(.97)}to{opacity:1;transform:none}}
  @keyframes pop{0%{transform:scale(1)}40%{transform:scale(1.14)}100%{transform:scale(1)}}
  @keyframes beat{0%,100%{opacity:1}50%{opacity:.45}}
  @media (prefers-reduced-motion: reduce){*{animation:none!important;transition:none!important}}
</style>
</head>
<body>
<header>
  <div class="htop">
    <h1>Fila</h1><span class="sub">resposta · por risco</span>
    <a class="nav" href="/inbox" title="abrir o inbox clássico">inbox →</a>
    <span class="grow"></span>
    <button class="hbtn" id="syncbtn" title="Sincronizar (fetch + triage)">Sincronizar</button>
    <button class="hbtn" id="denbtn" title="Alternar densidade">densidade</button>
    <span id="risk" class="risk" aria-live="polite"></span>
  </div>
</header>
<div class="wrap">
  <div class="bar">
    <span id="count"></span>
    <span id="fchip" class="fchip hidden"></span>
    <span class="cmdk"><kbd>⌘K</kbd> comandos</span>
  </div>
  <div id="list" class="list" role="list" aria-label="Fila de resposta"></div>
  <div id="zero" class="zero hidden">✓ Tudo tratado<span class="s">nada está a cair · 0 em risco</span></div>
  <div class="hint"><b>J/K</b> mover · <b>E</b> tratado · <b>A</b> dono · <b>Z</b> desfazer · <b>⌘K</b> comandos · <b>?</b> ajuda</div>
</div>
<div id="status" class="sr" aria-live="polite" aria-atomic="true"></div>
<div id="toast" class="toast hidden" role="status"></div>
<div id="menu" class="menu hidden"></div>
<div id="palette" class="overlay hidden"><div class="pcard" role="dialog" aria-label="Comandos">
  <input id="pq" placeholder="comandos, contrapartes, assuntos…" autocomplete="off" aria-label="Procurar"/>
  <div id="presults" role="listbox"></div>
</div></div>
<div id="help" class="overlay help hidden"><div class="card" role="dialog" aria-label="Atalhos">
  <h3>Atalhos</h3>
  <div class="kr"><span>Mover</span><span><kbd>J</kbd> <kbd>K</kbd></span></div>
  <div class="kr"><span>Marcar tratado</span><kbd>E</kbd></div>
  <div class="kr"><span>Atribuir dono</span><kbd>A</kbd></div>
  <div class="kr"><span>Desfazer</span><kbd>Z</kbd></div>
  <div class="kr"><span>Comandos</span><kbd>⌘K</kbd></div>
  <div class="kr"><span>Fechar</span><kbd>Esc</kbd></div>
</div></div>
<script>
const ROWS = __ROWS__, TEAM = __TEAM__, NOW = __NOW__;

// ── strings (B6 i18n: PT now; a future EN is a dict swap) ─────────────────────────────────────────
const T = {
  risk:n=>n+' em risco', threads:n=>n+(n===1?' thread':' threads'),
  semDados:'fila vazia', tratado:'tratado', desfeito:'desfeito',
  nadaDesfazer:'nada para desfazer', revertido:'falhou — revertido',
  sincronizando:'a sincronizar…', sincronizado:'sincronizado', syncEmCurso:'sync já em curso', syncFalhou:'sync falhou',
  filtrado:c=>'filtrado: '+c, atualizado:n=>n+' por tratar',
  actSync:'Sincronizar agora', actUndo:'Desfazer última ação', actDensity:'Alternar densidade', actInbox:'Abrir inbox',
};
const KIND = {acao:'ação', contraparte:'contraparte', assunto:'assunto'};

// ── state ─────────────────────────────────────────────────────────────────────────────────────────
let rows = ROWS.slice(), focus = 0, filter = null;
const undo = [];
let pitems = [], pfocus = 0, _prevRisk = null;

const $ = s => document.querySelector(s);
const esc = s => String(s==null?'':s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const reduceMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
function announce(m){ $('#status').textContent = m; }
function decidedShort(d){ d=(d||'').toLowerCase(); if(!d) return ''; if(d.indexOf('tier0')===0) return 'regra';
  if(d.indexOf('gemini')>-1) return 'Gemini'; if(d.indexOf('claude')>-1) return 'Claude'; if(d.indexOf('tier1')===0) return 'IA'; return d.split(':').pop(); }

function view(){ return filter ? rows.filter(r => (r.counterparty||'') === filter) : rows; }
function riskCount(){ return view().filter(r => ['red','amber'].includes((r.clock||{}).band)).length; }

// ── render ──────────────────────────────────────────────────────────────────────────────────────
function render(){
  const v = view(), n = riskCount();
  const risk = $('#risk');
  risk.textContent = v.length ? T.risk(n) : '0 em risco';
  risk.classList.toggle('clear', n === 0);
  if(_prevRisk !== null && _prevRisk !== n && !reduceMotion()){ risk.classList.remove('pulse'); void risk.offsetWidth; risk.classList.add('pulse'); }
  _prevRisk = n;
  $('#count').textContent = v.length ? T.threads(v.length) : T.semDados;
  const fc = $('#fchip'); fc.classList.toggle('hidden', !filter);
  if(filter){ fc.innerHTML = esc(T.filtrado(filter)) + ' ✕'; }
  $('#zero').classList.toggle('hidden', v.length > 0);
  if(focus >= v.length) focus = Math.max(0, v.length - 1);
  announce(v.length ? T.atualizado(v.length) : 'Tudo tratado');

  $('#list').innerHTML = v.map((r,i) => {
    const c = r.clock || {}, tr = r.trust || {};
    const owner = r.owner ? ('@'+esc(r.owner)) : 'sem dono';
    const decided = decidedShort(tr.decided_by);
    const conf = tr.confidence ? (' · '+Math.round(tr.confidence*100)+'%') : '';
    const trust = decided
      ? '<button class="trust '+(tr.committed?'committed':'proposed')+'" data-act="why" '
        + 'aria-label="origem da classificação — ver porquê" title="Porquê?">'+esc(decided)+conf+'</button>'
      : '';
    const meta = [esc(r.contact||''), r.n_messages>1?(r.n_messages+' msgs'):'', r.has_attachment?'📎':'',
                  r.purpose?esc(String(r.purpose).toLowerCase()):''].filter(Boolean).join(' · ');
    const why = (r._why && tr.reason) ? '<div class="why">'+esc(tr.reason)+'</div>' : '';
    return '<div class="row'+(i===focus?' on':'')+'" data-i="'+i+'" role="listitem"'+(i===focus?' aria-current="true"':'')+'>'
      + '<span class="cp '+esc(r.counterparty||'OTHER')+'">'+esc(r.counterparty||'—')+'</span>'
      + '<div class="rmain"><div class="subj">'+esc(r.subject||'(sem assunto)')+'</div>'
      + '<div class="rmeta">'+meta+(trust?(' '+trust):'')+'</div>'+why+'</div>'
      + '<span class="clock '+esc(c.band||'none')+'"><span class="d" aria-hidden="true"></span>'+esc(c.label||'')+'</span>'
      + '<button class="owner'+(r.owner?'':' empty')+'" data-act="owner" aria-label="atribuir dono">'+owner+'</button>'
      + '<div class="acts"><button data-act="handled" aria-label="marcar tratado" title="tratado (E)">✓</button>'
      + '<button data-act="owner" aria-label="atribuir dono" title="dono (A)">@</button></div></div>';
  }).join('');
}

function toast(m){ const t=$('#toast'); t.textContent=m; t.classList.remove('hidden'); clearTimeout(t._h); t._h=setTimeout(()=>t.classList.add('hidden'), 2600); }
async function post(url, body){ const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)}); if(!r.ok) throw new Error('HTTP '+r.status); return r.json(); }

// ── command bus — one action, reached from keyboard, on-row buttons, and the palette (B1) ─────────
function dispatch(action, i){
  if(action==='handled') handle(i);
  else if(action==='owner') ownerMenu(i);
  else if(action==='why') toggleWhy(i);
  else if(action==='undo') doUndo();
}

// ── mutations: optimistic + undo (B2) with a B4 slide-out ─────────────────────────────────────────
function handle(i){
  const v = view(), r = v[i]; if(!r) return;
  const at = rows.indexOf(r);
  const commit = () => {
    rows.splice(at, 1); undo.push({type:'handled', row:r, at}); announce(T.tratado); render();
    post('/api/thread/handled', {thread_root:r.thread_root, handled:true})
      .catch(() => { rows.splice(Math.min(at, rows.length), 0, r); undo.pop(); render(); toast(T.revertido); });
  };
  const el = document.querySelector('.row[data-i="'+i+'"]');
  if(el && !reduceMotion()){ let done=false; const go=()=>{ if(done) return; done=true; commit(); };
    el.classList.add('leaving'); el.addEventListener('transitionend', go, {once:true}); setTimeout(go, 240); }
  else commit();
}
function setOwner(i, owner){
  const r = view()[i]; if(!r) return;
  const prev = r.owner || '';
  r.owner = owner; undo.push({type:'owner', root:r.thread_root, prev}); render();
  post('/api/thread/owner', {thread_root:r.thread_root, owner})
    .catch(() => { r.owner = prev; undo.pop(); render(); toast(T.revertido); });
}
function doUndo(){
  const u = undo.pop();
  if(!u){ toast(T.nadaDesfazer); return; }
  if(u.type==='handled'){ rows.splice(Math.min(u.at, rows.length), 0, u.row); render();
    post('/api/thread/handled', {thread_root:u.row.thread_root, handled:false}).catch(()=>toast(T.revertido)); }
  else if(u.type==='owner'){ const r = rows.find(x => x.thread_root===u.root); if(r){ r.owner = u.prev; render(); }
    post('/api/thread/owner', {thread_root:u.root, owner:u.prev}).catch(()=>toast(T.revertido)); }
  toast(T.desfeito); announce(T.desfeito);
}
function toggleWhy(i){ const r = view()[i]; if(r){ r._why = !r._why; render(); } }

function ownerMenu(i){
  const m = $('#menu');
  m.innerHTML = TEAM.map(nm => '<div class="mi" data-n="'+esc(nm)+'">@'+esc(nm)+'</div>').join('')
              + '<div class="mi" data-n="">sem dono</div>';
  m.dataset.i = i; m.classList.remove('hidden');
  const row = document.querySelector('.row[data-i="'+i+'"]');
  if(row){ const b = row.getBoundingClientRect();
    m.style.top = (window.scrollY + b.bottom + 4) + 'px';
    m.style.left = (window.scrollX + Math.max(8, b.right - 180)) + 'px'; }
}

// ── filter / density / sync / refresh ─────────────────────────────────────────────────────────────
function setFilter(cp){ filter = cp; focus = 0; render(); }
function clearFilter(){ filter = null; render(); }
function toggleDensity(){ const c = document.body.classList.toggle('compact'); try{ localStorage.setItem('fila-density', c?'compact':''); }catch(e){} }
async function refresh(){ try{ const d = await (await fetch('/api/fila')).json(); rows = d.rows || []; focus = 0; render(); }catch(e){ toast(T.revertido); } }
async function sync(){ toast(T.sincronizando);
  try{ const r = await fetch('/api/sync', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
    if(r.status===409){ toast(T.syncEmCurso); return; } if(!r.ok){ toast(T.syncFalhou); return; }
    await refresh(); toast(T.sincronizado); }catch(e){ toast(T.syncFalhou); } }

function scrollFocus(){ const r = document.querySelector('.row.on'); if(r) r.scrollIntoView({block:'nearest'}); }
function focusThread(root){ const i = view().findIndex(r => r.thread_root === root); if(i>=0){ focus = i; render(); scrollFocus(); } }

// ── B3 command palette ─────────────────────────────────────────────────────────────────────────
function paletteItems(q){
  q = (q||'').toLowerCase().trim();
  const items = [
    {kind:KIND.acao, label:T.actSync, run:sync},
    {kind:KIND.acao, label:T.actUndo, run:doUndo},
    {kind:KIND.acao, label:T.actDensity, run:toggleDensity},
    {kind:KIND.acao, label:T.actInbox, run:()=>{ location.href='/inbox'; }},
  ];
  [...new Set(rows.map(r => r.counterparty).filter(Boolean))].forEach(cp =>
    items.push({kind:KIND.contraparte, label:cp, run:()=>setFilter(cp)}));
  view().forEach(r => items.push({kind:KIND.assunto, label:r.subject||'(sem assunto)',
    sub:(r.counterparty||'')+' · '+(r.contact||''), run:()=>focusThread(r.thread_root)}));
  return q ? items.filter(it => (it.label+' '+(it.sub||'')+' '+it.kind).toLowerCase().includes(q)) : items;
}
function renderPalette(){
  pfocus = Math.max(0, Math.min(pfocus, pitems.length-1));
  $('#presults').innerHTML = pitems.slice(0, 40).map((it,i) =>
    '<div class="pi'+(i===pfocus?' on':'')+'" data-i="'+i+'" role="option"><span class="k">'+esc(it.kind)+'</span>'
    + '<span class="pl">'+esc(it.label)+(it.sub?(' <span class="ps">'+esc(it.sub)+'</span>'):'')+'</span></div>').join('')
    || '<div class="pi"><span class="pl ps">sem resultados</span></div>';
}
function openPalette(){ pitems = paletteItems(''); pfocus = 0; $('#palette').classList.remove('hidden'); renderPalette();
  const q = $('#pq'); q.value = ''; q.focus(); }
function closePalette(){ $('#palette').classList.add('hidden'); }
function runPalette(i){ const it = pitems[i]; if(!it) return; closePalette(); it.run(); }

// ── events ────────────────────────────────────────────────────────────────────────────────────────
document.addEventListener('keydown', e => {
  // command palette: ⌘K / Ctrl-K from anywhere
  if((e.metaKey||e.ctrlKey) && (e.key==='k'||e.key==='K')){ e.preventDefault(); $('#palette').classList.contains('hidden') ? openPalette() : closePalette(); return; }
  if(!$('#palette').classList.contains('hidden')){
    if(e.key==='Escape'){ closePalette(); }
    else if(e.key==='ArrowDown'){ pfocus=Math.min(pitems.length-1,pfocus+1); renderPalette(); e.preventDefault(); }
    else if(e.key==='ArrowUp'){ pfocus=Math.max(0,pfocus-1); renderPalette(); e.preventDefault(); }
    else if(e.key==='Enter'){ runPalette(pfocus); e.preventDefault(); }
    return;
  }
  const tag = (e.target.tagName||'').toLowerCase();
  if(tag==='input' || tag==='textarea'){ if(e.key==='Escape') e.target.blur(); return; }
  if(e.key==='/'){ openPalette(); e.preventDefault(); return; }
  if(e.key==='?'){ $('#help').classList.toggle('hidden'); return; }
  if(e.key==='Escape'){ $('#help').classList.add('hidden'); $('#menu').classList.add('hidden'); if(filter) clearFilter(); return; }
  if(!$('#help').classList.contains('hidden')) return;   // help modal open — keys inert behind it
  if(!$('#menu').classList.contains('hidden')) return;    // owner menu open — let its clicks drive
  const v = view(); if(!v.length) return;
  if(e.key==='j' || e.key==='ArrowDown'){ focus=Math.min(v.length-1, focus+1); render(); scrollFocus(); e.preventDefault(); }
  else if(e.key==='k' || e.key==='ArrowUp'){ focus=Math.max(0, focus-1); render(); scrollFocus(); e.preventDefault(); }
  else if(e.key==='e' || e.key==='E') dispatch('handled', focus);
  else if(e.key==='a' || e.key==='A') dispatch('owner', focus);
  else if(e.key==='z' || e.key==='Z') dispatch('undo', focus);
});

$('#list').addEventListener('click', e => {
  const row = e.target.closest('.row'); if(!row) return;
  const i = parseInt(row.dataset.i, 10); focus = i;
  const act = e.target.closest('[data-act]');
  if(act){ dispatch(act.dataset.act, i); e.stopPropagation(); } else render();
});
$('#menu').addEventListener('click', e => { const mi = e.target.closest('.mi'); if(!mi) return;
  setOwner(parseInt($('#menu').dataset.i, 10), mi.dataset.n); $('#menu').classList.add('hidden'); });
$('#presults').addEventListener('click', e => { const pi = e.target.closest('.pi'); if(pi) runPalette(parseInt(pi.dataset.i,10)); });
$('#pq').addEventListener('input', e => { pitems = paletteItems(e.target.value); pfocus = 0; renderPalette(); });
$('#palette').addEventListener('click', e => { if(e.target.id==='palette') closePalette(); });
$('#help').addEventListener('click', e => { if(e.target.id==='help') $('#help').classList.add('hidden'); });
$('#fchip').addEventListener('click', clearFilter);
$('#syncbtn').addEventListener('click', sync);
$('#denbtn').addEventListener('click', toggleDensity);
document.addEventListener('click', e => { if(!e.target.closest('#menu') && !e.target.closest('[data-act="owner"]')) $('#menu').classList.add('hidden'); });

// ── init ──────────────────────────────────────────────────────────────────────────────────────────
try{ if(localStorage.getItem('fila-density')==='compact') document.body.classList.add('compact'); }catch(e){}
render();
</script>
</body>
</html>
"""
