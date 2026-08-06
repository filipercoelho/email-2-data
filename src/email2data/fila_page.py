"""Fila lens page — the «Mesa com Foco» response cockpit (home at ``/`` and ``/fila``).

ADR-033: a full-width split pane — bounded, group-collapsed queue on the left; an evidence dossier
on the right that auto-mounts the focused (riskiest, at load) thread and reuses the SAME thread
renderer (``msgHTML`` kit + per-root cache) the inline expansion used: one render path, no fork.
Counterparty fronts (Hoje · Clientes · Fornecedores · Leads) are first-class tabs; obligation stays
the partition *within* each front (ADR-029, refined). Risk is the default order.

Thin wrapper over ``cockpit_ui.page()``: this module owns only the Fila-specific data shaping and
the lens JS (state + render + paletteItems + onKey).
NEVER sends mail; writes go through /api/thread/handled, /api/thread/owner and /api/reclassify.
"""

from __future__ import annotations

from typing import Any

from . import cockpit_ui, labels as _labels

_LENS_JS = r"""
/* ── Fila lens state ────────────────────────────────────────────────── */
let rows = ROWS.slice();
/* Why an empty queue is empty (ADR-045): no grants, or genuinely nothing left. Read with a
   typeof guard like SYNCED_AT/NEEDS_REVIEW, so an older embed payload cannot ReferenceError
   the whole lens. */
const _noScopes = (typeof NO_SCOPES !== 'undefined') && !!NO_SCOPES;
/* Focus is CONTENT-KEYED (ADR-033 P1, prerequisite for the live poll): `focusRoot` names the
   conversation; `focus` is the derived index into view() for rendering/data-i. A re-render or a
   queue reorder re-derives the index from the root, so the caret can never silently re-point at a
   different conversation. */
let focus = 0;
let focusRoot = null;
let filters = {};   /* active filters — keys: counterparty, purpose, band, owner, domain,
                       hasAttachment, minAgeDays, search. Pass null to remove a key.
                       band pseudo-values: 'risk' = WE_OWE red|amber (the «a responder» chip),
                       'chase' = AWAITING amber, i.e. past the 72h chase threshold («a aguardar»). */
let _prevRisk = null, urlThread = null;
/* The «Registo do fio» value whose evidence is currently lit (§Phase 3), or null. Module state, NOT
   a field on the row: `refresh()` replaces every row object every 15 s and hand-copies a fixed list
   of underscore fields, so anything parked on the row would silently vanish on the next tick. Kept
   here, renderDossier() re-applies it after every re-render instead. */
let _evKey = null;
/* 'ativos' (default) or 'tratados' — the decided ledger (rows fetched lazily from
   /api/fila?include=resolved). A decision must be reviewable after it is made, not vanish. */
let mode = 'ativos', resolvedRows = null, resolvedErr = null;
/* Counterparty front (ADR-033): 'all' (Hoje) | 'CLIENT' | 'SUPPLIER' | 'LEAD'. A tab is a subset of
   the one queue in the one order — never a second data structure. */
let tab = 'all';

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
  /* Sticky: the chosen order survives the next visit (an explicit URL ?order= still wins on load). */
  try{localStorage.setItem('fila-order',o);}catch(_e){}
  const sel=$('#_order'); if(sel&&sel.value!==o) sel.value=o;
  sortRows(); focus=0; focusRoot=null; syncURL(); render();
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
  focus=0; focusRoot=null; syncURL(); render();
}
function clearFilters(){
  filters={};
  const si=$('#_search'); if(si) si.value='';
  focus=0; focusRoot=null; syncURL(); render();
}

/* ── URL state ──────────────────────────────────────────────────────────
   Deep-link state rides in the query string (ADR-014):
     ?tab=<CLIENT|SUPPLIER|LEAD> — the counterparty front ('all'/Hoje stays out of the URL)
     ?counterparty= ?purpose= ?band= ?owner= ?domain= ?attachment=1 ?minDays= ?search= — filters
     ?order=recent        — queue ordering (omitted = the ADR-033 default 'risk')
     ?thread=<root>       — the focused conversation (mounted in the dossier)
   Kept in sync with replaceState; the legacy ?focus=<root> link still focuses that row. */
function syncURL(){
  const p = new URLSearchParams();
  if(tab!=='all') p.set('tab', tab);
  if(filters.counterparty) p.set('counterparty', filters.counterparty);
  if(filters.purpose) p.set('purpose', filters.purpose);
  if(filters.band) p.set('band', filters.band);
  if('owner' in filters) p.set('owner', filters.owner||'');
  if(filters.domain) p.set('domain', filters.domain);
  if(filters.hasAttachment) p.set('attachment','1');
  if(filters.minAgeDays!=null) p.set('minDays', String(filters.minAgeDays));
  if(filters.search) p.set('search', filters.search);
  if(vista!=='fila') p.set('vista', vista);
  if(order!==ORDER_RISK) p.set('order', order);   /* the default stays out of the address bar */
  if(urlThread) p.set('thread', urlThread);
  const base = location.pathname.split('?')[0];
  const qs = p.toString(), url = base + (qs ? ('?'+qs) : '');
  if(location.pathname + location.search !== url){ try{history.replaceState(null,'',url);}catch(_){} }
}

/* ── obligation grouping (ADR-029, refined by ADR-033) ──────────────────
   semGroup() carries the SEMANTIC id (owe / chase / wait / other); groupOf() returns that group's
   TAB-AWARE RANK, so the same one-line stable partition lets Fornecedores lead with «A aguardar»
   while Hoje/Clientes lead with «Precisam de resposta». Collapse is keyed semantically — folding
   «À espera deles» folds the same pile on every tab, whatever rank the tab gives it. */
const G_OWE=0, G_CHASE=1, G_WAIT=2, G_OTHER=3, G_BILL=4, G_PAY=5, G_INFO=6;
function semGroup(r){
  if(r.group!=null) return r.group;                 /* derived in Python build_fila — single source (ADR-036) */
  const c=r.clock||{};                              /* fallback (pre-ADR-036 row): mirror cockpit.fila_group() */
  if(c.state==='WE_OWE') return G_OWE;
  if(c.state==='TO_PAY') return G_PAY;
  if(c.state==='INFO') return G_INFO;
  if(c.state==='AWAITING'){ if(c.band==='amber') return (r.purpose==='OUTBOUND_INVOICE')?G_BILL:G_CHASE; return G_WAIT; }
  return G_OTHER;
}
/* Ball is OURS (filled clock dot) when we owe a reply OR a payment; everything else is their move. */
function isOurMove(r){ const g=semGroup(r); return g===G_OWE||g===G_PAY; }
const TAB_SEQ={
  all:[G_OWE,G_PAY,G_BILL,G_CHASE,G_WAIT,G_INFO,G_OTHER], CLIENT:[G_OWE,G_BILL,G_CHASE,G_WAIT,G_INFO,G_OTHER,G_PAY],
  SUPPLIER:[G_CHASE,G_PAY,G_OWE,G_BILL,G_WAIT,G_INFO,G_OTHER], LEAD:[G_OWE,G_BILL,G_CHASE,G_WAIT,G_INFO,G_OTHER,G_PAY]};
function groupOf(r){ return TAB_SEQ[tab].indexOf(semGroup(r)); }
/* PT-PT, phrased as the answer to "who has the ball", keyed by SEMANTIC id. «A cobrar» is now GENUINE
   billing only (our unpaid OUTBOUND_INVOICE, G_BILL); the follow-up pile is «A aguardar» (G_CHASE);
   «A pagar» (G_PAY) is an inbound supplier bill WE must pay (ADR-036 Bug 2); «Informações» (G_INFO)
   is FYI, no action. The group is the folded OBLIGATION (Python build_fila), not a clock reading. */
const G_LABEL={0:'Precisam de resposta', 1:'A aguardar', 2:'À espera deles', 3:'Internos', 4:'A cobrar', 5:'A pagar', 6:'Informações'};
const G_HINT ={0:'a bola está do nosso lado', 1:'sem resposta deles há 72h+ — candidatas a seguimento',
               2:'já respondemos — a bola está do lado deles', 3:'sem relógio de resposta',
               4:'faturas nossas por liquidar — a receber do cliente',
               5:'faturas recebidas por liquidar — a bola está do nosso lado',
               6:'notificações — sem ação necessária'};
const G_CLASS={0:'owe', 1:'chase', 2:'wait', 3:'other', 4:'bill', 5:'pay', 6:'info'};

/* ── group collapse (ADR-033 P0) ────────────────────────────────────────
   «À espera deles» (and Internos) are status reports, not to-do lists — they START collapsed to a
   counted header, and any group can be folded. The choice persists (localStorage). Collapsed rows
   leave view() entirely, so J/K, focus and data-i can never land on an invisible row. */
const DEFAULT_COLLAPSED={[G_WAIT]:true,[G_INFO]:true,[G_OTHER]:true};
let collapsed=(function(){
  try{const s=localStorage.getItem('fila-collapsed'); return s?JSON.parse(s):{...DEFAULT_COLLAPSED};}
  catch(_e){return {...DEFAULT_COLLAPSED};}
})();
function isCollapsed(sg){ return mode!=='tratados' && !!collapsed[sg]; }
function toggleGroup(sg){
  collapsed[sg]=!collapsed[sg];
  try{localStorage.setItem('fila-collapsed',JSON.stringify(collapsed));}catch(_e){}
  focus=0; focusRoot=null; render();
}

/* ── view with multi-filter ─────────────────────────────────────────────
   viewAll() = tab + filters + group partition (the full set — headline and group counts read this).
   view() = viewAll() minus collapsed groups (what is actually rendered and keyboard-walked). */
/* Fixed vistas (ADR-033 §4 — no view builder): 'fila' (grouped default) · 'money' (€ em jogo —
   WE_OWE with an AI-estimated value, largest first, explicitly bannered as proposed) · 'prazos'
   (extracted deadlines, nearest first). Flat-rendered; the risk order stays the only default. */
let vista='fila';
function view(){
  const va=viewAll();
  if(mode==='tratados') return va;
  if(vista==='money') return va.filter(r=>((r.entities||{}).money_value!=null)&&((r.clock||{}).state==='WE_OWE'))
    .slice().sort((a,b)=>(b.entities.money_value)-(a.entities.money_value));
  if(vista==='prazos') return va.filter(r=>(r.entities||{}).deadline)
    .slice().sort((a,b)=>((Date.parse(a.entities.deadline)||0)-(Date.parse(b.entities.deadline)||0)));
  return va.filter(r=>!isCollapsed(semGroup(r)));
}
function setVista(k){
  if(k!=='fila'&&k!=='money'&&k!=='prazos') return;
  vista=k; focus=0; focusRoot=null; syncURL(); render();
}
function viewAll(){
  const src = mode==='tratados' ? (resolvedRows||[]) : rows;
  const out = src.filter(r=>{
    if(tab!=='all' && (r.counterparty||'')!==tab) return false;
    if('counterparty' in filters && (r.counterparty||'')!==filters.counterparty) return false;
    if('band' in filters){
      const c=r.clock||{}, b=c.band;
      /* Pseudo-bands carry OBLIGATION, not just colour (ADR-033): see the `filters` comment. */
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
      const hay=[(r.subject||''),(r.contact||''),(r.display_name||''),(r.counterparty||''),(r.purpose||'')].join(' ').toLowerCase();
      if(!hay.includes(q)) return false;
    }
    return true;
  });
  /* The ledger ("tratados") is one homogeneous pile — grouping there would draw a boundary with
     nothing on the other side of it. Group the ACTIVE queue only. Array.sort is stable (ES2019+),
     and `rows` is already ordered by sortRows(), so partitioning by group rank here preserves the
     chosen sort WITHIN each group — no second sort key needed. */
  if(mode==='tratados') return out;
  return out.sort((a,b)=>groupOf(a)-groupOf(b));
}

/* Honest headline counts (ADR-033): only what actually demands the user. */
function respondCount(list){ return list.filter(r=>{const c=r.clock||{};return c.state==='WE_OWE'&&(c.band==='red'||c.band==='amber');}).length; }
function chaseCount(list){ return list.filter(r=>{const c=r.clock||{};return c.state==='AWAITING'&&c.band==='amber';}).length; }

/* ── counterparty tabs (ADR-033) ────────────────────────────────────── */
const TABS=[['all','Hoje'],['CLIENT','Clientes'],['SUPPLIER','Fornecedores'],['LEAD','Leads']];
function tabCounts(){
  const cts={all:0,CLIENT:0,SUPPLIER:0,LEAD:0};
  rows.forEach(r=>{cts.all++; if(cts[r.counterparty]!=null) cts[r.counterparty]++;});
  return cts;
}
function setTab(t){
  if(tab===t||!TAB_SEQ[t]) return;
  tab=t; focus=0; focusRoot=null; syncURL(); render();
}
function cycleTab(d){
  const ks=TABS.map(x=>x[0]);
  setTab(ks[(ks.indexOf(tab)+d+ks.length)%ks.length]);
}
const FRONT_META={all:{lab:'Hoje'},CLIENT:{lab:'Clientes'},SUPPLIER:{lab:'Fornecedores'},LEAD:{lab:'Leads'}};
/* Per-front demand (ADR-034): scoped to the counterparty REGARDLESS of the active front, so each
   card always tells its own truth. Hoje = the whole active queue. */
function frontDemand(k){
  const s = k==='all' ? rows : rows.filter(r=>(r.counterparty||'')===k);
  return {total:s.length, resp:respondCount(s), chase:chaseCount(s), novo:s.some(r=>r.novo)};
}
function renderFronts(){
  const el=$('#_fronts'); if(!el) return;
  el.innerHTML=TABS.map(([k,lab])=>{
    const d=frontDemand(k), on=tab===k;
    let fs;
    if(mode==='tratados'){ fs='<span class="fmut">registo</span>'; }
    else if(d.resp>0||d.chase>0){
      const bits=[];
      if(d.resp>0) bits.push('<b class="r">'+d.resp+'</b> a responder');
      if(d.chase>0) bits.push('<b class="c">'+d.chase+'</b> a aguardar');
      fs=bits.join('<span class="fdiv">·</span>');
    } else {
      /* Calm at zero — colour only appears when something demands you (the design guardrail). */
      fs='<span class="ok">'+(k==='LEAD'?(d.novo?'novo hoje':'sem leads novos'):'em dia')+'</span>';
    }
    const dot=k!=='all'?'<span class="mdot '+k+'" aria-hidden="true"></span>':'';
    return '<button class="fc'+(on?' on':'')+'" role="tab" aria-selected="'+on+'" data-tab="'+k+'">'
      +'<span class="fn">'+dot+lab+'<span class="tot">'+d.total+'</span></span>'
      +'<span class="fs">'+fs+'</span></button>';
  }).join('');
}

/* ── vistas rail (fixed set — no view builder, ADR-033 §4; scoped + iconic, ADR-034) ──────────
   One stroke glyph per vista so the rail scans by SHAPE before words; the keyboard digit moves to
   a hover-only chip (ending the two-numbers-per-row illusion); counts are SCOPED to the active
   front (so a rail number can never contradict the front card above it — the 58-vs-32 confusion);
   and a facet only earns a row when it would filter to a MEANINGFUL subset (0 < count < total) —
   «Sem dono 121/121» discriminates nothing, so it hides. */
const V_ICON={
  risco:'<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.3"/><path d="M12 7.7V12l3 2.3"/></svg>',
  money:'<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.3"/><path d="M15 9.2a3.4 3.4 0 0 0-5.8 2.4c0 2.4 1.9 2.4 2.8 2.4M8.6 12h4.8"/></svg>',
  prazos:'<svg viewBox="0 0 24 24"><path d="M6 21V4"/><path d="M6 4.5h10.5L14.2 8l2.3 3.4H6"/></svg>',
  aguardar:'<svg viewBox="0 0 24 24"><path d="M4.5 11a7.5 7.5 0 0 1 13-4.4M19.5 13a7.5 7.5 0 0 1-13 4.4"/><path d="M17.5 3.2v3.4h-3.4M6.5 20.8v-3.4h3.4"/></svg>',
  tratados:'<svg viewBox="0 0 24 24"><path d="M4.5 12.5l4.8 4.8L19.5 7"/></svg>'};
function renderRail(){
  const el=$('#_vrail'); if(!el) return;
  /* Scope every count to the active front — see the block comment. */
  const act = tab==='all' ? rows : rows.filter(r=>(r.counterparty||'')===tab);
  const scopeLab=(FRONT_META[tab]||{}).lab||'Hoje';
  const nR=respondCount(act), nC=chaseCount(act);
  const semD=act.filter(r=>!(r.owners&&r.owners.length)).length;
  const attN=act.filter(r=>r.has_attachment).length;
  const draftN=act.filter(r=>r.can_draft).length;
  const pc={}; act.forEach(r=>{ if(r.purpose) pc[r.purpose]=(pc[r.purpose]||0)+1; });
  const tops=Object.entries(pc).sort((a,b)=>b[1]-a[1]).slice(0,4);
  const purLab=k=>(LABELS.purpose&&LABELS.purpose[k])||k.toLowerCase().replace(/_/g,' ');
  const nM=act.filter(r=>((r.entities||{}).money_value!=null)&&((r.clock||{}).state==='WE_OWE')).length;
  const nD=act.filter(r=>(r.entities||{}).deadline).length;
  const vit=(vk,lab,cnt,key,on)=>'<button class="vit'+(on?' on':'')+'" data-vista="'+vk+'">'
      +V_ICON[vk]+'<span class="vl">'+lab+'</span>'
      +(cnt!=null?'<span class="vc">'+cnt+'</span>':'')+'<kbd class="kh">'+key+'</kbd></button>';
  let h='<div class="rl">Vistas <span class="scope">· '+esc(scopeLab)+'</span></div>'
    +vit('risco','Em risco',nR,'1',(mode==='ativos'&&vista==='fila'&&!('band' in filters)))
    +vit('money','€ em jogo',nM,'2',vista==='money')
    +vit('prazos','Prazos',nD,'3',vista==='prazos')
    +vit('aguardar','A aguardar',nC,'4',(vista==='fila'&&filters.band==='chase'))
    +vit('tratados','Tratados',null,'5',mode==='tratados');
  const est=[];
  if(tops.length) h+='<div class="rl">Tipo de pedido</div>'
    +tops.map(([k,n])=>'<button class="fit'+(filters.purpose===k?' on':'')+'" data-fpur="'+esc(k)+'">'+esc(purLab(k))+'<span class="vc">'+n+'</span></button>').join('');
  if(semD>0&&semD<act.length) est.push('<button class="fit'+(('owner' in filters&&filters.owner==='')?' on':'')+'" data-fest="semdono">Sem dono<span class="vc">'+semD+'</span></button>');
  if(attN>0&&attN<act.length*0.9) est.push('<button class="fit'+(filters.hasAttachment?' on':'')+'" data-fest="anexo">Com anexo<span class="vc">'+attN+'</span></button>');
  if(draftN>0) est.push('<button class="fit ro" disabled title="rascunhos prontos — ✍ nas linhas">Com rascunho ✍<span class="vc">'+draftN+'</span></button>');
  /* «rever N» leaves the strip (owner feedback) — NEEDS_REVIEW is Para ti's business; it lands
     here as a quiet Estado facet linking there, hidden at zero. */
  if(_needsReview>0) est.push('<button class="fit rev" data-fest="rever">Rever classificação<span class="vc">'+_needsReview+'</span></button>');
  if(est.length) h+='<div class="rl">Estado</div>'+est.join('');
  el.innerHTML=h;
}

/* ── filter bar ─────────────────────────────────────────────────────── */
const _FLABEL = {
  counterparty: v=>'contraparte: '+v,
  purpose: v=>'tipo: '+v.toLowerCase().replace(/_/g,' '),
  band: v=>({'red':'urgente','amber':'a atrasar','green':'recente','risk':'a responder','chase':'a aguardar'}[v]||v),
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
  /* Re-derive the caret from its content key (see `focusRoot`). */
  if(focusRoot){
    const fi=v.findIndex(r=>r.thread_root===focusRoot);
    if(fi>=0) focus=fi;
    else { focus=Math.max(0,Math.min(focus,v.length-1)); focusRoot=v[focus]?v[focus].thread_root:null; }
  } else {
    focus=Math.max(0,Math.min(focus,v.length-1));
    focusRoot=v[focus]?v[focus].thread_root:null;
  }
  /* The demand headline lives INSIDE the fronts now (ADR-034) — no abstract strip number. */
  renderFronts(); renderRail(); renderFbar();
  const zero=$('#_zero');
  if(zero){
    zero.classList.toggle('hidden',v.length>0);
    if(!v.length){
      const noRes=hasFilters()&&(mode==='tratados'?(resolvedRows||[]):rows).length>0;
      zero.innerHTML=mode==='tratados'&&resolvedErr
        /* The list never loaded. «Nada tratado ainda» would be an assertion about a server we
           could not reach — the empty screen is our failure, not the ledger's content. */
        ?'Não foi possível carregar os tratados<span class="s">'+esc(resolvedErr)
          +' · o registo continua lá — recarrega para tentar outra vez</span>'
        :(mode==='tratados'&&!noRes
        ?'Nada tratado ainda<span class="s">as decisões que marcares como tratadas ficam registadas aqui</span>'
        :(noRes
          ?'Sem resultados<span class="s">nenhuma thread corresponde aos filtros activos</span>'
          :(_noScopes
            /* ADR-045. An empty queue has two very different causes and they must never render the
               same. «Tudo tratado» over a queue the reader was simply never granted is the ADR-040
               dishonest-refusal defect reborn one layer down: the app stating, on no evidence, that
               there is nothing to do — and the person acts on it by walking away from waiting work.
               A missing GRANT is a fact the app knows; it says it. */
            ?'Sem caixas atribuídas<span class="s">não tens nenhuma caixa de correio atribuída, por isso esta fila está vazia — pede a um administrador que te atribua uma</span>'
            :(tab==='LEAD'
              ?'Sem leads novos<span class="s">bom sinal — este separador acende quando chegar um</span>'
              :'✓ Tudo tratado<span class="s">nada está a cair · 0 a responder</span>'))));
    }
  }
  announce(mode==='tratados'?(v.length+' tratados')
    :(v.length?S.threads(v.length)+' por tratar'
      :(_noScopes?'Sem caixas atribuídas':'Tudo tratado')));

  /* Honest vista banner: the € lens is explicitly AI-estimated, never presented as fact. */
  const vb=$('#_vbanner');
  if(vb){
    if(mode!=='tratados'&&vista==='money'){ vb.textContent='€ em jogo — valores estimados pela IA (tracejado = proposto), maiores primeiro'; vb.classList.remove('hidden'); }
    else if(mode!=='tratados'&&vista==='prazos'){ vb.textContent='Prazos extraídos dos emails — do mais próximo para o mais distante'; vb.classList.remove('hidden'); }
    else vb.classList.add('hidden');
  }
  /* «Tratar agora» progress banner (F). */
  const fo=$('#_foco');
  if(fo){
    if(focoMode&&mode==='ativos'&&v.length){
      fo.classList.remove('hidden');
      fo.innerHTML='<b>Tratar agora</b> — '+(focus+1)+' de '+v.length
        +' · <kbd>E</kbd> tratado · <kbd>H</kbd> adiar · <kbd>R</kbd> responder · <kbd>→</kbd> salta · <kbd>Esc</kbd> sai';
    } else { fo.classList.add('hidden'); if(focoMode&&!v.length) focoMode=false; }
  }
  /* Bulk selection bar — the verbs are tratado/dono ONLY, structurally: a mass silent bin is the
     one unrecoverable triage mistake, so bulk IGNORE does not exist as a control (ADR-033 §2.11). */
  if(mode==='ativos') selected.forEach(root=>{ if(!rows.some(r=>r.thread_root===root)) selected.delete(root); });
  const sb=$('#_selbar');
  if(sb){
    if(selected.size&&mode==='ativos'){
      sb.classList.remove('hidden');
      sb.innerHTML='<b>'+selected.size+' selecionadas</b>'
        +'<button class="act-btn accept" data-bulk="handled">✓ Tratar todas</button>'
        +'<button class="act-btn" data-bulk="owner">@ Dono</button>'
        +'<button class="act-btn" data-bulk="clear">limpar</button>';
    } else { sb.classList.add('hidden'); sb.innerHTML=''; }
  }

  /* Section sizes from the UN-collapsed set (va), so a folded header still carries its true count.
     Flat renders (the ledger + the money/prazos vistas) skip headers entirely. */
  const flat=(mode==='tratados')||(vista!=='fila');
  const src=flat?v:va;
  const gCount={}; if(!flat) for(const r of va){const g=groupOf(r); gCount[g]=(gCount[g]||0)+1;}
  let lastG=null, vi=0;

  const list=$('#_list');
  list.innerHTML=src.map(r=>{
    const c=r.clock||{},tr=r.trust||{};
    /* Headers ride in the SAME map as the rows. The map walks va (all groups, so a collapsed group
       still emits its counted header) while `vi` indexes into view() — collapsed rows return only
       their header, so data-i / focus / j/k keep matching exactly what is rendered. */
    let head='';
    if(!flat){
      const g=groupOf(r), sg=TAB_SEQ[tab][g];
      if(g!==lastG){
        lastG=g;
        head='<div class="ghead '+G_CLASS[sg]+'" data-g="'+sg+'" role="button" '
          +'title="'+(isCollapsed(sg)?'expandir secção':'encolher secção')+'">'
          +'<span class="gchev" aria-hidden="true">'+(isCollapsed(sg)?'▸':'▾')+'</span>'
          +'<span class="gh-t">'+esc(G_LABEL[sg]||'')+'</span>'
          +'<span class="gh-n">'+(gCount[g]||0)+'</span>'
          +'<span class="gh-s">'+esc(G_HINT[sg]||'')+'</span></div>';
      }
      if(isCollapsed(sg)) return head;   /* folded: the counted header stands in for its rows */
    }
    const i=vi++;
    const en=r.entities||{};
    const cpLabel=(LABELS.counterparty&&LABELS.counterparty[r.counterparty])||r.counterparty||'—';
    const name=r.display_name||r.contact||'(sem contacto)';
    /* The scan line: the readable "what this is" — product_or_service → reason → subject, a
       deterministic fallback chain that demotes «RE: FW:» archaeology to the dossier. */
    const scan=en.product_or_service||tr.reason||r.subject||'(sem assunto)';
    const decided=decidedShort(tr.decided_by);
    const conf=tr.confidence?(' · '+Math.round(tr.confidence*100)+'%'):'';
    /* Off focus, trust collapses to a 2px dot — a repeated label is not signal (§9). */
    const trust=decided
      ?(i===focus
        ?'<span class="trust '+(tr.committed?'committed':'proposed')+'" title="'+esc(tr.reason||'')+'">'+esc(decided)+conf+'</span>'
        :'<span class="tdot '+(tr.committed?'committed':'proposed')+'" title="'+esc(decided)+conf+'"></span>')
      :'';
    /* «sem dono» renders only on the focused row; owned rows always show their owner. */
    const ownerBtn=(((r.owners&&r.owners.length)||i===focus)
      ?'<button class="owner'+((r.owners&&r.owners.length)?'':' empty')+'" data-act="owner" aria-label="atribuir donos">'+ownerLabel(r)+'</button>'
      :'');
    /* Row badges FILTER (the natural gesture); correcting the verdict lives in the dossier.
       Monogram, not the full word (P4a): «Cliente» ×58 is the repeated-label sin — the colour rail
       + one letter + tooltip carry the identity in 20px instead of 60. */
    const cpPill=(tab==='all')
      ?'<button class="cp mono '+esc(r.counterparty||'OTHER')+'" data-act="fcp" title="'+esc(cpLabel)+' — clica para filtrar">'+esc((cpLabel||'?').charAt(0))+'</button>'
      :'';
    /* Entity chips render ONLY when informative — a missing extraction is absence, never «—».
       The AI-estimated € wears a trailing «?» and dashed styling: proposed, not fact. */
    const chips=[
      r.novo?'<span class="rchip novo" title="novo contacto — primeiro email há menos de 14 dias">novo</span>':'',
      en.money?'<span class="rchip money" title="valor estimado pela IA — proposto, não confirmado">'+esc(en.money)+'?</span>':'',
      en.deadline?_ddlChip(en.deadline):'',
      (r.related_count||0)>0?'<span class="rchip rel" title="'+(r.related_count)+' conversas relacionadas (mesmo contacto ou entidade partilhada)">↻'+r.related_count+'</span>':'',
      r.can_draft?'<span class="rchip draft" title="rascunho de resposta pronto">✍</span>':'',
      _attChip(r),
    ].join('');
    /* The compact clock (P4a): «devemos resposta há 13 dias» ×58 ate ~30% of every row saying what
       the group header already says — the NUMBER is the signal. Full sentence in the tooltip. */
    const ageTxt=(c.age_hours!=null)?((c.age_hours>=48)?(Math.round(c.age_hours/24)+' d'):(Math.round(c.age_hours||0)+' h')):'';
    return head
      +'<div class="row cpr-'+esc(r.counterparty||'OTHER')+(i===focus?' on':'')+(selected.has(r.thread_root)?' picked':'')+'" data-i="'+i+'" role="listitem"'+(i===focus?' aria-current="true"':'')+' tabindex="0">'
      /* Motion is triaged like the mail: only the CRITICAL red tier pulses (WE_OWE ≥3 days).
         `wait` hollows the dot whenever the ball is NOT ours (wait + chase): colour carries
         URGENCY, fill carries OBLIGATION — a row read outside its section still says whose move. */
      +'<span class="clock '+esc(c.band||'none')+((c.band==='red'&&(c.age_hours||0)>=72)?' crit':'')
      +(!isOurMove(r)?' wait':'')
      +'" title="'+esc(c.label||'')+'"><span class="d" aria-hidden="true"></span>'+esc(ageTxt)+'</span>'
      +'<div class="rmain" data-act="thread" title="abrir no dossiê (Enter) · '+r.n_messages+' mensagens">'
      +'<div class="rline">'+cpPill+'<b class="rname">'+esc(name)+'</b>'
      +'<span class="rscan">'+esc(scan)+'</span></div>'
      +(i===focus?'<div class="rmeta"><span class="mtxt">'+esc(r.subject||'')+'</span></div>':'')
      +'</div>'
      +'<span class="rchips">'+chips+trust+'</span>'
      +ownerBtn
      +'</div>';
  }).join('');
  renderDossier();
}

/* Typed attachment chip (ADR-034): «📎PDF» / «📎CAD +2» — for a fabrication shop the CAD/vetor/PDF
   distinction tells you whether quoting means opening a file. Falls back to a plain 📎 when the crm
   has no typed kinds yet (pre-v4 db, before the next rebuild). */
const _K_LABEL={cad:'CAD',vetor:'VETOR',pdf:'PDF',folha:'FOLHA',img:'IMG',doc:'DOC',zip:'ZIP'};
const _K_ORDER=['cad','vetor','pdf','folha','img','doc','zip'];
function _attChip(r){
  const ks=r.attach_kinds||[];
  if(!ks.length) return r.has_attachment?'<span class="rchip att" title="com anexo">📎</span>':'';
  const ranked=_K_ORDER.filter(k=>ks.indexOf(k)>=0), more=ranked.length-1;
  return '<span class="rchip att typed" title="anexos: '+esc(ranked.map(k=>_K_LABEL[k]||k).join(', '))+'">📎'
    +esc(_K_LABEL[ranked[0]]||ranked[0])+(more>0?' +'+more:'')+'</span>';
}

/* Deadline chip: days-left from the client-stated date; red once it passed. */
function _ddlChip(iso){
  const t=Date.parse(iso); if(!t) return '';
  const days=Math.ceil((t-Date.now())/86400000);
  if(days<0) return '<span class="rchip ddl late" title="prazo indicado pelo cliente — já passou ('+esc(iso)+')">⚑ há '+(-days)+' d</span>';
  if(days===0) return '<span class="rchip ddl late" title="prazo indicado pelo cliente: '+esc(iso)+'">⚑ hoje</span>';
  return '<span class="rchip ddl" title="prazo indicado pelo cliente: '+esc(iso)+'">⚑ '+days+' d</span>';
}

/* ── the dossier (ADR-033 §6): the decision carries its evidence ─────── */
function focusedRow(){ const v=view(); return v[focus]||null; }

async function ensureThread(r){
  if(!r) return;
  const c0=_threadCache[r.thread_root];
  if(c0){ r._threadMsgs=c0.messages; r._facts=c0.facts; r._decisions=c0.decisions; r._ledgerProj=c0.proj;
          r._att=c0.att||null; r._narr=c0.narr||null; return; }
  if(r._threadBusy) return;
  r._threadBusy=true; r._threadErr=null; renderDossier();
  try{
    const d=await getJSON('/api/thread/'+encodeURIComponent(r.thread_root));
    if(d.error){ r._threadErr=d.error; }
    else{
      /* A per-thread key must be threaded in THREE places or it breaks in a way that looks
         intermittent: here, the cache-hit branch above, and refresh()'s carry list. Para Ti shipped
         with the attachment funnel in two of the three and never rendered it once. */
      _threadCache[r.thread_root]={messages:d.messages, facts:d.facts||[],
                                   decisions:d.decisions||[], proj:d.ledger_project||null,
                                   att:d.attachments||null, narr:d.narrative||null};
      r._threadMsgs=d.messages; r._facts=d.facts||[];
      r._decisions=d.decisions||[]; r._ledgerProj=d.ledger_project||null;
      r._att=d.attachments||null; r._narr=d.narrative||null;
    }
  }catch(e){ r._threadErr='falhou ao carregar'; }
  r._threadBusy=false; renderDossier();
}

function initialsOf(s){
  const p=String(s||'?').trim().split(/[\s.@_-]+/).filter(Boolean);
  return ((p[0]||'?')[0]+((p[1]||'')[0]||'')).toUpperCase();
}

/* One «Registo do fio» row. A named function, not an inline template, so the suite can EXECUTE it
   (test_fila.py::test_the_ledger_row_builder_widens_by_length_and_keeps_the_whole_value) instead of
   grepping for a shape that renders wrong.

   The value WRAPS and is clamped to 3 lines (CSS). Two consequences handled here:
     · a value past LG_WIDE chars spans the whole grid, where 3 lines actually hold it — driven by
       LENGTH, not by key, so a short «corte MDF» keeps its compact cell. 60 sits just above the
       measured p90 of product_or_service (51) and at the max of client_name (60).
     · a clamp HIDES text, so the untruncated value must stay reachable: it goes in `title=`.
   `esc()` escapes &<>" but NOT ' — hence the double-quoted attribute. Do not switch it to single
   quotes: a real product name is «chapa 3" inox», and the wrong quoting ends the attribute there.

   LG_WIDE lives INSIDE the function on purpose: the guard executes this source in node, and a
   module-scope constant would ReferenceError there — a helper that cannot be run cannot be tested. */
function _lgRow(k,label,f,nMore){
  const LG_WIDE=60;
  const val=String(f.value);
  /* data-lgk is the click target for the evidence highlight (§Phase 3). The KEY only — the value is
     re-read from r._facts, so a 226-char product name is not duplicated into a DOM attribute. */
  return '<div class="lg-r lg-k-'+k+(val.length>LG_WIDE?' wide':'')
    +'" data-lgk="'+k+'" title="'+esc(val)+'">'
    +'<small>'+label+'</small><b class="'+(f.fact?'solid':'prop')+'">'
    +esc(val)+(f.fact?'':'?')+'</b><span>'+esc(f.date||'')
    +(nMore>0?' · +'+nMore+' menções':'')+'</span></div>';
}

function dossierHTML(r){
  const c=r.clock||{}, tr=r.trust||{}, cl=r.cluster||{};
  const cpLabel=(LABELS.counterparty&&LABELS.counterparty[r.counterparty])||r.counterparty||'—';
  const purLabel=(LABELS.purpose&&LABELS.purpose[r.purpose])||(r.purpose?String(r.purpose).toLowerCase().replace(/_/g,' '):'');
  const name=r.display_name||r.contact||'';
  const decided=decidedShort(tr.decided_by);
  const conf=tr.confidence?(' · '+Math.round(tr.confidence*100)+'%'):'';
  /* Ritmo survives inline on the clock line (P4a) — the tile grid is gone. */
  const MOM={active:['Ativo','var(--green)'],slowing:['A abrandar','var(--amber)'],stalled:['Parado','var(--red)']};
  const mom=MOM[r.momentum];
  let h='<div class="dtop">'
    +'<button class="cp '+esc(r.counterparty||'OTHER')+'" data-act="reclassCp" title="contraparte: '+esc(cpLabel)+' — clica para corrigir">'+esc(cpLabel)+'</button>'
    +'<span class="dclock clock '+esc(c.band||'none')+(!isOurMove(r)?' wait':'')+'"><span class="d" aria-hidden="true"></span>'+esc(c.label||'')+'</span>'
    +(mom?'<span class="dritmo" title="cadência da conversa" style="color:'+mom[1]+'">· '+mom[0]+'</span>':'')
    +'<button class="pur'+(tr.committed?' committed':'')+'" data-act="reclassPur" title="tipo: '+esc(purLabel)+' — clica para corrigir">'+esc(purLabel)+'</button>'
    +'<span class="dgrow"></span>'
    +'<button class="owner'+((r.owners&&r.owners.length)?'':' empty')+'" data-act="owner" aria-label="atribuir donos">'+ownerLabel(r)+'</button>'
    +'</div>'
    +'<h1 class="dsubj">'+esc(r.subject||'(sem assunto)')+'</h1>';
  /* Verb bar — printed keys teach the fast path (the palette-as-trainer pattern). R and H are
     honestly disabled until Phase 3 ships them: a verb that explains itself beats a missing verb. */
  h+='<div class="dverbs">'
    +'<button class="verb good" data-act="handled">✓ '+(mode==='tratados'?'Reabrir':'Tratado')+'<kbd>E</kbd></button>'
    +'<button class="verb" data-act="reply" title="rascunho contextual — o tipo da conversa escolhe o modelo; nada é enviado">✍ Responder<kbd>R</kbd></button>'
    +'<button class="verb" data-act="snooze" title="adiar — acorda na data OU quando responderem">Adiar<kbd>H</kbd></button>'
    +'<button class="verb" data-act="owner">@ Dono<kbd>A</kbd></button>'
    +'<button class="verb" data-act="'+(r.project?'openproj':'mkproj')+'">▦ '+(r.project?'Abrir projeto':'Criar projeto')+'<kbd>P</kbd></button>'
    +'</div>';
  const en=r.entities||{};
  /* Análise IA — dashed = proposta; the reason is ALWAYS visible here (it was a hidden click),
     and the literal ask («Pedem:») leads when extraction caught it. */
  if(decided||tr.reason||en.action_requested){
    h+='<div class="dai'+(tr.committed?' committed':'')+'"><div class="dai-h"><span class="dai-k">Análise IA'+(tr.committed?' — confirmada':' — proposta')+'</span>'
      +(decided?'<span class="trust '+(tr.committed?'committed':'proposed')+'">'+esc(decided)+conf+'</span>':'')
      +'</div>'
      +(en.action_requested?'<p class="dpedem"><b>Pedem:</b> '+esc(en.action_requested)+'</p>':'')
      +(tr.reason?'<p>'+esc(tr.reason)+'</p>':'')
      +'</div>';
  }
  /* «Evolução da conversa» (ADR-054 Phase 5) — how this negotiation got here, so someone arriving
     now understands it without reading the fio. Its OWN block, deliberately not inside `.dai`:
     that block is conditional on `decided||tr.reason||en.action_requested` and a narrative rendered
     inside it would vanish on exactly the threads where all three are empty. Absent is silent — a
     one-message thread never gets a narrative, and an empty heading would be noise on 610 of 767
     threads. Each step carries the id of the message it came from and scrolls to it on click:
     every beat is grounded in one message, and the reader can check. */
  if(r._narr&&(r._narr.steps||[]).length){
    h+='<div class="dnarr"><div class="lg-h">Evolução da conversa</div><ol class="nsteps">'
      +r._narr.steps.map(s=>'<li data-nmid="'+esc(s.message_id||'')+'" title="ver a mensagem de onde saiu">'
        /* A handful of real interactions carry no `date` at all, so the chip is omitted rather than
           rendered blank — an empty box beside the text reads as a broken render, not as a gap. */
        +(s.date?'<span class="nd">'+esc(s.date)+'</span>':'')
        +'<span class="nt">'+esc(s.text||'')+'</span></li>').join('')
      +'</ol>'
      +(r._narr.state?'<p class="nstate">'+esc(r._narr.state)+'</p>':'')
      +'</div>';
  }
  /* «Registo do fio» (ADR-033 P4a, owner request): the ledger — one place where every fact the
     pipeline extracted across ALL the thread's messages accumulates (with its source message +
     date; NIF/IBAN are checksum FACTs and render solid, the rest dashed «?» until confirmed),
     followed by every HUMAN decision taken on the thread. Absence is one quiet line, never a
     grid of dashes. */
  const FK={money:'Valor',deadline:'Prazo',product_or_service:'Produto / serviço',
            action_requested:'Pedido',client_name:'Nome',nif:'NIF',iban:'IBAN'};
  if(r._facts===undefined){
    h+='<div class="dledger"><div class="lg-h">Registo do fio</div><div class="lg-none">a carregar registo…</div></div>';
  } else {
    const facts=r._facts||[], decs=r._decisions||[];
    const latest={}; facts.forEach(f=>{ latest[f.key]=f; });   /* chronological — last write wins */
    const factRows=Object.keys(FK).filter(k=>latest[k]).map(k=>
      _lgRow(k,FK[k],latest[k],facts.filter(x=>x.key===k).length-1)).join('');
    const decBits=[];
    if(r._ledgerProj) decBits.push('<span class="lg-d proj">▦ '+esc(r._ledgerProj.title||'')
      +' · '+esc(String(r._ledgerProj.stage||'').toLowerCase())
      +((r._ledgerProj.fields_confirmed||0)>0?' · '+r._ledgerProj.fields_confirmed+' campos':'')+'</span>');
    decs.forEach(d0=>{
      if(d0.kind==='reclass') decBits.push('<span class="lg-d">corrigido '+esc(d0.field)+' → '+esc(d0.value)+'</span>');
      else if(d0.kind==='owners') decBits.push('<span class="lg-d">dono @'+esc(d0.value)+'</span>');
      else if(d0.kind==='handled') decBits.push('<span class="lg-d">tratado '+esc(d0.value)+'</span>');
      else if(d0.kind==='snooze') decBits.push('<span class="lg-d">adiada até '+esc(d0.value)+'</span>');
    });
    h+='<div class="dledger"><div class="lg-h">Registo do fio</div>'
      +(factRows?'<div class="lg-facts">'+factRows+'</div>'
        :'<div class="lg-none">sem factos extraídos deste fio</div>')
      +(decBits.length?'<div class="lg-decs">'+decBits.join('')+'</div>'
        :'<div class="lg-none">sem decisões humanas registadas</div>')
      +'</div>';
  }
  /* Counterparty history — the cluster rollup the server already computes per request. */
  h+='<div class="dcp">'
    +'<span class="dcp-av cp '+esc(r.counterparty||'OTHER')+'" aria-hidden="true">'+esc(initialsOf(name))+'</span>'
    +'<span class="dcp-n"><b>'+esc(name)+'</b><small>'+esc(r.contact||'')+'</small></span>'
    +(cl.msg_count!=null?'<span class="dcp-s"><b>'+cl.msg_count+'</b><small>mensagens</small></span>':'')
    +((cl.we_owe_count||0)>0?'<span class="dcp-s"><b class="dred">'+cl.we_owe_count+'</b><small>a responder</small></span>':'')
    +((cl.open_projects||0)>0?'<span class="dcp-s"><b>'+cl.open_projects+'</b><small>projetos</small></span>':'')
    /* Gate-1 readiness (denormalized): the next click is predictable — quote, or chase fields. */
    +((r.project&&r.project.coverage!=null)
      ?'<span class="dcp-s"><b'+(r.project.estimable?' class="dgreen"':'')+'>'+Math.round((r.project.coverage||0)*100)+'%</b><small>'
        +(r.project.estimable?'pronto a orçamentar':'campos do spec')+'</small></span>':'')
    +'</div>';
  /* Related threads (ADR-034/-037): jump-links to other conversations with the same contact or a
     shared entity (NIF/name/e-mail/product) — assemble context before replying, never double-
     answer. Each item is labelled with WHY it's related (mesmo contacto vs. a specific shared
     field) and dotted with the OTHER thread's own momentum, reusing MOM from above — a same-
     contact hit that's long stalled reads differently from one still active. */
  const RELPT={contacto:'mesmo contacto',client_name:'nome',client_email:'e-mail',nif:'NIF',iban:'IBAN',product_or_service:'produto'};
  const rel=r.related||[];
  if(rel.length){
    h+='<div class="drel"><div class="drel-h">↻ '+rel.length+' conversa'+(rel.length===1?'':'s')+' relacionada'+(rel.length===1?'':'s')+'</div>'
      +rel.map(x=>{
        const rm=MOM[x.momentum];
        const dot='<span class="drel-dot" style="background:'+(rm?rm[1]:'var(--mut2)')+'" title="'+(rm?rm[0]:'sem atividade recente')+'"></span>';
        const isEnt=x.reason&&x.reason!=='contacto';
        const tag='<span class="drel-tag'+(isEnt?' ent':'')+'">'+esc(RELPT[x.reason]||x.reason||'')+'</span>';
        return '<a class="drel-i" data-relroot="'+esc(x.thread_root)+'" href="?thread='+encodeURIComponent(x.thread_root)+'" title="abrir esta conversa">'
          +dot+tag+'<span class="drel-s">'+esc(x.subject)+'</span></a>';
      }).join('')
      +'</div>';
  }
  /* Conversa: the vertical in/out timeline (the spine is integrated into _threadHTML now). */
  h+='<div class="dmsgs">'+_threadHTML(r)+'</div>';
  return h;
}

function renderDossier(){
  const el=$('#_doss'); if(!el) return;
  const r=focusedRow();
  if(!r){
    el.innerHTML='<div class="dempty">✉<b>'+(mode==='tratados'?'Nada tratado ainda':'Nada em foco')+'</b>'
      +'<span>o dossiê — evidência, contexto da contraparte e ações — aparece aqui</span></div>';
    return;
  }
  r._open=true;   /* the thread renderer's contract: an unfocused row is simply never passed in */
  el.innerHTML=dossierHTML(r);
  applyEvidence(r);
  if(r._threadMsgs==null&&!r._threadErr&&!r._threadBusy) ensureThread(r);
}

/* Re-paint the picked value's evidence after every dossier render — innerHTML above destroys the
   Ranges the Custom Highlight API holds, and the 15 s refresh re-renders whether or not anything
   changed. Absence is stated, not silent: 40% of extracted values are in the email text in NO form
   (they were ISO-normalised or paraphrased by the model), so «sem evidência visível» is a real and
   frequent answer, and printing nothing would read as a broken click. */
function applyEvidence(r){
  const d=$('#_doss'); if(!d||!r) return;
  evClear();
  /* Exactly one value is ever lit (decision D2) — clear the last one before lighting this one. */
  d.querySelectorAll('.lg-r.picked,.lg-r.noev').forEach(x=>x.classList.remove('picked','noev'));
  if(!_evKey) return;
  const cell=d.querySelector('[data-lgk="'+_evKey+'"]');
  if(!cell){ _evKey=null; return; }     /* the value is gone from this thread's ledger */
  cell.classList.add('picked');
  const fs=(r._facts||[]).filter(x=>x.key===_evKey);
  const f=fs.length?fs[fs.length-1]:null;   /* chronological — last write wins, as the ledger renders */
  /* f.quote is the ADR-054 located sentence, present on a minority of facts and absent everywhere
     until the locate pass has run. evHighlight uses it ONLY when the value itself is nowhere on
     screen, so a tree with no evidence.jsonl behaves exactly as it did before Phase 4. */
  const n=f?evHighlight($('#_doss'),_evKey,f.value,f.quote):0;
  if(!n) cell.classList.add('noev');
}

/* ── conversation timeline helpers (ADR-034 P5c) ─────────────────────────
   The thread is a VERTICAL timeline, newest→oldest, with the time difference between consecutive
   messages shown as a gap chip: minutes < 1 h, hours < 24 h, days above. The chip's connector
   height is banded (never linear), so one long silence can't eat the screen. */
function _fmtGap(ms){
  const mins=ms/60000;
  if(mins<60) return Math.max(1,Math.round(mins))+' min';
  const h=mins/60;
  if(h<24) return Math.round(h)+' h';
  const d=Math.round(h/24);
  return d+(d===1?' dia':' dias');
}
/* Mirrors cockpit._humanize_age EXACTLY (min <1h · «N h» <48h · else FLOOR days) so the timeline's
   open-debt chip reads the SAME number as the dossier/row clock label. _fmtGap ROUNDS days (fine for
   a gap BETWEEN two messages) but the debt must match the header, or you get «devemos resposta há 11
   dias» beside «sem resposta há 12 dias» — the round-vs-floor drift the owner flagged. */
function _humanizeAge(h){
  if(h<1) return Math.floor(h*60)+' min';
  if(h<48) return Math.round(h)+' h';
  return Math.floor(h/24)+' dias';
}
function _gapBand(ms){ const h=ms/3600000; return h>=168?'g7':(h>=24?'g3':'g1'); }
const CLOCK_ICON='<svg class="dicon" viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.3"/><path d="M12 7.7V12l3 2.3"/></svg>';
/* end-timeline */

/* ── mutations (optimistic + undo) ──────────────────────────────────── */
function handle(i){
  const v=view(),r=v[i]; if(!r) return;
  const at=rows.indexOf(r);
  /* Act-and-advance (ADR-033): the caret moves to the next riskiest BEFORE the row leaves. */
  const nxt=v[i+1]||v[i-1];
  const commit=()=>{
    rows.splice(at,1);
    if(resolvedRows) resolvedRows.unshift(r);   /* it belongs to the ledger now (if loaded) */
    focusRoot=nxt?nxt.thread_root:null;
    undo.push({label:S.tratado,revert:()=>{
      rows.splice(Math.min(at,rows.length),0,r);
      if(resolvedRows){const ri=resolvedRows.indexOf(r); if(ri>=0) resolvedRows.splice(ri,1);}
      focusRoot=r.thread_root;
      render();post('/api/thread/handled',{thread_root:r.thread_root,handled:false}).catch(()=>toast(S.undoFalhou));}});
    announce(S.tratado); render();
    post('/api/thread/handled',{thread_root:r.thread_root,handled:true}).catch(()=>{
      rows.splice(Math.min(at,rows.length),0,r);
      if(resolvedRows){const ri=resolvedRows.indexOf(r); if(ri>=0) resolvedRows.splice(ri,1);}
      focusRoot=r.thread_root;
      undo.pop();render();toast(S.revertido);});
  };
  const el=document.querySelector('.row[data-i="'+i+'"]');
  if(el&&!reduceMotion()){let done=false;const go=()=>{if(done)return;done=true;commit();};
    el.classList.add('leaving');el.addEventListener('transitionend',go,{once:true});setTimeout(go,240);}
  else commit();
}

/* ── the Tratados ledger (mode='tratados') ────────────────────────── */
async function setMode(m){
  if(m!==('ativos')&&m!==('tratados')) return;
  if(m===mode) return;
  mode=m; focus=0; focusRoot=null;
  if(m==='tratados'&&resolvedRows===null){
    const list=$('#_list'); if(list) list.innerHTML='<div class="row"><span class="tsum">a carregar tratados…</span></div>';
    try{
      const d=await getJSON('/api/fila?include=resolved');
      resolvedRows=(d.rows||[]).filter(r=>((r.clock||{}).state==='HANDLED'));
      resolvedRows.sort((a,b)=>cmpOrderKey((b.order_keys||{}).recent,(a.order_keys||{}).recent));
      resolvedErr=null;
    }catch(e){
      /* Do NOT set resolvedRows=[]. null means "not loaded"; [] means "loaded, and there is
         nothing" — and the zero-state reads the difference to decide between «Nada tratado ainda»
         and an honest failure. Collapsing them is how a failed request became a factual claim. */
      resolvedRows=null; resolvedErr=(e&&e.status===0)?'sem resposta do servidor':'falhou';
      toast(S.falhou);
    }
  }
  render();
}
async function reopenThread(i){
  const v=view(), r=v[i]; if(!r) return;
  const at=resolvedRows?resolvedRows.indexOf(r):-1; if(at<0) return;
  resolvedRows.splice(at,1); focusRoot=null; render();
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
    const d=await getJSON('/api/fila');
    rows=(d.rows||[]); sortRows(); if(mode==='ativos') render();
  }catch(e){
    /* Keep the rows we have. The old code let a 401 body through `d.rows||[]`, blanking the queue
       and rendering «✓ Tudo tratado · 0 a responder» — the app stating, on no evidence, that there
       is nothing left to do. Better a queue a few seconds stale than a confident lie. */
  }
}

/* ── owners (multi) — picked from the roster; "+ novo dono" adds to it ─── */
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

function positionMenu(i){
  const m=$('#_menu'), row=document.querySelector('.row[data-i="'+i+'"]')||$('#_doss .dtop');
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

/* ── reclassify: correct the LLM verdict from the dossier ─────────────
   The dossier's counterparty/purpose badges open this picker (row badges now FILTER). */
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

/* ── thread cache (shared with the dossier) ─────────────────────────── */
const _threadCache = {};   // thread_root → messages array (fetch-once)

/* project banner: open the existing project, or offer to create one (no dupes) */
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
       +'<button class="act-btn open" data-act="opendraft" title="'+esc(_mailToTitle(r))+'">✉ Abrir no mail</button>'
       +'<span class="tsum">rascunho'+(r._draftKind?' · modelo: '+esc(r._draftKind):'')+' — revê antes de enviar · a app nunca envia</span></div></div>'
      :'');
  /* The summary line keeps the CHRONOLOGICAL array — its date range must read "first → last". */
  const head='<div class="thead">'+_projHTML(r)+draftBtn+'<span class="tsum">'+esc(msgThreadSummary(msgs))+'</span></div>';
  /* The VERTICAL timeline (ADR-034 P5c): a left spine with a direction-coloured dot per message;
     newest at the top; inbound and outbound offset to opposite sides and tinted so who-said-what
     reads at a glance; a gap chip between cards shows the time difference; and the segment from the
     newest message up to «agora» is the OPEN response debt — drawn in the clock's band colour
     (hollow when the ball is theirs) WHEN the clock is anchored at that message, and as a plain
     elapsed gap when it is not (ADR-051). */
  const c=r.clock||{};
  /* The open debt (now → newest message) uses the AUTHORITATIVE clock age (server-computed, the
     same value the row/dossier clock shows) — NOT a client-side wall-clock recompute, which drifts
     a day out of step over time (the «10 dias» vs «11 dias» bug). Verb follows the clock state. */
  const debtVerb=(c.state==='AWAITING')?'à espera há ':'sem resposta há ';
  const debtMs=(c.age_hours!=null)?c.age_hours*3600000:0;
  /* ADR-051: this chip SPANS «agora» → the newest message, so it may only speak as the response
     debt when the clock is anchored THERE. When the obligation sits on an older message (an inbound
     bill we answered but have not paid; a handled thread), the honest chip is the plain elapsed
     gap — otherwise it prints «sem resposta há 2 dias» right above a mail we sent an hour ago. The
     obligation age keeps its home in the header pill and the row clock. `!==false` so an older
     cached payload without the flag renders exactly as it did before. */
  const debtOwnsSegment=(c.anchored_at_last!==false);
  const segMs=debtOwnsSegment?debtMs:((c.gap_hours!=null)?c.gap_hours*3600000:debtMs);
  let flow='<div class="dthread">'
    +'<div class="dt-now '+esc(c.band||'none')+(c.state==='AWAITING'?' hollow':'')+'"><span class="dt-dot"></span>agora</div>';
  if(segMs>=1800000)
    flow+='<div class="dt-gap '+(debtOwnsSegment?('debt '+esc(c.band||'none')):_gapBand(segMs))+'"><span class="dt-glab">'
      +esc(debtOwnsSegment?(debtVerb+_humanizeAge(c.age_hours||0)):_fmtGap(segMs))+'</span></div>';
  ordered.forEach((m,i)=>{
    /* Inter-message gaps only (the debt chip above already spans now → newest). */
    if(i>0){
      const t=Date.parse(m.date||'')||0, newerT=Date.parse(ordered[i-1].date||'')||0;
      if(t&&newerT&&newerT>t){
        const gap=newerT-t;
        if(gap>=1800000) flow+='<div class="dt-gap '+_gapBand(gap)+'"><span class="dt-glab">'+esc(_fmtGap(gap))+'</span></div>';
      }
    }
    const dir=(m.direction==='inbound')?'inbound':(m.direction==='internal')?'internal':'outbound';
    flow+='<div class="dt-msg dir-'+dir+'"><span class="dt-dot"></span>'+msgHTML(m)+'</div>';
  });
  flow+='</div>';
  /* The funnel sits ABOVE the timeline: it answers "what did they send us?" for the whole thread,
     which is the question that brings someone here. The per-message 📎 chips stay where they are —
     they answer the different question "what came with THIS message" (ADR-046). */
  return '<div class="texp">'+head+draftBox+attFunnelHTML(r._att)+flow+'</div>';
}

/* ── command bus ────────────────────────────────────────────────────── */
function dispatch(action,i){
  if(action==='handled'){ mode==='tratados'?reopenThread(i):handle(i); }
  else if(action==='owner')ownerMenu(i);
  else if(action==='reclassCp')reclassMenu(i,'counterparty');
  else if(action==='reclassPur')reclassMenu(i,'purpose');
  else if(action==='thread')focusTo(i);
  else if(action==='snooze')snoozeMenu(i);
  else if(action==='reply')contextualReply(i);
  else if(action==='fcp'){const r=view()[i]; if(r) setFilter('counterparty', filters.counterparty===r.counterparty?null:r.counterparty);}
  else if(action==='mkproj')makeProject(i);
  else if(action==='openproj')openProject(i);
  else if(action==='draft')draftReply(i);
  else if(action==='copydraft')copyDraft(i);
  else if(action==='opendraft')openDraftInMail(i);
}

/* Focusing IS opening: the dossier mounts the focused conversation (one render path). */
function focusTo(i){
  const v=view(); if(!v[i]) return;
  if(focusRoot!==v[i].thread_root) _evKey=null;   /* evidence belongs to the thread it was found in */
  focus=i; focusRoot=v[i].thread_root;
  urlThread=focusRoot; syncURL(); render();
}

/* ── Adiar (ADR-033 P3): optimistic, undoable, and it can never lose a client —
   build_fila wakes the thread on the date OR on their next inbound, whichever first. */
function snoozeMenu(i){
  const r=view()[i]; if(!r) return;
  const m=$('#_menu');
  m.innerHTML='<div class="mhdr">Adiar — acorda antes se responderem</div>'
    +'<div class="mi" data-sn="amanha">amanhã 09:00</div>'
    +'<div class="mi" data-sn="segunda">2ª feira 09:00</div>'
    +'<div class="mi" data-sn="semana">daqui a 7 dias</div>'
    +'<div class="mi reset" data-sn="limpar">não adiar</div>';
  m.dataset.i=i; m.dataset.kind='snooze'; m.classList.remove('hidden'); positionMenu(i);
}
function _snoozeUntil(k){
  const d=new Date();
  if(k==='amanha'){ d.setDate(d.getDate()+1); d.setHours(9,0,0,0); }
  else if(k==='segunda'){ d.setDate(d.getDate()+(((8-d.getDay())%7)||7)); d.setHours(9,0,0,0); }
  else { d.setDate(d.getDate()+7); }
  return d.toISOString();
}
async function snoozeThread(i,k){
  const v=view(), r=v[i]; if(!r) return;
  if(k==='limpar'){
    try{ await post('/api/thread/snooze',{thread_root:r.thread_root,until:null}); toast('não adiada'); }
    catch(e){ toast(S.falhou); }
    return;
  }
  const until=_snoozeUntil(k);
  const at=rows.indexOf(r); if(at<0) return;
  const nxt=v[i+1]||v[i-1];
  rows.splice(at,1); focusRoot=nxt?nxt.thread_root:null;
  undo.push({label:'adiada',revert:()=>{
    rows.splice(Math.min(at,rows.length),0,r); focusRoot=r.thread_root; render();
    post('/api/thread/snooze',{thread_root:r.thread_root,until:null}).catch(()=>toast(S.undoFalhou));
  }});
  announce('adiada'); render();
  toast('adiada até '+until.slice(0,10)+' — acorda antes se responderem · Z desfaz');
  try{ await post('/api/thread/snooze',{thread_root:r.thread_root,until}); }
  catch(e){ rows.splice(Math.min(at,rows.length),0,r); focusRoot=r.thread_root; undo.pop(); render(); toast(S.revertido); }
}

/* ── contextual R (ADR-033 §10): the conversation's kind picks the composer ──
   One deterministic endpoint maps purpose × state; a JobSpec thread is redirected to the tested
   /api/reply (the honest-conditional ask draft). NOTHING is ever sent. */
async function contextualReply(i){
  const v=view(), r=v[i]; if(!r||r._draftBusy) return;
  if(r._draft!=null){ r._draft=null; r._draftKind=''; renderDossier(); return; }   /* toggle off */
  r._draftBusy=true; renderDossier();
  try{
    const d=await post('/api/thread/reply-draft',{thread_root:r.thread_root});
    if(d.redirect){ const d2=await post(d.redirect,{message_id:r.message_id}); r._draft=d2.reply||''; }
    else r._draft=d.draft||'';
    r._draftKind=d.kind||'';
  }catch(e){ toast(S.falhou); }
  r._draftBusy=false; renderDossier();
}

/* ── reply draft (the queue that names the debt can start the reply) ─────
   Uses the tested non-streaming /api/reply; only rows with a JobSpec can draft (r.can_draft,
   stamped server-side). NOTHING is ever sent — the draft is copied into the person's own mail. */
async function draftReply(i){
  const v=view(), r=v[i]; if(!r||!r.can_draft||r._draftBusy) return;
  if(r._draft!=null){ r._draft=null; renderDossier(); return; }   /* toggle off */
  r._draftBusy=true; renderDossier();
  try{
    const d=await post('/api/reply',{message_id:r.message_id});
    r._draft=d.reply||'';
  }catch(e){ toast(S.falhou); }
  r._draftBusy=false; renderDossier();
}
function copyDraft(i){
  const r=view()[i]; if(!r||r._draft==null) return;
  (navigator.clipboard?navigator.clipboard.writeText(r._draft):Promise.reject())
    .then(()=>toast('rascunho copiado'))
    .catch(()=>toast(S.falhou));
}

/* ── hand the draft to the person's own mail client (ADR-047) ────────────
   The app never sends, and this does not change that: `mailto:` opens the DEFAULT client with the
   fields pre-filled and stops there — the human still writes, edits and presses send. It replaces
   "copy, switch app, find the thread, paste, retype the address and the subject", which is where a
   drafted reply used to go to die.

   `Re:` is added only when the subject does not already carry one (in either language), so a reply
   to a reply does not become «Re: Re: Re: Orçamento». */
function _replySubject(r){
  const s=(r.subject||'').trim();
  if(!s) return '';
  return /^\s*(re|rv|res|fw|fwd|enc)\s*:/i.test(s)?s:('Re: '+s);
}
function _mailToTitle(r){
  return r.contact?('abrir no cliente de email · para '+r.contact)
                   :'abrir no cliente de email · sem endereço conhecido — escreve-o tu';
}
function openDraftInMail(i){
  const r=view()[i]; if(!r||r._draft==null) return;
  /* encodeURIComponent, not encodeURI: a subject with & or # would otherwise truncate the body, and
     a truncated draft that LOOKS complete in the mail client is the failure mode worth paying one
     extra call to avoid. */
  const url='mailto:'+encodeURIComponent(r.contact||'')
    +'?subject='+encodeURIComponent(_replySubject(r))
    +'&body='+encodeURIComponent(r._draft);
  location.href=url;
  toast(r.contact?('a abrir o mail para '+r.contact):'a abrir o mail — falta o endereço');
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
  /* tab cycling works even on an empty view (an empty Leads tab must not trap the keyboard) */
  if(e.key==='t'){ cycleTab(1); return; }
  if(e.key==='T'){ cycleTab(-1); return; }
  if(e.key==='1'){ setMode('ativos').then(()=>{vista='fila'; setFilter('band',null);}); return; }
  if(e.key==='2'){ setMode('ativos').then(()=>setVista('money')); return; }
  if(e.key==='3'){ setMode('ativos').then(()=>setVista('prazos')); return; }
  if(e.key==='4'){ setMode('ativos').then(()=>{vista='fila'; setFilter('band','chase');}); return; }
  if(e.key==='5'){ setMode('tratados'); return; }
  const v=view(); if(!v.length) return;
  /* Shift+J/K jumps between section starts — reaching «À espera deles» used to cost ~5 screens. */
  if(e.shiftKey&&(e.key==='J'||e.key==='K')){
    const starts=[]; let lg=null;
    v.forEach((r,i)=>{const g=groupOf(r); if(g!==lg){lg=g; starts.push(i);}});
    if(starts.length){
      if(e.key==='J'){const nx=starts.find(s=>s>focus); focus=(nx===undefined)?starts[0]:nx;}
      else{const pv=starts.slice().reverse().find(s=>s<focus); focus=(pv===undefined)?starts[starts.length-1]:pv;}
      focusRoot=v[focus]?v[focus].thread_root:null;
      render(); const el=document.querySelector('.row.on'); if(el)el.scrollIntoView({block:'nearest'});
    }
    e.preventDefault(); return;
  }
  if(e.key==='j'||e.key==='ArrowDown'){focus=Math.min(v.length-1,focus+1);focusRoot=v[focus]?v[focus].thread_root:null;render();const r=document.querySelector('.row.on');if(r)r.scrollIntoView({block:'nearest'});e.preventDefault();}
  else if(e.key==='k'||e.key==='ArrowUp'){focus=Math.max(0,focus-1);focusRoot=v[focus]?v[focus].thread_root:null;render();const r=document.querySelector('.row.on');if(r)r.scrollIntoView({block:'nearest'});e.preventDefault();}
  else if(e.key==='e'||e.key==='E'){ if(selected.size&&mode==='ativos'){bulkHandled();} else dispatch('handled',focus); }
  else if(e.key==='x'){ const r=v[focus]; if(r&&mode==='ativos'){ selected.has(r.thread_root)?selected.delete(r.thread_root):selected.add(r.thread_root); render(); } }
  else if(e.key==='X'){ const r=v[focus]; if(r&&mode==='ativos'){ const sg=semGroup(r); const roots=v.filter(x=>semGroup(x)===sg).map(x=>x.thread_root); const all=roots.every(root=>selected.has(root)); roots.forEach(root=>all?selected.delete(root):selected.add(root)); render(); } }
  else if(e.key==='a'||e.key==='A'){ if(selected.size&&mode==='ativos'){bulkOwnerMenu();} else dispatch('owner',focus); }
  else if(e.key==='h'||e.key==='H')dispatch('snooze',focus);
  else if(e.key==='r'||e.key==='R')dispatch('reply',focus);
  else if(e.key==='f'||e.key==='F'){ focoMode=!focoMode; render(); }
  else if(e.key==='ArrowRight'&&focoMode){ focus=Math.min(v.length-1,focus+1); focusRoot=v[focus]?v[focus].thread_root:null; render(); e.preventDefault(); }
  else if(e.key==='p'||e.key==='P'){const r=v[focus]; if(r)dispatch(r.project?'openproj':'mkproj',focus);}
  else if(e.key==='Enter'||e.key==='o'||e.key==='O'){dispatch('thread',focus);e.preventDefault();}
}

/* ── «Tratar agora» (ADR-033 §3.4): F walks the SAME filtered, risk-ordered queue one decision at
   a time — E/H advance (they already act-and-advance), → skips free, Esc exits. One queue, one
   order, zero divergence: this is a lens over view(), never a second surface. */
let focoMode=false;
function onEsc(){
  if(focoMode){ focoMode=false; render(); return; }
  if(selected.size){ selected.clear(); render(); return; }
  if(hasFilters()) clearFilters();
}

/* ── bulk select (ADR-033 P2): X row · Shift+X group · verbs tratado/dono ONLY ── */
let selected=new Set();
async function bulkHandled(){
  const roots=[...selected]; if(!roots.length) return;
  const removed=[];
  roots.forEach(root=>{const at=rows.findIndex(r=>r.thread_root===root); if(at>=0) removed.push([at,rows[at]]);});
  removed.sort((a,b)=>b[0]-a[0]).forEach(([at])=>rows.splice(at,1));
  selected.clear(); focusRoot=null; render();
  undo.push({label:'tratadas',revert:()=>{
    removed.sort((a,b)=>a[0]-b[0]).forEach(([at,r])=>rows.splice(Math.min(at,rows.length),0,r));
    render();
    roots.forEach(root=>post('/api/thread/handled',{thread_root:root,handled:false}).catch(()=>toast(S.falhou)));
  }});
  announce(roots.length+' tratadas'); toast(roots.length+' tratadas — Z desfaz');
  for(const root of roots){
    try{ await post('/api/thread/handled',{thread_root:root,handled:true}); }
    catch(e){ toast(S.falhou); }
  }
}
function bulkOwnerMenu(){
  if(!selected.size) return;
  const m=$('#_menu');
  m.innerHTML='<div class="mhdr">Dono para '+selected.size+' selecionadas</div>'
    +filaRoster.map(nm=>'<div class="mi" data-n="'+esc(nm)+'">@'+esc(nm)+'</div>').join('');
  m.dataset.kind='bulkowner'; m.classList.remove('hidden');
  const sb=$('#_selbar');
  if(sb){const b=sb.getBoundingClientRect();m.style.top=(window.scrollY+b.bottom+4)+'px';m.style.left=(window.scrollX+b.left)+'px';}
}
async function bulkOwner(name){
  const roots=[...selected]; selected.clear();
  roots.forEach(root=>{const r=rows.find(x=>x.thread_root===root); if(r){r.owners=[name]; r.owner=name;}});
  render(); toast('dono: @'+name+' × '+roots.length);
  for(const root of roots){
    try{ await post('/api/thread/owner',{thread_root:root,owners:[name]}); }
    catch(e){ toast(S.falhou); }
  }
}

/* `/` focuses the visible search box (the natural gesture) — the shell falls back to the palette on
   lenses that define no onSlash. ⌘K keeps the palette here too. */
function onSlash(){ const si=$('#_search'); if(si){ si.focus(); si.select(); } }

/* ── freshness (ADR-033 P0) — now shown by the shell's sync pill (ADR-034 P5d) ─────
   The clocks' honesty depends on sync recency; the shell's setSynced() owns the «correio há N min»
   label (amber when stale) on the merged Sincronizar pill. The poll feeds it fresh values. */
let _syncedAt=(typeof SYNCED_AT!=='undefined'&&SYNCED_AT)?SYNCED_AT:null;
function paintFreshness(){ if(typeof setSynced==='function') setSynced(_syncedAt); }

/* ── NEEDS_REVIEW chip: tier-1 failures finally get a surface (quiet, links to Para ti) ── */
let _needsReview=(typeof NEEDS_REVIEW!=='undefined')?(NEEDS_REVIEW|0):0;
/* «rever N» now lives in the rail's Estado group (ADR-034) — re-render it after a poll updates the
   count. renderRail() is hoisted, so calling it here is safe regardless of source order. */
function paintRever(){ if($('#_vrail')) renderRail(); }

/* ── live refresh (ADR-023 reaches the hero, ADR-033 P2) ────────────────
   The server keeps ingesting on its own schedule; an open tab must converge without a reload.
   Poll the same endpoint, swap IN PLACE keyed by content: focus survives via focusRoot, fetched
   threads and open drafts are carried across by root, and the swap is skipped while a picker is
   open (rows must not shift under a menu). */
const REFRESH_MS=30000;
let _refreshing=false,_lastSig=null;
function _sig(list){
  return list.map(r=>r.thread_root+'|'+((r.clock||{}).state)+'|'+((r.clock||{}).band)+'|'+((r.owners||[]).join(','))).join('\n');
}
async function refresh(opts){
  opts=opts||{};
  if(_refreshing||mode==='tratados') return;
  const menu=$('#_menu');
  if(menu&&!menu.classList.contains('hidden')) return;   /* never swap under an open picker */
  _refreshing=true;
  try{
    const d=await getJSON('/api/fila');
    _syncedAt=d.synced_at||_syncedAt; paintFreshness();
    if(typeof setNavCounts==='function') setNavCounts(d.nav_counts);
    _needsReview=d.needs_review||0; paintRever();
    const next=d.rows||[];
    const sig=_sig(next);
    if(sig===_lastSig&&!opts.force) return;   /* nothing moved — don't clobber the DOM */
    const seen=new Set(rows.map(r=>r.thread_root));
    const added=next.filter(r=>!seen.has(r.thread_root)).length;
    const carry={}; rows.forEach(r=>{carry[r.thread_root]=r;});
    next.forEach(r=>{const o=carry[r.thread_root]; if(o){r._threadMsgs=o._threadMsgs;r._threadErr=o._threadErr;
      r._facts=o._facts;r._decisions=o._decisions;r._ledgerProj=o._ledgerProj;r._att=o._att;r._narr=o._narr;r._draft=o._draft;r._open=o._open;}});
    rows=next; sortRows(); _lastSig=sig;
    render();
    if(!opts.quiet&&added>0) toast(added+(added===1?' nova thread':' novas threads'));
  }catch(e){ /* offline / server restarting — keep showing what we have, retry next tick */ }
  finally{ _refreshing=false; }
}
everyMs(()=>{ if(!document.hidden) refresh(); },REFRESH_MS);
document.addEventListener('visibilitychange',()=>{ if(!document.hidden) refresh(); });
_lastSig=_sig(rows);

/* «Sincronizar» refreshes IN PLACE — the shell's onSynced hook replaces its location.reload(). */
function onSynced(){
  toast(S.sincronizado);
  /* A sync rewrites crm.db and re-runs the locate/narrate passes, so every cached /api/thread
     payload is now potentially wrong — and the staleness was doubly locked: _threadCache is keyed by
     thread_root with no version and was never invalidated anywhere, AND refresh() carries
     _threadMsgs forward, which keeps renderDossier's `if(r._threadMsgs==null) ensureThread(r)` guard
     permanently false after the first open. So a thread that GREW kept serving its pre-sync
     messages, ledger and narrative for the life of the tab. Both halves are dropped here, which is
     the one moment the data can actually have changed. _facts goes back to undefined, not [] — that
     is the ledger's «a carregar registo…» state, and [] would claim the thread has no facts. */
  Object.keys(_threadCache).forEach(k=>{ delete _threadCache[k]; });
  rows.forEach(r=>{ r._threadMsgs=null; r._threadErr=null; r._facts=undefined; r._decisions=null;
                    r._ledgerProj=null; r._att=null; r._narr=null; });
  refresh({force:true,quiet:true});
}

/* ── palette items ──────────────────────────────────────────────────── */
function paletteItems(q){
  q=(q||'').toLowerCase().trim();
  const items=[
    {kind:'ação',label:S.actSync,run:syncNow},
    {kind:'ação',label:S.actUndo,run:doUndo},
    {kind:'ação',label:S.actDensity,run:toggleDensity},
    {kind:'ação',label:'Início',run:()=>{location.href='/';}},
    {kind:'ação',label:'Contrapartes',run:()=>{location.href='/contrapartes';}},
    {kind:'ação',label:'Para ti',run:()=>{location.href='/para-ti';}},
    {kind:'ação',label:'Projetos',run:()=>{location.href='/projetos';}},
    {kind:'ação',label:'Capturas',run:()=>{location.href='/capturas';}},
  ];
  /* «Abrir inbox» only for an admin — /inbox is in _ADMIN_EXACT (it renders the report over EVERY
     message, with no ADR-045 per-person filtering), so a member choosing it got a 403. */
  if(IS_ADMIN) items.push({kind:'ação',label:S.actInbox,run:()=>{location.href='/inbox';}});
  if(hasFilters()) items.unshift({kind:'filtro',label:'limpar filtros',run:clearFilters});

  // Counterparty fronts (the tabs, reachable by name)
  TABS.forEach(([k,lab])=>items.push({kind:'separador',label:lab,sub:'t / T circula',run:()=>setTab(k)}));

  // Queue ordering
  items.push({kind:'ordem',label:'Risco de resposta',sub:'ordenar a fila (padrão)',run:()=>setOrder(ORDER_RISK)});
  items.push({kind:'ordem',label:'Mais recentes',sub:'ordenar a fila',run:()=>setOrder(ORDER_RECENT)});

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
  items.push({kind:'urgência',label:'a aguardar',sub:'à espera deles há 72h+',run:()=>setFilter('band','chase')});

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
    sub:(r.display_name||r.counterparty||'')+' · '+(r.contact||''),
    run:()=>{const i=view().findIndex(x=>x.thread_root===r.thread_root);if(i>=0)focusTo(i);
      const el=document.querySelector('.row.on');if(el)el.scrollIntoView({block:'nearest'});}}));

  return q?items.filter(it=>(it.label+' '+(it.sub||'')+' '+it.kind).toLowerCase().includes(q)):items;
}

/* ── list events ────────────────────────────────────────────────────── */
$('#_list').addEventListener('click',e=>{
  /* Collapsible section headers (ADR-033 P0). */
  const gh=e.target.closest('.ghead');
  if(gh&&gh.dataset.g!==undefined){ toggleGroup(parseInt(gh.dataset.g,10)); return; }
  const row=e.target.closest('.row'); if(!row) return;
  const i=parseInt(row.dataset.i,10);
  const act=e.target.closest('[data-act]');
  if(act){ focus=i; const v=view(); focusRoot=v[i]?v[i].thread_root:focusRoot; dispatch(act.dataset.act,i); e.stopPropagation(); }
  /* The whole row is one target (ADR-033): any background click opens the conversation in the
     dossier — a pointer that only focuses invites the click and discards it (§9). */
  else dispatch('thread',i);
});

/* dossier events: verbs + timeline jumps act on the FOCUSED row */
$('#_doss').addEventListener('click',e=>{
  /* «Registo do fio» → its evidence in the body (§Phase 3, decision D2: ONE accent, driven by the
     click rather than a permanent 7-colour palette). This branch must sit FIRST, ahead of the
     generic verb branch at the bottom, because `dispatch(action,i)` is never handed the element —
     the pattern the related-thread and timeline branches below already use.
     Written without naming that branch's attribute literally: the guard asserts the two appear in
     THIS order in the shipped source, so a prose mention of it here reads as the wrong order and
     fires the test on a comment. (test_the_evidence_branch_runs_before_the_verb_branch) */
  const lg=e.target.closest('[data-lgk]');
  if(lg){
    /* applyEvidence, NOT renderDossier: re-rendering would replace the dossier's innerHTML and so
       throw away whatever the reader had opened («assinatura», «mensagem citada», «ver original»)
       and reset the scroll — on the very click whose job is to show them something. */
    _evKey = (_evKey===lg.dataset.lgk) ? null : lg.dataset.lgk;
    applyEvidence(focusedRow());
    return;
  }
  /* «Evolução da conversa» step → the message it came from. Scrolls in place and never re-renders,
     for the same reason the evidence branch above doesn't: a rebuild would shut whatever the reader
     had opened. 'nearest' because .tbody/.tquote/.tsig are themselves scrollers. */
  const ns=e.target.closest('[data-nmid]');
  if(ns){
    const card=$('#_doss').querySelector('[data-tmid="'+(ns.dataset.nmid||'').replace(/"/g,'\\"')+'"]');
    if(card&&card.scrollIntoView) card.scrollIntoView({block:'nearest'});
    $('#_doss').querySelectorAll('.nsteps li.on').forEach(x=>x.classList.remove('on'));
    ns.classList.add('on');
    return;
  }
  /* related-thread jump-link: focus it in place if it's in the current queue, else navigate. */
  const rl=e.target.closest('[data-relroot]');
  if(rl){
    e.preventDefault();
    const root=rl.dataset.relroot, v=view(), i=v.findIndex(x=>x.thread_root===root);
    /* Not in the current view — reload the Fila focused on it. The prefix is explicit: a bare
       '/?thread=' lands on Início (ADR-044), which reads no query parameter, so the jump would
       drop the very conversation this click asked for. */
    if(i>=0) focusTo(i); else location.href='/fila?thread='+encodeURIComponent(root);
    return;
  }
  const tl=e.target.closest('[data-tl]');
  if(tl){
    const ms=$('#_doss').querySelectorAll('.tmsg');
    const m=ms[parseInt(tl.dataset.tl,10)];
    if(m){ m.scrollIntoView({block:'center'}); m.classList.remove('flash'); void m.offsetWidth; m.classList.add('flash'); }
    return;
  }
  const act=e.target.closest('[data-act]'); if(!act||act.disabled) return;
  dispatch(act.dataset.act, focus);
});
msgWireQuoteToggles($('#_doss'));

$('#_menu').addEventListener('click',e=>{
  const mi=e.target.closest('.mi'); if(!mi) return;
  const m=$('#_menu'), i=parseInt(m.dataset.i,10);
  if(m.dataset.kind==='reclass'){ reclassify(i,m.dataset.field,mi.dataset.val||''); m.classList.add('hidden'); return; }
  if(m.dataset.kind==='bulkowner'){ if(mi.dataset.n) bulkOwner(mi.dataset.n); m.classList.add('hidden'); return; }
  if(m.dataset.kind==='snooze'){ if(mi.dataset.sn) snoozeThread(i,mi.dataset.sn); m.classList.add('hidden'); return; }
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
  focus=0; focusRoot=null; syncURL(); render();
});

/* order picker */
const _so=$('#_order');
if(_so) _so.addEventListener('change',e=>setOrder(e.target.value));

/* owner filter — visible control over the same filters.owner the palette and URL use */
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

/* fronts (the hero cards) + rail */
const _tb=$('#_fronts');
if(_tb) _tb.addEventListener('click',e=>{
  const t=e.target.closest('[data-tab]'); if(t) setTab(t.dataset.tab);
});
const _vr=$('#_vrail');
if(_vr) _vr.addEventListener('click',e=>{
  const vi=e.target.closest('[data-vista]');
  if(vi){
    const k=vi.dataset.vista;
    if(k==='risco'){ setMode('ativos').then(()=>{vista='fila'; setFilter('band',null);}); }
    else if(k==='money'||k==='prazos'){ setMode('ativos').then(()=>setVista(vista===k?'fila':k)); }
    else if(k==='aguardar'){ setMode('ativos').then(()=>{vista='fila'; setFilter('band',filters.band==='chase'?null:'chase');}); }
    else if(k==='tratados'){ setMode(mode==='tratados'?'ativos':'tratados'); }
    return;
  }
  const fp=e.target.closest('[data-fpur]');
  if(fp){ setFilter('purpose',filters.purpose===fp.dataset.fpur?null:fp.dataset.fpur); return; }
  const fe=e.target.closest('[data-fest]');
  if(fe){
    if(fe.dataset.fest==='semdono') setFilter('owner',('owner' in filters&&filters.owner==='')?null:'');
    else if(fe.dataset.fest==='anexo') setFilter('hasAttachment',filters.hasAttachment?null:true);
    /* ?tipo= — land on the «Classificações a rever» GROUP, not the unfiltered Para ti list. The
       chip names one population; dropping the filter made the click answer a wider question than
       it asked. Para ti reads this key in applyURL() and writes it back in syncURL().
       KNOWN AND DELIBERATELY NOT PAPERED OVER: the N on this chip counts NEEDS_REVIEW interactions
       (webapp._needs_review_count) while that group counts rows under the confidence floor
       (para_ti.low_confidence_items) — two different populations, so the destination can hold a
       different number than the chip promised. That is a question about what «Rever classificação»
       means, and it needs an owner's answer, not a silent edit here. */
    else if(fe.dataset.fest==='rever') location.href='/para-ti?tipo=rever_classificacao';
  }
});

/* ── URL ↔ view sync (initial load + Back/Forward) ──────────────────── */
function applyURLState(){
  const p = new URLSearchParams(location.search);
  const tv=p.get('tab');
  tab=(tv==='CLIENT'||tv==='SUPPLIER'||tv==='LEAD')?tv:'all';
  filters = {};
  const cpv=p.get('counterparty'); if(cpv) filters.counterparty=cpv;
  const pv=p.get('purpose'); if(pv) filters.purpose=pv;
  const bv=p.get('band'); if(bv) filters.band=bv;
  if(p.has('owner')) filters.owner=p.get('owner')||'';  // '' = "sem dono" filter
  const dv=p.get('domain'); if(dv) filters.domain=dv;
  if(p.get('attachment')==='1') filters.hasAttachment=true;
  const md=p.get('minDays'); if(md) filters.minAgeDays=parseFloat(md);
  const sv=p.get('search'); if(sv) filters.search=sv;
  const vv=p.get('vista'); vista=(vv==='money'||vv==='prazos')?vv:'fila';
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
  rows.forEach(r=>{ r._open=false; });
  urlThread = open || null;
  const tgt = open || legacyFocus;
  if(tgt){
    /* A deep-link may point into a collapsed group — unfold it in memory (not persisted: the link
       asked to see one thread, not to change the standing preference). */
    const hit = viewAll().find(r => r.thread_root === tgt);
    if(hit && isCollapsed(semGroup(hit))) collapsed[semGroup(hit)]=false;
    focusRoot = tgt;
    render();
    const i = view().findIndex(r => r.thread_root === tgt);
    if(i>=0){
      setTimeout(()=>{const el=document.querySelector('.row.on');if(el)el.scrollIntoView({block:'center'});},0);
    } else if(mode==='ativos'){
      /* Not in the active queue — the conversation may already be DECIDED. Fall back to the
         Tratados ledger so a deep-link never lands on nothing. */
      setMode('tratados').then(()=>{
        const j=view().findIndex(r=>r.thread_root===tgt);
        if(j>=0){ focusRoot=tgt; render();
          setTimeout(()=>{const el=document.querySelector('.row.on');if(el)el.scrollIntoView({block:'center'});},0);
        } else setMode('ativos');               // truly unknown — back to the default view
      });
    }
  } else {
    focusRoot=null;
    render();
  }
  if(legacyFocus && !open){ urlThread=null; syncURL(); }   // canonicalize ?focus= out of the URL
}
window.addEventListener('popstate', applyURLState);
applyURLState();
"""

