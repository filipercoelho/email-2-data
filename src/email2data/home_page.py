"""Início — the landing page at ``/`` (ADR-044).

The cockpit opens on a three-pane Mesa: a 172px vistas rail, a 56-row queue, and a dossier already
mounted on whichever conversation happened to sort first. That is the right screen to *work* in and
the wrong screen to *arrive* at — the first thing it asks of you is to read it, and the decision it
exists to serve («what do I do first?») is buried under every filter that might refine that decision
later.

Início answers that one question and nothing else. It carries **four numbers per card and no rows**:
each counterparty front (Clientes · Fornecedores · Leads) as a big button showing its own demand,
plus Para ti. The Fila keeps everything it had, one click away at ``/fila``.

Two rules this page must not break:

* **Every number is scoped and says so** (ADR-034). A card counts only its own counterparty; the
  headline is explicitly «Hoje». A count that can be misread as a global total is the bug this
  design already fixed once in the Fila.
* **Calm at zero.** Colour appears only when something demands you. A morning with nothing owed
  should render as a quiet page, not a green one — the page is a signal, and a signal that is always
  on carries no information.

All presentation; every datum comes from ``cockpit.home_summary``.
"""

from __future__ import annotations

from typing import Any

from . import cockpit_ui

_BODY_HTML = """
<div class="home">
  <div class="hhero">
    <div class="hh-scope">Hoje</div>
    <h1 id="_hline" class="hh-line"></h1>
    <div id="_hsub" class="hh-sub"></div>
  </div>
  <div id="_hcards" class="hcards"></div>
  <div id="_hmore" class="hmore"></div>
  <div class="hint"><b>1–4</b> abrir · <b>S</b> sincronizar · <b>⌘K</b> comandos · <b>?</b> ajuda</div>
</div>
"""

