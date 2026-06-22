"""Fila lens page — the response cockpit's hero screen (home at ``/`` and ``/fila``).

Thin wrapper over ``cockpit_ui.page()``: this module owns only the Fila-specific
data shaping and the lens JS (state + render + paletteItems + onKey).
NEVER sends mail; writes go through /api/thread/handled and /api/thread/owner.
"""

from __future__ import annotations

from typing import Any

from . import cockpit_ui, labels as _labels

_LENS_JS = r"""
/* ── Fila lens state ────────────────────────────────────────────────── */
let rows = ROWS.slice(), focus = 0;
let filters = {};   /* active filters — keys: counterparty, purpose, band, owner, domain,
                       hasAttachment, minAgeDays, search. Pass null to remove a key. */
let _prevRisk = null, urlThread = null;

/* ── filter helpers ─────────────────────────────────────────────────── */
function hasFilters(){ return Object.keys(filters).length > 0; }
function setFilter(key, val){
  if(val===null){ delete filters[key]; }
  else{ filters[key]=val; }
  if(key==='search'){
    const si=$('#_search');
    if(si) si.value = (val===null) ? '' : (filters.search||'');
  }
  focus=0; syncURL(); render();
}
function clearFilters(){
  filters={};
  const si=$('#_search'); if(si) si.value='';
  focus=0; syncURL(); render();
}

/* ── URL state ──────────────────────────────────────────────────────────
   The Fila is a list with inline thread-expansion, so its deep-link state rides in the
   query string (not a path segment, unlike /projetos/<id> or /contrapartes/<key>):
     ?counterparty=<CP>   — counterparty filter (legacy key preserved)
     ?purpose=<P>         — purpose filter
     ?band=<B>            — urgency band filter
     ?owner=<O>           — owner filter (empty string = "sem dono")
     ?domain=<D>          — sender domain filter
     ?attachment=1        — has-attachment filter
     ?minDays=<N>         — minimum age in days
     ?search=<Q>          — free text on subject + contact
     ?thread=<root>       — the expanded thread
   The URL is kept in sync with replaceState (same approach as the report), so it is
   shareable / survives a refresh without spamming the Back history. The legacy
   ?focus=<root> link (Para-ti / Contrapartes) still focuses that row, then drops the
   param from the address bar. */
function syncURL(){
  const p = new URLSearchParams();
  if(filters.counterparty) p.set('counterparty', filters.counterparty);
  if(filters.purpose) p.set('purpose', filters.purpose);
  if(filters.band) p.set('band', filters.band);
  if('owner' in filters) p.set('owner', filters.owner||'');
  if(filters.domain) p.set('domain', filters.domain);
  if(filters.hasAttachment) p.set('attachment','1');
  if(filters.minAgeDays!=null) p.set('minDays', String(filters.minAgeDays));
  if(filters.search) p.set('search', filters.search);
  if(urlThread) p.set('thread', urlThread);
  const base = location.pathname.split('?')[0];
  const qs = p.toString(), url = base + (qs ? ('?'+qs) : '');
  if(location.pathname + location.search !== url){ try{history.replaceState(null,'',url);}catch(_){} }
}

/* ── view with multi-filter ─────────────────────────────────────────── */
function view(){
  return rows.filter(r=>{
    if('counterparty' in filters && (r.counterparty||'')!==filters.counterparty) return false;
    if('purpose' in filters && (r.purpose||'')!==filters.purpose) return false;
    if('band' in filters && (r.clock||{}).band!==filters.band) return false;
    if('owner' in filters && (r.owner||'')!==filters.owner) return false;
    if('domain' in filters){
      const d=(r.contact||'').split('@')[1]||'';
      if(d!==filters.domain) return false;
    }
    if('hasAttachment' in filters && !r.has_attachment) return false;
    if('minAgeDays' in filters && ((r.clock||{}).age_hours||0)/24 < filters.minAgeDays) return false;
    if('search' in filters && filters.search){
      const q=filters.search.toLowerCase();
      const hay=[(r.subject||''),(r.contact||''),(r.counterparty||''),(r.purpose||'')].join(' ').toLowerCase();
      if(!hay.includes(q)) return false;
    }
    return true;
  });
}

function riskCount(){ return view().filter(r=>['red','amber'].includes((r.clock||{}).band)).length; }

/* ── filter bar ─────────────────────────────────────────────────────── */
const _FLABEL = {
  counterparty: v=>'contraparte: '+v,
  purpose: v=>'tipo: '+v.toLowerCase().replace(/_/g,' '),
  band: v=>({'red':'urgente','amber':'a atrasar','green':'recente'}[v]||v),
  owner: v=>v?'dono: @'+v:'sem dono',
  domain: v=>'domínio: '+v,
  hasAttachment: ()=>'com anexo',
  minAgeDays: v=>'≥'+v+(v===1?' dia':' dias'),
  search: v=>'busca: "'+v+'"',
};
function renderFbar(){
  const chips=[];
  for(const [k,v] of Object.entries(filters)){
    const lf=_FLABEL[k]; if(!lf) continue;
    chips.push('<button class="fchip" data-fkey="'+esc(k)+'">'+esc(lf(v))+' ✕</button>');
  }
  const fb=$('#_fbar'); if(!fb) return;
  if(chips.length){ fb.innerHTML=chips.join(''); fb.classList.remove('hidden'); }
  else{ fb.innerHTML=''; fb.classList.add('hidden'); }
}

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
  renderFbar();
  const zero=$('#_zero');
  if(zero){
    zero.classList.toggle('hidden',v.length>0);
    if(!v.length){
      const noRes=hasFilters()&&rows.length>0;
      zero.innerHTML=noRes
        ?'Sem resultados<span class="s">nenhuma thread corresponde aos filtros activos</span>'
        :'✓ Tudo tratado<span class="s">nada está a cair · 0 em risco</span>';
    }
  }
  announce(v.length?S.threads(v.length)+' por tratar':'Tudo tratado');
  if(focus>=v.length) focus=Math.max(0,v.length-1);

  const list=$('#_list');
  list.innerHTML=v.map((r,i)=>{
    const c=r.clock||{},tr=r.trust||{};
    const owner=ownerLabel(r);
    const decided=decidedShort(tr.decided_by);
    const conf=tr.confidence?(' · '+Math.round(tr.confidence*100)+'%'):'';
    const trust=decided
      ?'<button class="trust '+(tr.committed?'committed':'proposed')+'" data-act="why" aria-label="ver porquê">'+esc(decided)+conf+'</button>'
      :'';
    // PT-labelled, clickable badges: the counterparty pill and the purpose chip each open a picker
    // to CORRECT the LLM's verdict from the Fila (was: raw enum text, no way to fix it here).
    const cpLabel=(LABELS.counterparty&&LABELS.counterparty[r.counterparty])||r.counterparty||'—';
    const purLabel=(LABELS.purpose&&LABELS.purpose[r.purpose])||(r.purpose?String(r.purpose).toLowerCase().replace(/_/g,' '):'');
    const purChip=purLabel?'<button class="pur'+(tr.committed?' committed':'')+'" data-act="reclassPur" title="tipo: '+esc(purLabel)+' — clica para corrigir">'+esc(purLabel)+'</button>':'';
    const metaText=[esc(r.contact||''),r.n_messages>1?(r.n_messages+' msgs'):'',r.has_attachment?'📎':''].filter(Boolean).join(' · ');
    const why=(r._why&&tr.reason)?'<div class="why">'+esc(tr.reason)+'</div>':'';
    return '<div class="row'+(i===focus?' on':'')+(r._open?' open':'')+'" data-i="'+i+'" role="listitem"'+(i===focus?' aria-current="true"':'')+' tabindex="0">'
      +'<button class="cp '+esc(r.counterparty||'OTHER')+'" data-act="reclassCp" title="contraparte: '+esc(cpLabel)+' — clica para corrigir">'+esc(cpLabel)+'</button>'
      +'<div class="rmain" data-act="thread" title="abrir conversa (Enter)">'
      +'<div class="subj">'+esc(r.subject||'(sem assunto)')+(r._open?' <span class="chev open">▾</span>':' <span class="chev">▸</span>')+'</div>'
      +'<div class="rmeta">'+purChip+(metaText?' <span class="mtxt">'+metaText+'</span>':'')+(trust?' '+trust:'')
      +(r.project?' <button class="rpchip" data-act="openproj" title="já está no projeto '+esc(r.project.project_id)+' — abrir">📁 '+esc(r.project.title||r.project.project_id)+'</button>':'')
      +'</div>'+why+_threadHTML(r)+'</div>'
      +'<span class="clock '+esc(c.band||'none')+'"><span class="d" aria-hidden="true"></span>'+esc(c.label||'')+'</span>'
      +'<button class="owner'+((r.owners&&r.owners.length)?'':' empty')+'" data-act="owner" aria-label="atribuir donos">'+owner+'</button>'
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
/* ── owners (multi) — picked from the roster; "+ novo dono" adds to it ─────────────── */
let filaRoster = (typeof TEAM!=='undefined'?TEAM:[]).slice();
function ownerLabel(r){ const o=r.owners||[]; return o.length?('@'+esc(o[0])+(o.length>1?(' +'+(o.length-1)):'')):'sem dono'; }
async function setThreadOwners(i,owners){
  const r=view()[i]; if(!r) return;
  const prev=r.owners||[];
  r.owners=owners; r.owner=owners[0]||''; render();
  try{ await post('/api/thread/owner',{thread_root:r.thread_root,owners}); }
  catch(e){ r.owners=prev; r.owner=prev[0]||''; render(); toast(S.revertido); }
}
function toggleThreadOwner(i,name){
  const r=view()[i]; if(!r) return;
  const own=new Set(r.owners||[]);
  own.has(name)?own.delete(name):own.add(name);
  setThreadOwners(i,[...own]).then(()=>ownerMenu(i));   // keep the picker open with refreshed checks
}
async function addFilaOwner(i){
  const nm=prompt('Novo dono (nome):'); if(!nm||!nm.trim()) return;
  try{ const r=await post('/api/roster',{name:nm.trim()}); filaRoster=r.roster||filaRoster; toggleThreadOwner(i,nm.trim()); }
  catch(e){ toast(S.revertido); }
}
function toggleWhy(i){ const r=view()[i]; if(r){r._why=!r._why;render();} }

function positionMenu(i){
  const m=$('#_menu'), row=document.querySelector('.row[data-i="'+i+'"]');
  if(row){const b=row.getBoundingClientRect();m.style.top=(window.scrollY+b.bottom+4)+'px';m.style.left=(window.scrollX+Math.max(8,b.right-180))+'px';}
}
function ownerMenu(i){
  const r=view()[i]; if(!r) return;
  const own=new Set(r.owners||[]);
  const m=$('#_menu');
  const items=filaRoster.map(nm=>'<div class="mi'+(own.has(nm)?' on':'')+'" data-n="'+esc(nm)+'">'+(own.has(nm)?'✓ ':'')+'@'+esc(nm)+'</div>').join('');
  m.innerHTML='<div class="mhdr">Donos</div>'+items
    +'<div class="mi reset" data-clear="1">sem dono</div>'
    +'<div class="mi reset" data-new="1">+ novo dono…</div>';
  m.dataset.i=i; m.dataset.kind='owner'; m.classList.remove('hidden'); positionMenu(i);
}

/* ── reclassify: correct the LLM verdict from the Fila (one field at a time) ──────────
   The purpose/counterparty badges open this picker; mirrors the /inbox rcPanel but inline. */
function reclassMenu(i,field){
  const r=view()[i]; if(!r) return;
  if(!r.message_id){ toast('sem id para corrigir'); return; }
  const m=$('#_menu'), dict=(LABELS&&LABELS[field])||{}, cur=r[field]||'';
  const auto=(r.auto&&r.auto[field])||'';
  const items=Object.keys(dict).map(k=>'<div class="mi'+(k===cur?' on':'')+'" data-val="'+esc(k)+'">'+esc(dict[k])+'</div>').join('');
  const reset=auto?'<div class="mi reset" data-val="">↺ auto ('+esc(dict[auto]||auto)+')</div>':'';
  m.innerHTML='<div class="mhdr">'+(field==='counterparty'?'Contraparte':'Tipo')+'</div>'+items+reset;
  m.dataset.i=i; m.dataset.kind='reclass'; m.dataset.field=field; m.classList.remove('hidden'); positionMenu(i);
}
function reclassify(i,field,value){
  const r=view()[i]; if(!r||!r.message_id) return;
  const auto=(r.auto&&r.auto[field])||r[field], prev=r[field];
  r[field]=value||auto;
  if(r.trust) r.trust.committed=!!value;
  announce(value?'corrigido':'reposto'); render();
  post('/api/reclassify',{message_id:r.message_id,field,value_auto:auto,value_human:value||null})
    .catch(()=>{ r[field]=prev; if(r.trust)r.trust.committed=false; render(); toast(S.revertido); });
}

/* ── thread expansion ───────────────────────────────────────────────── */
const _threadCache = {};   // thread_root → messages array (fetch-once)

async function toggleThread(i){
  const v=view(), r=v[i]; if(!r) return;
  if(r._open){ r._open=false;
    if(urlThread===r.thread_root){ urlThread=null; syncURL(); }   // collapsing the URL thread → clear it
    render(); return; }
  // show loading state immediately + reflect the open thread in the URL (shareable / refresh-safe)
  r._open=true; r._threadMsgs=null; r._threadErr=null;
  urlThread=r.thread_root; syncURL(); render();
  const root = r.thread_root;
  if(_threadCache[root]){
    r._threadMsgs=_threadCache[root]; render(); return;
  }
  try{
    const d = await (await fetch('/api/thread/'+encodeURIComponent(root))).json();
    if(d.error){ r._threadErr=d.error; }
    else{ _threadCache[root]=d.messages; r._threadMsgs=d.messages; }
  }catch(e){ r._threadErr='falhou ao carregar'; }
  render();
}

/* project banner: open the existing project, or offer to create one from this thread (no dupes) */
function _projHTML(r){
  if(r.project) return '<button class="pchip in" data-act="openproj" title="abrir o projeto onde este pedido já está a ser tratado">📁 '
    +esc(r.project.title||r.project.project_id)+' · '+esc((r.project.stage||'').toLowerCase())+' — abrir</button>';
  return '<button class="pchip new" data-act="mkproj" title="criar um projeto a partir desta thread (importa contexto + anexos)">+ criar projeto</button>';
}

function _threadHTML(r){
  if(!r._open) return '';
  const msgs = r._threadMsgs;
  const err  = r._threadErr;
  if(!msgs && !err) return '<div class="texp"><span class="tsum">a carregar…</span></div>';
  if(err) return '<div class="texp"><span style="color:var(--red);font-size:12.5px">'+esc(err)+'</span></div>';
  // project banner prepended before the shared summary line
  const head='<div class="thead">'+_projHTML(r)+'<span class="tsum">'+esc(msgThreadSummary(msgs))+'</span></div>';
  return '<div class="texp">'+head+msgs.map(m=>msgHTML(m)).join('')+'</div>';
}

/* ── command bus (B1) ───────────────────────────────────────────────── */
function dispatch(action,i){
  if(action==='handled')handle(i);
  else if(action==='owner')ownerMenu(i);
  else if(action==='reclassCp')reclassMenu(i,'counterparty');
  else if(action==='reclassPur')reclassMenu(i,'purpose');
  else if(action==='why')toggleWhy(i);
  else if(action==='thread')toggleThread(i);
  else if(action==='mkproj')makeProject(i);
  else if(action==='openproj')openProject(i);
}

/* project: jump into the existing one, or create from this thread and go straight to it */
function openProject(i){
  const r=view()[i]; if(r&&r.project) location.href='/projetos/'+encodeURIComponent(r.project.project_id);
}
async function makeProject(i){
  const r=view()[i]; if(!r) return;
  if(r.project){ openProject(i); return; }  // never double-create
  toast('a criar projeto…');
  try{
    const d=await post('/api/projects',{title:r.subject||'(sem assunto)',from_message:r.thread_root});
    location.href='/projetos/'+encodeURIComponent(d.project_id);
  }catch(e){ toast(S.revertido); }
}

/* ── lens keyboard handler ──────────────────────────────────────────── */
function onKey(e){
  const v=view(); if(!v.length) return;
  if(e.key==='j'||e.key==='ArrowDown'){focus=Math.min(v.length-1,focus+1);render();const r=document.querySelector('.row.on');if(r)r.scrollIntoView({block:'nearest'});e.preventDefault();}
  else if(e.key==='k'||e.key==='ArrowUp'){focus=Math.max(0,focus-1);render();const r=document.querySelector('.row.on');if(r)r.scrollIntoView({block:'nearest'});e.preventDefault();}
  else if(e.key==='e'||e.key==='E')dispatch('handled',focus);
  else if(e.key==='a'||e.key==='A')dispatch('owner',focus);
  else if(e.key==='Enter'||e.key==='o'||e.key==='O'){dispatch('thread',focus);e.preventDefault();}
}
function onEsc(){ if(hasFilters()) clearFilters(); }

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
    {kind:'ação',label:'Capturas',run:()=>{location.href='/capturas';}},
  ];
  if(hasFilters()) items.unshift({kind:'filtro',label:'limpar filtros',run:clearFilters});

  // Counterparty filters
  [...new Set(rows.map(r=>r.counterparty).filter(Boolean))].forEach(cp=>
    items.push({kind:'contraparte',label:cp,run:()=>setFilter('counterparty',cp)}));

  // Purpose filters
  [...new Set(rows.map(r=>r.purpose).filter(Boolean))].forEach(p=>
    items.push({kind:'tipo',label:p.toLowerCase().replace(/_/g,' '),sub:p,run:()=>setFilter('purpose',p)}));

  // Urgency band filters
  const _blab={'red':'urgente (vermelho)','amber':'a atrasar (laranja)','green':'recente (verde)'};
  ['red','amber','green'].forEach(b=>{
    if(rows.some(r=>(r.clock||{}).band===b))
      items.push({kind:'urgência',label:_blab[b]||b,run:()=>setFilter('band',b)});
  });

  // Owner filters
  [...new Set(rows.map(r=>r.owner).filter(Boolean))].forEach(o=>
    items.push({kind:'dono',label:'@'+o,run:()=>setFilter('owner',o)}));
  if(rows.some(r=>!r.owner))
    items.push({kind:'dono',label:'sem dono',run:()=>setFilter('owner','')});

  // Domain filters (derived from contact email)
  [...new Set(rows.map(r=>(r.contact||'').split('@')[1]).filter(Boolean))].forEach(d=>
    items.push({kind:'domínio',label:d,run:()=>setFilter('domain',d)}));

  // Has attachment
  if(rows.some(r=>r.has_attachment))
    items.push({kind:'filtro',label:'com anexo',run:()=>setFilter('hasAttachment',true)});

  // Age threshold filters
  [1,3,7].forEach(days=>{
    if(rows.some(r=>((r.clock||{}).age_hours||0)/24>=days))
      items.push({kind:'tempo',label:'≥'+days+(days===1?' dia':' dias')+' em espera',run:()=>setFilter('minAgeDays',days)});
  });

  // Subject search (navigate to row)
  view().forEach(r=>items.push({kind:'assunto',label:r.subject||'(sem assunto)',
    sub:(r.counterparty||'')+' · '+(r.contact||''),
    run:()=>{const i=view().findIndex(x=>x.thread_root===r.thread_root);if(i>=0){focus=i;render();const el=document.querySelector('.row.on');if(el)el.scrollIntoView({block:'nearest'});}}}));

  return q?items.filter(it=>(it.label+' '+(it.sub||'')+' '+it.kind).toLowerCase().includes(q)):items;
}

/* ── list events ────────────────────────────────────────────────────── */
$('#_list').addEventListener('click',e=>{
  const row=e.target.closest('.row'); if(!row) return;
  // quote/raw toggle: local show/hide, no re-render
  const qt=e.target.closest('.qtoggle');
  if(qt){const q=qt.nextElementSibling;
    if(q&&q.classList.contains('tquote')){const hid=q.classList.toggle('hidden');qt.textContent=(hid?'▸':'▾')+' mensagem citada';}
    e.stopPropagation();return;}
  const rt=e.target.closest('.rawtoggle');
  if(rt){const rb=rt.nextElementSibling;
    if(rb&&rb.classList.contains('rawbody')){const hid=rb.classList.toggle('hidden');rt.textContent=hid?'ver original':'ver limpo';}
    e.stopPropagation();return;}
  const i=parseInt(row.dataset.i,10); focus=i;
  const act=e.target.closest('[data-act]');
  const inThread=act&&act.dataset.act==='thread'&&e.target.closest('.texp');
  if(act&&!inThread){dispatch(act.dataset.act,i);e.stopPropagation();}else render();
});
$('#_menu').addEventListener('click',e=>{
  const mi=e.target.closest('.mi'); if(!mi) return;
  const m=$('#_menu'), i=parseInt(m.dataset.i,10);
  if(m.dataset.kind==='reclass'){ reclassify(i,m.dataset.field,mi.dataset.val||''); m.classList.add('hidden'); return; }
  // owner (multi-select): toggle keeps the picker open; clear / new are explicit
  if(mi.dataset.new){ addFilaOwner(i); return; }
  if(mi.dataset.clear){ setThreadOwners(i,[]); m.classList.add('hidden'); return; }
  toggleThreadOwner(i,mi.dataset.n);
});

/* filter bar chip clicks */
const _fb=$('#_fbar'); if(_fb)_fb.addEventListener('click',e=>{
  const chip=e.target.closest('.fchip'); if(chip) setFilter(chip.dataset.fkey,null);
});

/* search input — updates filters.search without going through setFilter to avoid cursor-jump */
const _si=$('#_search');
if(_si) _si.addEventListener('input',e=>{
  const v=e.target.value;
  if(v){ filters.search=v; }else{ delete filters.search; }
  focus=0; syncURL(); render();
});

/* ── URL ↔ view sync (initial load + Back/Forward) ──────────────────────── */
function applyURLState(){
  const p = new URLSearchParams(location.search);
  filters = {};
  const cpv=p.get('counterparty'); if(cpv) filters.counterparty=cpv;
  const pv=p.get('purpose'); if(pv) filters.purpose=pv;
  const bv=p.get('band'); if(bv) filters.band=bv;
  if(p.has('owner')) filters.owner=p.get('owner')||'';  // '' = "sem dono" filter
  const dv=p.get('domain'); if(dv) filters.domain=dv;
  if(p.get('attachment')==='1') filters.hasAttachment=true;
  const md=p.get('minDays'); if(md) filters.minAgeDays=parseFloat(md);
  const sv=p.get('search'); if(sv) filters.search=sv;
  const si=$('#_search'); if(si) si.value=filters.search||'';

  const open = p.get('thread') || '', legacyFocus = p.get('focus') || '';
  rows.forEach(r=>{ r._open=false; });          // the URL owns which thread is expanded
  urlThread = open || null;
  render();
  const tgt = open || legacyFocus;
  if(tgt){
    const i = view().findIndex(r => r.thread_root === tgt);
    if(i>=0){ focus=i;
      if(open) toggleThread(i);                 // expand it (syncURL is a no-op — URL already matches)
      setTimeout(()=>{const el=document.querySelector('.row.on');if(el)el.scrollIntoView({block:'center'});},0);
    }
  }
  if(legacyFocus && !open){ urlThread=null; syncURL(); }   // canonicalize ?focus= out of the address bar
}
window.addEventListener('popstate', applyURLState);
applyURLState();
"""

