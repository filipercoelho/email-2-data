"""C3 — Para ti lens page (/para-ti). Unified human-in-loop decision inbox.

Thin wrapper over cockpit_ui.page(). The gate items are built server-side in para_ti.py; the JS
handles grouping, the expandable detail panel, and the decision actions.

The queue answers "what needs me?" — but a decision you cannot inspect is a decision you cannot make,
so each gate expands **in place** (ADR-024) into the evidence behind it: the email thread with its
attachments, and what the extraction layer already pulled from it. The detail is fetched lazily from
``/api/thread/<root>`` on first expand and cached, so the 30 s refresh poll (ADR-023) never pays for
it. The thread panel itself is the shared ``msgThreadHTML`` kit — the same renderer the Fila and
Projetos use, so a fix there lands here too.
"""

from __future__ import annotations

from typing import Any

from . import cockpit_ui, jobspec as js, labels as _labels

_BODY = """
<div class="wrap">
  <div class="bar">
    <span id="_count"></span>
    <span id="_fresh" class="fresh" title="a página actualiza-se sozinha"></span>
    <span class="cmdk"><kbd>⌘K</kbd> comandos</span>
  </div>
  <div id="_chips" class="chips"></div>
  <div id="_list"></div>
  <div id="_zero" class="zero hidden">✓ Sem decisões pendentes<span class="s">nada precisa da tua atenção agora</span></div>
  <div class="hint"><b>J/K</b> navegar · <b>Enter</b> abrir · <b>Y</b> aceitar · <b>N</b> ignorar · <b>Z</b> desfazer · <b>?</b> ajuda</div>
</div>
"""

_EXTRA_CSS = """
  .gate.on{border-color:var(--ac);box-shadow:0 0 0 2px rgba(51,88,212,.15),var(--shadow)}
  .gate.open{border-color:var(--ac)}
  .gate{cursor:pointer;transition:border-color .14s ease,box-shadow .14s ease}
  .gate:hover{border-color:var(--ac-line)}
  .fresh{font-size:11px;color:var(--mut2);font-variant-numeric:tabular-nums}
  .fresh.stale{color:var(--amber);font-weight:600}
  /* filter chips */
  .chips{display:flex;gap:7px;flex-wrap:wrap;margin:0 2px 12px}
  .chip{border:1px solid var(--bd);background:var(--card);border-radius:20px;padding:3px 11px;
    font-size:12px;font-weight:600;color:var(--mut);cursor:pointer;display:inline-flex;gap:6px;align-items:center}
  .chip:hover{border-color:var(--ac);color:var(--ac)}
  .chip.on{background:var(--ac);border-color:var(--ac);color:var(--card)}
  .chip .n{font-variant-numeric:tabular-nums;opacity:.75;font-weight:700}
  /* group heading */
  .ghdr{display:flex;align-items:center;gap:9px;margin:16px 2px 8px;font-size:11px;font-weight:700;
    text-transform:uppercase;letter-spacing:.05em;color:var(--mut2)}
  .ghdr:first-child{margin-top:2px}
  .ghdr .gline{flex:1;height:1px;background:var(--bd)}
  /* card head + chevron */
  .ghead{display:flex;align-items:center;gap:8px;margin-bottom:3px}
  .gchev{color:var(--mut);font-size:13px;line-height:1;width:12px;flex:0 0 auto}
  .gate:hover .gchev{color:var(--ac)}
  .gate.open .gchev{color:var(--ac)}
  .ghint{margin-left:auto;font-size:11px;color:var(--mut2);font-weight:600}
  .gate:hover .ghint{color:var(--ac)}
  /* detail panel */
  .gdetail{margin:10px 0 4px;padding-top:10px;border-top:1px solid var(--bd2);cursor:default}
  .gload{font-size:12.5px;color:var(--mut)}
  /* what we already know (spec) */
  .spec{background:var(--surface2);border:1px solid var(--bd2);border-radius:10px;padding:10px 12px;margin-top:10px}
  .spec h4{margin:0 0 8px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--mut)}
  .specrow{display:flex;align-items:baseline;gap:9px;padding:3px 0;font-size:12.5px;border-bottom:1px solid var(--surface2)}
  .specrow:last-child{border-bottom:none}
  .specrow .sk{color:var(--mut);flex:0 0 150px}
  .specrow .sv{flex:1;color:var(--tx);word-break:break-word}
  .specrow.miss .sv{color:var(--mut2);font-style:italic}
  /* the unanswered must-haves, folded into one honest line instead of a row per line item */
  .missline{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:9px;padding-top:9px;
    border-top:1px dashed var(--bd)}
  .mtag{font-size:9.5px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:var(--red)}
  .mchip{font-size:11.5px;background:var(--card);border:1px solid var(--bd);border-radius:6px;
    padding:1px 7px;color:var(--mut)}
  .mnote{font-size:11px;color:var(--mut2)}
  .prov{font-size:9.5px;font-weight:700;text-transform:uppercase;letter-spacing:.03em;
    border-radius:5px;padding:1px 6px;flex:0 0 auto}
  .prov.user{background:var(--green-bg);border:1px solid var(--green-line);color:var(--green)}
  .prov.ia{background:var(--amber-bg);border:1px dashed var(--amber-line);color:var(--amber)}
  .prov.det{background:var(--ac-soft);border:1px solid var(--ac-line);color:var(--ac)}
  .prov.miss{background:var(--surface2);border:1px solid var(--bd);color:var(--mut2)}
  .speclead{display:flex;align-items:center;gap:8px;margin-bottom:8px;font-size:12px}
  .estim{font-weight:700;border-radius:6px;padding:1px 7px;font-size:10.5px;text-transform:uppercase;letter-spacing:.03em}
  .estim.yes{background:var(--green-bg);color:var(--green)}
  .estim.no{background:var(--red-bg);color:var(--red)}
  .cov{flex:1;height:5px;background:var(--surface2);border-radius:4px;overflow:hidden;max-width:160px}
  .cov i{display:block;height:100%;background:var(--ac);border-radius:4px}
  /* project chip inside a gate */
  .pchip{font-size:11.5px;font-weight:600;border-radius:7px;padding:3px 9px;cursor:pointer;
    border:1px solid var(--bd);background:var(--card);color:var(--mut)}
  .pchip.in{border-color:var(--green-line);background:var(--green-bg);color:var(--green)}
  .pchip:hover{border-color:var(--ac);color:var(--ac)}
  .gacts{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px}
  .gacts .sep{flex:1}
  .menu .mhdr{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:var(--mut2);padding:5px 11px 3px}
  .menu .mi.reset{color:var(--mut);border-top:1px solid var(--bd2);margin-top:3px}
"""