_EXTRA_CSS = """
  /* ── Início (ADR-044) ──────────────────────────────────────────────────
     Deliberately narrow (860px) against the Fila's 1720px Mesa: the whole point is that arriving
     costs one glance, and a full-width landing would just be a sparse cockpit. */
  .home{max-width:860px;margin:0 auto;padding:38px 22px 40px}
  .hhero{margin:0 0 26px}
  .hh-scope{font-size:9.5px;font-weight:800;letter-spacing:.11em;text-transform:uppercase;
    color:var(--mut2);margin:0 0 9px}
  .hh-line{font-size:27px;line-height:1.25;font-weight:760;letter-spacing:-.02em;color:var(--tx);margin:0}
  .hh-line .n{font-variant-numeric:tabular-nums}
  /* The headline number carries the band colour ONLY when something is owed (calm at zero). */
  .hh-line .n.dem{color:var(--red)}
  .hh-sub{margin:9px 0 0;font-size:13.5px;font-weight:600;color:var(--mut);line-height:1.6}
  .hh-sub .warn{color:var(--red)}
  .hh-sub .sep{color:var(--bd);margin:0 7px}

  .hcards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
  @media (max-width:680px){ .hcards{grid-template-columns:minmax(0,1fr)} }
  .hc{display:flex;flex-direction:column;gap:0;text-align:left;font-family:inherit;cursor:pointer;
    background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:17px 19px 16px;
    box-shadow:var(--shadow);text-decoration:none;transition:border-color .12s,transform .12s}
  .hc:hover{border-color:var(--ac-line);transform:translateY(-1px)}
  .hc:focus-visible{outline:2px solid var(--ac);outline-offset:2px}
  .hc .hc-top{display:flex;align-items:center;gap:8px;margin:0 0 11px}
  .hc .hc-name{font-size:15.5px;font-weight:750;color:var(--tx);letter-spacing:-.01em}
  .hc .hc-tot{margin-left:auto;font-size:11px;font-weight:700;font-variant-numeric:tabular-nums;
    color:var(--mut2)}
  /* The demand line is the reason the card exists — it gets the size, not the title. */
  .hc .hc-dem{display:flex;align-items:baseline;gap:7px;flex-wrap:wrap;font-size:13px;
    font-weight:600;color:var(--mut);min-height:30px}
  .hc .hc-dem b{font-size:23px;font-weight:800;font-variant-numeric:tabular-nums;letter-spacing:-.02em;
    line-height:1.1}
  .hc .hc-dem b.r{color:var(--red)} .hc .hc-dem b.c{color:var(--amber)}
  .hc .hc-dem .ok{font-size:13.5px;font-weight:650;color:var(--green)}
  .hc .hc-dem .sep{color:var(--bd)}
  /* «a mais antiga» — the second highlight. Reserved height so cards keep a common baseline whether
     or not this row has anything to say (a grid that reflows per card reads as broken). */
  .hc .hc-old{margin-top:10px;font-size:11.5px;font-weight:650;color:var(--mut2);min-height:16px}
  .hc .hc-old.warn{color:var(--red)}
  .hc .hc-sub{margin-top:3px;font-size:11.5px;font-weight:600;color:var(--mut2)}
  /* The dot is the CVD-validated COUNTERPARTY code (cliente teal · fornecedor blue · lead amber).
     Para ti is not a counterparty, so it gets the nav's bell glyph instead — a fourth dot would
     quietly claim membership of a colour scheme whose whole job is to mean one thing. */
  .hc .mdot{width:9px;height:9px;border-radius:50%;display:inline-block;flex:0 0 auto}
  .hc .mdot.CLIENT{background:var(--cli)} .hc .mdot.SUPPLIER{background:var(--forn)}
  .hc .mdot.LEAD{background:var(--lead)}
  .hc .hc-glyph{width:15px;height:15px;flex:0 0 auto;stroke:var(--ac);fill:none;stroke-width:1.7;
    stroke-linecap:round;stroke-linejoin:round}
  .hc .hc-key{margin-left:6px;font-size:9.5px;font-weight:800;color:var(--mut2);border:1px solid var(--bd);
    border-radius:4px;padding:1px 4px;line-height:1.5;opacity:0;transition:opacity .12s}
  .hc:hover .hc-key{opacity:1}

  /* Secondary destinations — quiet on purpose: reachable, never competing with the four cards. */
  .hmore{display:flex;flex-wrap:wrap;gap:7px;margin:22px 0 0;align-items:center}
  .hmore a{display:inline-flex;align-items:center;gap:7px;font-size:12.5px;font-weight:650;
    color:var(--mut);text-decoration:none;border:1px solid var(--bd);background:var(--card);
    border-radius:9px;padding:6px 12px}
  .hmore a:hover{border-color:var(--ac-line);color:var(--tx)}
  .hmore a .hb{font-size:10.5px;font-weight:800;font-variant-numeric:tabular-nums;color:#fff;
    background:var(--red);border-radius:999px;padding:1px 6px}
  .home .hint{margin-top:26px}
"""

