"""C2 — Contrapartes lens page (list at /contrapartes, detail at /contrapartes/{key}).

Thin wrapper over cockpit_ui.page(). The JS is pure presentation; every datum
(clusters, timeline) is computed server-side in accounts.py + crm.py.
"""

from __future__ import annotations

from typing import Any

from . import cockpit_ui

# ── list page ──────────────────────────────────────────────────────────────────

_LIST_BODY = """
<div class="wrap">
  <div class="bar">
    <span id="_count"></span>
    <span class="cmdk"><kbd>⌘K</kbd> comandos</span>
  </div>
  <div id="_list"></div>
  <div id="_zero" class="zero hidden">Sem contrapartes<span class="s">nenhuma interação encontrada</span></div>
  <div class="hint"><b>J/K</b> navegar · <b>Enter</b> abrir · <b>⌘K</b> comandos · <b>?</b> ajuda</div>
</div>
"""

_LIST_JS = r"""
let clusters = CLUSTERS.slice(), focus = 0;

function render(){
  const cnt = $('#_count');
  if(cnt) cnt.textContent = clusters.length ? clusters.length + ' contrapartes' : '';
  const zero = $('#_zero'); if(zero) zero.classList.toggle('hidden', clusters.length > 0);
  if(focus >= clusters.length) focus = Math.max(0, clusters.length - 1);
  const list = $('#_list');
  list.innerHTML = clusters.map((c, i) => {
    const risk = (c.we_owe_count > 0) ? '<span class="clock red"><span class="d" aria-hidden="true"></span>' + c.we_owe_count + ' devem resposta</span>' : '';
    const proj = c.open_projects ? '<span style="color:var(--purple);font-size:11.5px">'+c.open_projects+' projeto'+(c.open_projects===1?'':'s')+'</span>' : '';
    return '<div class="ccard'+(i===focus?' on':'')+'" data-i="'+i+'" data-key="'+esc(c.key)+'">'
      +'<div class="ch"><span class="cp '+(c.last_counterparty||'OTHER')+'">'+esc(c.last_counterparty||'—')+'</span>'
      +'<span class="cname">'+esc(c.display_name||c.key)+'</span>'
      +'<span class="cstat">'+risk+(risk&&proj?' · ':'')+proj+'</span></div>'
      +'<div class="cemails">'+esc((c.emails||[]).slice(0,3).join(' · ')+(c.emails.length>3?' + '+(c.emails.length-3)+' mais':''))+'</div>'
      +'</div>';
  }).join('');
}

function onKey(e){
  if(!clusters.length) return;
  if(e.key==='j'||e.key==='ArrowDown'){focus=Math.min(clusters.length-1,focus+1);render();const el=document.querySelectorAll('.ccard')[focus];if(el)el.scrollIntoView({block:'nearest'});e.preventDefault();}
  else if(e.key==='k'||e.key==='ArrowUp'){focus=Math.max(0,focus-1);render();const el=document.querySelectorAll('.ccard')[focus];if(el)el.scrollIntoView({block:'nearest'});e.preventDefault();}
  else if(e.key==='Enter'&&clusters[focus]){location.href='/contrapartes/'+encodeURIComponent(clusters[focus].key);}
}

function paletteItems(q){
  q=(q||'').toLowerCase().trim();
  const items=[
    {kind:'ação',label:'Fila',run:()=>{location.href='/';}},
    {kind:'ação',label:'Para ti',run:()=>{location.href='/para-ti';}},
    {kind:'ação',label:'Projetos',run:()=>{location.href='/projetos';}},
    {kind:'ação',label:S.actSync,run:syncNow},
  ];
  clusters.forEach(c=>items.push({kind:'contraparte',label:c.display_name||c.key,
    sub:(c.last_counterparty||'')+' · '+c.msg_count+' msgs',
    run:()=>{location.href='/contrapartes/'+encodeURIComponent(c.key);}}));
  return q?items.filter(it=>(it.label+' '+(it.sub||'')+' '+it.kind).toLowerCase().includes(q)):items;
}

$('#_list').addEventListener('click', e=>{
  const card=e.target.closest('.ccard'); if(!card) return;
  location.href='/contrapartes/'+encodeURIComponent(card.dataset.key);
});
"""


def build_list_html(clusters: list[dict[str, Any]],
                    nav_counts: dict[str, int] | None = None) -> str:
    return cockpit_ui.page(
        "Contrapartes", "contrapartes", _LIST_BODY,
        embeds={"clusters": clusters},
        lens_js=_LIST_JS,
        nav_counts=nav_counts,
    )


# ── detail page ────────────────────────────────────────────────────────────────

