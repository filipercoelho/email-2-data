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
/* Focus is CONTENT-KEYED (ADR-033 P1, prerequisite for the live poll): `focusRoot` names the
   conversation; `focus` is the derived index into view() for rendering/data-i. A re-render or a
   queue reorder re-derives the index from the root, so the caret can never silently re-point at a
   different conversation. */
let focus = 0;
let focusRoot = null;
let filters = {};   /* active filters — keys: counterparty, purpose, band, owner, domain,
                       hasAttachment, minAgeDays, search. Pass null to remove a key.
                       band pseudo-values: 'risk' = WE_OWE red|amber (the «a responder» chip),
                       'chase' = AWAITING amber, i.e. past the 72h chase threshold («a cobrar»). */
let _prevRisk = null, urlThread = null;
/* 'ativos' (default) or 'tratados' — the decided ledger (rows fetched lazily from
   /api/fila?include=resolved). A decision must be reviewable after it is made, not vanish. */
let mode = 'ativos', resolvedRows = null;
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
   TAB-AWARE RANK, so the same one-line stable partition lets Fornecedores lead with «A cobrar»
   while Hoje/Clientes lead with «Precisam de resposta». Collapse is keyed semantically — folding
   «À espera deles» folds the same pile on every tab, whatever rank the tab gives it. */
const G_OWE=0, G_CHASE=1, G_WAIT=2, G_OTHER=3;
function semGroup(r){
  const c=r.clock||{};
  if(c.state==='WE_OWE') return G_OWE;
  /* _band() only ambers AWAITING at the chase cutoff (72h), so band IS the chase signal. */
  if(c.state==='AWAITING') return c.band==='amber' ? G_CHASE : G_WAIT;
  return G_OTHER;
}
const TAB_SEQ={
  all:[G_OWE,G_CHASE,G_WAIT,G_OTHER], CLIENT:[G_OWE,G_CHASE,G_WAIT,G_OTHER],
  SUPPLIER:[G_CHASE,G_OWE,G_WAIT,G_OTHER], LEAD:[G_OWE,G_CHASE,G_WAIT,G_OTHER]};
function groupOf(r){ return TAB_SEQ[tab].indexOf(semGroup(r)); }
/* PT-PT, phrased as the answer to "who has the ball", keyed by SEMANTIC id. */
const G_LABEL={0:'Precisam de resposta', 1:'A cobrar', 2:'À espera deles', 3:'Internos'};
const G_HINT ={0:'a bola está do nosso lado', 1:'sem resposta deles há 72h+ — candidatas a seguimento',
               2:'já respondemos — a bola está do lado deles', 3:'sem relógio de resposta'};
const G_CLASS={0:'owe', 1:'chase', 2:'wait', 3:'other'};

/* ── group collapse (ADR-033 P0) ────────────────────────────────────────
   «À espera deles» (and Internos) are status reports, not to-do lists — they START collapsed to a
   counted header, and any group can be folded. The choice persists (localStorage). Collapsed rows
   leave view() entirely, so J/K, focus and data-i can never land on an invisible row. */
const DEFAULT_COLLAPSED={[G_WAIT]:true,[G_OTHER]:true};
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
function renderTabs(){
  const el=$('#_tabs'); if(!el) return;
  const cts=tabCounts();
  el.innerHTML=TABS.map(([k,lab])=>
    '<button class="mtab'+(tab===k?' on':'')+'" role="tab" aria-selected="'+(tab===k)+'" data-tab="'+k+'">'
    +(k!=='all'?'<span class="mdot '+k+'" aria-hidden="true"></span>':'')
    +lab+'<span class="mn">'+(cts[k]||0)+'</span></button>').join('');
}