_LENS_JS = r"""
/* Início (ADR-044) — pure presentation over SUMMARY, computed in cockpit.home_summary(). There is no
   fetch here and no rows: the page must render its answer from the first byte of HTML, because a
   landing page that shows a spinner has failed at the one job it has. */

const FRONTS = [
  {k:'CLIENT',  lab:'Clientes',     href:'/fila?tab=CLIENT',  sub:'Trabalho de clientes — pedidos, orçamentos, adjudicações.'},
  {k:'SUPPLIER',lab:'Fornecedores', href:'/fila?tab=SUPPLIER',sub:'Compras e respostas de fornecedores.'},
  {k:'LEAD',    lab:'Leads',        href:'/fila?tab=LEAD',    sub:'Contactos novos, ainda sem relação.'},
];

/* Para ti's mark — the same bell the nav uses, so the card and its nav entry are visibly one thing.
   Deliberately NOT a counterparty dot (see the .mdot comment in the CSS). */
const PARATI_GLYPH = '<svg class="hc-glyph" viewBox="0 0 24 24" aria-hidden="true">'
  + '<path d="M12 4a6 6 0 0 1 6 6v3l2 3H4l2-3v-3a6 6 0 0 1 6-6z"/>'
  + '<path d="M10 19a2 2 0 0 0 4 0"/></svg>';

/* The embeds arrive as `const` (cockpit_ui.page), so the live state that a sync repaints lives in
   these mutable copies — assigning to SUMMARY itself would throw in strict-mode-adjacent engines and
   silently break the refresh everywhere else. */
let summary = SUMMARY, paraTi = PARA_TI, capturas = CAPTURAS, syncedAt = SYNCED_AT;

/* The keyboard order IS the visual order, so «3» is whatever is third on screen. */
function cards(){
  const out = FRONTS.map(f => Object.assign({}, f, {sum: summary[f.k] || {}}));
  out.push({k:'PARATI', lab:'Para ti', href:'/para-ti', parati:true,
            sub:'Sugestões da IA à espera de uma decisão tua.'});
  return out;
}

function demandHTML(s, isLead){
  const resp = s.respond|0, chase = s.chase|0;
  if(resp>0 || chase>0){
    const bits=[];
    if(resp>0) bits.push('<b class="r">'+resp+'</b> a responder');
    if(chase>0) bits.push('<b class="c">'+chase+'</b> a aguardar');
    return bits.join('<span class="sep">·</span>');
  }
  /* Calm at zero — the guardrail. Leads say something different because "em dia" is a claim about
     work done, and there is no work to do on a front nobody has contacted. */
  return '<span class="ok">'+(isLead ? 'sem leads novos' : 'em dia')+'</span>';
}

function oldestHTML(s){
  if(!s.oldest_label) return '';
  const bad = (s.oldest_h||0) >= 72;      /* 3 days unanswered — the number worth a colour */
  return '<div class="hc-old'+(bad?' warn':'')+'">a mais antiga está parada há '+esc(s.oldest_label)+'</div>';
}

function render(){
  const all = summary.all || {};
  const resp = all.respond|0, chase = all.chase|0;

  const line = $('#_hline');
  if(resp>0){
    line.innerHTML = '<span class="n dem">'+resp+'</span> '
      + (resp===1 ? 'conversa espera resposta' : 'conversas esperam resposta');
  } else if(chase>0){
    /* Nothing owed by us, but something is overdue on their side — a nudge, not a fire. */
    line.innerHTML = 'Nada por responder — <span class="n">'+chase+'</span> '
      + (chase===1 ? 'conversa à espera deles' : 'conversas à espera deles');
  } else {
    line.textContent = 'Está tudo tratado.';
  }

  const sub = $('#_hsub'), bits = [];
  if(chase>0 && resp>0) bits.push('<b>'+chase+'</b> a aguardar deles');
  if(all.oldest_label) bits.push('<span class="warn">a mais antiga está parada há '+esc(all.oldest_label)+'</span>');
  if(!bits.length) bits.push((all.total|0) + ' conversas activas');
  sub.innerHTML = bits.join('<span class="sep">·</span>');

  $('#_hcards').innerHTML = cards().map((c,i)=>{
    const s = c.parati ? {} : (c.sum||{});
    const dem = c.parati
      ? (paraTi>0
          ? '<b class="c">'+paraTi+'</b> ' + (paraTi===1?'sugestão':'sugestões')
          : '<span class="ok">nada novo</span>')
      : demandHTML(s, c.k==='LEAD');
    const tot = c.parati ? '' : '<span class="hc-tot">'+(s.total|0)+'</span>';
    const mark = c.parati ? PARATI_GLYPH : '<span class="mdot '+c.k+'" aria-hidden="true"></span>';
    return '<a class="hc" href="'+c.href+'" data-i="'+i+'">'
      + '<span class="hc-top">'+mark
      + '<span class="hc-name">'+esc(c.lab)+'</span>'+tot
      + '<span class="hc-key">'+(i+1)+'</span></span>'
      + '<span class="hc-dem">'+dem+'</span>'
      + (c.parati ? '' : oldestHTML(s))
      + '<span class="hc-sub">'+esc(c.sub)+'</span>'
      + '</a>';
  }).join('');

  const more = [
    {href:'/capturas', lab:'Capturas', badge:capturas},
    {href:'/projetos', lab:'Projetos'},
    {href:'/contrapartes', lab:'Contrapartes'},
    {href:'/fila', lab:'Fila completa'},
  ];
  $('#_hmore').innerHTML = more.map(m =>
    '<a href="'+m.href+'">'+esc(m.lab)
    + (m.badge ? '<span class="hb">'+m.badge+'</span>' : '')
    + '</a>').join('');

  setSynced(syncedAt || null, false);
}

/* A sync must not throw the page away: re-fetch the numbers and repaint in place (ADR-023 §7). */
async function onSynced(){
  try{
    const d = await getJSON('/api/inicio');
    if(d && d.summary){
      const nc = d.nav_counts || {};
      summary = d.summary; paraTi = nc['para-ti']|0; capturas = nc['capturas']|0;
      syncedAt = d.synced_at || syncedAt;
      render(); setNavCounts(nc);
    }
    toast(S.sincronizado);
  }catch(_e){ location.reload(); }
}

function onKey(e){
  const cs = cards();
  if(e.key>='1' && e.key<=String(cs.length)){ location.href = cs[(+e.key)-1].href; e.preventDefault(); }
  else if(e.key==='s'||e.key==='S'){ syncNow(); e.preventDefault(); }
}

function paletteItems(q){
  q=(q||'').toLowerCase().trim();
  const items=[
    {kind:'ação',label:'Fila',run:()=>{location.href='/fila';}},
    {kind:'ação',label:'Clientes',run:()=>{location.href='/fila?tab=CLIENT';}},
    {kind:'ação',label:'Fornecedores',run:()=>{location.href='/fila?tab=SUPPLIER';}},
    {kind:'ação',label:'Leads',run:()=>{location.href='/fila?tab=LEAD';}},
    {kind:'ação',label:'Para ti',run:()=>{location.href='/para-ti';}},
    {kind:'ação',label:'Capturas',run:()=>{location.href='/capturas';}},
    {kind:'ação',label:'Projetos',run:()=>{location.href='/projetos';}},
    {kind:'ação',label:'Contrapartes',run:()=>{location.href='/contrapartes';}},
    {kind:'ação',label:S.actSync,run:syncNow},
  ];
  return q?items.filter(it=>(it.label+' '+it.kind).toLowerCase().includes(q)):items;
}
"""


def build_home_html(summary: dict[str, Any],
                    *,
                    synced_at: str = "",
                    nav_counts: dict[str, int] | None = None,
                    person: dict[str, Any] | None = None) -> str:
    """Render Início.

    ``summary`` is ``cockpit.home_summary(rows)``; ``nav_counts`` feeds both the nav badges and the
    Para ti / Capturas figures on the page, so the badge and the card can never disagree."""
    counts = nav_counts or {}
    return cockpit_ui.page(
        "Início",
        # No lens is active on Início — it is not one of the queues. The logo carries the active
        # state instead (see cockpit_ui._nav_html), so the header still says where you are.
        "inicio",
        _BODY_HTML,
        embeds={"summary": summary,
                "para_ti": int(counts.get("para-ti", 0)),
                "capturas": int(counts.get("capturas", 0)),
                "synced_at": synced_at},
        lens_js=_LENS_JS,
        nav_counts=counts,
        extra_css=_EXTRA_CSS,
        person=person,
    )