_BODY_HTML = """
<div class="wrap">
  <div class="bar">
    <span id="_risk" class="risk" aria-live="polite" style="font-size:12.5px;font-weight:680;font-variant-numeric:tabular-nums;border-radius:20px;padding:3px 12px;border:1px solid"></span>
    <span id="_count"></span>
    <input id="_search" type="text" placeholder="filtrar…" autocomplete="off" aria-label="Filtrar threads"/>
    <span class="cmdk"><kbd>⌘K</kbd> comandos</span>
  </div>
  <div id="_fbar" class="fbar hidden" aria-label="Filtros activos"></div>
  <div id="_list" class="list" role="list" aria-label="Fila de resposta"></div>
  <div id="_zero" class="zero hidden">✓ Tudo tratado<span class="s">nada está a cair · 0 em risco</span></div>
  <div class="hint"><b>J/K</b> mover · <b>Enter</b> abrir · <b>E</b> tratado · <b>A</b> dono · <b>Z</b> desfazer · <b>⌘K</b> comandos · <b>?</b> ajuda</div>
</div>
"""

_EXTRA_CSS = """
  /* Fila-specific (shared thread CSS lives in cockpit_ui) */
  .risk{color:var(--red);background:#fbeaea;border-color:#f3c9c9!important}
  .risk.clear{color:var(--green);background:#e7f6ee;border-color:#bfe6cf!important}
  .risk.pulse{animation:pop .35s ease}
  .rmain[data-act]{cursor:pointer}
  .chev{color:var(--mut2);font-size:11px}
  .chev.open{color:var(--ac)}
  .row.open{align-items:flex-start}
  .row .texp{margin:10px 0 2px;padding-left:11px;border-left:2px solid var(--bd)}
  .pchip{font-size:11.5px;font-weight:650;border-radius:8px;padding:3px 10px;cursor:pointer;border:1px solid}
  .pchip.in{background:#eef2ff;border-color:#cdd7ff;color:var(--ac)}
  .pchip.in:hover{background:#e0e8ff}
  .pchip.new{background:#fff;border-color:var(--bd);color:var(--mut)}
  .pchip.new:hover{border-color:var(--int);color:var(--int);background:#effbf7}
  .rpchip{font-size:10.5px;font-weight:650;border:1px solid #cdd7ff;background:#eef2ff;color:var(--ac);border-radius:6px;padding:1px 7px;cursor:pointer}
  .rpchip:hover{background:#e0e8ff}
  /* search input */
  #_search{border:1px solid var(--bd);border-radius:8px;padding:4px 10px;font-size:12.5px;color:var(--tx);background:var(--card);outline:none;width:160px;transition:width .15s,border-color .12s}
  #_search:focus{border-color:var(--ac);width:210px}
  #_search::placeholder{color:var(--mut2)}
  /* active filter chips */
  .fbar{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 10px}
  .fchip{display:inline-flex;align-items:center;gap:5px;background:#eef2ff;border:1px solid #cdd7ff;color:var(--ac);border-radius:20px;padding:3px 10px;font-weight:600;cursor:pointer;font-size:12px}
  .fchip:hover{background:#dfe8ff}
  /* counterparty pill is now a button (inline reclassify) — reset native chrome, keep .cp colours */
  .cp{border:none;cursor:pointer;font-family:inherit}
  .cp:hover{filter:brightness(.97)}
  /* purpose chip (PT label) — click to correct the LLM's purpose right from the Fila */
  .pur{font-size:10px;font-weight:650;border-radius:20px;padding:2px 9px;background:#f3f4f6;
    color:var(--mut);border:1px solid var(--bd);cursor:pointer;line-height:1.5}
  .pur:hover{border-color:var(--ac);color:var(--ac);background:#eef2ff}
  .pur.committed{border-color:var(--int);color:var(--int);background:#f0fdfa}
  .mtxt{color:var(--mut)}
  /* reclassify menu header + reset row (shared .menu chrome lives in cockpit_ui) */
  .menu .mhdr{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:var(--mut2);padding:5px 11px 3px}
  .menu .mi.reset{color:var(--mut);border-top:1px solid var(--bd2);margin-top:3px}
"""


def build_fila_html(rows: list[dict[str, Any]], team: list[str] | None = None,
                    *, now_iso: str = "",
                    nav_counts: dict[str, int] | None = None) -> str:
    return cockpit_ui.page(
        "Fila",
        "fila",
        _BODY_HTML,
        embeds={"rows": rows, "team": list(team or []), "now": now_iso,
                "labels": _labels.fila_labels()},
        lens_js=_LENS_JS,
        nav_counts=nav_counts,
        extra_css=_EXTRA_CSS,
    )
