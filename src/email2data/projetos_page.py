"""C4 — Projetos lens page (/projetos). Lead→estimable guided funnel.

Thin wrapper over cockpit_ui.page(). Uses the existing /api/projects* endpoints.
The detail view is a **job-spec workbench**: source emails (Origem) for context,
every must/should variable as an editable+confirmable field (Especificação), and
the missing must-haves as a copy/mailto client email (Perguntas). The field
registry is embedded straight from ``jobspec.FIELDS`` so the UI never drifts.
"""

from __future__ import annotations

from typing import Any

from . import cockpit_ui
from . import jobspec as _js

# Serialize the ONE field registry to the page so labels/questions/tiers/scope
# all come from jobspec (no hand-maintained JS copy to drift out of sync).
_FIELDS = [
    {"key": k, "label": lbl, "tier": tier, "q": q, "scope": scope}
    for k, lbl, tier, q, scope in _js.FIELDS
]

_BODY = """
<div class="wrap">
  <div class="bar">
    <span id="_count"></span>
    <span class="cmdk"><kbd>⌘K</kbd> comandos · <b>N</b> novo</span>
  </div>
  <div id="_list"></div>
  <div id="_zero" class="zero hidden">Sem projetos<span class="s">cria um projeto a partir da Fila ou do Para ti</span></div>
  <div id="_detail" class="hidden"></div>
</div>
<style>
  .pstage{display:inline-flex;gap:4px;align-items:center;flex-wrap:wrap}
  .pstage .st{padding:2px 8px;border-radius:20px;font-size:10px;font-weight:700;border:1px solid var(--bd);color:var(--mut);cursor:pointer}
  .pstage .st.on{background:var(--int);color:#fff;border-color:var(--int)}
  .pstage .st.terminal{background:var(--green);color:#fff;border-color:var(--green)}
  /* ── workbench sections ─────────────────────────────────────────────── */
  .psec{margin:20px 0 6px}
  .psec h3{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);font-weight:700;margin:0 0 8px;display:flex;align-items:center;gap:8px}
  .psec h3 .c{color:var(--mut2);font-weight:600;text-transform:none;letter-spacing:0}
  /* editable fields */
  .frow{display:flex;align-items:center;gap:10px;padding:4px 0}
  .frow label{flex:0 0 158px;font-size:12.5px;color:var(--mut);text-align:right}
  .frow.miss label{color:var(--red);font-weight:600}
  .frow.should label::after{content:' · opc';color:var(--mut2);font-size:10px}
  .fctl{flex:1;display:flex;align-items:center;gap:8px;min-width:0}
  .finput{flex:1;min-width:0;border:1px solid var(--bd);border-radius:8px;padding:6px 10px;font-size:13px;font-family:inherit;background:#fff;color:var(--tx)}
  .finput:focus{outline:none;border-color:var(--ac);box-shadow:0 0 0 3px #eef2ff}
  .frow.miss .finput{border-color:#f3c9c9;background:#fffafa}
  .finput::placeholder{color:var(--mut2)}
  .fsrc{font-size:9.5px;font-weight:700;text-transform:uppercase;letter-spacing:.03em;padding:2px 6px;border-radius:6px;flex:0 0 auto}
  .fsrc.s-offline{background:#eef2f7;color:var(--ext)} .fsrc.s-llm{background:#efeafb;color:var(--purple)} .fsrc.s-user{background:#e7f6ee;color:var(--green)}
  /* line-item cards */
  .item-card{border:1px solid var(--bd);border-radius:12px;padding:12px 14px;margin:10px 0;background:#fcfcfd}
  .item-card .ih{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}
  .item-card .ih b{font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em}
  .item-rm{border:1px solid var(--bd);background:#fff;border-radius:7px;font-size:11px;padding:2px 9px;cursor:pointer;color:var(--mut)}
  .item-rm:hover{border-color:var(--red);color:var(--red)}
  .addbtn{border:1px dashed var(--bd);background:#fff;border-radius:9px;padding:7px 13px;cursor:pointer;font-size:12.5px;color:var(--mut);font-weight:600;margin-top:4px}
  .addbtn:hover{border-color:var(--ac);color:var(--ac);background:#f6f8ff}
  /* origem (source emails) — thread CSS comes from cockpit_ui shared styles */
  .origem{max-height:420px;overflow:auto;border:1px solid var(--bd2);border-radius:10px;padding:8px 12px;background:#fcfcfd}
  .origem .texp{margin:0}
  .origem .tmsg{border-bottom:1px solid var(--bd2);border-radius:0;border-left:none;border-right:none;border-top:none;background:transparent;padding:10px 0}
  .origem .tmsg:last-child{border-bottom:none}
  .hint2{color:var(--mut2);font-size:12.5px;padding:9px 2px}
  .dwarn{background:#fff7ed;border:1px solid #fed7aa;color:#b45309;border-radius:8px;padding:6px 10px;font-size:12px;margin-top:8px}
  /* perguntas / ask */
  .qs li{margin-bottom:6px;font-size:13px;color:#3a4150}
  .qmail{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
  .ready{color:var(--green);font-size:12.5px;font-weight:600}
</style>
"""