_DETAIL_BODY = """
<div class="wrap">
  <div class="bar">
    <a class="nlink" href="/contrapartes" style="font-size:12.5px">← Contrapartes</a>
    <span id="_clock_label" style="font-size:12.5px;font-weight:600"></span>
    <span class="cmdk"><kbd>⌘K</kbd> comandos</span>
  </div>
  <div id="_header" style="margin-bottom:18px"></div>
  <div id="_threads" style="margin-bottom:24px"></div>
  <div id="_timeline"></div>
  <div id="_projects" style="margin-top:24px"></div>
</div>
"""

_DETAIL_JS = r"""
const cl = CLUSTER, tl = TIMELINE, proj = PROJECTS, frows = FILA_ROWS;

function render(){
  const cp = cl.last_counterparty || 'OTHER';
  $('#_header').innerHTML = '<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:6px">'
    +'<span class="cp '+esc(cp)+'">'+esc(cp)+'</span>'
    +'<span style="font-size:20px;font-weight:680;letter-spacing:-.01em">'+esc(cl.display_name||cl.key)+'</span>'
    +'</div>'
    +'<div style="color:var(--mut);font-size:12.5px">'+esc((cl.emails||[]).join(' · '))+'</div>';

  // Open threads (from the Fila)
  if(frows.length){
    $('#_threads').innerHTML = '<div style="font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);font-weight:680;margin-bottom:8px">Threads abertas</div>'
      + frows.map(r=>{
        const c=r.clock||{};
        return '<div class="row" style="border-left:3px solid transparent;border-radius:10px;margin-bottom:6px" onclick="location.href=\'/?focus='+encodeURIComponent(r.thread_root)+'\'">'
          +'<span class="cp '+esc(r.counterparty||'OTHER')+'">'+esc(r.counterparty||'—')+'</span>'
          +'<div class="rmain"><div class="subj">'+esc(r.subject||'(sem assunto)')+'</div>'
          +'<div class="rmeta">'+esc(r.contact||'')+'</div></div>'
          +'<span class="clock '+esc(c.band||'none')+'"><span class="d" aria-hidden="true"></span>'+esc(c.label||'')+'</span>'
          +'</div>';
      }).join('');
  } else { $('#_threads').innerHTML = ''; }

  // Timeline
  $('#_timeline').innerHTML = '<div style="font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);font-weight:680;margin-bottom:8px">Linha do tempo</div>'
    + '<ul class="timeline list" style="padding:0 14px">'
    + tl.map(item=>{
      const d=(item.date||'').slice(0,10);
      if(item.type==='interaction'){
        return '<li class="titem"><span class="td">'+esc(d)+'</span>'
          +'<div class="tc"><span class="ttype email">email</span>'+esc(item.subject||'(sem assunto)')
          +(item.purpose?' <span style="font-size:11px;color:var(--mut)">· '+esc(item.purpose)+'</span>':'')+'</div></li>';
      } else {
        return '<li class="titem"><span class="td">'+esc(d)+'</span>'
          +'<div class="tc"><span class="ttype projeto">projeto</span>'+esc(item.title||'')
          +' <span style="font-size:11px;color:var(--mut)">→ '+esc(item.stage||'')+'</span></div></li>';
      }
    }).join('')+'</ul>';

  // Projects
  if(proj.length){
    $('#_projects').innerHTML = '<div style="font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);font-weight:680;margin-bottom:8px">Projetos</div>'
      + proj.map(p=>'<div class="ccard" onclick="location.href=\'/projetos#'+esc(p.project_id)+'\'">'
        +'<div class="ch"><span class="cname">'+esc(p.title)+'</span>'
        +'<span class="cstat">'+esc(p.stage)+'</span></div></div>').join('');
  } else { $('#_projects').innerHTML = ''; }
}
function onKey(e){}
function paletteItems(q){
  return [
    {kind:'ação',label:'← Contrapartes',run:()=>{location.href='/contrapartes';}},
    {kind:'ação',label:'Fila',run:()=>{location.href='/';}},
    {kind:'ação',label:S.actSync,run:syncNow},
  ].filter(it=>!(q=q.toLowerCase().trim())||(it.label+' '+it.kind).toLowerCase().includes(q));
}
"""


def build_detail_html(cluster: dict[str, Any], timeline: list[dict[str, Any]],
                      projects: list[dict[str, Any]], fila_rows: list[dict[str, Any]],
                      nav_counts: dict[str, int] | None = None) -> str:
    return cockpit_ui.page(
        cluster.get("display_name") or cluster.get("key", "Contraparte"),
        "contrapartes", _DETAIL_BODY,
        embeds={"cluster": cluster, "timeline": timeline,
                "projects": projects, "fila_rows": fila_rows},
        lens_js=_DETAIL_JS,
        nav_counts=nav_counts,
    )
