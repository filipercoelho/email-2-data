"""Fila lens page — the response cockpit's hero screen (home at ``/`` and ``/fila``).

Thin wrapper over ``cockpit_ui.page()``: this module owns only the Fila-specific
data shaping and the lens JS (state + render + paletteItems + onKey).
NEVER sends mail; writes go through /api/thread/handled and /api/thread/owner.
"""

from __future__ import annotations

from typing import Any

from . import cockpit_ui

_LENS_JS = r"""
/* ── Fila lens state ────────────────────────────────────────────────── */
let rows = ROWS.slice(), focus = 0, filter = null;
let _prevRisk = null;

function view(){ return filter ? rows.filter(r=>(r.counterparty||'')===filter) : rows; }
function riskCount(){ return view().filter(r=>['red','amber'].includes((r.clock||{}).band)).length; }

/* ── render ────────────────────────────────────────────────────────── */
function render(){
  const v=view(), n=riskCount();
  const risk=$('#_risk');
  if(risk){
    risk.textContent=v.length?S.risk(n):'0 em risco';
    risk.classList.toggle('clear',n===0);
    if(_prevRisk!==null&&_prevRisk!==n&&!reduceMotion()){risk.classList.remove('pulse');void risk.offsetWidth;risk.classList.add('pulse');}
    _prevRisk=n;
  }
  const cnt=$('#_count'); if(cnt) cnt.textContent=v.length?S.threads(v.length):S.semDados;
  const fc=$('#_fchip'); if(fc){fc.classList.toggle('hidden',!filter);if(filter)fc.innerHTML=esc('filtrado: '+filter)+' ✕';}
  const zero=$('#_zero'); if(zero) zero.classList.toggle('hidden',v.length>0);
  announce(v.length?S.threads(v.length)+' por tratar':'Tudo tratado');
  if(focus>=v.length) focus=Math.max(0,v.length-1);

  const list=$('#_list');
  list.innerHTML=v.map((r,i)=>{
    const c=r.clock||{},tr=r.trust||{};
    const owner=r.owner?('@'+esc(r.owner)):'sem dono';
    const decided=decidedShort(tr.decided_by);
    const conf=tr.confidence?(' · '+Math.round(tr.confidence*100)+'%'):'';
    const trust=decided
      ?'<button class="trust '+(tr.committed?'committed':'proposed')+'" data-act="why" aria-label="ver porquê">'+esc(decided)+conf+'</button>'
      :'';
    const meta=[esc(r.contact||''),r.n_messages>1?(r.n_messages+' msgs'):'',r.has_attachment?'📎':'',
                r.purpose?esc(String(r.purpose).toLowerCase()):''].filter(Boolean).join(' · ');
    const why=(r._why&&tr.reason)?'<div class="why">'+esc(tr.reason)+'</div>':'';
    return '<div class="row'+(i===focus?' on':'')+'" data-i="'+i+'" role="listitem"'+(i===focus?' aria-current="true"':'')+' tabindex="0">'
      +'<span class="cp '+esc(r.counterparty||'OTHER')+'">'+esc(r.counterparty||'—')+'</span>'
      +'<div class="rmain"><div class="subj">'+esc(r.subject||'(sem assunto)')+'</div>'
      +'<div class="rmeta">'+meta+(trust?' '+trust:'')+'</div>'+why+'</div>'
      +'<span class="clock '+esc(c.band||'none')+'"><span class="d" aria-hidden="true"></span>'+esc(c.label||'')+'</span>'
      +'<button class="owner'+(r.owner?'':' empty')+'" data-act="owner" aria-label="atribuir dono">'+owner+'</button>'
      +'<div class="acts"><button data-act="handled" aria-label="marcar tratado" title="tratado (E)">✓</button>'
      +'<button data-act="owner" aria-label="atribuir dono" title="dono (A)">@</button></div></div>';
  }).join('');
}

/* ── mutations (optimistic + undo, B2) ──────────────────────────────── */
function handle(i){
  const v=view(),r=v[i]; if(!r) return;
  const at=rows.indexOf(r);
  const commit=()=>{
    rows.splice(at,1);
    undo.push({label:S.tratado,revert:()=>{rows.splice(Math.min(at,rows.length),0,r);render();post('/api/thread/handled',{thread_root:r.thread_root,handled:false}).catch(()=>toast(S.revertido));}});
    announce(S.tratado); render();
    post('/api/thread/handled',{thread_root:r.thread_root,handled:true}).catch(()=>{rows.splice(Math.min(at,rows.length),0,r);undo.pop();render();toast(S.revertido);});
  };
  const el=document.querySelector('.row[data-i="'+i+'"]');
  if(el&&!reduceMotion()){let done=false;const go=()=>{if(done)return;done=true;commit();};
    el.classList.add('leaving');el.addEventListener('transitionend',go,{once:true});setTimeout(go,240);}
  else commit();
}
function setOwner(i,owner){
  const r=view()[i]; if(!r) return;
  const prev=r.owner||'';
  r.owner=owner;
  undo.push({label:'dono',revert:()=>{r.owner=prev;render();post('/api/thread/owner',{thread_root:r.thread_root,owner:prev}).catch(()=>toast(S.revertido));}});
  render();
  post('/api/thread/owner',{thread_root:r.thread_root,owner}).catch(()=>{r.owner=prev;undo.pop();render();toast(S.revertido);});
}
function toggleWhy(i){ const r=view()[i]; if(r){r._why=!r._why;render();} }

function ownerMenu(i){
  const m=$('#_menu');
  m.innerHTML=TEAM.map(nm=>'<div class="mi" data-n="'+esc(nm)+'">@'+esc(nm)+'</div>').join('')+'<div class="mi" data-n="">sem dono</div>';
  m.dataset.i=i; m.classList.remove('hidden');
  const row=document.querySelector('.row[data-i="'+i+'"]');
  if(row){const b=row.getBoundingClientRect();m.style.top=(window.scrollY+b.bottom+4)+'px';m.style.left=(window.scrollX+Math.max(8,b.right-180))+'px';}
}

/* ── command bus (B1) ───────────────────────────────────────────────── */
function dispatch(action,i){
  if(action==='handled')handle(i);
  else if(action==='owner')ownerMenu(i);
  else if(action==='why')toggleWhy(i);
}

/* ── lens keyboard handler ──────────────────────────────────────────── */
function onKey(e){
  const v=view(); if(!v.length) return;
  if(e.key==='j'||e.key==='ArrowDown'){focus=Math.min(v.length-1,focus+1);render();const r=document.querySelector('.row.on');if(r)r.scrollIntoView({block:'nearest'});e.preventDefault();}
  else if(e.key==='k'||e.key==='ArrowUp'){focus=Math.max(0,focus-1);render();const r=document.querySelector('.row.on');if(r)r.scrollIntoView({block:'nearest'});e.preventDefault();}
  else if(e.key==='e'||e.key==='E')dispatch('handled',focus);
  else if(e.key==='a'||e.key==='A')dispatch('owner',focus);
}
function onEsc(){ if(filter){filter=null;render();} }

/* ── palette items ──────────────────────────────────────────────────── */
function paletteItems(q){
  q=(q||'').toLowerCase().trim();
  const items=[
    {kind:'ação',label:S.actSync,run:syncNow},
    {kind:'ação',label:S.actUndo,run:doUndo},
    {kind:'ação',label:S.actDensity,run:toggleDensity},
    {kind:'ação',label:S.actInbox,run:()=>{location.href='/inbox';}},
    {kind:'ação',label:'Contrapartes',run:()=>{location.href='/contrapartes';}},
    {kind:'ação',label:'Para ti',run:()=>{location.href='/para-ti';}},
    {kind:'ação',label:'Projetos',run:()=>{location.href='/projetos';}},
  ];
  [...new Set(rows.map(r=>r.counterparty).filter(Boolean))].forEach(cp=>
    items.push({kind:'contraparte',label:cp,run:()=>{filter=cp;focus=0;render();}}));
  view().forEach(r=>items.push({kind:'assunto',label:r.subject||'(sem assunto)',
    sub:(r.counterparty||'')+' · '+(r.contact||''),
    run:()=>{const i=view().findIndex(x=>x.thread_root===r.thread_root);if(i>=0){focus=i;render();const el=document.querySelector('.row.on');if(el)el.scrollIntoView({block:'nearest'});}}}));
  return q?items.filter(it=>(it.label+' '+(it.sub||'')+' '+it.kind).toLowerCase().includes(q)):items;
}

/* ── list events ────────────────────────────────────────────────────── */
$('#_list').addEventListener('click',e=>{
  const row=e.target.closest('.row'); if(!row) return;
  const i=parseInt(row.dataset.i,10); focus=i;
  const act=e.target.closest('[data-act]');
  if(act){dispatch(act.dataset.act,i);e.stopPropagation();}else render();
});
$('#_menu').addEventListener('click',e=>{
  const mi=e.target.closest('.mi'); if(!mi) return;
  setOwner(parseInt($('#_menu').dataset.i,10),mi.dataset.n);$('#_menu').classList.add('hidden');
});
const _fc=$('#_fchip'); if(_fc)_fc.addEventListener('click',()=>{filter=null;render();});
"""

