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
                       hasAttachment, minAgeDays, search. Pass null to remove a key.
                       band accepts the pseudo-value 'risk' = red OR amber (the "em risco" chip). */
let _prevRisk = null, urlThread = null;
/* 'ativos' (default) or 'tratados' — the decided ledger (rows fetched lazily from
   /api/fila?include=resolved). A decision must be reviewable after it is made, not vanish. */
let mode = 'ativos', resolvedRows = null;

/* ── queue ordering ─────────────────────────────────────────────────────
   'risk' (default, ADR-033) = the response-risk order (who owes a reply, and for how long) — the
   highest-stakes thread is on top at load, so the next move is never a question.
   'recent' = newest thread activity first, the mailbox-shaped view — still one click away.
   The server ships rows already in 'risk' order AND stamps BOTH keys on every row
   (r.order_keys, from cockpit.build_fila), so flipping here is a local re-sort — the risk tuple
   is never re-derived in JS and so can never drift from the Python definition. */
const ORDER_RECENT='recent', ORDER_RISK='risk';
let order = ORDER_RISK;
function cmpOrderKey(a,b){
  const x=Array.isArray(a)?a:[a], y=Array.isArray(b)?b:[b];
  for(let i=0;i<Math.max(x.length,y.length);i++){
    const p=x[i], q=y[i];
    if(p===q) continue;
    if(p===undefined||p===null) return -1;
    if(q===undefined||q===null) return 1;
    return p<q?-1:1;
  }
  return 0;
}
function sortRows(){   /* DESC on the chosen key (args swapped) */
  rows.sort((r1,r2)=>cmpOrderKey((r2.order_keys||{})[order],(r1.order_keys||{})[order]));
}
function setOrder(o){
  if(o!==ORDER_RECENT&&o!==ORDER_RISK) return;
  order=o;
  /* Sticky: the chosen order survives the next visit (an explicit URL ?order= still wins on load).
     Without this the queue reopened on 'recent' every morning and the oldest reply-debts started
     the day buried at the bottom. */
  try{localStorage.setItem('fila-order',o);}catch(_e){}
  const sel=$('#_order'); if(sel&&sel.value!==o) sel.value=o;
  sortRows(); focus=0; syncURL(); render();
}

/* ── filter helpers ─────────────────────────────────────────────────── */
function hasFilters(){ return Object.keys(filters).length > 0; }
function setFilter(key, val){
  if(val===null){ delete filters[key]; }
  else{ filters[key]=val; }
  if(key==='search'){
    const si=$('#_search');
    if(si) si.value = (val===null) ? '' : (filters.search||'');
  }
  if(key==='owner') renderOwnerFilter();   /* keep the visible control in sync (palette/chips path) */
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
     ?order=risk          — queue ordering (omitted = the default 'recent', newest first)
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
  if(order!==ORDER_RISK) p.set('order', order);   /* the default stays out of the address bar */
  if(urlThread) p.set('thread', urlThread);
  const base = location.pathname.split('?')[0];
  const qs = p.toString(), url = base + (qs ? ('?'+qs) : '');
  if(location.pathname + location.search !== url){ try{history.replaceState(null,'',url);}catch(_){} }
}

/* ── obligation grouping ────────────────────────────────────────────────
   WE_OWE and AWAITING are OPPOSITE obligations that the clock colour cannot tell apart: _band()
   (cockpit.py) encodes urgency only, so a fresh "devemos resposta" and a fresh "à espera" both
   render green. Interleaved by "Mais recentes", the queue asked the eye to READ Portuguese prose
   to answer "is this mine?" — the single most frequent question the Fila exists to answer.
   The group is now the PRIMARY partition; the chosen sort orders rows inside it. */
const G_OWE=0, G_WAIT=1, G_OTHER=2;
function groupOf(r){
  const st=(r.clock||{}).state;
  return st==='WE_OWE' ? G_OWE : st==='AWAITING' ? G_WAIT : G_OTHER;
}
/* PT-PT, phrased as the answer to "who has the ball": ours vs theirs. */
const G_LABEL={0:'Precisam de resposta', 1:'À espera deles', 2:'Internos'};
const G_HINT ={0:'a bola está do nosso lado', 1:'já respondemos — a bola está do lado deles', 2:'sem relógio de resposta'};
const G_CLASS={0:'owe', 1:'wait', 2:'other'};

/* ── group collapse (ADR-033 P0) ────────────────────────────────────────
   «À espera deles» is a status report, not a to-do list — it STARTS collapsed to a counted header,
   and any group can be folded. The choice persists (localStorage). Collapsed rows leave view()
   entirely, so J/K, focus and data-i can never land on an invisible row. */
const DEFAULT_COLLAPSED={[G_WAIT]:true};
let collapsed=(function(){
  try{const s=localStorage.getItem('fila-collapsed'); return s?JSON.parse(s):{...DEFAULT_COLLAPSED};}
  catch(_e){return {...DEFAULT_COLLAPSED};}
})();
function isCollapsed(g){ return mode!=='tratados' && !!collapsed[g]; }
function toggleGroup(g){
  collapsed[g]=!collapsed[g];
  try{localStorage.setItem('fila-collapsed',JSON.stringify(collapsed));}catch(_e){}
  focus=0; render();
}

