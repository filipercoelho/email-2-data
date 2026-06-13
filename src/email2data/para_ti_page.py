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
<style>
  .gate.on{border-color:var(--ac);box-shadow:0 0 0 2px rgba(51,88,212,.15),var(--shadow)}
</style>
"""

# Colours per gate kind
_KIND_CLASS = {"rever_classificacao": "rever", "propor_projeto": "projeto",
               "confirmar_identidade": "identidade"}
_KIND_LABEL = {"rever_classificacao": "Rever", "propor_projeto": "Propor projeto",
               "confirmar_identidade": "Identidade"}

_LENS_JS = r"""
let items = ITEMS.slice(), focus = 0, dismissed = new Set();

function visible(){ return items.filter((_,i)=>!dismissed.has(i)); }

function _clockDot(band){
  const col={'red':'var(--red)','amber':'var(--amber)','green':'var(--green)'}[band]||'var(--mut2)';
  return '<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:'+col+';margin-right:5px;vertical-align:middle"></span>';
}

function _purposeLabel(p){
  return {'ESTIMATE_REQUEST_FROM_CLIENT':'pedido orçamento','PO_FROM_CLIENT':'encomenda (PO)',
          'FOLLOW_UP':'follow-up','OUR_ORDER_TO_SUPPLIER':'encomenda a fornecedor',
          'SUPPLIER_REPLY_OR_CONFIRMATION':'resposta fornecedor','OUTBOUND_INVOICE':'fatura',
          'INVOICE_OR_ACCOUNTING':'faturação'}[p] || (p||'').toLowerCase().replace(/_/g,' ');
}

function renderCard(item, i){
  const ctx = item.context || {};
  const kindCls = KIND_CLASS[item.kind] || 'rever';
  const kindLbl = KIND_LABEL[item.kind] || item.kind;
  const acc = item.accept || {};
  const isFocused = i === focus;

  // context line: clock + contact + messages + attachment
  const clockPart = ctx.clock_label
    ? _clockDot(ctx.clock_band) + '<span style="font-weight:600;color:'
      + ({'red':'var(--red)','amber':'var(--amber)','green':'var(--green)'}[ctx.clock_band]||'var(--mut)')
      + '">' + esc(ctx.clock_label) + '</span>'
    : '';
  const contactPart = ctx.contact ? '<span style="color:var(--mut)">'+esc(ctx.contact)+'</span>' : '';
  const msgsPart = ctx.n_messages > 1
    ? '<span style="color:var(--mut2)">'+ctx.n_messages+' msgs</span>' : '';
  const attPart = ctx.has_attachment
    ? '<span title="tem anexo">📎</span>' : '';
  const purposePart = ctx.purpose
    ? '<span style="color:var(--mut2);font-style:italic">'+esc(_purposeLabel(ctx.purpose))+'</span>' : '';
  const ctxParts = [clockPart, purposePart, contactPart, msgsPart, attPart].filter(Boolean);
  const ctxLine = ctxParts.length
    ? '<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:6px 0 8px;font-size:12px">'
      + ctxParts.join('') + '</div>'
    : '';

  // AI reason — the most valuable context: what the model understood
  const reasonBlock = ctx.reason
    ? '<div style="background:#fffdf3;border:1px solid #f0e6c0;border-radius:8px;padding:8px 12px;'
      + 'font-size:12.5px;color:#4a4326;line-height:1.55;margin-bottom:10px">'
      + '<span style="font-size:10px;text-transform:uppercase;letter-spacing:.05em;font-weight:700;'
      + 'color:var(--mut);margin-right:6px">O que a IA leu</span>'
      + esc(ctx.reason) + '</div>'
    : '';

  // identity-specific extra context
  const identityExtra = (item.kind === 'confirmar_identidade' && ctx.proposed_cluster)
    ? '<div style="font-size:12.5px;color:var(--mut);margin-bottom:8px">'
      + 'Proposta de ligação: <strong>'+esc(ctx.contact)+'</strong> → empresa <strong>'
      + esc(ctx.proposed_cluster)+'</strong>'
      + (ctx.last_seen ? ' · última actividade: '+esc(ctx.last_seen.slice(0,10)) : '')
      + '</div>'
    : '';

  const acceptBtn = acc.api
    ? '<button class="act-btn accept" data-i="'+i+'" data-act="accept">'+esc(acc.label||'Aceitar')+'</button>'
    : (acc.href ? '<a class="act-btn accept" href="'+esc(acc.href)+'">'+esc(acc.label||'Ver')+'</a>' : '');

  const filaLink = item.thread_root
    ? ' <a href="/?focus='+esc(item.thread_root)+'" style="font-size:12px;color:var(--ac);text-decoration:none;margin-left:6px">ver na fila →</a>'
    : '';

  return '<div class="gate'+(isFocused?' on':'')+'" data-i="'+i+'" style="cursor:pointer">'
    + '<span class="gkind '+kindCls+'">'+esc(kindLbl)+'</span>'
    + '<div style="font-weight:650;font-size:14.5px;margin-bottom:2px">'+esc(item.title||'')+'</div>'
    + ctxLine
    + reasonBlock
    + identityExtra
    + '<div class="gacts">' + acceptBtn
    + '<button class="act-btn" data-i="'+i+'" data-act="dismiss">Ignorar</button>'
    + filaLink + '</div></div>';
}

function render(){
  const v = visible();
  const cnt = $('#_count');
  if(cnt) cnt.textContent = v.length ? v.length + ' pendente'+(v.length===1?'':'s') : '';
  const zero = $('#_zero'); if(zero) zero.classList.toggle('hidden', v.length > 0);
  if(focus >= v.length) focus = Math.max(0, v.length - 1);
  $('#_list').innerHTML = v.map((item, i) => renderCard(item, i)).join('');
}

async function acceptItem(i){
  const v=visible(); const item=v[i]; if(!item) return;
  const acc=item.accept||{};
  if(acc.api){
    try{
      await post(acc.api, acc.payload||{});
      dismissed.add(items.indexOf(item)); render(); toast('feito');
      if(acc.nav) setTimeout(()=>{ location.href=acc.nav; }, 700);
    } catch(e){ toast(S.revertido); }
  } else if(acc.href||acc.nav){
    // navigation-only accept (e.g. "Ver na Fila") — the mouse path is a native <a>, but the
    // keyboard 'y' accept routes here, so honour it too instead of silently doing nothing.
    location.href=acc.href||acc.nav;
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