/* ── vistas rail (fixed set — no view builder, ADR-033 §4) ──────────── */
function renderRail(){
  const el=$('#_vrail'); if(!el) return;
  const act=rows;   /* rail counts read the whole active queue, not the current subset */
  const nR=respondCount(act), nC=chaseCount(act);
  const semD=act.filter(r=>!(r.owners&&r.owners.length)).length;
  const attN=act.filter(r=>r.has_attachment).length;
  const draftN=act.filter(r=>r.can_draft).length;
  const pc={}; act.forEach(r=>{ if(r.purpose) pc[r.purpose]=(pc[r.purpose]||0)+1; });
  const tops=Object.entries(pc).sort((a,b)=>b[1]-a[1]).slice(0,4);
  const purLab=k=>(LABELS.purpose&&LABELS.purpose[k])||k.toLowerCase().replace(/_/g,' ');
  const nM=act.filter(r=>((r.entities||{}).money_value!=null)&&((r.clock||{}).state==='WE_OWE')).length;
  const nD=act.filter(r=>(r.entities||{}).deadline).length;
  el.innerHTML='<div class="rl">Vistas</div>'
    +'<button class="vit'+((mode==='ativos'&&vista==='fila'&&!('band' in filters))?' on':'')+'" data-vista="risco"><span class="vd red"></span>Em risco<span class="vc">'+nR+'</span><kbd>1</kbd></button>'
    +'<button class="vit'+(vista==='money'?' on':'')+'" data-vista="money"><span class="vd ac"></span>€ em jogo<span class="vc">'+nM+'</span><kbd>2</kbd></button>'
    +'<button class="vit'+(vista==='prazos'?' on':'')+'" data-vista="prazos"><span class="vd purple"></span>Prazos<span class="vc">'+nD+'</span><kbd>3</kbd></button>'
    +'<button class="vit'+(vista==='fila'&&filters.band==='chase'?' on':'')+'" data-vista="cobrar"><span class="vd amber"></span>Cobranças<span class="vc">'+nC+'</span><kbd>4</kbd></button>'
    +'<button class="vit'+(mode==='tratados'?' on':'')+'" data-vista="tratados"><span class="vd green"></span>Tratados<kbd>5</kbd></button>'
    +'<div class="rl">Tipo</div>'
    +tops.map(([k,n])=>'<button class="fit'+(filters.purpose===k?' on':'')+'" data-fpur="'+esc(k)+'">'+esc(purLab(k))+'<span class="vc">'+n+'</span></button>').join('')
    +'<div class="rl">Estado</div>'
    +'<button class="fit'+(('owner' in filters&&filters.owner==='')?' on':'')+'" data-fest="semdono">Sem dono<span class="vc">'+semD+'</span></button>'
    +'<button class="fit'+(filters.hasAttachment?' on':'')+'" data-fest="anexo">Com anexo<span class="vc">'+attN+'</span></button>'
    +(draftN?'<button class="fit ro" disabled title="rascunhos prontos — visíveis nas linhas ✍">Com rascunho ✍<span class="vc">'+draftN+'</span></button>':'');
}

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
  /* Re-derive the caret from its content key (see `focusRoot`). */
  if(focusRoot){
    const fi=v.findIndex(r=>r.thread_root===focusRoot);
    if(fi>=0) focus=fi;
    else { focus=Math.max(0,Math.min(focus,v.length-1)); focusRoot=v[focus]?v[focus].thread_root:null; }
  } else {
    focus=Math.max(0,Math.min(focus,v.length-1));
    focusRoot=v[focus]?v[focus].thread_root:null;
  }
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
  /* The chase half of the honest headline. Hidden at zero — «0 a cobrar» would be noise. */
  const cob=$('#_cobrar');
  if(cob){
    cob.classList.toggle('hidden',mode==='tratados'||(nc===0&&filters.band!=='chase'));
    cob.textContent=nc+' a cobrar';
    cob.classList.toggle('filtering',filters.band==='chase');
  }
  const cnt=$('#_count'); if(cnt) cnt.textContent=v.length?S.threads(v.length):S.semDados;
  renderTabs(); renderRail(); renderFbar();
  const zero=$('#_zero');
  if(zero){
    zero.classList.toggle('hidden',v.length>0);
    if(!v.length){
      const noRes=hasFilters()&&(mode==='tratados'?(resolvedRows||[]):rows).length>0;
      zero.innerHTML=mode==='tratados'&&!noRes
        ?'Nada tratado ainda<span class="s">as decisões que marcares como tratadas ficam registadas aqui</span>'
        :(noRes
          ?'Sem resultados<span class="s">nenhuma thread corresponde aos filtros activos</span>'
          :(tab==='LEAD'
            ?'Sem leads novos<span class="s">bom sinal — este separador acende quando chegar um</span>'
            :'✓ Tudo tratado<span class="s">nada está a cair · 0 a responder</span>'));
    }
  }
  announce(mode==='tratados'?(v.length+' tratados'):(v.length?S.threads(v.length)+' por tratar':'Tudo tratado'));

  /* Honest vista banner: the € lens is explicitly AI-estimated, never presented as fact. */
  const vb=$('#_vbanner');
  if(vb){
    if(mode!=='tratados'&&vista==='money'){ vb.textContent='€ em jogo — valores estimados pela IA (tracejado = proposto), maiores primeiro'; vb.classList.remove('hidden'); }
    else if(mode!=='tratados'&&vista==='prazos'){ vb.textContent='Prazos extraídos dos emails — do mais próximo para o mais distante'; vb.classList.remove('hidden'); }
    else vb.classList.add('hidden');
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
    /* Row badges FILTER (the natural gesture); correcting the verdict lives in the dossier. */
    const cpPill=(tab==='all')
      ?'<button class="cp sm '+esc(r.counterparty||'OTHER')+'" data-act="fcp" title="filtrar: '+esc(cpLabel)+'">'+esc(cpLabel)+'</button>'
      :'';
    /* Entity chips render ONLY when informative — a missing extraction is absence, never «—».
       The AI-estimated € wears a trailing «?» and dashed styling: proposed, not fact. */
    const chips=[
      r.novo?'<span class="rchip novo" title="novo contacto — primeiro email há menos de 14 dias">novo</span>':'',
      en.money?'<span class="rchip money" title="valor estimado pela IA — proposto, não confirmado">'+esc(en.money)+'?</span>':'',
      en.deadline?_ddlChip(en.deadline):'',
      (r.related_count||0)>0?'<span class="rchip rel" title="'+(r.related_count)+' conversas relacionadas (mesmo contacto ou entidade partilhada)">↻'+r.related_count+'</span>':'',
      r.can_draft?'<span class="rchip draft" title="rascunho de resposta pronto">✍</span>':'',
      r.has_attachment?'<span class="rchip att" aria-hidden="true">📎</span>':'',
      r.n_messages>1?'<span class="rchip n">×'+r.n_messages+'</span>':'',
    ].join('');
    return head
      +'<div class="row cpr-'+esc(r.counterparty||'OTHER')+(i===focus?' on':'')+(selected.has(r.thread_root)?' picked':'')+'" data-i="'+i+'" role="listitem"'+(i===focus?' aria-current="true"':'')+' tabindex="0">'
      /* Motion is triaged like the mail: only the CRITICAL red tier pulses (WE_OWE ≥3 days).
         `wait` hollows the dot whenever the ball is NOT ours (wait + chase): colour carries
         URGENCY, fill carries OBLIGATION — a row read outside its section still says whose move. */
      +'<span class="clock '+esc(c.band||'none')+((c.band==='red'&&(c.age_hours||0)>=72)?' crit':'')
      +(semGroup(r)!==G_OWE?' wait':'')
      +'"><span class="d" aria-hidden="true"></span>'+esc(c.label||'')+'</span>'
      +'<div class="rmain" data-act="thread" title="abrir no dossiê (Enter)">'
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
  if(_threadCache[r.thread_root]){ r._threadMsgs=_threadCache[r.thread_root]; return; }
  if(r._threadBusy) return;
  r._threadBusy=true; r._threadErr=null; renderDossier();
  try{
    const d=await (await fetch('/api/thread/'+encodeURIComponent(r.thread_root))).json();
    if(d.error){ r._threadErr=d.error; }
    else{ _threadCache[r.thread_root]=d.messages; r._threadMsgs=d.messages; }
  }catch(e){ r._threadErr='falhou ao carregar'; }
  r._threadBusy=false; renderDossier();
}