_STAGES = ["LEAD", "GATHERING", "ESTIMABLE", "QUOTED", "WON", "LOST", "ARCHIVED"]
_TERMINAL = {"QUOTED", "WON", "LOST", "ARCHIVED"}

_LENS_JS = r"""
let projects = PROJECTS.slice(), focus = 0, selected = null;
const STAGES = ['LEAD','GATHERING','ESTIMABLE','QUOTED','WON','LOST','ARCHIVED'];
const TERMINAL = new Set(['QUOTED','WON','LOST','ARCHIVED']);

/* ── field registry (from jobspec.FIELDS — single source of truth) ────── */
const byKey = {}; FIELDS.forEach(f=>byKey[f.key]=f);
const JOB_F  = FIELDS.filter(f=>f.scope==='job'  && f.tier!=='context');
const ITEM_F = FIELDS.filter(f=>f.scope==='item' && f.tier!=='context');
function srcLabel(s){return s==='user'?'tu':s==='llm'?'IA':s==='offline'?'auto':'';}

/* ── readiness ring ───────────────────────────────────────────────────── */
function ringHTML(cov, estimable){
  const r=17, c=2*Math.PI*r, fill=Math.round(cov*c);
  const cls='ring-fill'+(estimable?' done':'');
  return '<div class="ring-wrap"><svg viewBox="0 0 42 42"><circle class="ring-track" cx="21" cy="21" r="'+r+'"/>'
    +'<circle class="'+cls+'" cx="21" cy="21" r="'+r+'" stroke-dasharray="'+c+'" stroke-dashoffset="'+(c-fill)+'"/></svg>'
    +'<div class="ring-pct">'+Math.round(cov*100)+'%</div></div>';
}

/* ── list ─────────────────────────────────────────────────────────────── */
function renderList(){
  const cnt=$('#_count'); if(cnt) cnt.textContent=projects.length?projects.length+' projeto'+(projects.length===1?'':'s'):'';
  const zero=$('#_zero'); if(zero) zero.classList.toggle('hidden',projects.length>0);
  if(focus>=projects.length) focus=Math.max(0,projects.length-1);
  $('#_list').innerHTML=projects.map((p,i)=>{
    const cov=p.coverage||0, est=p.estimable||false;
    return '<div class="row'+(i===focus?' on':'')+'" data-i="'+i+'" data-pid="'+esc(p.project_id)+'">'
      +ringHTML(cov,est)
      +'<div class="rmain"><div class="subj">'+esc(p.title)+'</div>'
      +'<div class="rmeta">'+esc(p.client_name||p.client_email||'')
      +' · '+esc(p.stage)
      +(p.n_threads?' · '+p.n_threads+' thread'+(p.n_threads===1?'':'s'):'')+'</div></div>'
      +'</div>';
  }).join('');
}

async function loadDetail(pid){
  try{
    const d=await (await fetch('/api/projects/'+pid)).json();
    selected=d; renderDetail();
  }catch(e){toast(S.revertido);}
}

/* ── one editable field row ───────────────────────────────────────────── */
function fieldRow(f, addr, fobj){
  const val=(fobj&&fobj.value)||'';
  const src=(fobj&&fobj.source)||'';
  const miss=!val;
  const badge=src?'<span class="fsrc s-'+esc(src)+'" title="origem do valor">'+srcLabel(src)+'</span>':'';
  return '<div class="frow'+(miss?' miss':'')+(f.tier==='should'?' should':'')+'" data-addr="'+esc(addr)+'">'
    +'<label>'+esc(f.label)+'</label>'
    +'<div class="fctl"><input class="finput" data-addr="'+esc(addr)+'" value="'+esc(val)+'" '
    +'placeholder="'+esc(f.q||'…')+'" autocomplete="off" spellcheck="false"/>'+badge+'</div>'
    +'</div>';
}

/* ── the client email built from the missing must-haves ───────────────── */
function clientEmailText(){
  const rd=selected.readiness||{};
  // internal-only questions (e.g. "(interno) processo de fabrico") never go to the client
  const qs=(rd.questions||[]).filter(q=>!q.startsWith('(interno'));
  const L=['Bom dia,','','Para conseguirmos avançar com o orçamento, precisávamos de confirmar:',''];
  qs.forEach((q,i)=>L.push((i+1)+'. '+q));
  L.push('','Obrigado.');
  return L.join('\n');
}

function detailHTML(){
  const p=selected.project, rd=selected.readiness||{};
  const job=selected.job_fields||{}, items=selected.items||[];
  const stages=STAGES.map(s=>'<span class="st'+(p.stage===s?' on':'')+(TERMINAL.has(s)&&p.stage===s?' terminal':'')+'" data-stage="'+s+'">'+s+'</span>').join('');
  const client=esc(p.client_name||p.client_email||'sem cliente');

  /* Origem — source emails (lazy-filled by loadSource) + dangling warning */
  const dangling=(selected.dangling_threads||[]).length;
  const dwarn=dangling?'<div class="dwarn">⚠ '+dangling+' thread'+(dangling===1?'':'s')+' sem contexto no CRM — reconstrói o crm ou volta a ligar o email.</div>':'';
  const nthreads=(selected.threads||[]).length;
  const origem='<div class="psec"><h3>Origem <span class="c">'+nthreads+' email'+(nthreads===1?'':'s')+'</span>'
    +'<button class="item-rm" id="_attachbtn" style="margin-left:auto">+ ligar email</button></h3>'
    +'<div id="_origem" class="origem"><div class="hint2">a carregar contexto…</div></div>'+dwarn+'</div>';

  /* Especificação — job-level + per-item editable fields */
  const jobRows=JOB_F.map(f=>fieldRow(f,f.key,job[f.key])).join('');
  const itemCards=items.map((it,i)=>
    '<div class="item-card"><div class="ih"><b>peça '+(i+1)+'</b>'
    +(items.length>1?'<button class="item-rm" data-idx="'+i+'">remover</button>':'')+'</div>'
    +ITEM_F.map(f=>fieldRow(f,f.key+'#'+i,it[f.key])).join('')+'</div>').join('');
  const espec='<div class="psec"><h3>Especificação <span class="c">os campos a vermelho faltam · escreve para preencher</span></h3>'
    +'<div style="margin-bottom:6px">'+jobRows+'</div>'
    +itemCards
    +'<button class="addbtn" id="_additem">+ adicionar peça</button></div>';

  /* Perguntas — the client email (copy / mailto), only while there are gaps */
  const qs=(rd.questions||[]);
  const qlist=qs.length?'<ol class="qs">'+qs.map(q=>'<li>'+esc(q)+'</li>').join('')+'</ol>':'';
  const mailto='mailto:'+encodeURIComponent(p.client_email||'')
    +'?subject='+encodeURIComponent('Re: '+(p.title||''))
    +'&body='+encodeURIComponent(clientEmailText());
  const ask=qs.length
    ? '<div class="psec"><h3>Perguntas para o cliente <span class="c">o que ainda falta perguntar</span></h3>'
      +qlist
      +'<div class="qmail"><button class="act-btn" id="_copyq">Copiar email</button>'
      +'<a class="act-btn" href="'+mailto+'">Abrir no email</a></div></div>'
    : '<div class="psec"><span class="ready">✓ Todos os obrigatórios estão preenchidos.</span></div>';

  const exp=rd.estimable?'<div class="psec"><button class="act-btn accept" id="_exportbtn">Exportar para custeio</button></div>':'';

  return '<button class="hbtn" id="_backbtn" style="margin-bottom:14px">← Projetos</button>'
    +'<h2 style="margin:0 0 8px;font-size:20px;letter-spacing:-.01em">'+esc(p.title)+'</h2>'
    +'<div style="display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:4px">'
    +'<span id="_ring">'+ringHTML(rd.coverage||0,rd.estimable||false)+'</span>'
    +'<div class="pstage">'+stages+'</div>'
    +'<span style="color:var(--mut);font-size:12.5px">'+client+'</span></div>'
    +origem+espec+'<div id="_ask">'+ask+'</div><div id="_exportwrap">'+exp+'</div>';
}

function renderDetail(){
  if(!selected){$('#_detail').classList.add('hidden');$('#_list').classList.remove('hidden');return;}
  $('#_list').classList.add('hidden');
  $('#_detail').classList.remove('hidden');
  $('#_detail').innerHTML=detailHTML();
  loadSource();
}

/* ── refresh only the summary bits after a field save (keep input focus) ─ */
function refreshSummary(){
  const rd=selected.readiness||{};
  const ring=$('#_ring'); if(ring) ring.innerHTML=ringHTML(rd.coverage||0,rd.estimable||false);
  const p=selected.project;
  const qs=rd.questions||[];
  const qlist=qs.length?'<ol class="qs">'+qs.map(q=>'<li>'+esc(q)+'</li>').join('')+'</ol>':'';
  const mailto='mailto:'+encodeURIComponent(p.client_email||'')+'?subject='+encodeURIComponent('Re: '+(p.title||''))+'&body='+encodeURIComponent(clientEmailText());
  const ask=$('#_ask');
  if(ask) ask.innerHTML=qs.length
    ? '<div class="psec"><h3>Perguntas para o cliente <span class="c">o que ainda falta perguntar</span></h3>'+qlist
      +'<div class="qmail"><button class="act-btn" id="_copyq">Copiar email</button>'
      +'<a class="act-btn" href="'+mailto+'">Abrir no email</a></div></div>'
    : '<div class="psec"><span class="ready">✓ Todos os obrigatórios estão preenchidos.</span></div>';
  const ew=$('#_exportwrap');
  if(ew) ew.innerHTML=rd.estimable?'<div class="psec"><button class="act-btn accept" id="_exportbtn">Exportar para custeio</button></div>':'';
}

/* update one field row's visual state in place (no re-render → keep focus) */
function markRow(addr, value){
  const row=$('#_detail').querySelector('.frow[data-addr="'+addr+'"]'); if(!row) return;
  row.classList.toggle('miss', !value);
  let b=row.querySelector('.fsrc');
  if(value){ if(!b){b=document.createElement('span');row.querySelector('.fctl').appendChild(b);} b.className='fsrc s-user'; b.textContent='tu'; }
  else if(b){ b.remove(); }
}

/* ── source emails (lazy, cached per project) ─────────────────────────── */
const _srcCache = {};
async function loadSource(){
  const box=$('#_origem'); if(!box) return;
  const pid=selected.project_id, roots=selected.threads||[];
  if(!roots.length){
    box.innerHTML='<div class="hint2">Sem emails ligados — este projeto não tem contexto. '
      +'Usa <b>+ ligar email</b> para anexar a thread de origem (importa também os campos já conhecidos).</div>';
    return;
  }
  if(_srcCache[pid]){box.innerHTML=_srcCache[pid];msgWireQuoteToggles(box);return;}
  try{
    const all=[];
    for(const root of roots){
      const d=await (await fetch('/api/thread/'+encodeURIComponent(root))).json();
      if(d&&d.messages) all.push(...d.messages);
    }
    // provenance: {field_addr: message_id} — shows which message supplied each spec field
    const prov=selected.provenance||{};
    const html=all.length
      ? msgThreadHTML(all, {provenance: prov})
      : '<div class="hint2">sem mensagens neste projeto</div>';
    _srcCache[pid]=html;
    const b2=$('#_origem'); if(b2){b2.innerHTML=html; msgWireQuoteToggles(b2);}
  }catch(e){
    const b2=$('#_origem');
    if(b2) b2.innerHTML='<div class="hint2" style="color:var(--red)">falhou ao carregar contexto</div>';
  }
}

function render(){ if(selected) renderDetail(); else renderList(); }

/* ── keyboard ─────────────────────────────────────────────────────────── */
function onKey(e){
  if(selected){ if(e.key==='Escape'){selected=null;render();} return; }
  if(!projects.length) return;
  if(e.key==='j'||e.key==='ArrowDown'){focus=Math.min(projects.length-1,focus+1);renderList();const el=document.querySelectorAll('.row')[focus];if(el)el.scrollIntoView({block:'nearest'});e.preventDefault();}
  else if(e.key==='k'||e.key==='ArrowUp'){focus=Math.max(0,focus-1);renderList();const el=document.querySelectorAll('.row')[focus];if(el)el.scrollIntoView({block:'nearest'});e.preventDefault();}
  else if(e.key==='Enter'&&projects[focus]) loadDetail(projects[focus].project_id);
  else if(e.key==='n'||e.key==='N') promptNew();
}

function paletteItems(q){
  q=(q||'').toLowerCase().trim();
  const base=[
    {kind:'ação',label:'Fila',run:()=>{location.href='/';}},
    {kind:'ação',label:'Contrapartes',run:()=>{location.href='/contrapartes';}},
    {kind:'ação',label:'Para ti',run:()=>{location.href='/para-ti';}},
    {kind:'ação',label:'Novo projeto',run:promptNew},
    {kind:'ação',label:S.actSync,run:syncNow},
  ];
  projects.forEach(p=>base.push({kind:'projeto',label:p.title,sub:p.stage,run:()=>loadDetail(p.project_id)}));
  return q?base.filter(it=>(it.label+' '+(it.sub||'')+' '+it.kind).toLowerCase().includes(q)):base;
}

function promptNew(){
  const t=prompt('Título do projeto:'); if(!t||!t.trim()) return;
  post('/api/projects',{title:t.trim()}).then(r=>{
    return fetch('/api/projects').then(res=>res.json()).then(list=>{projects=list;renderList();toast('criado: '+t);});
  }).catch(()=>toast(S.revertido));
}

/* ── list selection ───────────────────────────────────────────────────── */
$('#_list').addEventListener('click',e=>{
  const row=e.target.closest('.row'); if(!row) return;
  focus=parseInt(row.dataset.i,10); loadDetail(row.dataset.pid);
});

/* ── detail: save a field on change (blur/Enter), keep the user's place ── */
$('#_detail').addEventListener('change', async e=>{
  const inp=e.target.closest('.finput'); if(!inp||!selected) return;
  const addr=inp.dataset.addr, value=inp.value.trim();
  try{
    const d=await post('/api/projects/'+selected.project_id+'/field',{field:addr,value});
    selected=d; markRow(addr,value); refreshSummary();
  }catch(err){ toast(S.revertido); }
});

/* ── detail: all click actions via delegation (survive partial re-renders) */
$('#_detail').addEventListener('click', async e=>{
  if(!selected) return;
  if(e.target.closest('#_backbtn')){selected=null;render();return;}
  const st=e.target.closest('.pstage .st');
  if(st){ try{await post('/api/projects/'+selected.project_id+'/stage',{stage:st.dataset.stage});
    selected=await (await fetch('/api/projects/'+selected.project_id)).json(); renderDetail();}
    catch(err){toast(S.revertido);} return; }
  if(e.target.closest('#_attachbtn')){
    const ref=prompt('Cola o thread_root ou message_id do email a ligar:'); if(!ref||!ref.trim()) return;
    try{ selected=await post('/api/projects/'+selected.project_id+'/attach',{ref:ref.trim()});
      delete _srcCache[selected.project_id]; renderDetail(); toast('email ligado'); }
    catch(err){ toast(S.revertido); } return; }
  if(e.target.closest('#_additem')){ try{
    selected=await post('/api/projects/'+selected.project_id+'/item/add',{}); renderDetail();}
    catch(err){toast(S.revertido);} return; }
  const rm=e.target.closest('.item-rm');
  if(rm){ try{ selected=await post('/api/projects/'+selected.project_id+'/item/remove',{index:parseInt(rm.dataset.idx,10)}); renderDetail();}
    catch(err){toast(S.revertido);} return; }
  if(e.target.closest('#_copyq')){
    const txt=clientEmailText();
    try{ await navigator.clipboard.writeText(txt); toast('email copiado'); }
    catch(err){ toast('copia manual: '+txt.slice(0,40)+'…'); } return; }
  if(e.target.closest('#_exportbtn')){ try{
    const r=await post('/api/projects/'+selected.project_id+'/export',{adapter:'json'});
    toast(r.ok?'exportado: '+(r.external_id||'ok'):S.revertido);}
    catch(err){toast(S.revertido);} return; }
});

/* deep-link: /projetos?p=<pid> opens that project's detail directly (from the Fila chip) */
(function(){
  const pid=new URLSearchParams(location.search).get('p');
  if(pid){ history.replaceState(null,'','/projetos'); loadDetail(pid); }
})();
"""


def build_html(projects: list[dict[str, Any]],
               nav_counts: dict[str, int] | None = None) -> str:
    return cockpit_ui.page(
        "Projetos", "projetos", _BODY,
        embeds={"projects": projects, "fields": _FIELDS},
        lens_js=_LENS_JS,
        nav_counts=nav_counts,
    )