# Colours per gate kind
_KIND_CLASS = {"rever_classificacao": "rever", "propor_projeto": "projeto",
               "confirmar_identidade": "identidade"}
_KIND_LABEL = {"rever_classificacao": "Rever", "propor_projeto": "Propor projeto",
               "confirmar_identidade": "Identidade"}
# Plural heading per group (the flat list made every badge read the same, so the badge stopped
# carrying information — the heading + count does that job now).
_KIND_HEADING = {"rever_classificacao": "Classificações a rever",
                 "propor_projeto": "Propostas de projeto",
                 "confirmar_identidade": "Identidades a confirmar"}

_LENS_JS = r"""
let items = ITEMS.slice(), focus = 0, dismissed = new Set();
let openKey = null;          // accordion: at most one gate expanded (keeps the URL well-defined)
let kindFilter = '';         // '' = todos
const _detail = {};          // thread_root -> {messages, spec}   (fetch-once)
const _detailErr = {};       // thread_root -> message
let roster = (ROSTER || []).slice();

/* Stable per-item identity. The list is re-fetched by the refresh poll, so positions shift as mail
   arrives — anything remembered about an item (dismissed, expanded, focus) MUST key off content,
   not index, or a refresh silently re-points it at a different decision. Each gate's natural key:
   the thread it concerns, or the address for the identity gate. */
function itemKey(it){
  if(!it) return '';
  return (it.kind||'')+'|'+(it.thread_root||it.email||it.title||'');
}

function notDismissed(){ return items.filter(it=>!dismissed.has(itemKey(it))); }
function visible(){
  const live = notDismissed();
  return kindFilter ? live.filter(it=>it.kind===kindFilter) : live;
}

function _clockDot(band){
  const col={'red':'var(--red)','amber':'var(--amber)','green':'var(--green)'}[band]||'var(--mut2)';
  return '<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:'+col+';margin-right:5px;vertical-align:middle"></span>';
}

/* Canonical pt-PT labels (labels.py) — no drifting per-page copy. */
function _purposeLabel(p){
  return (LABELS.purpose&&LABELS.purpose[p]) || (p||'').toLowerCase().replace(/_/g,' ');
}
function _cpLabel(c){ return (LABELS.counterparty&&LABELS.counterparty[c]) || c || '—'; }

/* ── filter chips ───────────────────────────────────────────────────── */
function renderChips(){
  const el=$('#_chips'); if(!el) return;
  const live=notDismissed();
  if(live.length===0){ el.innerHTML=''; return; }
  const counts={};
  live.forEach(it=>{ counts[it.kind]=(counts[it.kind]||0)+1; });
  const kinds=Object.keys(counts);
  // One kind only → the filter would be a no-op control; don't show chrome that does nothing.
  if(kinds.length<2){ el.innerHTML=''; return; }
  let html='<button class="chip'+(kindFilter?'':' on')+'" data-kind="">Todos <span class="n">'+live.length+'</span></button>';
  kinds.forEach(k=>{
    html+='<button class="chip'+(kindFilter===k?' on':'')+'" data-kind="'+esc(k)+'">'
      +esc(KIND_LABEL[k]||k)+' <span class="n">'+counts[k]+'</span></button>';
  });
  el.innerHTML=html;
}

function setFilter(k){
  kindFilter=(kindFilter===k)?'':k;
  focus=0; syncURL(); render();
}

/* ── the "what we already know" panel ───────────────────────────────── */
function _provChip(f){
  if(!f || !f.value) return '<span class="prov miss">em falta</span>';
  if(f.confirmed || f.source==='user') return '<span class="prov user">confirmado</span>';
  if(f.source==='llm') return '<span class="prov ia">IA</span>';
  return '<span class="prov det">extraído</span>';
}

/* Fold the spec to ONE row per field, not one per line item.

   A job with 5 pieces and 5 unknown materials is a single fact — "material: em falta" — not five.
   Rendering it per-item produced 25 identical rows that buried the four things actually known, which
   is a worse lie than showing nothing: the gaps were technically all there, and completely
   unreadable. Known values are listed (deduped, with partial coverage stated honestly, e.g.
   "150 · 1 de 5"); the must-haves nobody has answered collapse into one compact line. */
function _specFold(spec){
  const known=[], missing=[];
  const items=spec.items||[], nItems=items.length;
  FIELDS.forEach(function(f){
    const key=f[0], label=f[1], tier=f[2], scope=f[4];
    const vals=[], fields=[];
    let total=0;
    if(scope==='job'){
      total=1;
      const sf=(spec.job_fields||{})[key];
      if(sf&&sf.value){ vals.push(sf.value); fields.push(sf); }
    } else {
      total=nItems;
      items.forEach(function(it){
        const sf=it[key];
        if(sf&&sf.value){ vals.push(sf.value); fields.push(sf); }
      });
    }
    if(vals.length){
      const uniq=[]; vals.forEach(v=>{ if(uniq.indexOf(v)<0) uniq.push(v); });
      known.push({label:label, value:uniq.join(', '), sf:fields[0],
                  have:vals.length, total:total});
    } else if(tier==='must' && total>0){
      missing.push(label);
    }
  });
  return {known:known, missing:missing, nItems:nItems};
}

function specHTML(spec){
  if(!spec) return '';
  const rd=spec.readiness||{};
  const fold=_specFold(spec);
  if(!fold.known.length && !fold.missing.length) return '';
  const cov=Math.round((rd.coverage||0)*100);
  const estim=rd.estimable
    ? '<span class="estim yes">estimável</span>'
    : '<span class="estim no">falta responder</span>';
  const rows=fold.known.map(function(r){
    // "1 de 5" only when the field is answered for some line items but not all — silence otherwise.
    const partial=(r.total>1 && r.have<r.total)
      ? '<span style="color:var(--mut2);font-size:11px"> · '+r.have+' de '+r.total+'</span>' : '';
    return '<div class="specrow">'
      +'<span class="sk">'+esc(r.label)+'</span>'
      +'<span class="sv">'+esc(r.value)+partial+'</span>'
      +_provChip(r.sf)+'</div>';
  }).join('');
  const missBlock=fold.missing.length
    ? '<div class="missline"><span class="mtag">Em falta</span>'
      +fold.missing.map(m=>'<span class="mchip">'+esc(m)+'</span>').join('')
      +(fold.nItems>1?'<span class="mnote">para '+fold.nItems+' peças</span>':'')+'</div>'
    : '';
  return '<div class="spec"><h4>O que já sabemos</h4>'
    +'<div class="speclead">'+estim+'<span class="cov"><i style="width:'+cov+'%"></i></span>'
    +'<span style="color:var(--mut2);font-size:11px">'+cov+'% dos obrigatórios</span></div>'
    +rows+missBlock+'</div>';
}

/* ── the expandable detail ──────────────────────────────────────────── */
function detailHTML(item){
  const root=item.thread_root;
  if(!root){
    return '<div class="gdetail"><span class="gload">Esta decisão não tem conversa associada — '
      +'é sobre o contacto em si.</span></div>';
  }
  const err=_detailErr[root];
  if(err) return '<div class="gdetail"><span style="color:var(--red);font-size:12.5px">'+esc(err)+'</span></div>';
  const d=_detail[root];
  if(!d) return '<div class="gdetail"><span class="gload">a carregar a conversa…</span></div>';
  const msgs=d.messages||[];
  const body=msgs.length
    ? msgThreadHTML(msgs, {provenance:(d.spec&&d.spec.provenance)||{}, attachments:d.attachments})
    : '<span class="gload">sem mensagens guardadas para esta thread</span>';
  return '<div class="gdetail">'+body+specHTML(d.spec)+'</div>';
}

async function loadDetail(item){
  const root=item.thread_root;
  if(!root || _detail[root] || _detailErr[root]) return;
  try{
    const d=await getJSON('/api/thread/'+encodeURIComponent(root));
    if(d.error) _detailErr[root]=d.error;
    /* `attachments` is KEPT. It was dropped here while detailHTML went on passing
       `attachments: d.attachments` — undefined — so attFunnelHTML returned '' and Para Ti has never
       once rendered the ADR-046 funnel, on a page whose whole job is deciding about a conversation.
       Confirmed in Chrome: a thread showing 8 📎 chips and no `.attf` at all. The sweep in
       tests/test_attachments.py only ever asserted that no lens FORKS the shared kit — never that a
       lens RENDERS one, which is exactly why two lines of omission survived. */
    else _detail[root]={messages:d.messages||[], spec:d.spec||null,
                        attachments:d.attachments||null};
  }catch(e){ _detailErr[root]='falhou ao carregar a conversa'; }
}

async function toggleExpand(i){
  const v=visible(), it=v[i]; if(!it) return;
  const k=itemKey(it);
  focus=i;
  if(openKey===k){ openKey=null; syncURL(); render(); return; }
  openKey=k; syncURL(); render();       // paint the loading state immediately
  await loadDetail(it);
  if(openKey===k) render();             // still the open one? (user may have moved on)
}

/* ── actions ────────────────────────────────────────────────────────── */
function actionsHTML(item, i){
  const acc=item.accept||{}, ctx=item.context||{};
  const accBtn = acc.api
    ? '<button class="act-btn accept" data-i="'+i+'" data-act="accept">'+esc(acc.label||'Aceitar')+'</button>'
    : (acc.href ? '<a class="act-btn accept" data-i="'+i+'" data-act="nav" href="'+esc(acc.href)+'">'+esc(acc.label||'Ver')+'</a>' : '');
  let html=accBtn+'<button class="act-btn" data-i="'+i+'" data-act="dismiss">Ignorar</button>';
  if(ctx.message_id){
    html+='<button class="act-btn" data-i="'+i+'" data-act="reclassCp" title="corrigir a contraparte">'
      +esc(_cpLabel(ctx.counterparty))+' ▾</button>';
    if(ctx.purpose){
      html+='<button class="act-btn" data-i="'+i+'" data-act="reclassPur" title="corrigir o tipo">'
        +esc(_purposeLabel(ctx.purpose))+' ▾</button>';
    }
  }
  if(item.thread_root){
    const owners=(ctx.owners||[]);
    html+='<button class="act-btn" data-i="'+i+'" data-act="owner" title="atribuir dono">'
      +(owners.length?esc(owners.join(', ')):'Dono')+' ▾</button>';
    html+='<button class="act-btn" data-i="'+i+'" data-act="handled" title="marcar como tratado — sai da Fila">Tratado</button>';
  }
  html+='<span class="sep"></span>';
  if(ctx.project){
    html+='<button class="pchip in" data-i="'+i+'" data-act="openproj" title="abrir o projeto onde já está a ser tratado">📁 '
      +esc(ctx.project.title||ctx.project.project_id)+'</button>';
  }
  if(item.thread_root){
    /* '/fila?thread=' — explicit prefix and the CANONICAL param. It said '/?focus=' until ADR-044
       moved the Fila off the root, which sent this straight to Início (no query params read at all).
       encodeURIComponent, not esc(): a Message-ID carrying '&' or '+' produced a URL that pointed at
       a different conversation, or none — and its output is already safe in an attribute. */
    html+='<a href="/fila?thread='+encodeURIComponent(item.thread_root)+'" data-act="nav" style="font-size:12px;color:var(--ac);text-decoration:none">ver na fila →</a>';
  }
  return '<div class="gacts">'+html+'</div>';
}

async function acceptItem(i){
  const v=visible(); const item=v[i]; if(!item) return;
  const acc=item.accept||{};
  if(acc.api){
    try{
      await post(acc.api, acc.payload||{});
      dismissed.add(itemKey(item)); render(); toast('feito');
      // The accept changed server state (new project / identity link), so the queue it was built
      // from is now out of date — pull the rebuilt one instead of waiting for the next tick.
      refresh({quiet:true});
      if(acc.nav) setTimeout(()=>{ location.href=acc.nav; }, 700);
    } catch(e){ toast(S.falhou); }   /* the accept never happened — nothing to revert */
  } else if(acc.href||acc.nav){
    // navigation-only accept (e.g. "Ver na Fila") — the mouse path is a native <a>, but the
    // keyboard 'y' accept routes here, so honour it too instead of silently doing nothing.
    location.href=acc.href||acc.nav;
  }
}
function dismissItem(i){
  const v=visible(); const item=v[i]; if(!item) return;
  const k=item.key||itemKey(item);
  if(openKey===k) openKey=null;
  // Optimistic + PERSISTED + undoable: the old version kept the dismissal in this Set only, so the
  // toast said "ignorado" while every ignored proposal resurrected on the next page load.
  dismissed.add(k); render(); toast('ignorado · Z desfaz'); announce('ignorado');
  undo.push({label:'ignorado',revert:()=>{
    dismissed.delete(k); render();
    post('/api/para-ti/undismiss',{key:k}).then(()=>refresh({quiet:true})).catch(()=>toast(S.falhou));
  }});
  post('/api/para-ti/dismiss',{key:k,kind:item.kind||''})
    .catch(()=>{ dismissed.delete(k); undo.pop(); render(); toast(S.revertido); });
}

/* Mark the underlying thread handled — the decision is resolved without creating anything. */
async function markHandled(i){
  const v=visible(), it=v[i]; if(!it||!it.thread_root) return;
  const k=it.key||itemKey(it);
  const root=it.thread_root;
  try{
    await post('/api/thread/handled',{thread_root:root,handled:true});
    if(openKey===k) openKey=null;
    dismissed.add(k); render(); toast('tratado · Z desfaz'); announce('tratado');
    undo.push({label:S.tratado,revert:()=>{
      dismissed.delete(k); render();
      post('/api/thread/handled',{thread_root:root,handled:false})
        .then(()=>refresh({quiet:true})).catch(()=>toast(S.falhou));
    }});
    refresh({quiet:true});
  }catch(e){ toast(S.falhou); }   /* nothing was changed, so don't claim a revert */
}

function positionMenu(i){
  const card=document.querySelector('.gate[data-i="'+i+'"]');
  const m=$('#_menu'); if(!card||!m) return;
  const r=card.getBoundingClientRect();
  m.style.left=Math.round(r.left+18)+'px';
  m.style.top=Math.round(r.bottom+window.scrollY-8)+'px';
}

function reclassMenu(i, field){
  const v=visible(), it=v[i]; if(!it) return;
  const ctx=it.context||{};
  if(!ctx.message_id){ toast('sem id para corrigir'); return; }
  const m=$('#_menu'), dict=(LABELS&&LABELS[field])||{}, cur=ctx[field]||'';
  const auto=(ctx.auto&&ctx.auto[field])||'';
  const rows=Object.keys(dict).map(k=>'<div class="mi'+(k===cur?' on':'')+'" data-val="'+esc(k)+'">'+esc(dict[k])+'</div>').join('');
  const reset=auto?'<div class="mi reset" data-val="">↺ auto ('+esc(dict[auto]||auto)+')</div>':'';
  m.innerHTML='<div class="mhdr">'+(field==='counterparty'?'Contraparte':'Tipo')+'</div>'+rows+reset;
  m.dataset.i=i; m.dataset.kind='reclass'; m.dataset.field=field;
  m.classList.remove('hidden'); positionMenu(i);
}

function reclassify(i, field, value){
  const v=visible(), it=v[i]; if(!it) return;
  const ctx=it.context||{};
  if(!ctx.message_id) return;
  const auto=(ctx.auto&&ctx.auto[field])||ctx[field], prev=ctx[field];
  ctx[field]=value||auto;
  announce(value?'corrigido':'reposto'); render();
  post('/api/reclassify',{message_id:ctx.message_id, field, value_auto:auto, value_human:value||null})
    .then(()=>refresh({quiet:true}))
    .catch(()=>{ ctx[field]=prev; render(); toast(S.revertido); });
}

function ownerMenu(i){
  const v=visible(), it=v[i]; if(!it||!it.thread_root) return;
  const ctx=it.context||{}, owners=(ctx.owners||[]);
  const m=$('#_menu');
  const rows=roster.map(n=>'<div class="mi'+(owners.indexOf(n)>=0?' on':'')+'" data-val="'+esc(n)+'">'
    +(owners.indexOf(n)>=0?'✓ ':'')+esc(n)+'</div>').join('');
  const clear=owners.length?'<div class="mi reset" data-val="">↺ sem dono</div>':'';
  m.innerHTML='<div class="mhdr">Dono</div>'+(rows||'<div class="mi reset">sem equipa definida</div>')+clear;
  m.dataset.i=i; m.dataset.kind='owner'; m.dataset.field='';
  m.classList.remove('hidden'); positionMenu(i);
}

function setOwner(i, name){
  const v=visible(), it=v[i]; if(!it||!it.thread_root) return;
  const ctx=it.context||{}, prev=(ctx.owners||[]).slice();
  let next;
  if(!name) next=[];
  else next = prev.indexOf(name)>=0 ? prev.filter(n=>n!==name) : prev.concat([name]);
  ctx.owners=next; render();
  post('/api/thread/owner',{thread_root:it.thread_root, owners:next})
    .catch(()=>{ ctx.owners=prev; render(); toast(S.revertido); });
}

function openProject(i){
  const v=visible(), it=v[i]; if(!it) return;
  const p=(it.context||{}).project; if(!p) return;
  location.href='/projetos/'+encodeURIComponent(p.project_id);
}

function dispatch(action, i){
  if(action==='accept') acceptItem(i);
  else if(action==='dismiss') dismissItem(i);
  else if(action==='handled') markHandled(i);
  else if(action==='reclassCp') reclassMenu(i,'counterparty');
  else if(action==='reclassPur') reclassMenu(i,'purpose');
  else if(action==='owner') ownerMenu(i);
  else if(action==='openproj') openProject(i);
}

/* ── render ─────────────────────────────────────────────────────────── */
function renderCard(item, i){
  const ctx = item.context || {};
  const kindCls = KIND_CLASS[item.kind] || 'rever';
  const kindLbl = KIND_LABEL[item.kind] || item.kind;
  const isFocused = i === focus;
  const isOpen = openKey === itemKey(item);

  // context line: clock + purpose + contact + messages + attachment
  const clockPart = ctx.clock_label
    ? _clockDot(ctx.clock_band) + '<span style="font-weight:600;color:'
      + ({'red':'var(--red)','amber':'var(--amber)','green':'var(--green)'}[ctx.clock_band]||'var(--mut)')
      + '">' + esc(ctx.clock_label) + '</span>'
    : '';
  const contactPart = ctx.contact ? '<span style="color:var(--mut)">'+esc(ctx.contact)+'</span>' : '';
  const msgsPart = ctx.n_messages > 1
    ? '<span style="color:var(--mut2)">'+ctx.n_messages+' msgs</span>' : '';
  const attPart = ctx.has_attachment ? '<span title="tem anexo">📎</span>' : '';
  const purposePart = ctx.purpose
    ? '<span style="color:var(--mut2);font-style:italic">'+esc(_purposeLabel(ctx.purpose))+'</span>' : '';
  const ctxParts = [clockPart, purposePart, contactPart, msgsPart, attPart].filter(Boolean);
  const ctxLine = ctxParts.length
    ? '<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:6px 0 8px;font-size:12px">'
      + ctxParts.join('') + '</div>'
    : '';

  // AI reason — what the model understood. Dashed ink: a proposal, not a fact.
  const reasonBlock = ctx.reason
    ? '<div style="background:var(--amber-bg);border:1px solid var(--amber-line);border-radius:8px;padding:8px 12px;'
      + 'font-size:12.5px;color:var(--amber);line-height:1.55;margin-bottom:10px">'
      + '<span style="font-size:10px;text-transform:uppercase;letter-spacing:.05em;font-weight:700;'
      + 'color:var(--mut);margin-right:6px">O que a IA leu</span>'
      + esc(ctx.reason) + '</div>'
    : '';

  const identityExtra = (item.kind === 'confirmar_identidade' && ctx.proposed_cluster)
    ? '<div style="font-size:12.5px;color:var(--mut);margin-bottom:8px">'
      + 'Proposta de ligação: <strong>'+esc(ctx.contact)+'</strong> → empresa <strong>'
      + esc(ctx.proposed_cluster)+'</strong>'
      + (ctx.last_seen ? ' · última actividade: '+esc(ctx.last_seen.slice(0,10)) : '')
      + '</div>'
    : '';

  return '<div class="gate'+(isFocused?' on':'')+(isOpen?' open':'')+'" data-i="'+i+'"'
    + ' role="button" tabindex="0" aria-expanded="'+(isOpen?'true':'false')+'">'
    + '<div class="ghead">'
    +   '<span class="gchev">'+(isOpen?'▾':'▸')+'</span>'
    +   '<span class="gkind '+kindCls+'">'+esc(kindLbl)+'</span>'
    +   '<span class="ghint">'+(isOpen?'fechar':'ver conversa')+'</span>'
    + '</div>'
    + '<div style="font-weight:650;font-size:14.5px;margin-bottom:2px">'+esc(item.title||'')+'</div>'
    + ctxLine
    + reasonBlock
    + identityExtra
    + (isOpen ? detailHTML(item) : '')
    + actionsHTML(item, i)
    + '</div>';
}

function groupHTML(kind, n){
  return '<div class="ghdr"><span>'+esc(KIND_HEADING[kind]||kind)+'</span>'
    +'<span>'+n+'</span><span class="gline"></span></div>';
}

function render(){
  const v = visible();
  const cnt = $('#_count');
  if(cnt) cnt.textContent = v.length ? v.length + ' pendente'+(v.length===1?'':'s') : '';
  const zero = $('#_zero'); if(zero) zero.classList.toggle('hidden', v.length > 0);
  if(focus >= v.length) focus = Math.max(0, v.length - 1);
  renderChips();
  // Group by kind. all_items() already emits kind-major order, so a run-break is a group boundary.
  const counts={};
  v.forEach(it=>{ counts[it.kind]=(counts[it.kind]||0)+1; });
  let html='', last=null;
  v.forEach(function(item, i){
    if(item.kind!==last){ html+=groupHTML(item.kind, counts[item.kind]); last=item.kind; }
    html+=renderCard(item, i);
  });
  $('#_list').innerHTML = html;
  const open=document.querySelector('.gate.open .gdetail');
  if(open) msgWireQuoteToggles(open);
}

/* ── URL state (ADR-014: the open gate + filter are addressable) ─────── */
function syncURL(){
  try{
    const u=new URL(location.href);
    if(openKey) u.searchParams.set('item', openKey); else u.searchParams.delete('item');
    if(kindFilter) u.searchParams.set('tipo', kindFilter); else u.searchParams.delete('tipo');
    history.replaceState(null,'',u.pathname+(u.search||'')+u.hash);
  }catch(e){}
}

function applyURL(){
  try{
    const q=new URLSearchParams(location.search);
    const tipo=q.get('tipo')||'';
    if(tipo) kindFilter=tipo;
    const it=q.get('item')||'';
    if(it){
      const at=visible().findIndex(x=>itemKey(x)===it);
      if(at>=0){ openKey=it; focus=at; loadDetail(visible()[at]).then(render); }
    }
  }catch(e){}
}

function onKey(e){
  const v=visible(); if(!v.length) return;
  if(e.key==='j'||e.key==='ArrowDown'){focus=Math.min(v.length-1,focus+1);render();e.preventDefault();}
  else if(e.key==='k'||e.key==='ArrowUp'){focus=Math.max(0,focus-1);render();e.preventDefault();}
  else if(e.key==='Enter'||e.key===' '||e.key==='e'||e.key==='E'){toggleExpand(focus);e.preventDefault();}
  else if(e.key==='y'||e.key==='Y') acceptItem(focus);
  else if(e.key==='n'||e.key==='N') dismissItem(focus);
  else if(e.key==='a'||e.key==='A'){ownerMenu(focus);e.preventDefault();}
}

/* Esc closes the open gate (the shell calls this when no overlay is up). */
function onEsc(){ if(openKey){ openKey=null; syncURL(); render(); } }

/* ── live refresh (ADR-023) ────────────────────────────────────────────
   The server re-syncs mail on its own schedule, so an open tab would otherwise show the queue as
   of page load. Poll the same endpoint the page was rendered from and swap the list in place —
   keeping scroll, focus, the expanded gate and any dismissals — instead of reloading and throwing
   away the user's position mid-decision.                                                        */
const REFRESH_MS = 30000;
let _refreshing = false, _lastSig = null, _syncedAt = null;

function _sig(list){ return list.map(itemKey).join('\n'); }

function setNavCounts(counts){
  document.querySelectorAll('.nlink[data-nav]').forEach(a=>{
    const n = (counts||{})[a.dataset.nav] || 0;
    let b = a.querySelector('.nbadge');
    if(n){
      if(!b){ b=document.createElement('span'); b.className='nbadge'; a.appendChild(b); }
      b.textContent = n;
    } else if(b){ b.remove(); }
  });
}

function _agoLabel(iso){
  if(!iso) return '';
  const secs = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  if(secs < 90) return 'agora mesmo';
  const mins = Math.round(secs/60);
  if(mins < 60) return 'há '+mins+' min';
  const hrs = Math.round(mins/60);
  return 'há '+hrs+(hrs===1?' hora':' horas');
}

function paintFreshness(syncing){
  const el = $('#_fresh'); if(!el) return;
  if(syncing){ el.textContent='· a sincronizar…'; el.classList.remove('stale'); return; }
  if(!_syncedAt){ el.textContent=''; return; }
  const age = (Date.now() - Date.parse(_syncedAt)) / 1000;
  el.textContent = '· correio '+_agoLabel(_syncedAt);
  // Louder once the data outlives roughly two server sync intervals — the poll is working but
  // ingestion isn't, which is exactly the failure this feature exists to make visible.
  el.classList.toggle('stale', age > 45*60);
}

async function refresh(opts){
  opts = opts || {};
  if(_refreshing) return;
  _refreshing = true;
  try{
    const d = await getJSON('/api/para-ti');
    const next = d.items || [];
    _syncedAt = d.synced_at || _syncedAt;
    setNavCounts(d.nav_counts);
    paintFreshness(d.syncing);
    if(typeof setSynced==='function') setSynced(d.synced_at, d.syncing);   // feed the shell sync pill (P5d)
    const sig = _sig(next);
    if(sig === _lastSig && !opts.force) return;   // nothing moved — don't clobber the DOM
    // Preserve the caret: remember what was focused, restore it by key after the swap.
    const focusedKey = itemKey(visible()[focus]);
    const seen = new Set(items.map(itemKey));
    const added = next.filter(it => !seen.has(itemKey(it)) && !dismissed.has(itemKey(it))).length;
    items = next;
    _lastSig = sig;
    const v = visible();
    const at = v.findIndex(it => itemKey(it) === focusedKey);
    focus = at >= 0 ? at : Math.min(focus, Math.max(0, v.length - 1));
    // The expanded gate is remembered by key, so it survives a reorder; drop it only if the
    // decision it named is genuinely gone from the queue.
    if(openKey && !v.some(it => itemKey(it) === openKey)) openKey = null;
    render();
    if(!opts.quiet && added > 0) toast(added+(added===1?' novo item':' novos itens'));
  } catch(e){ /* offline / server restarting — keep showing what we have, retry next tick */ }
  finally{ _refreshing = false; }
}

// Poll only while the tab is actually being looked at; catch up the moment it regains focus.
everyMs(()=>{ if(!document.hidden) refresh(); }, REFRESH_MS);
document.addEventListener('visibilitychange', ()=>{ if(!document.hidden) refresh(); });
_lastSig = _sig(items);
applyURL();
refresh({quiet:true});
everyMs(()=>paintFreshness(false), 60000);

function paletteItems(q){
  q=(q||'').toLowerCase().trim();
  const base=[
    {kind:'ação',label:'Início',run:()=>{location.href='/';}},
    {kind:'ação',label:'Fila',run:()=>{location.href='/fila';}},
    {kind:'ação',label:'Contrapartes',run:()=>{location.href='/contrapartes';}},
    {kind:'ação',label:'Projetos',run:()=>{location.href='/projetos';}},
    {kind:'ação',label:'Capturas',run:()=>{location.href='/capturas';}},
    {kind:'ação',label:S.actSync,run:syncNow},
    {kind:'ação',label:'Mostrar todos os tipos',run:()=>{kindFilter='';syncURL();render();}},
  ];
  // Jump straight to a decision by name — the queue is a haystack once it gets long.
  visible().forEach((it,i)=>base.push({
    kind:'decisão', label:it.title||'(sem assunto)',
    sub:(KIND_LABEL[it.kind]||it.kind), run:()=>{ focus=i; toggleExpand(i); }}));
  return q?base.filter(it=>(it.label+' '+it.kind+' '+(it.sub||'')).toLowerCase().includes(q)):base;
}

/* ── events ─────────────────────────────────────────────────────────── */
$('#_chips').addEventListener('click', e=>{
  const c=e.target.closest('.chip'); if(!c) return;
  setFilter(c.dataset.kind||'');
});

$('#_list').addEventListener('click', e=>{
  // Inside the detail panel: let links/toggles work, never collapse the card under the user.
  if(e.target.closest('.gdetail')) return;
  const btn=e.target.closest('[data-act]');
  if(btn){
    const i=parseInt(btn.dataset.i,10);
    if(btn.dataset.act==='nav') return;          // plain <a> — let the browser navigate
    e.stopPropagation();
    dispatch(btn.dataset.act, i);
    return;
  }
  const card=e.target.closest('.gate');
  if(card) toggleExpand(parseInt(card.dataset.i,10));
});

$('#_list').addEventListener('keydown', e=>{
  if(e.key!=='Enter' && e.key!==' ') return;
  const card=e.target.closest('.gate'); if(!card) return;
  if(e.target.closest('[data-act]') || e.target.closest('.gdetail')) return;
  e.preventDefault();
  toggleExpand(parseInt(card.dataset.i,10));
});

/* menu selection (reclassify / owner) */
$('#_menu').addEventListener('click', e=>{
  const mi=e.target.closest('.mi'); if(!mi) return;
  const m=$('#_menu'), i=parseInt(m.dataset.i,10);
  if(mi.dataset.val===undefined){ m.classList.add('hidden'); return; }
  if(m.dataset.kind==='reclass') reclassify(i, m.dataset.field, mi.dataset.val);
  else if(m.dataset.kind==='owner') setOwner(i, mi.dataset.val);
  m.classList.add('hidden');
});
"""


def build_html(items: list[dict[str, Any]],
               nav_counts: dict[str, int] | None = None,
               roster: list[str] | None = None,
               person: dict[str, Any] | None = None) -> str:
    return cockpit_ui.page(
        "Para ti", "para-ti", _BODY,
        embeds={"items": items,
                "kind_class": _KIND_CLASS,
                "kind_label": _KIND_LABEL,
                "kind_heading": _KIND_HEADING,
                # Canonical pt-PT enum labels + the field registry, so the detail panel names things
                # exactly as the rest of the cockpit does (labels.py / jobspec.FIELDS).
                "labels": _labels.fila_labels(),
                "fields": [list(f) for f in js.FIELDS],
                "roster": list(roster or [])},
        lens_js=_LENS_JS,
        nav_counts=nav_counts,
        extra_css=_EXTRA_CSS,
        person=person,
    )