function initialsOf(s){
  const p=String(s||'?').trim().split(/[\s.@_-]+/).filter(Boolean);
  return ((p[0]||'?')[0]+((p[1]||'')[0]||'')).toUpperCase();
}

function dossierHTML(r){
  const c=r.clock||{}, tr=r.trust||{}, cl=r.cluster||{};
  const cpLabel=(LABELS.counterparty&&LABELS.counterparty[r.counterparty])||r.counterparty||'—';
  const purLabel=(LABELS.purpose&&LABELS.purpose[r.purpose])||(r.purpose?String(r.purpose).toLowerCase().replace(/_/g,' '):'');
  const name=r.display_name||r.contact||'';
  const decided=decidedShort(tr.decided_by);
  const conf=tr.confidence?(' · '+Math.round(tr.confidence*100)+'%'):'';
  let h='<div class="dtop">'
    +'<button class="cp '+esc(r.counterparty||'OTHER')+'" data-act="reclassCp" title="contraparte: '+esc(cpLabel)+' — clica para corrigir">'+esc(cpLabel)+'</button>'
    +'<span class="dclock clock '+esc(c.band||'none')+(semGroup(r)!==G_OWE?' wait':'')+'"><span class="d" aria-hidden="true"></span>'+esc(c.label||'')+'</span>'
    +'<button class="pur'+(tr.committed?' committed':'')+'" data-act="reclassPur" title="tipo: '+esc(purLabel)+' — clica para corrigir">'+esc(purLabel)+'</button>'
    +'<span class="dgrow"></span>'
    +'<button class="owner'+((r.owners&&r.owners.length)?'':' empty')+'" data-act="owner" aria-label="atribuir donos">'+ownerLabel(r)+'</button>'
    +'</div>'
    +'<h1 class="dsubj">'+esc(r.subject||'(sem assunto)')+'</h1>';
  /* Verb bar — printed keys teach the fast path (the palette-as-trainer pattern). R and H are
     honestly disabled until Phase 3 ships them: a verb that explains itself beats a missing verb. */
  h+='<div class="dverbs">'
    +'<button class="verb good" data-act="handled">✓ '+(mode==='tratados'?'Reabrir':'Tratado')+'<kbd>E</kbd></button>'
    +'<button class="verb" disabled title="Responder contextual — chega na fase 3 (ADR-033 §10)">✍ Responder<kbd>R</kbd></button>'
    +'<button class="verb" disabled title="Adiar (acorda na data OU quando responderem) — fase 3">Adiar<kbd>H</kbd></button>'
    +'<button class="verb" data-act="owner">@ Dono<kbd>A</kbd></button>'
    +'<button class="verb" data-act="'+(r.project?'openproj':'mkproj')+'">▦ '+(r.project?'Abrir projeto':'Criar projeto')+'<kbd>P</kbd></button>'
    +'</div>';
  /* Signal tiles (ADR-033 §6.4): Em jogo / Prazo / Resposta / Ritmo. A tile states absence in
     words («nenhum prazo detetado») — the fixed grid is where honest absence is the information. */
  const en=r.entities||{};
  const MOM={active:['Ativo','var(--green)'],slowing:['A abrandar','var(--amber)'],stalled:['Parado','var(--red)']};
  const mom=MOM[r.momentum];
  const ageTxt=(c.age_hours!=null)?((c.age_hours>=48)?(Math.round(c.age_hours/24)+' d'):(Math.round(c.age_hours)+' h')):'—';
  h+='<div class="dtiles">'
    +'<div class="dtile"><small>Em jogo</small><b'+(en.money?' class="tband red"':'')+'>'+(en.money?esc(en.money)+'?':'—')+'</b><span>'+(en.money?'valor estimado (IA)':'sem valor associado')+'</span></div>'
    +'<div class="dtile"><small>Prazo</small><b>'+(en.deadline?esc(en.deadline):'—')+'</b><span>'+(en.deadline?'indicado pelo cliente':'nenhum prazo detetado')+'</span></div>'
    +'<div class="dtile"><small>Resposta</small><b class="tband '+esc(c.band||'none')+'">'+esc(ageTxt)+'</b><span>'+(semGroup(r)===G_OWE?'a bola do nosso lado':'à espera deles')+'</span></div>'
    +(mom?'<div class="dtile"><small>Ritmo</small><b style="color:'+mom[1]+'">'+mom[0]+'</b><span>cadência da conversa</span></div>':'')
    +'</div>';
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
  /* Conversa: vertical timeline + the SAME thread renderer the inline expansion used. */
  h+='<div class="dconv">'+timelineHTML(r._threadMsgs||[], c)
    +'<div class="dmsgs">'+_threadHTML(r)+'</div></div>';
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
  if(r._threadMsgs==null&&!r._threadErr&&!r._threadBusy) ensureThread(r);
}