_BODY_HTML = """
<div class="wrap">
  <div class="bar">
    <span id="_risk" class="risk" aria-live="polite" style="font-size:12.5px;font-weight:680;font-variant-numeric:tabular-nums;border-radius:20px;padding:3px 12px;border:1px solid"></span>
    <span id="_count"></span>
    <span id="_fchip" class="hidden" style="display:inline-flex;align-items:center;gap:6px;background:#eef2ff;border:1px solid #cdd7ff;color:var(--ac);border-radius:20px;padding:2px 10px;font-weight:600;cursor:pointer;font-size:12px"></span>
    <span class="cmdk"><kbd>⌘K</kbd> comandos</span>
  </div>
  <div id="_list" class="list" role="list" aria-label="Fila de resposta"></div>
  <div id="_zero" class="zero hidden">✓ Tudo tratado<span class="s">nada está a cair · 0 em risco</span></div>
  <div class="hint"><b>J/K</b> mover · <b>E</b> tratado · <b>A</b> dono · <b>Z</b> desfazer · <b>⌘K</b> comandos · <b>?</b> ajuda</div>
</div>
"""

_EXTRA_CSS = """
  .risk{color:var(--red);background:#fbeaea;border-color:#f3c9c9!important}
  .risk.clear{color:var(--green);background:#e7f6ee;border-color:#bfe6cf!important}
  .risk.pulse{animation:pop .35s ease}
"""


def build_fila_html(rows: list[dict[str, Any]], team: list[str] | None = None,
                    *, now_iso: str = "",
                    nav_counts: dict[str, int] | None = None) -> str:
    return cockpit_ui.page(
        "Fila",
        "fila",
        _BODY_HTML,
        embeds={"rows": rows, "team": list(team or []), "now": now_iso},
        lens_js=_LENS_JS,
        nav_counts=nav_counts,
        extra_css=_EXTRA_CSS,
    )