_BODY_HTML = """
<div class="mesa">
  <div class="bar">
    <!-- The fronts are the hero (ADR-034): each counterparty front is a status-bearing card whose
         OWN demand («N a responder · N a aguardar») lives inside the button it describes — navigation
         and status in one glance, and a count that can never be misread as a global headline. -->
    <div id="_fronts" class="fronts" role="tablist" aria-label="Contraparte"></div>
    <span class="bgrow"></span>
    <input id="_search" type="text" placeholder="/ procurar…" autocomplete="off" aria-label="Filtrar threads"/>
    <select id="_order" class="tsel" aria-label="Ordenar a fila" title="Ordenar a fila">
      <option value="risk">Risco de resposta</option>
      <option value="recent">Mais recentes</option>
    </select>
    <select id="_ownerf" class="tsel" aria-label="Filtrar por dono" title="Filtrar por dono">
      <option value="__all">dono: todos</option>
      <option value="">sem dono</option>
    </select>
    <span class="cmdk"><kbd>⌘K</kbd> comandos</span>
  </div>
  <div id="_fbar" class="fbar hidden" aria-label="Filtros activos"></div>
  <div class="mesa-body">
    <nav id="_vrail" class="vrail" aria-label="Vistas e filtros"></nav>
    <div class="mcol">
      <div id="_foco" class="focobar hidden" aria-live="polite"></div>
      <div id="_selbar" class="selbar hidden" aria-label="Seleção em massa"></div>
      <div id="_vbanner" class="vbanner hidden" aria-live="polite"></div>
      <div id="_list" class="list" role="list" aria-label="Fila de resposta"></div>
      <div id="_zero" class="zero hidden">✓ Tudo tratado<span class="s">nada está a cair · 0 a responder</span></div>
    </div>
    <aside id="_doss" class="mdoss" aria-label="Dossiê da conversa"></aside>
  </div>
  <div class="hint"><b>J/K</b> mover · <b>Shift+J/K</b> secções · <b>Enter</b> abrir · <b>E</b> tratado · <b>A</b> dono · <b>P</b> projeto · <b>X</b> selecionar · <b>T</b> separador · <b>1–5</b> vistas · <b>Z</b> desfazer · <b>/</b> procurar · <b>⌘K</b> comandos · <b>?</b> ajuda</div>
</div>
"""