/* ── vertical conversation timeline (ADR-033 §7) ─────────────────────────
   One direction-coded marker per message, NEWEST FIRST (matching the rendered message order —
   data-tl is the index into the .tmsg list, so a click can jump). Silences ≥24h between
   consecutive messages render as LABELED gaps — the rhythm of the thread is visible without
   reading dates — banded (1d/3d/7d+), never linear, so one long silence cannot eat the screen.
   The rail tops out at «agora» in the clock's band colour: the current open silence IS the
   response debt, drawn as the growing tail of the conversation (hollow when the ball is theirs). */
function timelineHTML(msgs, clock){
  if(!msgs||!msgs.length) return '';
  const ms=msgs.map(m=>({dir:m.direction||'outbound', t:Date.parse(m.date||'')||0}));
  const newest=ms.slice().reverse();
  const c=clock||{};
  let out='<div class="vtl" role="navigation" aria-label="Linha do tempo da conversa">';
  out+='<div class="vtl-now '+esc(c.band||'none')+(c.state==='AWAITING'?' hollow':'')+'" title="'+esc(c.label||'')+'">agora</div>';
  newest.forEach((m,di)=>{
    const cls=m.dir==='inbound'?'in':(m.dir==='internal'?'int':'out');
    out+='<button class="vtl-m '+cls+'" data-tl="'+di+'" title="ir para a mensagem"></button>';
    const prev=newest[di+1];   /* chronologically the previous message */
    if(prev&&m.t&&prev.t){
      const gapH=(m.t-prev.t)/3600000;
      if(gapH>=24){
        const d=Math.round(gapH/24);
        out+='<div class="vtl-gap'+(gapH>=168?' g7':(gapH>=72?' g3':''))+'">'+esc(d+' '+(d===1?'dia':'dias')+' sem resposta')+'</div>';
      }
    }
  });
  return out+'</div>';
}
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
      render();post('/api/thread/handled',{thread_root:r.thread_root,handled:false}).catch(()=>toast(S.revertido));}});
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
    const d=await (await fetch('/api/fila')).json();
    rows=(d.rows||[]); sortRows(); if(mode==='ativos') render();
  }catch(e){}
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
       +'<span class="tsum">rascunho — revê antes de enviar · a app nunca envia</span></div></div>'
      :'');
  /* The summary line keeps the CHRONOLOGICAL array — its date range must read "first → last". */
  const head='<div class="thead">'+_projHTML(r)+draftBtn+'<span class="tsum">'+esc(msgThreadSummary(msgs))+'</span></div>';
  return '<div class="texp">'+head+draftBox+ordered.map(m=>msgHTML(m)).join('')+'</div>';
}

/* ── command bus ────────────────────────────────────────────────────── */
function dispatch(action,i){
  if(action==='handled'){ mode==='tratados'?reopenThread(i):handle(i); }
  else if(action==='owner')ownerMenu(i);
  else if(action==='reclassCp')reclassMenu(i,'counterparty');
  else if(action==='reclassPur')reclassMenu(i,'purpose');
  else if(action==='thread')focusTo(i);
  else if(action==='fcp'){const r=view()[i]; if(r) setFilter('counterparty', filters.counterparty===r.counterparty?null:r.counterparty);}
  else if(action==='mkproj')makeProject(i);
  else if(action==='openproj')openProject(i);
  else if(action==='draft')draftReply(i);
  else if(action==='copydraft')copyDraft(i);
}

