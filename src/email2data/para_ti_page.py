"""C3 — Para ti lens page (/para-ti). Unified human-in-loop decision inbox.

Thin wrapper over cockpit_ui.page(). The gate items are built server-side
in para_ti.py; the JS handles rendering + accept/reject actions.
"""

from __future__ import annotations

from typing import Any

from . import cockpit_ui

_BODY = """
<div class="wrap">
  <div class="bar">
    <span id="_count"></span>
    <span class="cmdk"><kbd>⌘K</kbd> comandos</span>
  </div>
  <div id="_list"></div>
  <div id="_zero" class="zero hidden">✓ Sem decisões pendentes<span class="s">nada precisa da tua atenção agora</span></div>
  <div class="hint"><b>J/K</b> navegar · <b>Y</b> aceitar · <b>N</b> ignorar · <b>?</b> ajuda</div>
</div>
"""

# Colours per gate kind
_KIND_CLASS = {"rever_classificacao": "rever", "propor_projeto": "projeto",
               "confirmar_identidade": "identidade"}
_KIND_LABEL = {"rever_classificacao": "Rever", "propor_projeto": "Propor projeto",
               "confirmar_identidade": "Identidade"}

_LENS_JS = r"""
let items = ITEMS.slice(), focus = 0, dismissed = new Set();

function visible(){ return items.filter((_,i)=>!dismissed.has(i)); }

function render(){
  const v = visible();
  const cnt = $('#_count'); if(cnt) cnt.textContent = v.length ? v.length + ' pendente'+(v.length===1?'':'s') : '';
  const zero = $('#_zero'); if(zero) zero.classList.toggle('hidden', v.length > 0);
  if(focus >= v.length) focus = Math.max(0, v.length - 1);
  const list = $('#_list');
  list.innerHTML = v.map((item, i) => {
    const kindCls = KIND_CLASS[item.kind] || 'rever';
    const kindLbl = KIND_LABEL[item.kind] || item.kind;
    const acc = item.accept || {};
    const acceptBtn = acc.api
      ? '<button class="act-btn accept" data-i="'+i+'" data-act="accept">'+esc(acc.label||'Aceitar')+'</button>'
      : (acc.href ? '<a class="act-btn accept" href="'+esc(acc.href)+'">'+esc(acc.label||'Ver')+'</a>' : '');
    return '<div class="gate'+(i===focus?' on':'')+'" data-i="'+i+'">'
      +'<span class="gkind '+kindCls+'">'+esc(kindLbl)+'</span>'
      +'<div class="gtitle">'+esc(item.title||'')+'</div>'
      +'<div class="gwhy">'+esc(item.why||'')+'</div>'
      +'<div class="gacts">'+acceptBtn
      +'<button class="act-btn" data-i="'+i+'" data-act="dismiss">Ignorar</button></div></div>';
  }).join('');
}

async function acceptItem(i){
  const v=visible(); const item=v[i]; if(!item) return;
  const acc=item.accept||{};
  if(acc.api){
    try{
      await post(acc.api, acc.payload||{});
      dismissed.add(items.indexOf(item)); render(); toast('feito');
    } catch(e){ toast(S.revertido); }
  }
}
function dismissItem(i){
  const v=visible(); const item=v[i]; if(!item) return;
  dismissed.add(items.indexOf(item)); render(); toast('ignorado');
}

function onKey(e){
  const v=visible(); if(!v.length) return;
  if(e.key==='j'||e.key==='ArrowDown'){focus=Math.min(v.length-1,focus+1);render();e.preventDefault();}
  else if(e.key==='k'||e.key==='ArrowUp'){focus=Math.max(0,focus-1);render();e.preventDefault();}
  else if(e.key==='y'||e.key==='Y') acceptItem(focus);
  else if(e.key==='n'||e.key==='N') dismissItem(focus);
}

function paletteItems(q){
  q=(q||'').toLowerCase().trim();
  const base=[
    {kind:'ação',label:'Fila',run:()=>{location.href='/';}},
    {kind:'ação',label:'Contrapartes',run:()=>{location.href='/contrapartes';}},
    {kind:'ação',label:'Projetos',run:()=>{location.href='/projetos';}},
    {kind:'ação',label:S.actSync,run:syncNow},
  ];
  return q?base.filter(it=>(it.label+' '+it.kind).toLowerCase().includes(q)):base;
}

$('#_list').addEventListener('click', e=>{
  const btn=e.target.closest('[data-act]'); if(!btn) return;
  const i=parseInt(btn.dataset.i,10);
  if(btn.dataset.act==='accept') acceptItem(i);
  else if(btn.dataset.act==='dismiss') dismissItem(i);
});
"""


def build_html(items: list[dict[str, Any]],
               nav_counts: dict[str, int] | None = None) -> str:
    kind_class = _KIND_CLASS
    kind_label = _KIND_LABEL
    return cockpit_ui.page(
        "Para ti", "para-ti", _BODY,
        embeds={"items": items, "kind_class": kind_class, "kind_label": kind_label},
        lens_js=_LENS_JS,
        nav_counts=nav_counts,
    )
