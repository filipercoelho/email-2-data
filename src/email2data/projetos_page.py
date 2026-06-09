"""C4 — Projetos lens page (/projetos). Lead→estimable guided funnel.

Thin wrapper over cockpit_ui.page(). Uses the existing /api/projects* endpoints.
The readiness ring, gaps-as-PT-questions, and stage pipeline are rendered here —
this sidesteps the report.py WIP entirely (same pattern as the Fila).
"""

from __future__ import annotations

from typing import Any

from . import cockpit_ui

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
  .pstage{display:inline-flex;gap:4px;align-items:center;margin-left:8px;flex-wrap:wrap}
  .pstage .st{padding:2px 8px;border-radius:20px;font-size:10px;font-weight:700;border:1px solid var(--bd);color:var(--mut);cursor:pointer}
  .pstage .st.on{background:var(--int);color:#fff;border-color:var(--int)}
  .pstage .st.terminal{background:var(--green);color:#fff;border-color:var(--green)}
  .qs li{margin-bottom:6px;font-size:13px;color:#3a4150}
  .miss-tag{background:#fff3f3;border:1px solid #f3c9c9;color:#b4424a;border-radius:7px;padding:2px 8px;font-size:11.5px;display:inline-block;margin:2px}
</style>
"""

_STAGES = ["LEAD", "GATHERING", "ESTIMABLE", "QUOTED", "WON", "LOST", "ARCHIVED"]
_TERMINAL = {"QUOTED", "WON", "LOST", "ARCHIVED"}

_LENS_JS = r"""
let projects = PROJECTS.slice(), focus = 0, selected = null;
const STAGES = ['LEAD','GATHERING','ESTIMABLE','QUOTED','WON','LOST','ARCHIVED'];
const TERMINAL = new Set(['QUOTED','WON','LOST','ARCHIVED']);

function ringHTML(cov, estimable){
  const r=17, c=2*Math.PI*r, fill=Math.round(cov*c);
  const cls='ring-fill'+(estimable?' done':'');
  return '<div class="ring-wrap"><svg viewBox="0 0 42 42"><circle class="ring-track" cx="21" cy="21" r="'+r+'"/>'
    +'<circle class="'+cls+'" cx="21" cy="21" r="'+r+'" stroke-dasharray="'+c+'" stroke-dashoffset="'+(c-fill)+'"/></svg>'
    +'<div class="ring-pct">'+Math.round(cov*100)+'%</div></div>';
}

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

function renderDetail(){
  if(!selected){$('#_detail').classList.add('hidden');$('#_list').classList.remove('hidden');return;}
  $('#_list').classList.add('hidden');
  $('#_detail').classList.remove('hidden');
  const p=selected.project, rd=selected.readiness||{};
  const stages=STAGES.map(s=>'<span class="st'+(p.stage===s?' on':'')+(TERMINAL.has(s)&&p.stage===s?' terminal':'')+'" data-stage="'+s+'">'+s+'</span>').join('');
  const miss=(rd.missing||[]).map(a=>'<span class="miss-tag">'+esc(a)+'</span>').join('');
  const qs=(rd.questions||[]).length?'<ol class="qs">'+rd.questions.map(q=>'<li>'+esc(q)+'</li>').join('')+'</ol>':'';
  $('#_detail').innerHTML='<button class="hbtn" id="_backbtn" style="margin-bottom:14px">← Projetos</button>'
    +'<h2 style="margin:0 0 6px;font-size:20px;letter-spacing:-.01em">'+esc(p.title)+'</h2>'
    +'<div style="display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:16px">'
    +ringHTML(rd.coverage||0,rd.estimable||false)
    +'<div class="pstage">'+stages+'</div></div>'
    +(miss?'<div style="margin-bottom:12px"><div style="font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);font-weight:680;margin-bottom:6px">Em falta</div>'+miss+'</div>':'')
    +(qs?'<div style="margin-bottom:12px"><div style="font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);font-weight:680;margin-bottom:6px">Perguntas para o cliente</div>'+qs+'</div>':'')
    +(rd.estimable?'<button class="act-btn accept" id="_exportbtn">Exportar para custeio</button>':'');
  $('#_backbtn').onclick=()=>{selected=null;renderList();$('#_detail').classList.add('hidden');$('#_list').classList.remove('hidden');};
  document.querySelectorAll('.pstage .st').forEach(el=>el.addEventListener('click',async()=>{
    try{await post('/api/projects/'+selected.project_id+'/stage',{stage:el.dataset.stage});
      const d=await (await fetch('/api/projects/'+selected.project_id)).json();selected=d;renderDetail();}
    catch(e){toast(S.revertido);}
  }));
  const eb=$('#_exportbtn');
  if(eb) eb.addEventListener('click',async()=>{
    try{const r=await post('/api/projects/'+selected.project_id+'/export',{adapter:'json'});
      toast(r.ok?'exportado: '+(r.external_id||'ok'):S.revertido);}
    catch(e){toast(S.revertido);}
  });
}

function render(){ if(selected) renderDetail(); else renderList(); }

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

$('#_list').addEventListener('click',e=>{
  const row=e.target.closest('.row'); if(!row) return;
  focus=parseInt(row.dataset.i,10); loadDetail(row.dataset.pid);
});
"""


def build_html(projects: list[dict[str, Any]],
               nav_counts: dict[str, int] | None = None) -> str:
    return cockpit_ui.page(
        "Projetos", "projetos", _BODY,
        embeds={"projects": projects},
        lens_js=_LENS_JS,
        nav_counts=nav_counts,
    )