/* Focusing IS opening: the dossier mounts the focused conversation (one render path). */
function focusTo(i){
  const v=view(); if(!v[i]) return;
  focus=i; focusRoot=v[i].thread_root;
  urlThread=focusRoot; syncURL(); render();
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
  else if(e.key==='p'||e.key==='P'){const r=v[focus]; if(r)dispatch(r.project?'openproj':'mkproj',focus);}
  else if(e.key==='Enter'||e.key==='o'||e.key==='O'){dispatch('thread',focus);e.preventDefault();}
}
function onEsc(){
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

/* ── NEEDS_REVIEW chip: tier-1 failures finally get a surface (quiet, links to Para ti) ── */
let _needsReview=(typeof NEEDS_REVIEW!=='undefined')?(NEEDS_REVIEW|0):0;
function paintRever(){
  const el=$('#_rever'); if(!el) return;
  el.textContent='rever '+_needsReview;
  el.classList.toggle('hidden',!_needsReview);
}
paintRever();
const _rv=$('#_rever'); if(_rv)_rv.addEventListener('click',()=>{location.href='/para-ti';});

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
    const resp=await fetch('/api/fila',{cache:'no-store'});
    if(!resp.ok) return;
    const d=await resp.json();
    _syncedAt=d.synced_at||_syncedAt; paintFreshness();
    if(typeof setNavCounts==='function') setNavCounts(d.nav_counts);
    _needsReview=d.needs_review||0; paintRever();
    const next=d.rows||[];
    const sig=_sig(next);
    if(sig===_lastSig&&!opts.force) return;   /* nothing moved — don't clobber the DOM */
    const seen=new Set(rows.map(r=>r.thread_root));
    const added=next.filter(r=>!seen.has(r.thread_root)).length;
    const carry={}; rows.forEach(r=>{carry[r.thread_root]=r;});
    next.forEach(r=>{const o=carry[r.thread_root]; if(o){r._threadMsgs=o._threadMsgs;r._threadErr=o._threadErr;r._draft=o._draft;r._open=o._open;}});
    rows=next; sortRows(); _lastSig=sig;
    render();
    if(!opts.quiet&&added>0) toast(added+(added===1?' nova thread':' novas threads'));
  }catch(e){ /* offline / server restarting — keep showing what we have, retry next tick */ }
  finally{ _refreshing=false; }
}
setInterval(()=>{ if(!document.hidden) refresh(); },REFRESH_MS);
document.addEventListener('visibilitychange',()=>{ if(!document.hidden) refresh(); });
_lastSig=_sig(rows);

/* «Sincronizar» refreshes IN PLACE — the shell's onSynced hook replaces its location.reload(). */
function onSynced(){ toast(S.sincronizado); refresh({force:true,quiet:true}); }

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

/* the headline chips are FILTERS, not labels — each half toggles down to what it counts */
const _rk=$('#_risk');
if(_rk) _rk.addEventListener('click',()=>setFilter('band',filters.band==='risk'?null:'risk'));
const _cb=$('#_cobrar');
if(_cb) _cb.addEventListener('click',()=>setFilter('band',filters.band==='chase'?null:'chase'));

