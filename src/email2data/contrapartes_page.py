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
    <span class="cmdk"><kbd>⌘K</kbd> comandos</span>
  </div>
  <div id="_header" style="margin-bottom:14px"></div>
  <div id="_stats" class="stats"></div>
  <div id="_purposes" style="margin:6px 0 18px"></div>
  <div id="_threads" class="dsec"></div>
  <div id="_gates" class="dsec"></div>
  <div id="_projects" class="dsec"></div>
  <div id="_timeline" class="dsec"></div>
</div>
<style>
  /* header */
  .chead{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:6px}
  .ctitle{font-size:21px;font-weight:700;letter-spacing:-.01em}
  .kv{font-size:11px;font-weight:700;color:var(--mut);background:var(--bg);border:1px solid var(--bd);border-radius:20px;padding:2px 9px}
  .cemails{font-size:12.5px;color:var(--mut);display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-bottom:10px}
  .emlink{color:var(--ac);text-decoration:none;border-bottom:1px dotted var(--bd)}
  .emlink:hover{border-bottom-color:var(--ac)}
  .cemails .sep{color:var(--mut2)}
  .cactions{display:flex;gap:8px;flex-wrap:wrap}
  .cactions .act-btn{text-decoration:none;display:inline-flex;align-items:center;height:30px}
  /* stats strip */
  .stats{display:flex;gap:10px;flex-wrap:wrap}
  .stat{flex:1 1 96px;min-width:96px;background:var(--card);border:1px solid var(--bd);border-radius:12px;
    padding:11px 13px;box-shadow:var(--shadow)}
  .stat .sv{font-size:19px;font-weight:720;letter-spacing:-.01em;font-variant-numeric:tabular-nums}
  .stat .sl{font-size:11px;color:var(--mut);margin-top:1px}
  .stat.risk{background:#fbeaea;border-color:#f3c9c9}
  .stat.risk .sv,.stat.risk .sl{color:var(--red)}
  /* section labels + list rows */
  .dsec{margin-top:22px}
  .seclabel{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);font-weight:700;margin-bottom:9px}
  .seclabel .sh{text-transform:none;letter-spacing:0;color:var(--mut2);font-weight:500}
  .pchips{display:flex;gap:6px;flex-wrap:wrap}
  .pchip{font-size:11.5px;color:var(--tx);background:var(--card);border:1px solid var(--bd);border-radius:20px;padding:3px 10px}
  .pchip b{color:var(--mut);font-variant-numeric:tabular-nums}
  .lrow{display:flex;align-items:center;gap:11px;background:var(--card);border:1px solid var(--bd);
    border-left:3px solid transparent;border-radius:11px;padding:10px 13px;margin-bottom:7px;
    text-decoration:none;color:var(--tx);box-shadow:var(--shadow);transition:background .12s,border-color .12s}
  .lrow:hover{background:#f8f9fb;border-left-color:var(--ac)}
  .lrow .lmain{flex:1;min-width:0}
  .lrow .ltitle{font-weight:620;font-size:13.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .lrow .lsub{font-size:11.5px;color:var(--mut);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .lrow .go{color:var(--mut2);font-size:15px}
  .lrow .ststage{font-size:10px;font-weight:700;color:var(--mut);background:var(--bg);border:1px solid var(--bd);border-radius:20px;padding:2px 9px}
  /* clickable timeline */
  .timeline .tc.tclick{text-decoration:none;color:var(--tx);display:block;border-radius:7px;padding:2px 6px;margin:-2px -6px}
  .timeline .tc.tclick:hover{background:#eef2ff}
  .timeline .tp{font-size:11px;color:var(--mut);font-style:italic}
  .timeline .tdir{margin-right:5px}
</style>
"""

_DETAIL_JS = r"""
const cl = CLUSTER, st = STATS, tl = TIMELINE, proj = PROJECTS, frows = FILA_ROWS, gates = GATES;

/* pt-PT purpose labels (shared idea with the Para-ti lens) + gate labels */
const PURpt = {ESTIMATE_REQUEST_FROM_CLIENT:'pedido orçamento',PO_FROM_CLIENT:'encomenda',
  FOLLOW_UP:'follow-up',OUR_ORDER_TO_SUPPLIER:'encomenda a fornecedor',
  SUPPLIER_REPLY_OR_CONFIRMATION:'resposta fornecedor',OUTBOUND_INVOICE:'fatura nossa',
  INVOICE_OR_ACCOUNTING:'faturação',PUBLICITY:'publicidade',INTERNAL_OPS:'operações internas',OTHER:'outro'};
const purposeLabel = p => PURpt[p] || (p||'').toLowerCase().replace(/_/g,' ');
const GATEpt  = {rever_classificacao:'Rever', propor_projeto:'Propor projeto', confirmar_identidade:'Identidade'};
const GATEcls = {rever_classificacao:'rever', propor_projeto:'projeto', confirmar_identidade:'identidade'};

function relTime(iso){ if(!iso) return '—'; const ms=Date.now()-Date.parse(iso); if(isNaN(ms)) return '—';
  const d=Math.floor(ms/86400000);
  return d<=0?'hoje':d===1?'ontem':d<30?('há '+d+' dias'):d<365?('há '+Math.floor(d/30)+' meses'):('há '+Math.floor(d/365)+' anos'); }
/* deep-links to where the data lives */
const inboxContact = e   => '/inbox#tab=contacts&sel='+encodeURIComponent(e);
const inboxEmail   = mid => '/inbox#tab=emails&sel='+encodeURIComponent(mid);
const filaThread   = root=> '/?thread='+encodeURIComponent(root);

function statCard(v,l,cls){ return '<div class="stat'+(cls?' '+cls:'')+'"><div class="sv">'+esc(v)+'</div><div class="sl">'+esc(l)+'</div></div>'; }
function seclabel(t,hint){ return '<div class="seclabel">'+esc(t)+(hint?' <span class="sh">'+esc(hint)+'</span>':'')+'</div>'; }

function render(){
  const cp = cl.last_counterparty || 'OTHER';

  // ── header: identity + email chips (→ inbox history) + quick actions ──
  const emails = (cl.emails||[]).map(e=>'<a class="emlink" href="'+inboxContact(e)+'" title="histórico de '+esc(e)+' no inbox">'+esc(e)+'</a>')
                  .join('<span class="sep">·</span>');
  const nif = cl.nif ? '<span class="kv">NIF '+esc(cl.nif)+'</span>' : '';
  const acts = '<div class="cactions">'
    + (st.primary_email?'<a class="act-btn" href="'+inboxContact(st.primary_email)+'">Histórico completo →</a>':'')
    + (frows.length?'<a class="act-btn" href="/?counterparty='+encodeURIComponent(cp)+'">Abrir na Fila →</a>':'')
    + (proj.length?'<a class="act-btn" href="/projetos">Ver projetos →</a>':'')
    + '</div>';
  $('#_header').innerHTML =
      '<div class="chead"><span class="cp '+esc(cp)+'">'+esc(cp)+'</span>'
    + '<span class="ctitle">'+esc(cl.display_name||cl.key)+'</span>'+nif+'</div>'
    + '<div class="cemails">'+emails+'</div>'+acts;

  // ── insight strip ──
  $('#_stats').innerHTML =
      statCard(st.messages, 'mensagens')
    + statCard(st.threads, st.threads===1?'conversa':'conversas')
    + statCard(st.inbound, 'recebidas')
    + statCard(st.outbound, 'enviadas')
    + (st.with_attachments?statCard(st.with_attachments, 'com anexo'):'')
    + statCard(relTime(st.last_seen), 'última atividade')
    + (proj.length?statCard(proj.length, proj.length===1?'projeto':'projetos'):'')
    + (st.we_owe>0?statCard(st.we_owe, st.we_owe===1?'devemos resposta':'devemos respostas','risk'):'');

  // ── what we exchanged (purpose breakdown) ──
  const purp = (st.purposes||[]).map(pc=>'<span class="pchip">'+esc(purposeLabel(pc[0]))+' <b>×'+pc[1]+'</b></span>').join('');
  $('#_purposes').innerHTML = purp ? seclabel('O que trocámos') + '<div class="pchips">'+purp+'</div>' : '';

  // ── threads abertas → open in the Fila ──
  $('#_threads').innerHTML = !frows.length ? '' : seclabel('Threads abertas','na Fila — clica para abrir')
    + frows.map(r=>{ const c=r.clock||{};
        return '<a class="lrow" href="'+filaThread(r.thread_root)+'">'
          +'<span class="cp '+esc(r.counterparty||'OTHER')+'">'+esc(r.counterparty||'—')+'</span>'
          +'<div class="lmain"><div class="ltitle">'+esc(r.subject||'(sem assunto)')+'</div>'
          +'<div class="lsub">'+esc(r.contact||'')+(r.owner?(' · @'+esc(r.owner)):'')+'</div></div>'
          +'<span class="clock '+esc(c.band||'none')+'"><span class="d" aria-hidden="true"></span>'+esc(c.label||'')+'</span></a>';
      }).join('');

  // ── pending decisions → Para ti ──
  $('#_gates').innerHTML = !gates.length ? '' : seclabel('Decisões pendentes','no Para ti')
    + gates.map(g=>{ const acc=g.accept||{};
        const href = acc.nav || acc.href || (g.thread_root?filaThread(g.thread_root):'/para-ti');
        return '<a class="lrow" href="'+esc(href)+'">'
          +'<span class="gkind '+(GATEcls[g.kind]||'rever')+'">'+esc(GATEpt[g.kind]||g.kind)+'</span>'
          +'<div class="lmain"><div class="ltitle">'+esc(g.title||'')+'</div>'
          +'<div class="lsub">'+esc(g.why||'')+'</div></div><span class="go">→</span></a>';
      }).join('');

  // ── projetos → open the workbench ──
  $('#_projects').innerHTML = !proj.length ? '' : seclabel('Projetos','clica para abrir')
    + proj.map(p=>'<a class="lrow" href="/projetos/'+encodeURIComponent(p.project_id)+'">'
        +'<div class="lmain"><div class="ltitle">'+esc(p.title||'(sem título)')+'</div>'
        +'<div class="lsub">'+esc(p.client_name||p.client_email||'')+'</div></div>'
        +'<span class="ststage">'+esc(p.stage||'')+'</span></a>').join('');

  // ── full timeline → each message opens in the inbox report ──
  $('#_timeline').innerHTML = seclabel('Linha do tempo', tl.length+' mensagem'+(tl.length===1?'':'s')+' · clica para ver no inbox')
    + '<ul class="timeline list" style="padding:0 14px">'
    + tl.slice().reverse().map(item=>{          // newest-first
        const d=(item.date||'').slice(0,10);
        const dir=msgDirTag(item.direction||'');
        const att=item.has_attachment?' 📎':'';
        const pur=item.purpose?' <span class="tp">· '+esc(purposeLabel(item.purpose))+'</span>':'';
        return '<li class="titem"><span class="td">'+esc(d)+'</span>'
          +'<a class="tc tclick" href="'+inboxEmail(item.message_id)+'" title="ver no inbox">'
          +'<span class="tdir" style="color:'+dir.c+'">'+esc(dir.t)+'</span>'
          +esc(item.subject||'(sem assunto)')+pur+att+'</a></li>';
      }).join('')+'</ul>';
}

function onKey(e){}
function paletteItems(q){
  const items=[
    {kind:'ação',label:'← Contrapartes',run:()=>{location.href='/contrapartes';}},
    {kind:'ação',label:'Histórico completo (inbox)',run:()=>{location.href=inboxContact(st.primary_email||(cl.emails||[''])[0]);}},
    {kind:'ação',label:'Fila',run:()=>{location.href='/';}},
    {kind:'ação',label:'Projetos',run:()=>{location.href='/projetos';}},
    {kind:'ação',label:S.actSync,run:syncNow},
  ];
  (cl.emails||[]).forEach(e=>items.push({kind:'email',label:e,sub:'histórico no inbox',run:()=>{location.href=inboxContact(e);}}));
  proj.forEach(p=>items.push({kind:'projeto',label:p.title||p.project_id,sub:p.stage,run:()=>{location.href='/projetos/'+encodeURIComponent(p.project_id);}}));
  return (q=q.toLowerCase().trim())?items.filter(it=>(it.label+' '+(it.sub||'')+' '+it.kind).toLowerCase().includes(q)):items;
}
"""


def build_detail_html(cluster: dict[str, Any], timeline: list[dict[str, Any]],
                      projects: list[dict[str, Any]], fila_rows: list[dict[str, Any]],
                      *, stats: dict[str, Any] | None = None,
                      gates: list[dict[str, Any]] | None = None,
                      nav_counts: dict[str, int] | None = None) -> str:
    return cockpit_ui.page(
        cluster.get("display_name") or cluster.get("key", "Contraparte"),
        "contrapartes", _DETAIL_BODY,
        embeds={"cluster": cluster, "timeline": timeline,
                "projects": projects, "fila_rows": fila_rows,
                "stats": stats or {}, "gates": gates or []},
        lens_js=_DETAIL_JS,
        nav_counts=nav_counts,
    )