/* ── view with multi-filter ─────────────────────────────────────────────
   viewAll() = filters + group partition (the full active set — headline counts + group counts read
   this). view() = viewAll() minus collapsed groups (what is actually rendered and keyboard-walked). */
function view(){
  const va=viewAll();
  if(mode==='tratados') return va;
  return va.filter(r=>!isCollapsed(groupOf(r)));
}
function viewAll(){
  const src = mode==='tratados' ? (resolvedRows||[]) : rows;
  const out = src.filter(r=>{
    if('counterparty' in filters && (r.counterparty||'')!==filters.counterparty) return false;
    if('band' in filters){
      const c=r.clock||{}, b=c.band;
      /* Pseudo-bands carry OBLIGATION, not just colour (ADR-033): 'risk' = the reply debt we owe
         (WE_OWE red|amber); 'chase' = suppliers/clients silent past the 72h chase threshold
         (AWAITING amber — _band() only ambers AWAITING at the chase cutoff). */
      if(filters.band==='risk'){ if(!(c.state==='WE_OWE'&&(b==='red'||b==='amber'))) return false; }
      else if(filters.band==='chase'){ if(!(c.state==='AWAITING'&&b==='amber')) return false; }
      else if(b!==filters.band) return false;
    }
    if('purpose' in filters && (r.purpose||'')!==filters.purpose) return false;
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
  /* The ledger ("tratados") is one homogeneous pile — every row is HANDLED, so grouping there would
     draw a boundary with nothing on the other side of it. Group the ACTIVE queue only.
     Array.sort is stable (ES2019+), and `rows` is already ordered by sortRows(), so partitioning by
     group here preserves the user's chosen sort WITHIN each group — no second sort key needed. */
  if(mode==='tratados') return out;
  return out.sort((a,b)=>groupOf(a)-groupOf(b));
}

/* Honest headline counts (ADR-033): only what actually demands the user. The old chip counted every
   red+amber row — including the passive AWAITING pile — and inflated the workload. */
function respondCount(list){ return list.filter(r=>{const c=r.clock||{};return c.state==='WE_OWE'&&(c.band==='red'||c.band==='amber');}).length; }
function chaseCount(list){ return list.filter(r=>{const c=r.clock||{};return c.state==='AWAITING'&&c.band==='amber';}).length; }

/* ── filter bar ─────────────────────────────────────────────────────── */
const _FLABEL = {
  counterparty: v=>'contraparte: '+v,
  purpose: v=>'tipo: '+v.toLowerCase().replace(/_/g,' '),
  band: v=>({'red':'urgente','amber':'a atrasar','green':'recente','risk':'a responder','chase':'a cobrar'}[v]||v),
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
  const v=view(), va=(mode==='tratados')?v:viewAll();
  const n=respondCount(va), nc=chaseCount(va);
  const risk=$('#_risk');
  if(risk){
    risk.classList.toggle('hidden',mode==='tratados');   /* the ledger has no response risk */
    risk.textContent=n+' a responder';
    risk.classList.toggle('clear',n===0);
    risk.classList.toggle('filtering',filters.band==='risk');
    if(_prevRisk!==null&&_prevRisk!==n&&!reduceMotion()){risk.classList.remove('pulse');void risk.offsetWidth;risk.classList.add('pulse');}
    _prevRisk=n;
  }
  /* The chase half of the honest headline: suppliers/clients silent past 72h. Hidden at zero —
     a chip that always reads «0 a cobrar» would be noise, not signal. */
  const cob=$('#_cobrar');
  if(cob){
    cob.classList.toggle('hidden',mode==='tratados'||(nc===0&&filters.band!=='chase'));
    cob.textContent=nc+' a cobrar';
    cob.classList.toggle('filtering',filters.band==='chase');
  }
  const cnt=$('#_count'); if(cnt) cnt.textContent=v.length?S.threads(v.length):S.semDados;
  renderFbar();
  const zero=$('#_zero');
  if(zero){
    zero.classList.toggle('hidden',v.length>0);
    if(!v.length){
      const noRes=hasFilters()&&(mode==='tratados'?(resolvedRows||[]):rows).length>0;
      zero.innerHTML=mode==='tratados'&&!noRes
        ?'Nada tratado ainda<span class="s">as decisões que marcares como tratadas ficam registadas aqui</span>'
        :(noRes
          ?'Sem resultados<span class="s">nenhuma thread corresponde aos filtros activos</span>'
          :'✓ Tudo tratado<span class="s">nada está a cair · 0 a responder</span>');
    }
  }
  announce(mode==='tratados'?(v.length+' tratados'):(v.length?S.threads(v.length)+' por tratar':'Tudo tratado'));
  if(focus>=v.length) focus=Math.max(0,v.length-1);

  /* Section sizes from the UN-collapsed set (va), so a folded header still carries its true count. */
  const gCount={}; if(mode!=='tratados') for(const r of va){const g=groupOf(r); gCount[g]=(gCount[g]||0)+1;}
  let lastG=null, vi=0;

  const list=$('#_list');
  list.innerHTML=va.map(r=>{
    const c=r.clock||{},tr=r.trust||{};
    /* Headers ride in the SAME map as the rows. The map walks va (all groups, so a collapsed group
       still emits its counted header) while `vi` indexes into view() — collapsed rows return only
       their header, so data-i / focus / j/k keep matching exactly what is rendered.
       .ghead is not .row, so closest('.row') never matches it and it is skipped by keyboard nav. */
    let head='';
    if(mode!=='tratados'){
      const g=groupOf(r);
      if(g!==lastG){
        lastG=g;
        head='<div class="ghead '+G_CLASS[g]+'" data-g="'+g+'" role="button" '
          +'title="'+(isCollapsed(g)?'expandir secção':'encolher secção')+'">'
          +'<span class="gchev" aria-hidden="true">'+(isCollapsed(g)?'▸':'▾')+'</span>'
          +'<span class="gh-t">'+esc(G_LABEL[g]||'')+'</span>'
          +'<span class="gh-n">'+(gCount[g]||0)+'</span>'
          +'<span class="gh-s">'+esc(G_HINT[g]||'')+'</span></div>';
      }
      if(isCollapsed(g)) return head;   /* folded: the counted header stands in for its rows */
    }
    const i=vi++;
    const owner=ownerLabel(r);
    const decided=decidedShort(tr.decided_by);
    const conf=tr.confidence?(' · '+Math.round(tr.confidence*100)+'%'):'';
    /* Off focus, trust collapses to a 2px dot — «Gemini · 95%» repeated on ~every row carries zero
       bits and camouflages the clock (cockpit-design §9). The full chip returns on the focused row. */
    const trust=decided
      ?(i===focus
        ?'<button class="trust '+(tr.committed?'committed':'proposed')+'" data-act="why" aria-label="ver porquê">'+esc(decided)+conf+'</button>'
        :'<span class="tdot '+(tr.committed?'committed':'proposed')+'" title="'+esc(decided)+conf+'"></span>')
      :'';
    // PT-labelled, clickable badges: the counterparty pill and the purpose chip each open a picker
    // to CORRECT the LLM's verdict from the Fila (was: raw enum text, no way to fix it here).
    const cpLabel=(LABELS.counterparty&&LABELS.counterparty[r.counterparty])||r.counterparty||'—';
    const purLabel=(LABELS.purpose&&LABELS.purpose[r.purpose])||(r.purpose?String(r.purpose).toLowerCase().replace(/_/g,' '):'');
    const purChip=purLabel?'<button class="pur'+(tr.committed?' committed':'')+'" data-act="reclassPur" title="tipo: '+esc(purLabel)+' — clica para corrigir">'+esc(purLabel)+'</button>':'';
    const metaText=[esc(r.contact||''),r.n_messages>1?(r.n_messages+' msgs'):'',r.has_attachment?'📎':''].filter(Boolean).join(' · ');
    const why=(r._why&&tr.reason)?'<div class="why">'+esc(tr.reason)+'</div>':'';
    return head
      +'<div class="row'+(i===focus?' on':'')+(r._open?' open':'')+'" data-i="'+i+'" role="listitem"'+(i===focus?' aria-current="true"':'')+' tabindex="0">'
      +'<button class="cp '+esc(r.counterparty||'OTHER')+'" data-act="reclassCp" title="contraparte: '+esc(cpLabel)+' — clica para corrigir">'+esc(cpLabel)+'</button>'
      +'<div class="rmain" data-act="thread" title="abrir conversa (Enter)">'
      +'<div class="subj">'+esc(r.subject||'(sem assunto)')+(r._open?' <span class="chev open">▾</span>':' <span class="chev">▸</span>')+'</div>'
      +'<div class="rmeta">'+purChip+(metaText?' <span class="mtxt">'+metaText+'</span>':'')+(trust?' '+trust:'')
      +(r.project?' <button class="rpchip" data-act="openproj" title="já está no projeto '+esc(r.project.project_id)+' — abrir">📁 '+esc(r.project.title||r.project.project_id)+'</button>':'')
      +'</div>'+why+_threadHTML(r)+'</div>'
      /* Motion is triaged like the mail is: only the CRITICAL red tier pulses (WE_OWE and ≥3 days,
         i.e. 3× the red threshold). With ~half a real queue red, 29 pulsing dots meant none did. */
      /* `wait` hollows the dot: colour still carries URGENCY (red/amber/green), the fill now carries
         OBLIGATION. So a row torn out of its section — in a screenshot, or as the last row before a
         scroll boundary — still says whose move it is without reading the label. */
      +'<span class="clock '+esc(c.band||'none')+((c.band==='red'&&(c.age_hours||0)>=72)?' crit':'')
      +(groupOf(r)===G_WAIT?' wait':'')
      +'"><span class="d" aria-hidden="true"></span>'+esc(c.label||'')+'</span>'
      /* «sem dono» ×112 said nothing (0 threads owned): the empty chip renders only on the focused
         row; owned rows always show their owner. A is unaffected — it acts on the focused row. */
      +(((r.owners&&r.owners.length)||i===focus)
        ?'<button class="owner'+((r.owners&&r.owners.length)?'':' empty')+'" data-act="owner" aria-label="atribuir donos">'+owner+'</button>'
        :'')
      +'<div class="acts"><button data-act="handled" aria-label="'+(mode==='tratados'?'reabrir':'marcar tratado')+'" title="'+(mode==='tratados'?'reabrir — volta à fila (E)':'tratado (E)')+'">'+(mode==='tratados'?'↺':'✓')+'</button>'
      +'<button data-act="owner" aria-label="atribuir dono" title="dono (A)">@</button></div></div>';
  }).join('');
}

/* ── mutations (optimistic + undo, B2) ──────────────────────────────── */
function handle(i){
  const v=view(),r=v[i]; if(!r) return;
  const at=rows.indexOf(r);
  const commit=()=>{
    rows.splice(at,1);
    if(resolvedRows) resolvedRows.unshift(r);   /* it belongs to the ledger now (if loaded) */
    undo.push({label:S.tratado,revert:()=>{
      rows.splice(Math.min(at,rows.length),0,r);
      if(resolvedRows){const ri=resolvedRows.indexOf(r); if(ri>=0) resolvedRows.splice(ri,1);}
      render();post('/api/thread/handled',{thread_root:r.thread_root,handled:false}).catch(()=>toast(S.revertido));}});
    announce(S.tratado); render();
    post('/api/thread/handled',{thread_root:r.thread_root,handled:true}).catch(()=>{
      rows.splice(Math.min(at,rows.length),0,r);
      if(resolvedRows){const ri=resolvedRows.indexOf(r); if(ri>=0) resolvedRows.splice(ri,1);}
      undo.pop();render();toast(S.revertido);});
  };
  const el=document.querySelector('.row[data-i="'+i+'"]');
  if(el&&!reduceMotion()){let done=false;const go=()=>{if(done)return;done=true;commit();};
    el.classList.add('leaving');el.addEventListener('transitionend',go,{once:true});setTimeout(go,240);}
  else commit();
}

/* ── the Tratados ledger (mode='tratados') ──────────────────────────────
   What was already decided, reviewable and reversible — a decision that vanishes without a trace
   the moment it is made cannot be audited or corrected. E/✓ becomes "reabrir" here. */
async function setMode(m){
  if(m!==('ativos')&&m!==('tratados')) return;
  if(m===mode) return;
  mode=m; focus=0;
  const t=$('#_tratados'); if(t) t.classList.toggle('on',m==='tratados');
  if(m==='tratados'&&resolvedRows===null){
    const list=$('#_list'); if(list) list.innerHTML='<div class="row"><span class="tsum">a carregar tratados…</span></div>';
    try{
      const d=await (await fetch('/api/fila?include=resolved')).json();
      resolvedRows=(d.rows||[]).filter(r=>((r.clock||{}).state==='HANDLED'));
      resolvedRows.sort((a,b)=>cmpOrderKey((b.order_keys||{}).recent,(a.order_keys||{}).recent));
    }catch(e){ resolvedRows=[]; toast(S.falhou); }
  }
  render();
}
async function reopenThread(i){
  const v=view(), r=v[i]; if(!r) return;
  const at=resolvedRows?resolvedRows.indexOf(r):-1; if(at<0) return;
  resolvedRows.splice(at,1); render();
  try{
    await post('/api/thread/handled',{thread_root:r.thread_root,handled:false});
    announce('reaberto'); toast('reaberto — voltou à fila');
    undo.push({label:'reaberto',revert:()=>{
      resolvedRows.splice(Math.min(at,resolvedRows.length),0,r); render();
      post('/api/thread/handled',{thread_root:r.thread_root,handled:true})
        .then(()=>refreshActiveRows()).catch(()=>toast(S.falhou));}});
    refreshActiveRows();
  }catch(e){ resolvedRows.splice(Math.min(at,resolvedRows.length),0,r); render(); toast(S.revertido); }
}
async function refreshActiveRows(){
  /* the reopened thread must reappear in the active queue with a server-computed clock */
  try{
    const d=await (await fetch('/api/fila')).json();
    rows=(d.rows||[]); sortRows(); if(mode==='ativos') render();
  }catch(e){}
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
  try{ const r=await post('/api/roster',{name:nm.trim()}); filaRoster=r.roster||filaRoster; renderOwnerFilter(); toggleThreadOwner(i,nm.trim()); }
  catch(e){ toast(S.falhou); }   /* the roster was not changed */
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
  /* Newest message FIRST — reading a thread starts from what just landed, not from last month.
     .slice() before .reverse() is load-bearing: `msgs` IS the cached array in _threadCache, so an
     in-place reverse would flip it again on every re-render (open→render→render = wrong order). */
  const ordered = msgs.slice().reverse();
  const draftBtn=r.can_draft
    ?'<button class="pchip draft" data-act="draft" title="rascunho de resposta (IA) — nada é enviado; copia para o teu mail">✍ '
      +(r._draft!=null?'fechar rascunho':'rascunho de resposta')+'</button>'
    :'';
  const draftBox=r._draftBusy
    ?'<div class="draftbox"><span class="tsum">a redigir rascunho…</span></div>'
    :(r._draft!=null
      ?'<div class="draftbox"><textarea readonly rows="9" aria-label="Rascunho de resposta">'+esc(r._draft)+'</textarea>'
       +'<div class="dfoot"><button class="act-btn" data-act="copydraft">Copiar</button>'
       +'<span class="tsum">rascunho — revê antes de enviar · a app nunca envia</span></div></div>'
      :'');
  /* The summary line keeps the CHRONOLOGICAL array — its date range must read "first → last". */
  const head='<div class="thead">'+_projHTML(r)+draftBtn+'<span class="tsum">'+esc(msgThreadSummary(msgs))+'</span></div>';
  return '<div class="texp">'+head+draftBox+ordered.map(m=>msgHTML(m)).join('')+'</div>';
}

/* ── command bus (B1) ───────────────────────────────────────────────── */
function dispatch(action,i){
  if(action==='handled'){ mode==='tratados'?reopenThread(i):handle(i); }
  else if(action==='owner')ownerMenu(i);
  else if(action==='reclassCp')reclassMenu(i,'counterparty');
  else if(action==='reclassPur')reclassMenu(i,'purpose');
  else if(action==='why')toggleWhy(i);
  else if(action==='thread')toggleThread(i);
  else if(action==='mkproj')makeProject(i);
  else if(action==='openproj')openProject(i);
  else if(action==='draft')draftReply(i);
  else if(action==='copydraft')copyDraft(i);
}

/* ── reply draft (the queue says who owes a reply — now it can also START the reply) ──────
   Uses the tested non-streaming /api/reply; only rows with a JobSpec can draft (r.can_draft,
   stamped server-side). NOTHING is ever sent — the draft is copied into the person's own mail. */
async function draftReply(i){
  const v=view(), r=v[i]; if(!r||!r.can_draft||r._draftBusy) return;
  if(r._draft!=null){ r._draft=null; render(); return; }   /* toggle off */
  r._draftBusy=true; render();
  try{
    const d=await post('/api/reply',{message_id:r.message_id});
    r._draft=d.reply||'';
  }catch(e){ toast(S.falhou); }
  r._draftBusy=false; render();
}
function copyDraft(i){
  const r=view()[i]; if(!r||r._draft==null) return;
  (navigator.clipboard?navigator.clipboard.writeText(r._draft):Promise.reject())
    .then(()=>toast('rascunho copiado'))
    .catch(()=>toast(S.falhou));
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
  }catch(e){ toast(S.falhou); }   /* no project was created — nothing was reverted */
}

/* ── lens keyboard handler ──────────────────────────────────────────── */
function onKey(e){
  const v=view(); if(!v.length) return;
  /* Shift+J/K jumps between section starts — reaching «À espera deles» used to cost ~5 screens of
     scrolling. Wraps around; in the flat Tratados ledger it degenerates to jump-to-top (harmless). */
  if(e.shiftKey&&(e.key==='J'||e.key==='K')){
    const starts=[]; let lg=null;
    v.forEach((r,i)=>{const g=groupOf(r); if(g!==lg){lg=g; starts.push(i);}});
    if(starts.length){
      if(e.key==='J'){const nx=starts.find(s=>s>focus); focus=(nx===undefined)?starts[0]:nx;}
      else{const pv=starts.slice().reverse().find(s=>s<focus); focus=(pv===undefined)?starts[starts.length-1]:pv;}
      render(); const el=document.querySelector('.row.on'); if(el)el.scrollIntoView({block:'nearest'});
    }
    e.preventDefault(); return;
  }
  if(e.key==='j'||e.key==='ArrowDown'){focus=Math.min(v.length-1,focus+1);render();const r=document.querySelector('.row.on');if(r)r.scrollIntoView({block:'nearest'});e.preventDefault();}
  else if(e.key==='k'||e.key==='ArrowUp'){focus=Math.max(0,focus-1);render();const r=document.querySelector('.row.on');if(r)r.scrollIntoView({block:'nearest'});e.preventDefault();}
  else if(e.key==='e'||e.key==='E')dispatch('handled',focus);
  else if(e.key==='a'||e.key==='A')dispatch('owner',focus);
  else if(e.key==='Enter'||e.key==='o'||e.key==='O'){dispatch('thread',focus);e.preventDefault();}
}
function onEsc(){ if(hasFilters()) clearFilters(); }

/* `/` focuses the visible search box (the natural gesture) — the shell falls back to the palette on
   lenses that define no onSlash. ⌘K keeps the palette here too. */
function onSlash(){ const si=$('#_search'); if(si){ si.focus(); si.select(); } }

/* ── freshness stamp (ADR-033 P0) ───────────────────────────────────────
   The clocks' honesty depends on sync recency: say how old the synced mail is, and turn amber when
   ingestion has stalled (the poll works but nothing new is being read — ADR-023's failure case). */
let _syncedAt=(typeof SYNCED_AT!=='undefined'&&SYNCED_AT)?SYNCED_AT:null;
function _agoLabel(iso){
  if(!iso) return '';
  const secs=Math.max(0,(Date.now()-Date.parse(iso))/1000);
  if(secs<90) return 'agora mesmo';
  const mins=Math.round(secs/60);
  if(mins<60) return 'há '+mins+' min';
  const hrs=Math.round(mins/60);
  return 'há '+hrs+(hrs===1?' hora':' horas');
}
function paintFreshness(){
  const el=$('#_fresh'); if(!el) return;
  if(!_syncedAt){ el.textContent=''; return; }
  el.textContent='correio '+_agoLabel(_syncedAt);
  el.classList.toggle('stale',(Date.now()-Date.parse(_syncedAt))/1000>45*60);
}
paintFreshness(); setInterval(paintFreshness,60000);

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

  // Queue ordering
  items.push({kind:'ordem',label:'Mais recentes',sub:'ordenar a fila',run:()=>setOrder(ORDER_RECENT)});
  items.push({kind:'ordem',label:'Risco de resposta',sub:'ordenar a fila',run:()=>setOrder(ORDER_RISK)});

  // The decided ledger
  items.push(mode==='tratados'
    ?{kind:'vista',label:'Voltar aos ativos',run:()=>setMode('ativos')}
    :{kind:'vista',label:'Ver tratados',sub:'o que já foi decidido',run:()=>setMode('tratados')});

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
  // The obligation pseudo-bands (the headline chips, reachable by keyboard too)
  items.push({kind:'urgência',label:'a responder',sub:'devemos resposta, vermelho+laranja',run:()=>setFilter('band','risk')});
  items.push({kind:'urgência',label:'a cobrar',sub:'à espera deles há 72h+',run:()=>setFilter('band','chase')});

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
  /* Collapsible section headers (ADR-033 P0). */
  const gh=e.target.closest('.ghead');
  if(gh&&gh.dataset.g!==undefined){ toggleGroup(parseInt(gh.dataset.g,10)); return; }
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
  if(act&&!inThread){dispatch(act.dataset.act,i);e.stopPropagation();}
  /* The whole row is one target (ADR-033 P0): a background click — outside buttons and outside the
     expanded thread — opens the conversation. A pointer that only focuses invites the click and
     discards it (cockpit-design §9). */
  else if(!e.target.closest('.texp')){dispatch('thread',i);}
  else render();
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

/* order picker */
const _so=$('#_order');
if(_so) _so.addEventListener('change',e=>setOrder(e.target.value));

/* owner filter — the invisible palette-only filter, now a visible control. Options: todos /
   sem dono / every roster name (the same filters.owner the palette and URL already use). */
function renderOwnerFilter(){
  const sel=$('#_ownerf'); if(!sel) return;
  const cur=('owner' in filters)?filters.owner:'__all';
  sel.innerHTML='<option value="__all">dono: todos</option><option value="">sem dono</option>'
    +filaRoster.map(n=>'<option value="'+esc(n)+'">@'+esc(n)+'</option>').join('');
  sel.value=[...sel.options].some(o=>o.value===cur)?cur:'__all';
}
const _of=$('#_ownerf');
if(_of) _of.addEventListener('change',e=>{
  const v=e.target.value;
  setFilter('owner', v==='__all'?null:v);
});

/* the headline chips are FILTERS, not labels — each half toggles down to what it counts */
const _rk=$('#_risk');
if(_rk) _rk.addEventListener('click',()=>setFilter('band',filters.band==='risk'?null:'risk'));
const _cb=$('#_cobrar');
if(_cb) _cb.addEventListener('click',()=>setFilter('band',filters.band==='chase'?null:'chase'));

/* ativos ↔ tratados (the decided ledger) */
const _tr=$('#_tratados');
if(_tr) _tr.addEventListener('click',()=>setMode(mode==='tratados'?'ativos':'tratados'));

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
  /* Order: an explicit ?order= wins; otherwise the STORED preference (sticky); otherwise the
     ADR-033 default (risk) — the oldest debts open on top, not at the bottom. */
  const stored=(function(){try{return localStorage.getItem('fila-order');}catch(_e){return null;}})();
  const urlOrder=p.get('order');
  order = urlOrder===ORDER_RISK ? ORDER_RISK
        : urlOrder===ORDER_RECENT ? ORDER_RECENT
        : (stored===ORDER_RECENT ? ORDER_RECENT : ORDER_RISK);
  const so=$('#_order'); if(so) so.value=order;
  renderOwnerFilter();
  sortRows();

  const open = p.get('thread') || '', legacyFocus = p.get('focus') || '';
  rows.forEach(r=>{ r._open=false; });          // the URL owns which thread is expanded
  urlThread = open || null;
  render();
  const tgt = open || legacyFocus;
  if(tgt){
    /* A deep-link may point into a collapsed group (e.g. an AWAITING thread linked from a
       contraparte timeline) — unfold that group in memory (not persisted: the link asked to see
       one thread, not to change the standing preference). */
    const hit = viewAll().find(r => r.thread_root === tgt);
    if(hit && isCollapsed(groupOf(hit))){ collapsed[groupOf(hit)]=false; render(); }
    const i = view().findIndex(r => r.thread_root === tgt);
    if(i>=0){ focus=i;
      if(open) toggleThread(i);                 // expand it (syncURL is a no-op — URL already matches)
      setTimeout(()=>{const el=document.querySelector('.row.on');if(el)el.scrollIntoView({block:'center'});},0);
    } else if(mode==='ativos'){
      /* Not in the active queue — the conversation may already be DECIDED. Fall back to the
         Tratados ledger so a deep-link (e.g. from a contraparte timeline) never lands on nothing. */
      setMode('tratados').then(()=>{
        const j=view().findIndex(r=>r.thread_root===tgt);
        if(j>=0){ focus=j;
          if(open) toggleThread(j);
          setTimeout(()=>{const el=document.querySelector('.row.on');if(el)el.scrollIntoView({block:'center'});},0);
        } else setMode('ativos');               // truly unknown — back to the default view
      });
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
    <button id="_risk" class="risk" aria-live="polite" title="devemos resposta, vermelho+laranja (clica para filtrar)"
      style="font-size:12.5px;font-weight:680;font-variant-numeric:tabular-nums;border-radius:20px;padding:3px 12px;border:1px solid"></button>
    <button id="_cobrar" class="hidden" title="à espera deles há 72h+ — candidatas a cobrança (clica para filtrar)"></button>
    <span id="_count"></span>
    <span id="_fresh" class="fresh" title="idade do correio sincronizado"></span>
    <input id="_search" type="text" placeholder="filtrar…" autocomplete="off" aria-label="Filtrar threads"/>
    <select id="_order" aria-label="Ordenar a fila" title="Ordenar a fila">
      <option value="risk">Risco de resposta</option>
      <option value="recent">Mais recentes</option>
    </select>
    <select id="_ownerf" aria-label="Filtrar por dono" title="Filtrar por dono">
      <option value="__all">dono: todos</option>
      <option value="">sem dono</option>
    </select>
    <button id="_tratados" class="vtoggle" title="o registo do que já foi decidido — reabre com E">tratados</button>
    <span class="cmdk"><kbd>⌘K</kbd> comandos</span>
  </div>
  <div id="_fbar" class="fbar hidden" aria-label="Filtros activos"></div>
  <div id="_list" class="list" role="list" aria-label="Fila de resposta"></div>
  <div id="_zero" class="zero hidden">✓ Tudo tratado<span class="s">nada está a cair · 0 em risco</span></div>
  <div class="hint"><b>J/K</b> mover · <b>Enter</b> abrir · <b>E</b> tratado · <b>A</b> dono · <b>Z</b> desfazer · <b>⌘K</b> comandos · <b>?</b> ajuda</div>
</div>
"""

_EXTRA_CSS = """
  /* ── obligation section headers ────────────────────────────────────────
     The Fila's primary partition: ours vs theirs. Sticky, because the answer to "whose move is
     this?" must survive scrolling a 79-thread queue — a header you have scrolled past is a header
     that stopped working. z-index sits under the row menu (which anchors to a row) so an open
     owner/reclass picker is never clipped by a header sliding beneath it. */
  .ghead{position:sticky;top:0;z-index:2;display:flex;align-items:baseline;gap:9px;
    padding:9px 14px 8px;margin:0;background:var(--bg);
    border-bottom:1px solid var(--bd);font-size:11.5px;font-weight:750;
    letter-spacing:.06em;text-transform:uppercase}
  .ghead:not(:first-child){margin-top:16px}
  .ghead .gh-n{font-size:11px;font-weight:700;letter-spacing:0;padding:1px 7px;border-radius:999px;
    font-variant-numeric:tabular-nums}
  /* Lowercase, un-tracked hint — it explains, it does not shout. Hidden on narrow viewports where
     the title + count are what matter. */
  .ghead .gh-s{font-size:11px;font-weight:500;letter-spacing:0;text-transform:none;color:var(--mut2)}
  /* OURS = the actionable pile, carries the red accent that means "work". */
  .ghead.owe{color:var(--red);border-bottom-color:#f3c9c9}
  .ghead.owe .gh-n{background:#fbeaea;color:var(--red)}
  /* THEIRS = deliberately muted. This section is a status report, not a to-do list; giving it equal
     visual weight is what made the queue feel like 79 things to do instead of 34. */
  .ghead.wait{color:var(--mut)}
  .ghead.wait .gh-n{background:var(--bd2);color:var(--mut)}
  .ghead.other{color:var(--ext)}
  .ghead.other .gh-n{background:var(--bd2);color:var(--ext)}
  @media (max-width:820px){ .ghead .gh-s{display:none} }
  /* Collapsible headers (ADR-033 P0): the chevron is the affordance, the count keeps working folded. */
  .ghead{cursor:pointer}
  .ghead:hover .gh-t{text-decoration:underline}
  .gchev{font-size:10px;color:var(--mut2);width:11px;display:inline-block;flex:0 0 auto}
  /* Hollow dot = we already replied, the ball is theirs. Colour keeps meaning urgency. */
  .clock.wait .d{background:transparent;box-shadow:inset 0 0 0 1.5px currentColor}
  /* Off-focus trust dot: dashed ring = proposto, solid = confirmado (the chip returns on focus). */
  .tdot{display:inline-block;width:7px;height:7px;border-radius:50%;flex:0 0 auto;vertical-align:middle}
  .tdot.proposed{border:1.5px dashed var(--mut2);background:transparent}
  .tdot.committed{border:1.5px solid var(--int);background:var(--int)}
  /* The chase chip — the amber half of the honest headline. */
  #_cobrar{color:var(--amber);background:#fdf4e3;border:1px solid #f0dcb0;cursor:pointer;
    font-family:inherit;font-size:12.5px;font-weight:680;font-variant-numeric:tabular-nums;
    border-radius:20px;padding:3px 12px}
  #_cobrar:hover{filter:brightness(.96)}
  #_cobrar.filtering{outline:2px solid var(--amber);outline-offset:1px}
  /* Freshness stamp — amber once the mail behind the clocks is older than 45 min. */
  .fresh{color:var(--mut2);font-size:11.5px;font-variant-numeric:tabular-nums}
  .fresh.stale{color:var(--amber);font-weight:700}

  /* Fila-specific (shared thread CSS lives in cockpit_ui) */
  .risk{color:var(--red);background:#fbeaea;border-color:#f3c9c9!important;cursor:pointer;font-family:inherit}
  .risk:hover{filter:brightness(.96)}
  .risk.filtering{outline:2px solid var(--red);outline-offset:1px}
  .risk.clear{color:var(--green);background:#e7f6ee;border-color:#bfe6cf!important}
  .risk.pulse{animation:pop .35s ease}
  /* view toggle: ativos ↔ tratados (the decided ledger) */
  .vtoggle{border:1px solid var(--bd);background:var(--card);color:var(--mut);border-radius:8px;
    padding:4px 10px;font-size:12.5px;font-weight:600;cursor:pointer;font-family:inherit}
  .vtoggle:hover{border-color:var(--ac);color:var(--ac)}
  .vtoggle.on{background:var(--ac);border-color:var(--ac);color:#fff}
  /* owner filter select — same chrome as #_order */
  #_ownerf{border:1px solid var(--bd);border-radius:8px;padding:4px 8px;font-size:12.5px;
    font-family:inherit;color:var(--tx);background:var(--card);outline:none;cursor:pointer}
  #_ownerf:hover{border-color:var(--ac);color:var(--ac)}
  /* reply draft box (inside the expanded thread) */
  .pchip.draft{background:#fff;border-color:var(--bd);color:var(--mut)}
  .pchip.draft:hover{border-color:var(--purple);color:var(--purple);background:#f7f4fd}
  .draftbox{margin-top:8px;flex-basis:100%}
  .draftbox textarea{width:100%;border:1px solid var(--bd);border-radius:9px;padding:9px 11px;
    font:12.5px/1.5 inherit;color:var(--tx);background:#fffdf8;resize:vertical}
  .draftbox .dfoot{display:flex;align-items:center;gap:9px;margin-top:5px}
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
  /* order picker — "Mais recentes" (default) vs "Risco de resposta" */
  #_order{border:1px solid var(--bd);border-radius:8px;padding:4px 8px;font-size:12.5px;
    font-family:inherit;color:var(--tx);background:var(--card);outline:none;cursor:pointer}
  #_order:hover{border-color:var(--ac);color:var(--ac)}
  #_order:focus{border-color:var(--ac)}
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
                    *, now_iso: str = "", synced_at: str = "",
                    nav_counts: dict[str, int] | None = None) -> str:
    return cockpit_ui.page(
        "Fila",
        "fila",
        _BODY_HTML,
        embeds={"rows": rows, "team": list(team or []), "now": now_iso,
                # Freshness (ADR-033 P0): when the mail behind the clocks was last synced.
                "synced_at": synced_at,
                "labels": _labels.fila_labels()},
        lens_js=_LENS_JS,
        nav_counts=nav_counts,
        extra_css=_EXTRA_CSS,
    )