/* tabs + rail */
const _tb=$('#_tabs');
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
    else if(k==='cobrar'){ setMode('ativos').then(()=>{vista='fila'; setFilter('band',filters.band==='chase'?null:'chase');}); }
    else if(k==='tratados'){ setMode(mode==='tratados'?'ativos':'tratados'); }
    return;
  }
  const fp=e.target.closest('[data-fpur]');
  if(fp){ setFilter('purpose',filters.purpose===fp.dataset.fpur?null:fp.dataset.fpur); return; }
  const fe=e.target.closest('[data-fest]');
  if(fe){
    if(fe.dataset.fest==='semdono') setFilter('owner',('owner' in filters&&filters.owner==='')?null:'');
    else if(fe.dataset.fest==='anexo') setFilter('hasAttachment',filters.hasAttachment?null:true);
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
    <button id="_risk" class="risk" aria-live="polite" title="devemos resposta, vermelho+laranja (clica para filtrar)"
      style="font-size:12.5px;font-weight:680;font-variant-numeric:tabular-nums;border-radius:20px;padding:3px 12px;border:1px solid"></button>
    <button id="_cobrar" class="hidden" title="à espera deles há 72h+ — candidatas a cobrança (clica para filtrar)"></button>
    <button id="_rever" class="hidden" title="classificações que a cascata não conseguiu decidir — rever no Para ti"></button>
    <span id="_count"></span>
    <span id="_fresh" class="fresh" title="idade do correio sincronizado"></span>
    <div id="_tabs" class="mtabs" role="tablist" aria-label="Contraparte"></div>
    <input id="_search" type="text" placeholder="/ procurar…" autocomplete="off" aria-label="Filtrar threads"/>
    <select id="_order" aria-label="Ordenar a fila" title="Ordenar a fila">
      <option value="risk">Risco de resposta</option>
      <option value="recent">Mais recentes</option>
    </select>
    <select id="_ownerf" aria-label="Filtrar por dono" title="Filtrar por dono">
      <option value="__all">dono: todos</option>
      <option value="">sem dono</option>
    </select>
    <span class="cmdk"><kbd>⌘K</kbd> comandos</span>
  </div>
  <div id="_fbar" class="fbar hidden" aria-label="Filtros activos"></div>
  <div class="mesa-body">
    <nav id="_vrail" class="vrail" aria-label="Vistas e filtros"></nav>
    <div class="mcol">
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
  /* counterparty tabs */
  .mtabs{display:flex;gap:2px;background:var(--bd2);border-radius:10px;padding:3px}
  .mtab{display:inline-flex;align-items:center;gap:6px;border:none;background:none;cursor:pointer;
    font-family:inherit;font-size:12.5px;font-weight:650;color:var(--mut);border-radius:8px;padding:4px 10px}
  .mtab.on{background:var(--card);color:var(--tx);box-shadow:var(--shadow)}
  .mtab .mn{font-size:10.5px;font-weight:700;font-variant-numeric:tabular-nums;color:var(--mut2)}
  .mtab.on .mn{color:var(--tx)}
  .mdot{width:7px;height:7px;border-radius:50%;display:inline-block}
  .mdot.CLIENT{background:var(--green)} .mdot.SUPPLIER{background:var(--ac)} .mdot.LEAD{background:var(--purple)}
  /* vistas rail */
  .vrail .rl{font-size:9.5px;font-weight:800;letter-spacing:.09em;text-transform:uppercase;color:var(--mut2);padding:10px 8px 5px}
  .vit,.fit{display:flex;align-items:center;gap:8px;border:none;background:none;cursor:pointer;text-align:left;
    font-family:inherit;font-size:12.5px;font-weight:600;color:var(--mut);border-radius:8px;padding:5px 9px;width:100%}
  .vit:hover,.fit:hover{background:var(--bd2);color:var(--tx)}
  .vit.on,.fit.on{background:#e6ecfb;color:var(--ac);font-weight:700}
  .vit .vd{width:7px;height:7px;border-radius:3px;flex:0 0 auto}
  .vd.red{background:var(--red)} .vd.amber{background:var(--amber)} .vd.green{background:var(--green)}
  .vit .vc,.fit .vc{margin-left:auto;font-size:10.5px;font-weight:700;font-variant-numeric:tabular-nums;color:var(--mut2)}
  .vit kbd{font-size:9.5px;color:var(--mut2);background:var(--bd2);border-radius:4px;padding:0 4px;font-family:ui-monospace,monospace}
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
  .ghead.owe{color:var(--red);border-bottom-color:#f3c9c9}
  .ghead.owe .gh-n{background:#fbeaea;color:var(--red)}
  .ghead.chase{color:var(--amber);border-bottom-color:#f0dcb0}
  .ghead.chase .gh-n{background:#fdf4e3;color:var(--amber)}
  .ghead.wait{color:var(--mut)}
  .ghead.wait .gh-n{background:var(--bd2);color:var(--mut)}
  .ghead.other{color:var(--ext)}
  .ghead.other .gh-n{background:var(--bd2);color:var(--ext)}
  @media (max-width:820px){ .ghead .gh-s{display:none} }
  /* Hollow dot = the ball is NOT ours (wait + chase). Colour keeps meaning urgency. */
  .clock.wait .d{background:transparent;box-shadow:inset 0 0 0 1.5px currentColor}
  /* ── rows: single line, counterparty rail, clock-first ───────────── */
  .mesa .row{gap:10px;padding:calc(var(--rpad) - 3px) 13px calc(var(--rpad) - 3px) 10px}
  .mesa .row.cpr-CLIENT{border-left-color:var(--green)!important}
  .mesa .row.cpr-SUPPLIER{border-left-color:var(--ac)!important}
  .mesa .row.cpr-LEAD{border-left-color:var(--purple)!important}
  .mesa .row.on{background:#eef2ff}
  .mesa .clock{min-width:150px;text-align:left;font-size:11.5px}
  .rline{display:flex;align-items:baseline;gap:8px;min-width:0}
  .rname{font-size:var(--rfont);font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:0 1 auto;max-width:220px}
  .rscan{flex:1;min-width:0;color:var(--mut);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  /* the focused row breathes: the name un-clamps and the scan line yields to the subject (rmeta) */
  .mesa .row.on .rname{max-width:none;white-space:normal}
  .mesa .row.on .rscan{display:none}
  .cp.sm{min-width:0;padding:1px 7px;cursor:pointer;border:none;font-family:inherit}
  .rchips{flex:0 0 auto;display:inline-flex;align-items:center;gap:5px}
  .rchip{font-size:10.5px;color:var(--mut2);font-variant-numeric:tabular-nums}
  .rchip.draft{color:var(--ac);background:#eef2ff;border-radius:5px;padding:0 5px}
  /* Off-focus trust dot: dashed ring = proposto, solid = confirmado (the chip returns on focus). */
  .tdot{display:inline-block;width:7px;height:7px;border-radius:50%;flex:0 0 auto;vertical-align:middle}
  .tdot.proposed{border:1.5px dashed var(--mut2);background:transparent}
  .tdot.committed{border:1.5px solid var(--int);background:var(--int)}
  /* ── headline chips + freshness ───────────────────────────────────── */
  .risk{color:var(--red);background:#fbeaea;border-color:#f3c9c9!important;cursor:pointer;font-family:inherit}
  .risk:hover{filter:brightness(.96)}
  .risk.filtering{outline:2px solid var(--red);outline-offset:1px}
  .risk.clear{color:var(--green);background:#e7f6ee;border-color:#bfe6cf!important}
  .risk.pulse{animation:pop .35s ease}
  #_cobrar{color:var(--amber);background:#fdf4e3;border:1px solid #f0dcb0;cursor:pointer;
    font-family:inherit;font-size:12.5px;font-weight:680;font-variant-numeric:tabular-nums;
    border-radius:20px;padding:3px 12px}
  #_cobrar:hover{filter:brightness(.96)}
  #_cobrar.filtering{outline:2px solid var(--amber);outline-offset:1px}
  .fresh{color:var(--mut2);font-size:11.5px;font-variant-numeric:tabular-nums}
  .fresh.stale{color:var(--amber);font-weight:700}
  #_rever{color:var(--purple);background:#efeafb;border:1px solid #ddd2f5;cursor:pointer;
    font-family:inherit;font-size:11.5px;font-weight:680;border-radius:20px;padding:3px 11px}
  #_rever:hover{filter:brightness(.96)}
  /* ── entity chips (P2): dashed € = proposed, ⚑ deadline, ↻ related, novo ── */
  .rchip.money{color:var(--mut);border:1px dashed var(--mut2);border-radius:5px;padding:0 5px;font-size:10px;font-weight:700}
  .rchip.ddl{color:var(--amber);background:#fdf4e3;border-radius:5px;padding:0 5px;font-size:10px;font-weight:700}
  .rchip.ddl.late{color:var(--red);background:#fbeaea}
  .rchip.novo{color:var(--purple);background:#efeafb;border-radius:5px;padding:0 5px;font-size:10px;font-weight:800}
  .rchip.rel{color:var(--mut2);font-size:10px;font-weight:700}
  /* vistas rail extras */
  .vd.ac{background:var(--ac)} .vd.purple{background:var(--purple)}
  .vbanner{margin:0 0 8px;padding:7px 12px;border:1px dashed var(--mut2);border-radius:9px;
    color:var(--mut);font-size:12px;background:var(--card)}
  /* bulk selection */
  .selbar{display:flex;align-items:center;gap:9px;margin:0 0 8px;padding:7px 12px;
    border:1px solid #cdd7ff;border-radius:9px;background:#eef2ff;font-size:12.5px}
  .selbar b{color:var(--ac);font-variant-numeric:tabular-nums}
  .mesa .row.picked{outline:2px solid var(--ac);outline-offset:-2px}
  /* dossier signal tiles */
  .dtiles{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:12px}
  @media (max-width:1360px){ .dtiles{grid-template-columns:repeat(2,1fr)} }
  .dtile{border:1px solid var(--bd);border-radius:10px;padding:9px 11px;background:var(--card);
    display:flex;flex-direction:column;gap:2px}
  .dtile small{font-size:9px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;color:var(--mut2)}
  .dtile b{font-size:14.5px;font-variant-numeric:tabular-nums;letter-spacing:-.01em}
  .dtile span{font-size:10.5px;color:var(--mut2)}
  .tband.red{color:var(--red)} .tband.amber{color:var(--amber)} .tband.green{color:var(--green)} .tband.none{color:var(--mut2)}
  .dpedem{margin:0 0 4px}
  .dgreen{color:var(--green)}
  /* ── dossier ──────────────────────────────────────────────────────── */
  .dtop{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
  .dgrow{flex:1}
  .dclock{font-size:12px;font-weight:650}
  .dsubj{font-size:17px;font-weight:750;letter-spacing:-.01em;line-height:1.3;margin:10px 0 12px}
  .dverbs{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:13px}
  .verb{display:inline-flex;align-items:center;gap:7px;border:1px solid var(--bd);background:var(--card);
    color:var(--tx);border-radius:9px;padding:7px 12px;font-family:inherit;font-size:12.5px;font-weight:650;cursor:pointer}
  .verb:hover:not([disabled]){border-color:var(--ac);color:var(--ac)}
  .verb.good{color:var(--green);border-color:#bfe6cf}
  .verb.good:hover{background:#e7f6ee;color:var(--green)}
  .verb[disabled]{opacity:.5;cursor:not-allowed}
  .verb kbd{background:var(--bg);border:1px solid var(--bd);border-radius:4px;padding:0 5px;
    font-family:ui-monospace,monospace;font-size:10.5px;color:var(--mut)}
  .dai{border:1px dashed var(--mut2);border-radius:11px;padding:11px 14px;margin-bottom:11px;background:var(--card)}
  .dai.committed{border-style:solid;border-color:#bfe6e0}
  .dai-h{display:flex;align-items:center;gap:9px;margin-bottom:4px}
  .dai-k{font-size:10px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;color:var(--mut)}
  .dai p{margin:0;font-size:12.5px;line-height:1.55;color:var(--tx)}
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
  .dconv{display:flex;gap:12px;align-items:stretch}
  .dmsgs{flex:1;min-width:0}
  .dempty{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;
    min-height:220px;color:var(--mut2);text-align:center;font-size:26px}
  .dempty b{font-size:14px;color:var(--mut)}
  .dempty span{font-size:12px;max-width:260px;line-height:1.5}
  .tmsg.flash{outline:2px solid var(--ac);outline-offset:1px}
  /* ── vertical conversation timeline ───────────────────────────────── */
  .vtl{flex:0 0 52px;display:flex;flex-direction:column;align-items:center;gap:6px;padding-top:6px}
  .vtl-now{font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.05em;
    border-radius:6px;padding:3px 6px;text-align:center;line-height:1.2}
  .vtl-now.red{color:var(--red);background:#fbeaea}
  .vtl-now.amber{color:var(--amber);background:#fdf4e3}
  .vtl-now.green{color:var(--green);background:#e7f6ee}
  .vtl-now.none{color:var(--mut2);background:var(--bd2)}
  .vtl-now.hollow{background:transparent;border:1.5px dashed currentColor}
  .vtl-m{width:11px;height:11px;border-radius:50%;border:none;cursor:pointer;padding:0;flex:0 0 auto}
  .vtl-m.in{background:var(--ac)}
  .vtl-m.out{background:var(--int)}
  .vtl-m.int{background:var(--mut2)}
  .vtl-m:hover{transform:scale(1.25)}
  .vtl-gap{font-size:9px;color:var(--mut2);text-align:center;line-height:1.25;max-width:52px;
    border-left:2px dotted var(--bd);padding:6px 0 6px 0;margin:2px 0}
  .vtl-gap.g3{padding:12px 0;color:var(--amber)}
  .vtl-gap.g7{padding:20px 0;color:var(--red)}
  /* ── inherited chrome (search, order, fbar, chips, draft box) ─────── */
  #_search{border:1px solid var(--bd);border-radius:8px;padding:4px 10px;font-size:12.5px;color:var(--tx);background:var(--card);outline:none;width:150px;transition:width .15s,border-color .12s}
  #_search:focus{border-color:var(--ac);width:200px}
  #_search::placeholder{color:var(--mut2)}
  #_order,#_ownerf{border:1px solid var(--bd);border-radius:8px;padding:4px 8px;font-size:12.5px;
    font-family:inherit;color:var(--tx);background:var(--card);outline:none;cursor:pointer}
  #_order:hover,#_ownerf:hover{border-color:var(--ac);color:var(--ac)}
  .fbar{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 10px}
  .fchip{display:inline-flex;align-items:center;gap:5px;background:#eef2ff;border:1px solid #cdd7ff;color:var(--ac);border-radius:20px;padding:3px 10px;font-weight:600;cursor:pointer;font-size:12px}
  .fchip:hover{background:#dfe8ff}
  .cp{border:none;font-family:inherit}
  .pur{font-size:10px;font-weight:650;border-radius:20px;padding:2px 9px;background:#f3f4f6;
    color:var(--mut);border:1px solid var(--bd);cursor:pointer;line-height:1.5}
  .pur:hover{border-color:var(--ac);color:var(--ac);background:#eef2ff}
  .pur.committed{border-color:var(--int);color:var(--int);background:#f0fdfa}
  .mtxt{color:var(--mut)}
  .rmeta .mtxt{font-size:11px}
  .pchip{font-size:11.5px;font-weight:650;border-radius:8px;padding:3px 10px;cursor:pointer;border:1px solid}
  .pchip.in{background:#eef2ff;border-color:#cdd7ff;color:var(--ac)}
  .pchip.in:hover{background:#e0e8ff}
  .pchip.new{background:#fff;border-color:var(--bd);color:var(--mut)}
  .pchip.new:hover{border-color:var(--int);color:var(--int);background:#effbf7}
  .pchip.draft{background:#fff;border-color:var(--bd);color:var(--mut)}
  .pchip.draft:hover{border-color:var(--purple);color:var(--purple);background:#f7f4fd}
  .draftbox{margin-top:8px;flex-basis:100%}
  .draftbox textarea{width:100%;border:1px solid var(--bd);border-radius:9px;padding:9px 11px;
    font:12.5px/1.5 inherit;color:var(--tx);background:#fffdf8;resize:vertical}
  .draftbox .dfoot{display:flex;align-items:center;gap:9px;margin-top:5px}
  .rmain[data-act]{cursor:pointer}
  .menu .mhdr{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:var(--mut2);padding:5px 11px 3px}
  .menu .mi.reset{color:var(--mut);border-top:1px solid var(--bd2);margin-top:3px}
"""


def build_fila_html(rows: list[dict[str, Any]], team: list[str] | None = None,
                    *, now_iso: str = "", synced_at: str = "", needs_review: int = 0,
                    nav_counts: dict[str, int] | None = None) -> str:
    return cockpit_ui.page(
        "Fila",
        "fila",
        _BODY_HTML,
        embeds={"rows": rows, "team": list(team or []), "now": now_iso,
                # Freshness (ADR-033 P0): when the mail behind the clocks was last synced.
                "synced_at": synced_at,
                # NEEDS_REVIEW count (ADR-033 P2): the «rever N» chip's initial value.
                "needs_review": int(needs_review),
                "labels": _labels.fila_labels()},
        lens_js=_LENS_JS,
        nav_counts=nav_counts,
        extra_css=_EXTRA_CSS,
    )