_EXTRA_CSS = """
  /* ── the Mesa (ADR-033): full width, split pane ───────────────────────
     The 1000px .wrap cap left a third of the screen as dead gutter; the Mesa owns its width. */
  .mesa{max-width:1720px;margin:0 auto;padding:14px 22px 40px}
  .mesa-body{display:flex;gap:14px;align-items:flex-start}
  .mcol{flex:1 1 46%;min-width:520px;min-height:0}
  .mdoss{flex:1 1 54%;min-width:0;position:sticky;top:64px;max-height:calc(100vh - 84px);
    overflow-y:auto;background:var(--card);border:1px solid var(--bd);border-radius:14px;
    padding:16px 18px 22px;box-shadow:var(--shadow)}
  .vrail{flex:0 0 172px;position:sticky;top:64px;display:flex;flex-direction:column;gap:1px}
  @media (max-width:1100px){
    .mesa-body{flex-direction:column}
    .mcol{min-width:0;width:100%}
    .mdoss{position:static;max-height:none;width:100%}
    .vrail{position:static;flex-direction:row;flex-wrap:wrap;gap:4px}
    .vrail .rl{width:100%}
  }
  /* ── counterparty fronts (the hero cards, ADR-034) ─────────────────────
     The demand lives inside the button it describes; the card is calm (no colour) until something
     actually demands you, then the numbers carry the band colour. */
  .bar{align-items:stretch}
  .bgrow{flex:1}
  .fronts{display:flex;gap:8px;align-items:stretch;flex-wrap:wrap}
  .fc{display:flex;flex-direction:column;gap:3px;align-items:flex-start;border:1px solid var(--bd);
    background:var(--card);border-radius:11px;padding:8px 13px;min-width:158px;cursor:pointer;text-align:left;
    font-family:inherit}
  .fc:hover{border-color:var(--ac-line);background:var(--surface2)}
  .fc.on{border-color:var(--ac);background:var(--ac-soft);box-shadow:inset 0 -2.5px 0 var(--ac)}
  .fc .fn{display:flex;align-items:center;gap:7px;font-weight:750;font-size:14px;color:var(--tx)}
  .fc .fn .tot{font-size:10.5px;font-weight:600;font-variant-numeric:tabular-nums;color:var(--mut2)}
  .fc .fs{font-size:11.5px;font-weight:600;color:var(--mut);display:flex;align-items:center;gap:6px}
  .fc .fs b{font-size:12.5px;font-weight:800;font-variant-numeric:tabular-nums}
  .fc .fs b.r{color:var(--red)} .fc .fs b.c{color:var(--amber)}
  .fc .fs .ok{color:var(--green)} .fc .fs .fmut{color:var(--mut2)}
  .fc .fs .fdiv{color:var(--bd)}
  .mdot{width:8px;height:8px;border-radius:50%;display:inline-block;flex:0 0 auto}
  .mdot.CLIENT{background:var(--cli)} .mdot.SUPPLIER{background:var(--forn)} .mdot.LEAD{background:var(--lead)}
  /* right-side controls retreat to a modest scale */
  .tsel{font-size:12px}
  /* ── vistas rail (scoped, iconic) ─────────────────────────────────────── */
  .vrail .rl{font-size:9.5px;font-weight:800;letter-spacing:.09em;text-transform:uppercase;color:var(--mut2);padding:10px 8px 5px}
  .vrail .rl .scope{color:var(--ac);letter-spacing:.04em}
  .vit,.fit{display:flex;align-items:center;gap:9px;border:none;background:none;cursor:pointer;text-align:left;
    font-family:inherit;font-size:12.5px;font-weight:600;color:var(--mut);border-radius:8px;padding:5px 9px;width:100%}
  .vit:hover,.fit:hover{background:var(--bd2);color:var(--tx)}
  .vit.on,.fit.on{background:var(--ac-soft);color:var(--ac);font-weight:700}
  .vit svg{width:15px;height:15px;stroke:currentColor;fill:none;stroke-width:1.7;stroke-linecap:round;
    stroke-linejoin:round;flex:0 0 auto;color:var(--mut2)}
  .vit:hover svg,.vit.on svg{color:currentColor}
  .vit .vl{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .fit{gap:0}
  .vit .vc,.fit .vc{margin-left:auto;font-size:10.5px;font-weight:700;font-variant-numeric:tabular-nums;color:var(--mut2)}
  .fit.rev{color:var(--lead)} .fit.rev .vc{color:var(--lead)}
  /* keyboard digit: hover-only chip, so the count column reads as ONE number per row */
  .vit .kh{opacity:0;transition:opacity .12s ease;font-size:9.5px;color:var(--mut2);background:var(--bd2);
    border-radius:4px;padding:0 4px;font-family:ui-monospace,monospace;margin-left:6px}
  .vit:hover .kh{opacity:1}
  @media (prefers-reduced-motion:reduce){.vit .kh{transition:none}}
  .fit.ro{cursor:default;opacity:.75}
  /* ── obligation section headers (sticky, collapsible) ─────────────── */
  .ghead{position:sticky;top:0;z-index:2;display:flex;align-items:baseline;gap:9px;
    padding:9px 14px 8px;margin:0;background:var(--bg);cursor:pointer;
    border-bottom:1px solid var(--bd);font-size:11.5px;font-weight:750;
    letter-spacing:.06em;text-transform:uppercase}
  .ghead:not(:first-child){margin-top:16px}
  .ghead:hover .gh-t{text-decoration:underline}
  .gchev{font-size:10px;color:var(--mut2);width:11px;display:inline-block;flex:0 0 auto}
  .ghead .gh-n{font-size:11px;font-weight:700;letter-spacing:0;padding:1px 7px;border-radius:999px;
    font-variant-numeric:tabular-nums}
  .ghead .gh-s{font-size:11px;font-weight:500;letter-spacing:0;text-transform:none;color:var(--mut2)}
  .ghead.owe{color:var(--red);border-bottom-color:var(--red-line)}
  .ghead.owe .gh-n{background:var(--red-bg);color:var(--red)}
  .ghead.chase{color:var(--amber);border-bottom-color:var(--amber-line)}
  .ghead.chase .gh-n{background:var(--amber-bg);color:var(--amber)}
  /* genuine billing (G_BILL «A cobrar») — our unpaid invoices; shares the AWAITING-amber urgency family */
  .ghead.bill{color:var(--amber);border-bottom-color:var(--amber-line)}
  .ghead.bill .gh-n{background:var(--amber-bg);color:var(--amber)}
  /* «A pagar» (G_PAY) — inbound bills WE owe; our-move obligation, so the red family like «Precisam» */
  .ghead.pay{color:var(--red);border-bottom-color:var(--red-line)}
  .ghead.pay .gh-n{background:var(--red-bg);color:var(--red)}
  /* «Informações» (G_INFO) — FYI, no action; muted like «À espera deles» */
  .ghead.info{color:var(--mut)}
  .ghead.info .gh-n{background:var(--bd2);color:var(--mut)}
  .ghead.wait{color:var(--mut)}
  .ghead.wait .gh-n{background:var(--bd2);color:var(--mut)}
  .ghead.other{color:var(--ext)}
  .ghead.other .gh-n{background:var(--bd2);color:var(--ext)}
  @media (max-width:820px){ .ghead .gh-s{display:none} }
  /* Hollow dot = the ball is NOT ours (wait + chase). Colour keeps meaning urgency. */
  .clock.wait .d{background:transparent;box-shadow:inset 0 0 0 1.5px currentColor}
  /* ── rows: single line, counterparty rail, clock-first ───────────── */
  .mesa .row{gap:10px;padding:calc(var(--rpad) - 3px) 13px calc(var(--rpad) - 3px) 10px}
  .mesa .row.cpr-CLIENT{border-left-color:var(--cli)!important}
  .mesa .row.cpr-SUPPLIER{border-left-color:var(--forn)!important}
  .mesa .row.cpr-LEAD{border-left-color:var(--lead)!important}
  .mesa .row.on{background:var(--ac-soft)}
  .mesa .clock{min-width:52px;text-align:right;font-size:11.5px}
  .rline{display:flex;align-items:baseline;gap:8px;min-width:0}
  .rname{font-size:var(--rfont);font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:0 1 auto;max-width:220px}
  .rscan{flex:1;min-width:0;color:var(--mut);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  /* the focused row breathes: the name un-clamps and the scan line yields to the subject (rmeta) */
  .mesa .row.on .rname{max-width:none;white-space:normal}
  .mesa .row.on .rscan{display:none}
  .cp.mono{min-width:20px;width:20px;height:20px;padding:0;border-radius:6px;cursor:pointer;border:none;
    font-family:inherit;display:inline-flex;align-items:center;justify-content:center;font-size:10px;flex:0 0 auto}
  .rchips{flex:0 0 auto;display:inline-flex;align-items:center;gap:5px}
  .rchip{font-size:10.5px;color:var(--mut2);font-variant-numeric:tabular-nums}
  .rchip.draft{color:var(--ac);background:var(--ac-soft);border-radius:5px;padding:0 5px}
  /* Off-focus trust dot: dashed ring = proposto, solid = confirmado (the chip returns on focus). */
  .tdot{display:inline-block;width:7px;height:7px;border-radius:50%;flex:0 0 auto;vertical-align:middle}
  .tdot.proposed{border:1.5px dashed var(--mut2);background:transparent}
  .tdot.committed{border:1.5px solid var(--int);background:var(--int)}
  /* ── entity chips (P2): dashed € = proposed, ⚑ deadline, ↻ related, novo ── */
  .rchip.money{color:var(--mut);border:1px dashed var(--mut2);border-radius:5px;padding:0 5px;font-size:10px;font-weight:700}
  .rchip.ddl{color:var(--amber);background:var(--amber-bg);border-radius:5px;padding:0 5px;font-size:10px;font-weight:700}
  .rchip.ddl.late{color:var(--red);background:var(--red-bg)}
  .rchip.novo{color:var(--lead);background:var(--lead-bg);border-radius:5px;padding:0 5px;font-size:10px;font-weight:800}
  .rchip.rel{color:var(--mut2);font-size:10px;font-weight:700}
  /* typed 📎: a small labeled pill (📎CAD) — the plain .att stays a bare glyph for the pre-v4 db */
  .rchip.att.typed{color:var(--mut);background:var(--surface2);border:1px solid var(--bd);
    border-radius:5px;padding:0 5px;font-size:9.5px;font-weight:800;letter-spacing:.02em}
  /* vistas rail extras */
  .vd.ac{background:var(--ac)} .vd.purple{background:var(--purple)}
  .vbanner{margin:0 0 8px;padding:7px 12px;border:1px dashed var(--mut2);border-radius:9px;
    color:var(--mut);font-size:12px;background:var(--card)}
  /* «Tratar agora» banner */
  .focobar{display:flex;align-items:center;gap:8px;margin:0 0 8px;padding:8px 13px;
    border:1px solid var(--ac-line);border-radius:9px;background:var(--ac-soft);font-size:12.5px;color:var(--tx)}
  .focobar b{color:var(--ac)}
  .focobar kbd{background:var(--card);border:1px solid var(--bd);border-radius:4px;padding:0 5px;
    font-family:ui-monospace,monospace;font-size:10.5px}
  /* bulk selection */
  .selbar{display:flex;align-items:center;gap:9px;margin:0 0 8px;padding:7px 12px;
    border:1px solid var(--ac-line);border-radius:9px;background:var(--ac-soft);font-size:12.5px}
  .selbar b{color:var(--ac);font-variant-numeric:tabular-nums}
  .mesa .row.picked{outline:2px solid var(--ac);outline-offset:-2px}
  /* «Registo do fio» — the thread ledger (facts w/ provenance + human decisions) */
  .dledger{border:1px solid var(--bd);border-radius:11px;background:var(--card);padding:11px 14px;margin-bottom:12px}
  .lg-h{font-size:10px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;color:var(--mut);margin-bottom:7px}
  .lg-facts{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:6px 14px;margin-bottom:8px}
  .lg-r{display:flex;flex-direction:column;gap:1px;min-width:0}
  .lg-r small{font-size:9px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;color:var(--mut2)}
  /* The value WRAPS (owner request): truncating it hid the job. Clamped at 3 lines because grid
     rows are auto-sized — one 226-char «Produto / serviço» (the measured max) would otherwise add
     ~15 lines of height to every OTHER cell in its row. `overflow-wrap:anywhere` so an unbroken
     IBAN/reference wraps rather than widening the track. The full value lives in `title=`. */
  .lg-r b{font-size:12.5px;font-variant-numeric:tabular-nums;display:-webkit-box;
    -webkit-line-clamp:3;line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}
  .lg-r.wide{grid-column:1/-1}   /* long value → the whole grid, where 3 clamped lines fit it */
  /* §Phase 3: a value is a button onto its own evidence. Whole-cell target, like the row in §5. */
  .lg-r{cursor:pointer;border-radius:6px;padding:2px 5px;margin:-2px -5px;transition:background .12s}
  .lg-r:hover{background:var(--bd2)}
  .lg-r.picked{background:var(--hl-bg)}
  .lg-r.picked small,.lg-r.picked b,.lg-r.picked span{color:var(--hl-tx)}
  /* Absence, said out loud. 40% of extracted values are in the email in no form at all — a silent
     click would read as broken, and inventing a nearest match would be a zero-hallucination breach. */
  .lg-r.noev::after{content:'sem evidência visível';font-size:9.5px;font-style:italic;color:var(--hl-tx);opacity:.8}
  .lg-r.picked.noev{background:var(--bd2)}
  .lg-r.picked.noev small,.lg-r.picked.noev b,.lg-r.picked.noev span,.lg-r.noev::after{color:var(--mut)}
  .lg-r b.prop{border-bottom:1px dashed var(--mut2)}      /* LLM-extracted: proposto, não confirmado */
  .lg-r b.solid{color:var(--int)}                          /* checksum FACT (NIF/IBAN, ADR-007) */
  .lg-r span{font-size:10px;color:var(--mut2);font-variant-numeric:tabular-nums}
  .lg-decs{display:flex;flex-wrap:wrap;gap:5px;border-top:1px solid var(--bd2);padding-top:8px}
  .lg-d{font-size:10.5px;font-weight:650;color:var(--mut);background:var(--bd2);border-radius:5px;padding:2px 8px}
  .lg-d.proj{color:var(--ac);background:var(--ac-soft)}
  .lg-none{font-size:11.5px;color:var(--mut2);padding:2px 0}
  .dritmo{font-size:11px;font-weight:700}
  .dpedem{margin:0 0 4px}
  .dgreen{color:var(--green)}
  /* related-thread jump-links (ADR-034/-037) */
  .drel{border:1px solid var(--bd);border-radius:11px;background:var(--card);padding:9px 12px;margin-bottom:12px}
  .drel-h{font-size:10px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;color:var(--purple);margin-bottom:5px}
  .drel-i{display:flex;align-items:center;gap:7px;font-size:12px;color:var(--ac);text-decoration:none;padding:5px 0;
    white-space:nowrap;overflow:hidden;border-top:1px solid var(--bd2)}
  .drel-i:first-of-type{border-top:none}
  .drel-i:hover .drel-s{text-decoration:underline}
  .drel-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
  .drel-tag{font-size:9.5px;text-transform:uppercase;letter-spacing:.04em;font-weight:680;border-radius:5px;
    padding:1px 6px;white-space:nowrap;flex-shrink:0;background:var(--ac-soft);color:var(--ac);border:1px solid var(--ac-line)}
  .drel-tag.ent{background:var(--green-bg);color:var(--green);border-color:var(--green-line)}
  .drel-s{overflow:hidden;text-overflow:ellipsis;min-width:0}
  /* ── dossier ──────────────────────────────────────────────────────── */
  .dtop{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
  .dgrow{flex:1}
  .dclock{font-size:12px;font-weight:650}
  .dsubj{font-size:17px;font-weight:750;letter-spacing:-.01em;line-height:1.3;margin:10px 0 12px}
  .dverbs{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:13px}
  .verb{display:inline-flex;align-items:center;gap:7px;border:1px solid var(--bd);background:var(--card);
    color:var(--tx);border-radius:9px;padding:7px 12px;font-family:inherit;font-size:12.5px;font-weight:650;cursor:pointer}
  .verb:hover:not([disabled]){border-color:var(--ac);color:var(--ac)}
  .verb.good{color:var(--green);border-color:var(--green-line)}
  .verb.good:hover{background:var(--green-bg);color:var(--green)}
  .verb[disabled]{opacity:.5;cursor:not-allowed}
  .verb kbd{background:var(--bg);border:1px solid var(--bd);border-radius:4px;padding:0 5px;
    font-family:ui-monospace,monospace;font-size:10.5px;color:var(--mut)}
  .dai{border:1px dashed var(--mut2);border-radius:11px;padding:11px 14px;margin-bottom:11px;background:var(--card)}
  .dai.committed{border-style:solid;border-color:var(--int-line)}
  .dai-h{display:flex;align-items:center;gap:9px;margin-bottom:4px}
  .dai-k{font-size:10px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;color:var(--mut)}
  .dai p{margin:0;font-size:12.5px;line-height:1.55;color:var(--tx)}
  /* «Evolução da conversa» (ADR-054 Phase 5). Solid border, not the dashed INFERENCE one: every step
     is quoted from a message whose id was checked before it was stored, so this is not a proposal.
     Its own container rather than a child of .dai, whose `p{margin:0}` would butt the two together. */
  .dnarr{border:1px solid var(--bd);border-radius:11px;padding:11px 14px;margin-bottom:11px;background:var(--card)}
  .nsteps{list-style:none;margin:6px 0 0;padding:0;display:flex;flex-direction:column;gap:0}
  .nsteps li{display:flex;gap:9px;align-items:baseline;padding:5px 6px;border-radius:7px;cursor:pointer;
    border-left:2px solid var(--bd);margin-left:2px}
  .nsteps li:hover{background:var(--hl-bg);border-left-color:var(--ac)}
  .nsteps li.on{background:var(--hl-bg);border-left-color:var(--ac)}
  .nd{font-size:10.5px;color:var(--mut2);font-variant-numeric:tabular-nums;white-space:nowrap;flex:0 0 auto}
  .nt{font-size:12.5px;line-height:1.5;color:var(--tx);min-width:0}
  .nstate{margin:8px 0 0;padding-top:8px;border-top:1px solid var(--bd);font-size:12.5px;
    line-height:1.5;color:var(--mut);font-style:italic}
  .dcp{display:flex;align-items:center;gap:10px;border:1px solid var(--bd);border-radius:11px;
    padding:10px 14px;margin-bottom:12px;background:var(--card)}
  .dcp-av{width:34px;height:34px;border-radius:9px;display:flex;align-items:center;justify-content:center;
    font-weight:800;font-size:12.5px;flex:0 0 auto;min-width:0;padding:0}
  .dcp-n{flex:1;min-width:0;display:flex;flex-direction:column}
  .dcp-n b{font-size:13.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .dcp-n small{font-size:11px;color:var(--mut2)}
  .dcp-s{text-align:right;padding-left:10px;display:flex;flex-direction:column}
  .dcp-s b{font-size:14px;font-variant-numeric:tabular-nums}
  .dcp-s b.dred{color:var(--red)}
  .dcp-s small{font-size:9px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut2)}
  .dmsgs{min-width:0}
  .dempty{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;
    min-height:220px;color:var(--mut2);text-align:center;font-size:26px}
  .dempty b{font-size:14px;color:var(--mut)}
  .dempty span{font-size:12px;max-width:260px;line-height:1.5}
  .tmsg.flash{outline:2px solid var(--ac);outline-offset:1px}
  /* ── the vertical in/out timeline (ADR-034 P5c) ───────────────────────
     A left spine (the ::before line) carries a direction-coloured dot per message; newest at the
     top; inbound and outbound offset to opposite sides + tinted; a gap chip shows the time between
     messages, and the top segment to «agora» is the open response debt in the clock's band colour. */
  .dthread{position:relative;padding-left:24px;margin-top:2px}
  .dthread::before{content:"";position:absolute;left:9px;top:9px;bottom:9px;width:2px;background:var(--bd2)}
  /* «agora» anchor + gap chips use the SANS interface font (not ui-monospace) so the timeline
     aligns with the rest of the UI. */
  .dt-now{position:relative;display:inline-flex;align-items:center;font:650 11px/1 inherit;
    letter-spacing:.02em;color:var(--mut2);margin-bottom:2px}
  .dt-now.red{color:var(--red)} .dt-now.amber{color:var(--amber)} .dt-now.green{color:var(--green)}
  .dt-now .dt-dot{position:absolute;left:-19px;top:50%;transform:translateY(-50%);width:11px;height:11px;
    border-radius:50%;background:currentColor;box-shadow:0 0 0 3px var(--card)}
  .dt-now.hollow .dt-dot{background:var(--card);border:2px solid currentColor;box-shadow:0 0 0 2px var(--card)}
  /* gap chip — connector height banded, never linear */
  .dt-gap{position:relative;display:flex;align-items:center;min-height:18px;padding:3px 0}
  .dt-gap.g3{padding:8px 0} .dt-gap.g7{padding:14px 0}
  .dt-glab{font:600 11px inherit;color:var(--mut2);background:var(--bd2);border-radius:20px;padding:2px 9px}
  .dt-gap.debt .dt-glab{font-weight:700}
  .dt-gap.debt.red .dt-glab{color:var(--red);background:var(--red-bg)}
  .dt-gap.debt.amber .dt-glab{color:var(--amber);background:var(--amber-bg)}
  .dt-gap.debt.green .dt-glab{color:var(--green);background:var(--green-bg)}
  /* message row: dot on the spine + the (offset, tinted) card */
  .dt-msg{position:relative;margin:5px 0}
  .dt-msg .dt-dot{position:absolute;left:-19px;top:13px;width:10px;height:10px;border-radius:50%;
    box-shadow:0 0 0 3px var(--card);z-index:1}
  .dt-msg.dir-inbound .dt-dot{background:var(--forn)}
  .dt-msg.dir-outbound .dt-dot{background:var(--cli)}
  .dt-msg.dir-internal .dt-dot{background:var(--mut2)}
  .dt-msg .tmsg{margin:0}
  .dt-msg.dir-inbound .tmsg{margin-right:26px;background:var(--card);border-left:3px solid var(--forn)}
  .dt-msg.dir-outbound .tmsg{margin-left:26px;background:var(--cli-bg);border-left:3px solid var(--cli)}
  .dt-msg.dir-internal .tmsg{border-left:3px solid var(--mut2)}
  @media (max-width:1360px){ .dt-msg.dir-inbound .tmsg,.dt-msg.dir-outbound .tmsg{margin-left:0;margin-right:0} }
  /* ── inherited chrome (search, order, fbar, chips, draft box) ─────── */
  #_search{border:1px solid var(--bd);border-radius:8px;padding:4px 10px;font-size:12.5px;color:var(--tx);background:var(--card);outline:none;width:150px;transition:width .15s,border-color .12s}
  #_search:focus{border-color:var(--ac);width:200px}
  #_search::placeholder{color:var(--mut2)}
  #_order,#_ownerf{border:1px solid var(--bd);border-radius:8px;padding:4px 8px;font-size:12.5px;
    font-family:inherit;color:var(--tx);background:var(--card);outline:none;cursor:pointer}
  #_order:hover,#_ownerf:hover{border-color:var(--ac);color:var(--ac)}
  .fbar{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 10px}
  .fchip{display:inline-flex;align-items:center;gap:5px;background:var(--ac-soft);border:1px solid var(--ac-line);color:var(--ac);border-radius:20px;padding:3px 10px;font-weight:600;cursor:pointer;font-size:12px}
  .fchip:hover{filter:brightness(1.05)}
  .cp{border:none;font-family:inherit}
  .pur{font-size:10px;font-weight:650;border-radius:20px;padding:2px 9px;background:var(--surface2);
    color:var(--mut);border:1px solid var(--bd);cursor:pointer;line-height:1.5}
  .pur:hover{border-color:var(--ac);color:var(--ac);background:var(--ac-soft)}
  .pur.committed{border-color:var(--int);color:var(--int);background:var(--int-bg)}
  .mtxt{color:var(--mut)}
  .rmeta .mtxt{font-size:11px}
  .pchip{font-size:11.5px;font-weight:650;border-radius:8px;padding:3px 10px;cursor:pointer;border:1px solid}
  .pchip.in{background:var(--ac-soft);border-color:var(--ac-line);color:var(--ac)}
  .pchip.in:hover{filter:brightness(1.05)}
  .pchip.new{background:var(--card);border-color:var(--bd);color:var(--mut)}
  .pchip.new:hover{border-color:var(--int);color:var(--int);background:var(--int-bg)}
  .pchip.draft{background:var(--card);border-color:var(--bd);color:var(--mut)}
  .pchip.draft:hover{border-color:var(--purple);color:var(--purple);background:var(--purple-bg)}
  .draftbox{margin-top:8px;flex-basis:100%}
  .draftbox textarea{width:100%;border:1px solid var(--bd);border-radius:9px;padding:9px 11px;
    font:12.5px/1.5 inherit;color:var(--tx);background:var(--surface2);resize:vertical}
  .draftbox .dfoot{display:flex;align-items:center;gap:9px;margin-top:5px;flex-wrap:wrap}
  /* «Abrir no mail» is the ACT (it leaves the app carrying the draft); «Copiar» is the fallback for
     a machine with no mail client configured. The accent says which one to reach for. */
  .draftbox .act-btn.open{border-color:var(--ac-line);color:var(--ac);background:var(--ac-soft)}
  .draftbox .act-btn.open:hover{filter:brightness(1.05)}
  .rmain[data-act]{cursor:pointer}
  .menu .mhdr{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:var(--mut2);padding:5px 11px 3px}
  .menu .mi.reset{color:var(--mut);border-top:1px solid var(--bd2);margin-top:3px}
"""


def build_fila_html(rows: list[dict[str, Any]], team: list[str] | None = None,
                    *, now_iso: str = "", synced_at: str = "", needs_review: int = 0,
                    nav_counts: dict[str, int] | None = None,
                    no_scopes: bool = False,
                    person: dict[str, Any] | None = None) -> str:
    """``no_scopes`` marks "this queue is empty because nothing is granted, not because it is done"
    (ADR-045) — the two states are indistinguishable from the rows alone, and only one of them is
    good news."""
    return cockpit_ui.page(
        "Fila",
        "fila",
        _BODY_HTML,
        embeds={"rows": rows, "team": list(team or []), "now": now_iso,
                # Freshness (ADR-033 P0): when the mail behind the clocks was last synced.
                "synced_at": synced_at,
                # NEEDS_REVIEW count (ADR-033 P2): the «rever N» chip's initial value.
                "needs_review": int(needs_review),
                # Why the queue is empty, when it is (ADR-045).
                "no_scopes": bool(no_scopes),
                "labels": _labels.fila_labels()},
        lens_js=_LENS_JS,
        nav_counts=nav_counts,
        extra_css=_EXTRA_CSS,
        person=person,
    )
