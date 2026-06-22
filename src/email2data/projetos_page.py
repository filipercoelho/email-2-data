"""C4 — Projetos lens page (/projetos). Lead→estimable guided funnel.

Thin wrapper over cockpit_ui.page(). Uses the existing /api/projects* endpoints.
The detail view is a **job-spec workbench**: source emails (Origem) for context,
every must/should variable as an editable+confirmable field (Especificação), and a
client-email **composer** (Email para o cliente) — pick which gaps to ask about, review
the auto-assembled draft, edit it, then copy or open in the mail client. The field
registry is embedded straight from ``jobspec.FIELDS`` so the UI never drifts; the draft
itself is assembled server-side (``/api/projects/{id}/draft`` → ``clientdraft``) so the
pt-PT skeleton lives in editable config, not hard-coded JS.
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
  /* Tier-aware row state — red is RESERVED for a must-tier gap that blocks estimability (the page's
     one scarce alarm signal; readiness.missing only ever holds must-gaps). Optional gaps recede
     (calm dashed), filled values read quiet + committed. */
  .frow.miss-must label{color:var(--red);font-weight:600}
  .frow.miss-opt label{color:var(--mut2)}
  .frow.filled label{color:var(--mut)}
  .fctl{flex:1;display:flex;align-items:center;gap:8px;min-width:0}
  .finput{flex:1;min-width:0;border:1px solid var(--bd);border-radius:8px;padding:6px 10px;font-size:13px;font-family:inherit;background:#fff;color:var(--tx)}
  .finput:focus{outline:none;border-color:var(--ac);box-shadow:0 0 0 3px #eef2ff}
  .frow.miss-must .finput{border-color:#f3c9c9;background:#fffafa}
  .frow.miss-opt .finput{border-color:var(--bd);background:#fbfbfc;border-style:dashed}
  .finput::placeholder{color:var(--mut2);font-style:italic}
  /* brief confirmation that an inline edit committed (data-entry feedback) */
  .frow.saved .finput{animation:savedflash .9s ease}
  @keyframes savedflash{0%{box-shadow:0 0 0 3px #d1fae5}100%{box-shadow:none}}
  /* required-vs-optional divider inside a field group */
  .fopt-sep{font-size:10px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut2);font-weight:700;
    margin:9px 0 3px;padding-top:7px;border-top:1px dashed var(--bd2)}
  /* clickable gap-count in the section header → jumps to the first missing must field */
  .gapjump{border:none;background:none;cursor:pointer;color:var(--red);font-weight:700;font-size:11px;
    text-transform:none;letter-spacing:0;padding:0;text-decoration:underline dotted}
  .gapjump.done{color:var(--green);cursor:default;text-decoration:none}
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
  .qmail{display:flex;gap:8px;margin-top:14px;flex-wrap:wrap}
  .ready{color:var(--green);font-size:12.5px;font-weight:600}
  /* ── client-email composer ──────────────────────────────────────────── */
  .cmp .hdr{display:flex;flex-direction:column;gap:8px;margin-bottom:10px}
  .cmp .to{font-size:12.5px;color:var(--mut)}
  .cmp .to b{color:var(--tx);font-weight:600}
  .cmp .subj{display:flex;align-items:center;gap:8px}
  .cmp .subj label{flex:0 0 auto;font-size:12px;color:var(--mut)}
  .cmp .subj input{flex:1;min-width:0;border:1px solid var(--bd);border-radius:8px;padding:6px 10px;font-size:13px;font-family:inherit;background:#fff;color:var(--tx)}
  .cmp .subj input:focus{outline:none;border-color:var(--ac);box-shadow:0 0 0 3px #eef2ff}
  .askgrp{margin:10px 0}
  .askgrp .gl{font-size:10.5px;text-transform:uppercase;letter-spacing:.04em;font-weight:700;color:var(--mut2);margin-bottom:5px}
  .askgrp.must .gl{color:var(--red)}
  .ask-opt{display:flex;align-items:flex-start;gap:8px;padding:3px 0;font-size:13px;color:#3a4150;cursor:pointer}
  .ask-opt input{margin-top:2px;accent-color:var(--ac);cursor:pointer}
  .ask-opt.intern{color:var(--mut2);cursor:default}
  .custq{display:flex;align-items:center;gap:8px;padding:3px 0;font-size:13px;color:#3a4150}
  .custq .rm{cursor:pointer;color:var(--mut2);border:none;background:none;font-size:15px;line-height:1;padding:0}
  .custq .rm:hover{color:var(--red)}
  .addq{border:1px dashed var(--bd);background:#fff;border-radius:8px;padding:5px 11px;cursor:pointer;font-size:12px;color:var(--mut);font-weight:600;margin-top:4px}
  .addq:hover{border-color:var(--ac);color:var(--ac);background:#f6f8ff}
  .draftbox{margin-top:14px}
  .draftbox .dl{display:flex;align-items:center;justify-content:space-between;margin-bottom:5px;min-height:22px}
  .draftbox .dl h4{margin:0;font-size:10.5px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut2);font-weight:700}
  .draftbox .dirty{font-size:11px;color:#b45309;display:flex;align-items:center;gap:8px}
  .draftbox .dirty .regen{border:1px solid #fed7aa;background:#fff7ed;color:#b45309;border-radius:7px;padding:2px 9px;cursor:pointer;font-size:11px;font-weight:600}
  .draftbox .dirty .regen:hover{background:#ffedd5}
  .draftbox textarea{width:100%;box-sizing:border-box;min-height:200px;border:1px solid var(--bd);border-radius:10px;padding:11px 13px;font-size:13px;line-height:1.5;font-family:inherit;background:#fff;color:var(--tx);resize:vertical}
  .draftbox textarea:focus{outline:none;border-color:var(--ac);box-shadow:0 0 0 3px #eef2ff}
  /* ── tab strip (ADR-015 — only the active panel shows; keeps the page from being one long wall) ── */
  .ptabs{display:flex;gap:4px;flex-wrap:wrap;border-bottom:1px solid var(--bd);margin:14px 0 0}
  .ptab-btn{border:none;background:none;padding:8px 12px;font-size:12.5px;font-weight:600;color:var(--mut);
    cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px}
  .ptab-btn:hover{color:var(--tx)}
  .ptab-btn.on{color:var(--ac);border-bottom-color:var(--ac)}
  .ptab-btn .bdg{display:inline-block;min-width:16px;padding:0 5px;margin-left:5px;border-radius:9px;
    background:var(--bd);color:var(--mut);font-size:10px;font-weight:700;text-align:center}
  .ptab-btn .bdg.warn{background:#fff7ed;color:#b45309}
  .ppanel{padding-top:6px}
  .ppanel.hidden{display:none}
  /* provenance + conflict chips on a field row */
  .pchan{font-size:9.5px;font-weight:700;padding:2px 6px;border-radius:6px;background:#eef4ff;color:var(--ac);
    flex:0 0 auto;cursor:default}
  .frow.conflict .finput{border-color:#fed7aa;background:#fff7ed}
  .cwarn{font-size:11px;flex:0 0 auto;cursor:help}
  /* contested-on-top banner */
  .contested{background:#fff7ed;border:1px solid #fed7aa;border-radius:10px;padding:9px 12px;margin:10px 0;font-size:12.5px;color:#92400e}
  .contested b{color:#7c2d12}
  .contested .cv{display:inline-block;margin:2px 6px 0 0;padding:1px 7px;border-radius:6px;background:#fff;border:1px solid #fed7aa;font-size:11px}
  /* custom fields */
  .custf{display:flex;align-items:center;gap:8px;padding:4px 0}
  .custf label{flex:0 0 158px;font-size:12.5px;color:var(--mut);text-align:right;font-style:italic}
  .addcust{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
  .addcust input{border:1px solid var(--bd);border-radius:8px;padding:6px 10px;font-size:13px;font-family:inherit}
  .addcust .cn{flex:0 0 158px} .addcust .cv2{flex:1;min-width:0}
  /* ── Registar (capture) surface ─────────────────────────────────────── */
  .cap{max-width:620px}
  .cap .chips{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px}
  .chip{border:1px solid var(--bd);background:#fff;border-radius:20px;padding:4px 12px;font-size:12px;
    cursor:pointer;color:var(--mut);font-weight:600}
  .chip.on{background:var(--ac);border-color:var(--ac);color:#fff}
  .cap .meta{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}
  .cap .meta input{border:1px solid var(--bd);border-radius:8px;padding:6px 10px;font-size:13px;font-family:inherit}
  .cap textarea{width:100%;box-sizing:border-box;min-height:90px;border:1px solid var(--bd);border-radius:10px;
    padding:10px 12px;font-size:13px;line-height:1.5;font-family:inherit;resize:vertical}
  .cap textarea:focus{outline:none;border-color:var(--ac);box-shadow:0 0 0 3px #eef2ff}
  .cap .lbl{font-size:10.5px;text-transform:uppercase;letter-spacing:.04em;font-weight:700;color:var(--mut2);margin:8px 0 4px}
  /* ── timeline (Linha do tempo) ──────────────────────────────────────── */
  .tl{border-left:2px solid var(--bd);margin-left:6px;padding-left:14px}
  .tl-row{position:relative;padding:8px 0;border-bottom:1px solid var(--bd2)}
  .tl-row:last-child{border-bottom:none}
  .tl-row::before{content:'';position:absolute;left:-21px;top:13px;width:9px;height:9px;border-radius:50%;background:var(--ac)}
  .tl-row.removed::before{background:var(--red)}
  .tl-row.event::before{background:var(--green)}
  .tl-h{font-size:13px;color:var(--tx)}
  .tl-h b{font-weight:700}
  .tl-m{font-size:11px;color:var(--mut2);margin-top:2px}
  .tl-old{color:var(--mut2);text-decoration:line-through;margin-right:6px}
  /* the photo in the project timeline — a capture event's sole-copy media (ADR-020) */
  .tl-thumb{margin-top:6px;width:84px;height:84px;object-fit:cover;border-radius:9px;border:1px solid var(--bd);cursor:zoom-in;display:block}
  /* ── owners (multi) ─────────────────────────────────────────────────── */
  .owners{display:flex;align-items:center;flex-wrap:wrap;gap:6px;margin:2px 0 4px}
  .owners .olbl{font-size:11.5px;color:var(--mut);font-weight:600}
  .ochip{display:inline-flex;align-items:center;gap:4px;font-size:11.5px;font-weight:600;color:var(--int);
    background:#f0fdfa;border:1px solid #bfe6e0;border-radius:20px;padding:2px 4px 2px 9px}
  .ochip .ox{border:none;background:none;color:var(--mut2);cursor:pointer;font-size:11px;padding:0 2px;line-height:1}
  .ochip .ox:hover{color:var(--red)}
  .oadd{font-size:11.5px;font-weight:600;color:var(--mut);background:#fff;border:1px solid var(--bd);
    border-radius:20px;padding:2px 10px;cursor:pointer}
  .oadd:hover{border-color:var(--ac);color:var(--ac)}
  .menu .mhdr{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:var(--mut2);padding:5px 11px 3px}
  .menu .mi.reset{color:var(--int);border-top:1px solid var(--bd2);margin-top:3px}
  /* ── close-out (cancel / lost) ──────────────────────────────────────── */
  .closed{background:#fbeaea;border:1px solid #f3c9c9;color:var(--red);border-radius:9px;
    padding:7px 12px;font-size:12.5px;font-weight:600;margin:4px 0 8px}
  .cof{background:var(--card);border:1px solid #f3c9c9;border-radius:11px;padding:13px 15px;margin:6px 0 10px}
  .cof .lbl{font-size:12.5px;font-weight:650;color:var(--tx);margin-bottom:8px}
  .cof textarea{width:100%;min-height:54px;border:1px solid var(--bd);border-radius:8px;padding:8px 10px;
    font:13px/1.5 inherit;color:var(--tx);resize:vertical;outline:none;margin-top:8px}
  .cof textarea:focus{border-color:var(--ac)}
  .cofacts{display:flex;justify-content:flex-end;gap:8px;margin-top:9px}
  .act-btn.danger{border-color:var(--red);color:var(--red)} .act-btn.danger:hover{background:#fbeaea}
  /* ── participants (who contributed) ─────────────────────────────────── */
  .parts{display:flex;align-items:center;flex-wrap:wrap;gap:7px;margin:2px 0 8px;font-size:11.5px}
  .parts .plbl{color:var(--mut);font-weight:600}
  .pcontrib{color:var(--purple);background:#efeafb;border:1px solid #ddd2f5;border-radius:20px;padding:1px 9px;cursor:default}
  .pcontrib b{font-variant-numeric:tabular-nums}
</style>
"""

_STAGES = ["LEAD", "GATHERING", "ESTIMABLE", "QUOTED", "WON", "LOST", "CANCELLED", "ARCHIVED"]
_TERMINAL = {"QUOTED", "WON", "LOST", "CANCELLED", "ARCHIVED"}

_LENS_JS = r"""
let projects = PROJECTS.slice(), focus = 0, selected = null;
const STAGES = ['LEAD','GATHERING','ESTIMABLE','QUOTED','WON','LOST','CANCELLED','ARCHIVED'];
const TERMINAL = new Set(['QUOTED','WON','LOST','CANCELLED','ARCHIVED']);
const CLOSED_STAGES = new Set(['CANCELLED','LOST']);     // carry a close-out (party + reason)
const STAGEpt = {LEAD:'Lead',GATHERING:'A reunir',ESTIMABLE:'Orçamentável',QUOTED:'Orçamentado',WON:'Ganho',LOST:'Perdido',CANCELLED:'Cancelado',ARCHIVED:'Arquivado'};
const PARTYpt = {client:'Cliente',supplier:'Fornecedor',our:'Nós'};

/* ── field registry (from jobspec.FIELDS — single source of truth) ────── */
const byKey = {}; FIELDS.forEach(f=>byKey[f.key]=f);
const JOB_F  = FIELDS.filter(f=>f.scope==='job'  && f.tier!=='context');
const ITEM_F = FIELDS.filter(f=>f.scope==='item' && f.tier!=='context');
function srcLabel(s){return s==='user'?'tu':s==='llm'?'IA':s==='offline'?'auto':'';}

/* ── ADR-015: provenance/conflict chips + Registar (capture) state ─────── */
const CHAN_ICON={call:'📞',meeting:'🤝',whatsapp:'💬',sms:'✉',email:'',manual:''};
let capChan='call', capKind='note';
function chanChip(addr){
  const p=(selected&&selected.field_provenance&&selected.field_provenance[addr])||null;
  if(!p||!p.channel||!CHAN_ICON[p.channel]) return '';
  const who=p.asserted_by?(' · '+p.asserted_by):'', when=p.acquired_at?(' · '+p.acquired_at):'';
  return '<span class="pchan" title="'+esc(p.channel+who+when)+'">'+CHAN_ICON[p.channel]+'</span>';
}
function _registarFromURL(){return new URLSearchParams(location.search).get('registar')==='nota';}

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
  // restore list visibility — the back button / Escape route here via render() and the detail
  // panel is left showing otherwise (the list owns this toggle; renderDetail owns the inverse).
  $('#_detail').classList.add('hidden');
  $('#_list').classList.remove('hidden');
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

/* ── REST deep-linking ────────────────────────────────────────────────────
   /projetos/<pid> is the detail *resource* URL (mirrors /contrapartes/<key>);
   /projetos is the list. Opening a project pushes its id onto history, so the
   address bar always names what's on screen and the browser back/forward buttons
   move between list and detail (popstate). `push=false` reflects an existing URL
   (initial load / popstate) without stacking a duplicate history entry. */
function _pidFromURL(){
  const m=location.pathname.match(/^\/projetos\/(.+)$/);
  if(m) return decodeURIComponent(m[1]);
  return new URLSearchParams(location.search).get('p')||'';   // legacy ?p=<pid>
}
async function loadDetail(pid, push){
  if(push===undefined) push=true;
  try{
    const d=await (await fetch('/api/projects/'+encodeURIComponent(pid))).json();
    if(d&&d.error){ toast(S.revertido); return; }   // unknown id (e.g. stale link) → stay on list
    selected=d;
    if(push){ try{history.pushState(null,'','/projetos/'+encodeURIComponent(pid));}catch(_){} }
    renderDetail();
  }catch(e){toast(S.revertido);}
}
/* Return to the list. Pushes /projetos so Back from the list leaves the lens cleanly. */
function closeDetail(push){
  selected=null;
  if(push!==false){ try{history.pushState(null,'','/projetos');}catch(_){} }
  render();
}

/* ── one editable field row ───────────────────────────────────────────── */
function fieldRow(f, addr, fobj){
  const val=(fobj&&fobj.value)||'';
  const src=(fobj&&fobj.source)||'';
  const base=addr.split('#')[0];
  const conflicted=!!(selected&&selected.conflicts&&selected.conflicts[base]);
  // Tier-aware state: red is RESERVED for a must-tier gap (a real blocker); optional gaps recede,
  // filled values read quiet. Keeps the page's single alarm signal honest (ADR-015 UX).
  const stcls = val ? 'filled' : (f.tier==='must' ? 'miss-must' : 'miss-opt');
  const badge=src?'<span class="fsrc s-'+esc(src)+'" title="origem do valor">'+srcLabel(src)+'</span>':'';
  const cw=conflicted?'<span class="cwarn" title="fontes de igual autoridade divergem — ver Linha do tempo">⚠</span>':'';
  return '<div class="frow '+stcls+(conflicted?' conflict':'')+'" data-addr="'+esc(addr)+'">'
    +'<label>'+esc(f.label)+'</label>'
    +'<div class="fctl"><input class="finput" data-addr="'+esc(addr)+'" value="'+esc(val)+'" '
    +'placeholder="'+esc(f.q||'…')+'" autocomplete="off" spellcheck="false"/>'+chanChip(addr)+badge+cw+'</div>'
    +'</div>';
}

/* Render a field group with required rows first, then an "opcionais" divider, then optional rows —
   robust required/optional fencing that doesn't depend on registry order or :first-of-type. */
function fieldGroup(fields, addrFn, valFn){
  const must=fields.filter(f=>f.tier==='must'), should=fields.filter(f=>f.tier!=='must');
  const rows=fs=>fs.map(f=>fieldRow(f,addrFn(f),valFn(f))).join('');
  return rows(must)+(should.length?'<div class="fopt-sep">opcionais</div>'+rows(should):'');
}

/* ── client-email composer ──────────────────────────────────────────────
   State for the open project's draft. The selectable prompts + the assembled
   body both come from the server (/api/projects/{id}/draft) so the pt-PT
   skeleton lives in config, not here. `dirty` = the user hand-edited the
   textarea, so toggling a prompt no longer auto-rewrites it (offer Regenerar). */
let draft = null;

async function loadDraft(){
  const box=$('#_ask'); if(!box||!selected) return;
  try{
    const d=await (await fetch('/api/projects/'+selected.project_id+'/draft')).json();
    const asks=d.askables||[];
    draft={to:d.to||'', subject:d.subject||'', askables:asks,
           selected:new Set(asks.filter(a=>a.default).map(a=>a.key)),
           custom:[], body:d.body||'', dirty:false};
    renderComposer();
  }catch(e){ box.innerHTML='<div class="hint2" style="color:var(--red)">falhou ao preparar o email</div>'; }
}

function composerHTML(){
  const d=draft;
  if(!d.askables.length)
    return '<div class="psec"><span class="ready">✓ Todos os obrigatórios estão preenchidos.</span></div>';
  const must=d.askables.filter(a=>a.tier==='must'&&!a.internal);
  const should=d.askables.filter(a=>a.tier==='should'&&!a.internal);
  const intern=d.askables.filter(a=>a.internal);
  const opt=a=>'<label class="ask-opt'+(a.internal?' intern':'')+'">'
    +'<input type="checkbox" data-key="'+esc(a.key)+'"'+(d.selected.has(a.key)?' checked':'')+(a.internal?' disabled':'')+'/>'
    +'<span>'+esc(a.question)+(a.internal?' · interno, não vai para o cliente':'')+'</span></label>';
  const grp=(cls,title,arr)=>arr.length?'<div class="askgrp '+cls+'"><div class="gl">'+title+'</div>'+arr.map(opt).join('')+'</div>':'';
  const custom=d.custom.length?'<div class="askgrp"><div class="gl">As tuas perguntas</div>'
    +d.custom.map((c,i)=>'<div class="custq"><input type="checkbox" checked disabled/><span>'+esc(c)
      +'</span><button class="rm" data-ci="'+i+'" title="remover">×</button></div>').join('')+'</div>':'';
  const dirty=d.dirty?'<span class="dirty">✎ editado <button class="regen" id="_regenq">Regenerar</button></span>':'';
  return '<div class="psec"><h3>Email para o cliente <span class="c">escolhe o que perguntar, revê e copia</span></h3>'
    +'<div class="cmp">'
    +'<div class="hdr"><div class="to">Para: <b>'+esc(d.to||'sem email')+'</b></div>'
    +'<div class="subj"><label>Assunto</label><input id="_subj" value="'+esc(d.subject)+'" autocomplete="off" spellcheck="false"/></div></div>'
    +grp('must','Em falta',must)
    +grp('should','Opcionais',should)
    +custom
    +grp('intern','Internos',intern)
    +'<button class="addq" id="_addq">+ pergunta personalizada</button>'
    +'<div class="draftbox"><div class="dl"><h4>Rascunho</h4>'+dirty+'</div>'
    +'<textarea id="_draftbody" spellcheck="false">'+esc(d.body)+'</textarea></div>'
    +'<div class="qmail"><button class="act-btn" id="_copyq">Copiar email</button>'
    +'<button class="act-btn" id="_openq">Abrir no email</button></div>'
    +'</div></div>';
}

function renderComposer(){ const box=$('#_ask'); if(box) box.innerHTML=composerHTML(); }

/* Rebuild the generated body from the current selection. While dirty we keep the
   user's manual edits and just re-render (the Regenerar button is their way back). */
async function resyncDraft(){
  if(!draft||!selected) return;
  if(draft.dirty){ renderComposer(); return; }
  try{
    const r=await post('/api/projects/'+selected.project_id+'/draft',
      {selected:[...draft.selected], custom:draft.custom});
    draft.body=r.body; renderComposer();
  }catch(e){ toast(S.revertido); }
}

/* contested-on-top: genuine contradictions (equal-authority sources disagree) sit ABOVE the tabs
   so they're never buried (ADR-015). merge_job_fields only flags real ties now, so this is signal. */
function contestedBanner(){
  const cf=selected.conflicts||{}, keys=Object.keys(cf);
  if(!keys.length) return '';
  const rows=keys.map(k=>{
    const lbl=(byKey[k]&&byKey[k].label)||k;
    const vals=cf[k].map(c=>'<span class="cv">'+esc(c.value)+' · '+esc(srcLabel(c.source)||c.source)+'</span>').join('');
    return '<div><b>'+esc(lbl)+'</b>: '+vals+'</div>';
  }).join('');
  return '<div class="contested">⚠ Valores em conflito (fontes de igual autoridade divergem) — confirma o correto na Especificação:'+rows+'</div>';
}

/* Registar — deterministic capture of off-email knowledge (no LLM, stored verbatim) */
function captureHTML(){
  const chans=[['call','📞 Chamada'],['meeting','🤝 Reunião'],['whatsapp','💬 WhatsApp'],['sms','✉ SMS'],['email','✉ Email'],['manual','✎ Outro']];
  const kinds=[['note','Nota'],['decision','Decisão'],['opinion','Opinião'],['todo','To-do']];
  return '<div class="cap">'
    +'<div class="lbl">Canal</div><div class="chips" id="_capchans">'
    +chans.map(c=>'<span class="chip'+(c[0]===capChan?' on':'')+'" data-chan="'+c[0]+'">'+c[1]+'</span>').join('')+'</div>'
    +'<div class="meta"><input id="_capwho" placeholder="quem disse (opcional)" autocomplete="off"/>'
    +'<input id="_capwhen" type="date" title="quando foi adquirido"/></div>'
    +'<div class="lbl">Tipo</div><div class="chips" id="_capkinds">'
    +kinds.map(k=>'<span class="chip'+(k[0]===capKind?' on':'')+'" data-kind="'+k[0]+'">'+k[1]+'</span>').join('')+'</div>'
    +'<textarea id="_captext" placeholder="O que aconteceu? Conclusão da chamada, decisão, opinião… (guardado tal e qual, sem IA)" spellcheck="false"></textarea>'
    +'<div style="margin-top:8px"><button class="act-btn accept" id="_capsave">Registar</button></div>'
    +'</div>';
}

/* ── owners (multi) · close-out (cancel/lost) · participants — ADR-017/-018, ADR-015 surfacing ── */
let roster = (typeof ROSTER!=='undefined'?ROSTER:[]).slice();
let _pendingStage=null, _coParty='client';

function ownersBarHTML(){
  const ow=selected.owners||[];
  const chips=ow.length
    ? ow.map(o=>'<span class="ochip">@'+esc(o)+'<button class="ox" data-own-rm="'+esc(o)+'" aria-label="remover dono">✕</button></span>').join('')
    : '<span class="hint2">sem donos</span>';
  return '<div class="owners" id="_ownersbar"><span class="olbl">Donos:</span>'+chips
    +'<button class="oadd" id="_ownadd">+ atribuir</button></div>';
}
function ownerPicker(){
  const ow=new Set(selected.owners||[]);
  const items=roster.map(nm=>'<div class="mi'+(ow.has(nm)?' on':'')+'" data-own-tg="'+esc(nm)+'">'+(ow.has(nm)?'✓ ':'')+'@'+esc(nm)+'</div>').join('')
    ||'<div class="mi" style="color:var(--mut2)">sem equipa — adiciona um</div>';
  const m=$('#_menu');
  m.innerHTML='<div class="mhdr">Donos do projeto</div>'+items+'<div class="mi reset" data-own-new="1">+ novo dono…</div>';
  m.dataset.kind='projowner'; m.classList.remove('hidden');
  const b=$('#_ownadd').getBoundingClientRect();
  m.style.top=(window.scrollY+b.bottom+4)+'px'; m.style.left=(window.scrollX+Math.max(8,b.left))+'px';
}
async function setOwners(owners){
  try{ selected=await post('/api/projects/'+selected.project_id+'/owners',{owners});
    const bar=$('#_ownersbar'); if(bar) bar.outerHTML=ownersBarHTML(); }
  catch(e){ toast(S.revertido); }
}
async function toggleOwner(name){
  const ow=new Set(selected.owners||[]);
  ow.has(name)?ow.delete(name):ow.add(name);
  await setOwners([...ow]); ownerPicker();        // keep the picker open with refreshed checks
}
async function addRosterOwner(){
  const nm=prompt('Novo dono (nome):'); if(!nm||!nm.trim()) return;
  try{ const r=await post('/api/roster',{name:nm.trim()}); roster=r.roster||roster; await toggleOwner(nm.trim()); }
  catch(e){ toast(S.revertido); }
}

function closeoutBannerHTML(){
  const p=selected.project;
  if(!CLOSED_STAGES.has(p.stage)) return '';
  const party=p.close_party?(' · '+(PARTYpt[p.close_party]||p.close_party)):'';
  const reason=p.close_reason?(' — '+esc(p.close_reason)):'';
  return '<div class="closed">✗ '+(STAGEpt[p.stage]||p.stage)+party+reason+'</div>';
}
function openCloseout(stage){
  _pendingStage=stage; _coParty='client';
  const parties=[['client','Cliente'],['supplier','Fornecedor'],['our','Nós']];
  const box=$('#_closeform'); if(!box) return;
  box.innerHTML='<div class="cof"><div class="lbl">'+(stage==='CANCELLED'?'Cancelar projeto':'Marcar como perdido')+' — de quem partiu e porquê?</div>'
    +'<div class="chips" id="_coparty">'+parties.map((p,i)=>'<span class="chip'+(i===0?' on':'')+'" data-party="'+p[0]+'">'+p[1]+'</span>').join('')+'</div>'
    +'<textarea id="_coreason" placeholder="Motivo (opcional): o que aconteceu?" spellcheck="false"></textarea>'
    +'<div class="cofacts"><button class="act-btn" id="_cocancel">Voltar</button>'
    +'<button class="act-btn danger" id="_coconfirm">Confirmar</button></div></div>';
  box.classList.remove('hidden');
  const ta=$('#_coreason'); if(ta) ta.focus();
}
async function confirmCloseout(){
  try{ selected=await post('/api/projects/'+selected.project_id+'/stage',
        {stage:_pendingStage, close_party:_coParty, close_reason:(($('#_coreason')||{}).value||'').trim()});
    renderDetail(); toast('atualizado'); }
  catch(e){ toast(S.revertido); }
}

async function loadParticipants(){
  const box=$('#_participants'); if(!box||!selected) return;
  try{
    const d=await (await fetch('/api/projects/'+encodeURIComponent(selected.project_id)+'/participants')).json();
    const ps=d.participants||[];
    box.innerHTML=ps.length?('<span class="plbl">Contribuíram:</span>'
      +ps.map(p=>'<span class="pcontrib" title="'+p.contributions+' contribuição(ões)'+(p.channels&&p.channels.length?' · '+esc(p.channels.join(', ')):'')+'">@'+esc(p.name)+' <b>'+p.contributions+'</b></span>').join('')):'';
  }catch(e){ box.innerHTML=''; }
}

function detailHTML(){
  const p=selected.project, rd=selected.readiness||{};
  const job=selected.job_fields||{}, items=selected.items||[], customs=selected.custom_fields||{};
  const stages=STAGES.map(s=>'<span class="st'+(p.stage===s?' on':'')+(TERMINAL.has(s)&&p.stage===s?' terminal':'')+'" data-stage="'+s+'">'+s+'</span>').join('');
  const nmiss=(rd.missing||[]).length;

  /* Relationship axis (counterparty) — its own color-coded .cp badge from the real source
     (job_fields.client_identity = the COUNTERPARTY enum), kept VISUALLY SEPARATE from the lifecycle
     stage pills so neither reads as the other. A bare enum client_name is suppressed (no real name). */
  const cpval=(job.client_identity&&job.client_identity.value)||'';
  const CPSET={CLIENT:1,LEAD:1,SUPPLIER:1}, ENUMSET={CLIENT:1,LEAD:1,SUPPLIER:1,INTERNAL:1,BULK:1,OTHER:1};
  const cpBadge=CPSET[cpval]?'<span class="cp '+cpval+'">'+cpval+'</span> ':'';
  const clientNm=p.client_name||p.client_email||'';
  const clientSpan=(clientNm&&!ENUMSET[clientNm])?'<span style="color:var(--mut);font-size:12.5px">'+esc(clientNm)+'</span>':'';

  /* Origem panel — source emails (lazy-filled by loadSource) + dangling warning */
  const dangling=(selected.dangling_threads||[]).length;
  const dwarn=dangling?'<div class="dwarn">⚠ '+dangling+' thread'+(dangling===1?'':'s')+' sem contexto no CRM — reconstrói o crm ou volta a ligar o email.</div>':'';
  const nthreads=(selected.threads||[]).length;
  const origem='<div style="display:flex;justify-content:flex-end;margin-bottom:6px">'
    +'<button class="item-rm" id="_attachbtn">+ ligar email</button></div>'
    +'<div id="_origem" class="origem"><div class="hint2">a carregar contexto…</div></div>'+dwarn;

  /* Especificação panel — named, bounded sections; required-first; composer lives in its own tab */
  const gapTxt=nmiss?(nmiss+' obrigatório'+(nmiss===1?'':'s')+' em falta'):'✓ obrigatórios completos';
  const gap='<button class="gapjump'+(nmiss?'':' done')+'" id="_gapjump">'+gapTxt+'</button>';
  const jobRows=fieldGroup(JOB_F, f=>f.key, f=>job[f.key]);
  const itemCards=items.map((it,i)=>
    '<div class="item-card"><div class="ih"><b>peça '+(i+1)+'</b>'
    +(items.length>1?'<button class="item-rm" data-idx="'+i+'">remover</button>':'')+'</div>'
    +fieldGroup(ITEM_F, f=>f.key+'#'+i, f=>it[f.key])+'</div>').join('');
  const custRows=Object.keys(customs).map(addr=>
    '<div class="frow filled" data-addr="'+esc(addr)+'"><label style="font-style:italic">'+esc(addr.replace(/^custom:/,''))+'</label>'
    +'<div class="fctl"><input class="finput" data-addr="'+esc(addr)+'" value="'+esc((customs[addr]||{}).value||'')+'" autocomplete="off" spellcheck="false"/>'
    +chanChip(addr)+'<span class="fsrc s-user">tu</span></div></div>').join('');
  const addCust='<div class="addcust"><input class="cn" id="_cfname" placeholder="campo personalizado" autocomplete="off"/>'
    +'<input class="cv2" id="_cfval" placeholder="valor" autocomplete="off"/><button class="addbtn" id="_cfadd">+ adicionar</button></div>';
  const custSec='<div class="psec"><h3>Campos personalizados <span class="c">contexto — não contam para o orçamento</span></h3>'+custRows+addCust+'</div>';
  const espec='<div class="psec"><h3>Especificação do trabalho '+gap+'</h3>'+jobRows+'</div>'
    +'<div class="psec"><h3>Peças <span class="c">'+items.length+' peça'+(items.length===1?'':'s')+'</span></h3>'
    +itemCards+'<button class="addbtn" id="_additem">+ adicionar peça</button></div>'
    +custSec
    +'<div id="_exportwrap">'+(rd.estimable?'<div class="psec"><button class="act-btn accept" id="_exportbtn">Exportar para custeio</button></div>':'')+'</div>';

  /* Email ao cliente panel — the composer is a distinct OUTBOUND task; one tab = one task. */
  const emailTab='<div class="ppanel hidden" data-panel="email"><div id="_ask"><div class="hint2">a preparar email…</div></div></div>';

  const tabs='<div class="ptabs">'
    +'<button class="ptab-btn on" data-tab="espec">Especificação</button>'
    +'<button class="ptab-btn" data-tab="origem">Origem'+(nthreads?' <span class="bdg">'+nthreads+'</span>':'')+'</button>'
    +'<button class="ptab-btn" data-tab="timeline">Linha do tempo</button>'
    +'<button class="ptab-btn" data-tab="email">Email ao cliente'+(nmiss?' <span class="bdg warn">'+nmiss+'</span>':'')+'</button>'
    +'<button class="ptab-btn" data-tab="registar">Registar</button></div>';

  return '<button class="hbtn" id="_backbtn" style="margin-bottom:14px">← Projetos</button>'
    +'<h2 style="margin:0 0 8px;font-size:20px;letter-spacing:-.01em">'+esc(p.title)+'</h2>'
    +'<div style="display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:4px">'
    +'<span id="_ring">'+ringHTML(rd.coverage||0,rd.estimable||false)+'</span>'
    +'<div class="pstage">'+stages+'</div>'
    +'<span class="grow"></span>'+cpBadge+clientSpan+'</div>'
    +ownersBarHTML()
    +'<div id="_participants" class="parts"></div>'
    +closeoutBannerHTML()
    +'<div id="_closeform" class="hidden"></div>'
    +contestedBanner()+tabs
    +'<div class="ppanel" data-panel="espec">'+espec+'</div>'
    +'<div class="ppanel hidden" data-panel="origem">'+origem+'</div>'
    +'<div class="ppanel hidden" data-panel="timeline"><div id="_timeline"><div class="hint2">a carregar histórico…</div></div></div>'
    +emailTab
    +'<div class="ppanel hidden" data-panel="registar">'+captureHTML()+'</div>';
}

/* ── tab strip (show/hide; lazy-load the timeline; reflect Registar in the URL) ─────────── */
function showTab(name){
  const root=$('#_detail'); if(!root) return;
  root.querySelectorAll('.ptab-btn').forEach(b=>b.classList.toggle('on', b.dataset.tab===name));
  root.querySelectorAll('.ppanel').forEach(pl=>pl.classList.toggle('hidden', pl.dataset.panel!==name));
  if(name==='timeline') loadTimeline();
  try{
    const want=name==='registar'?(location.pathname+'?registar=nota'):location.pathname;
    if(location.pathname+location.search!==want) history.replaceState(null,'',want);
  }catch(_){}
}

let _tlSeq=0;
async function loadTimeline(){
  const box=$('#_timeline'); if(!box||!selected) return;
  const pid=selected.project_id, seq=++_tlSeq;
  try{
    const d=await (await fetch('/api/projects/'+encodeURIComponent(pid)+'/timeline')).json();
    if(seq!==_tlSeq) return;                       // a newer load superseded this one
    box.innerHTML=timelineHTML(d.timeline||[]);
  }catch(e){ box.innerHTML='<div class="hint2" style="color:var(--red)">falhou ao carregar histórico</div>'; }
}

function timelineHTML(rows){
  if(!rows.length) return '<div class="hint2">Sem histórico ainda — usa <b>Registar</b> para anotar uma chamada, reunião ou decisão.</div>';
  const KIND={note:'Nota',decision:'Decisão',opinion:'Opinião',todo:'To-do'};
  return '<div class="tl">'+rows.map(r=>{
    const isEvent=r.op==='event', isClear=r.op==='clear', base=(r.field||'').split('#')[0];
    let head;
    if(isEvent){ const k=(r.field||'').replace(/^__|__$/g,''); head='<b>'+esc(KIND[k]||k)+'</b> '+esc(r.new_value||''); }
    else { const lbl=(byKey[base]&&byKey[base].label)||r.field;
      head=isClear ? '<b>'+esc(lbl)+'</b> removido <span class="tl-old">'+esc(r.old_value||'')+'</span>'
                   : '<b>'+esc(lbl)+'</b> '+(r.old_value?'<span class="tl-old">'+esc(r.old_value)+'</span>':'')+esc(r.new_value||''); }
    const chan=(r.channel&&CHAN_ICON[r.channel])?(CHAN_ICON[r.channel]+' '):'';
    const who=r.asserted_by?(' · '+esc(r.asserted_by)):'';
    const when=esc((r.acquired_at||r.ts||'').slice(0,10));
    // The photo in the project timeline: a capture event carries its media via source_mid
    // ("capture:<cid>", set on apply) — render the sole-copy thumbnail inline (ADR-020).
    const sm=r.source_mid||'';
    const thumb=(isEvent&&sm.indexOf('capture:')===0)
      ? '<img class="tl-thumb" src="/api/captures/'+encodeURIComponent(sm.slice(8))+'/media/0"'
        +' alt="captura" loading="lazy" onclick="window.open(this.src)">'
      : '';
    return '<div class="tl-row'+(isEvent?' event':'')+(isClear?' removed':'')+'">'
      +'<div class="tl-h">'+head+'</div>'+thumb+'<div class="tl-m">'+chan+when+who+'</div></div>';
  }).join('')+'</div>';
}

function renderDetail(){
  if(!selected){$('#_detail').classList.add('hidden');$('#_list').classList.remove('hidden');return;}
  $('#_list').classList.add('hidden');
  $('#_detail').classList.remove('hidden');
  $('#_detail').innerHTML=detailHTML();
  const wd=$('#_capwhen'); if(wd&&!wd.value){ try{wd.value=new Date().toISOString().slice(0,10);}catch(_){} }
  loadSource();
  loadDraft();
  loadParticipants();
  if(_registarFromURL()) showTab('registar');   // deep-link straight into capture (?registar=nota)
}

/* ── refresh only the summary bits after a field save (keep input focus) ─ */
function refreshSummary(){
  const rd=selected.readiness||{}, nmiss=(rd.missing||[]).length;
  const ring=$('#_ring'); if(ring) ring.innerHTML=ringHTML(rd.coverage||0,rd.estimable||false);
  // live gap-count in the section header + the Email-tab badge (the page's "what's next" signal)
  const gj=$('#_gapjump');
  if(gj){ gj.textContent=nmiss?(nmiss+' obrigatório'+(nmiss===1?'':'s')+' em falta'):'✓ obrigatórios completos';
    gj.classList.toggle('done', !nmiss); }
  const et=$('#_detail').querySelector('.ptab-btn[data-tab="email"]');
  if(et) et.innerHTML='Email ao cliente'+(nmiss?' <span class="bdg warn">'+nmiss+'</span>':'');
  // a field save changes the gaps → refresh the composer's prompt list, but only when the
  // user hasn't started hand-editing the draft (we must not wipe their wording).
  if(draft&&!draft.dirty) loadDraft();
  const ew=$('#_exportwrap');
  if(ew) ew.innerHTML=rd.estimable?'<div class="psec"><button class="act-btn accept" id="_exportbtn">Exportar para custeio</button></div>':'';
  announce(rd.estimable?'projeto estimável':(nmiss+' campos obrigatórios em falta'));
}

/* update one field row's visual state in place (no re-render → keep focus) + brief save-flash */
function markRow(addr, value){
  const row=$('#_detail').querySelector('.frow[data-addr="'+addr+'"]'); if(!row) return;
  const t=(byKey[addr.split('#')[0]]||{}).tier;   // custom: addrs → undefined tier → calm optional state
  row.classList.remove('miss-must','miss-opt','filled');
  row.classList.add(value?'filled':(t==='must'?'miss-must':'miss-opt'));
  let b=row.querySelector('.fsrc');
  if(value){ if(!b){b=document.createElement('span');row.querySelector('.fctl').appendChild(b);} b.className='fsrc s-user'; b.textContent='tu'; }
  else if(b){ b.remove(); }
  row.classList.remove('saved'); void row.offsetWidth; row.classList.add('saved');  // commit confirmation
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
  if(selected){ if(e.key==='Escape'){closeDetail();} return; }
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
    {kind:'ação',label:'Capturas',run:()=>{location.href='/capturas';}},
    {kind:'ação',label:'Novo projeto',run:promptNew},
    {kind:'ação',label:'Registar conhecimento',run:()=>{ if(selected) showTab('registar'); else toast('abre um projeto primeiro'); }},
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
  // composer: a prompt checkbox toggled → update selection + re-assemble the draft
  const cb=e.target.closest('.ask-opt input[data-key]');
  if(cb&&draft){ const k=cb.dataset.key;
    if(cb.checked) draft.selected.add(k); else draft.selected.delete(k);
    resyncDraft(); return; }
  const inp=e.target.closest('.finput'); if(!inp||!selected) return;
  const addr=inp.dataset.addr, value=inp.value.trim();
  try{
    const d=await post('/api/projects/'+selected.project_id+'/field',{field:addr,value});
    selected=d; markRow(addr,value); refreshSummary();
  }catch(err){ toast(S.revertido); }
});

/* composer: hand-editing the draft marks it dirty (keep edits; offer Regenerar). We mutate the
   DOM in place rather than re-render, so the textarea keeps focus while typing. */
$('#_detail').addEventListener('input', e=>{
  if(e.target.id!=='_draftbody'||!draft) return;
  draft.body=e.target.value;
  if(!draft.dirty){
    draft.dirty=true;
    const dl=$('#_detail').querySelector('.draftbox .dl');
    if(dl&&!dl.querySelector('.dirty'))
      dl.insertAdjacentHTML('beforeend','<span class="dirty">✎ editado <button class="regen" id="_regenq">Regenerar</button></span>');
  }
});

/* ── detail: all click actions via delegation (survive partial re-renders) */
$('#_detail').addEventListener('click', async e=>{
  if(!selected) return;
  if(e.target.closest('#_backbtn')){closeDetail();return;}
  const st=e.target.closest('.pstage .st');
  if(st){ const stage=st.dataset.stage;
    // CANCELLED/LOST open an inline close-out form (party + reason) instead of posting immediately.
    if(CLOSED_STAGES.has(stage) && selected.project.stage!==stage){ openCloseout(stage); e.stopPropagation(); return; }
    try{await post('/api/projects/'+selected.project_id+'/stage',{stage});
      selected=await (await fetch('/api/projects/'+selected.project_id)).json(); renderDetail();}
    catch(err){toast(S.revertido);} return; }
  /* owners (multi) */
  if(e.target.closest('#_ownadd')){ ownerPicker(); e.stopPropagation(); return; }
  const orm=e.target.closest('[data-own-rm]');
  if(orm){ await setOwners((selected.owners||[]).filter(o=>o!==orm.dataset.ownRm)); return; }
  /* close-out form */
  const coc=e.target.closest('#_coparty .chip');
  if(coc){ _coParty=coc.dataset.party; coc.parentElement.querySelectorAll('.chip').forEach(x=>x.classList.toggle('on',x===coc)); return; }
  if(e.target.closest('#_coconfirm')){ await confirmCloseout(); return; }
  if(e.target.closest('#_cocancel')){ const b=$('#_closeform'); if(b){b.classList.add('hidden');b.innerHTML='';} return; }
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
  /* ── composer actions ─────────────────────────────────────────────── */
  if(e.target.closest('#_addq')&&draft){
    const q=prompt('Pergunta para o cliente:'); if(!q||!q.trim()) return;
    draft.custom.push(q.trim()); resyncDraft(); return; }
  const crm=e.target.closest('.custq .rm');
  if(crm&&draft){ draft.custom.splice(parseInt(crm.dataset.ci,10),1); resyncDraft(); return; }
  if(e.target.closest('#_regenq')&&draft){
    draft.dirty=false;
    try{ const r=await post('/api/projects/'+selected.project_id+'/draft',
      {selected:[...draft.selected], custom:draft.custom});
      draft.body=r.body; renderComposer(); }
    catch(err){ toast(S.revertido); } return; }
  if(e.target.closest('#_copyq')){
    const txt=(($('#_draftbody')||{}).value)||'';
    try{ await navigator.clipboard.writeText(txt); toast('email copiado'); }
    catch(err){ toast('copia manual: '+txt.slice(0,40)+'…'); } return; }
  if(e.target.closest('#_openq')&&draft){
    const subj=(($('#_subj')||{}).value)||draft.subject||'';
    const body=(($('#_draftbody')||{}).value)||'';
    location.href='mailto:'+encodeURIComponent(draft.to||'')
      +'?subject='+encodeURIComponent(subj)+'&body='+encodeURIComponent(body); return; }
  if(e.target.closest('#_exportbtn')){ try{
    const r=await post('/api/projects/'+selected.project_id+'/export',{adapter:'json'});
    toast(r.ok?'exportado: '+(r.external_id||'ok'):S.revertido);}
    catch(err){toast(S.revertido);} return; }
});

/* ── ADR-015 capture/tabs: a SEPARATE delegated listener so the existing handler is untouched.
   #_detail persists across innerHTML swaps, so delegation survives re-renders. */
$('#_detail').addEventListener('click', async e=>{
  if(!selected) return;
  const tab=e.target.closest('.ptab-btn');
  if(tab){ showTab(tab.dataset.tab); return; }
  // gap-count in the section header → jump to + focus the first missing required field ("what's next")
  if(e.target.closest('#_gapjump')){
    const el=$('#_detail').querySelector('.frow.miss-must .finput');
    if(el){ showTab('espec'); el.scrollIntoView({block:'center'}); el.focus(); }
    else toast('sem obrigatórios em falta'); return; }
  const chc=e.target.closest('#_capchans .chip');
  if(chc){ capChan=chc.dataset.chan; chc.parentElement.querySelectorAll('.chip').forEach(x=>x.classList.toggle('on',x===chc)); return; }
  const kc=e.target.closest('#_capkinds .chip');
  if(kc){ capKind=kc.dataset.kind; kc.parentElement.querySelectorAll('.chip').forEach(x=>x.classList.toggle('on',x===kc)); return; }
  if(e.target.closest('#_capsave')){
    const text=(($('#_captext')||{}).value||'').trim(); if(!text){ toast('escreve algo primeiro'); return; }
    const who=(($('#_capwho')||{}).value||'').trim(), when=(($('#_capwhen')||{}).value||'').trim();
    try{ await post('/api/projects/'+selected.project_id+'/event',
        {kind:capKind, text:text, channel:capChan, asserted_by:who, acquired_at:when});
      const t=$('#_captext'); if(t) t.value=''; toast('registado'); showTab('timeline'); }
    catch(err){ toast(S.revertido); } return; }
  if(e.target.closest('#_cfadd')){
    const name=(($('#_cfname')||{}).value||'').trim(), val=(($('#_cfval')||{}).value||'').trim();
    if(!name||!val){ toast('nome e valor'); return; }
    try{ selected=await post('/api/projects/'+selected.project_id+'/custom-field',{name:name, value:val});
      renderDetail(); toast('campo adicionado'); }
    catch(err){ toast(S.revertido); } return; }
});

/* owner picker menu (shared #_menu): toggle a roster name, or add a brand-new owner */
$('#_menu').addEventListener('click', async e=>{
  const mi=e.target.closest('.mi'); if(!mi||!selected) return;
  if(mi.dataset.ownNew){ await addRosterOwner(); return; }
  if(mi.dataset.ownTg){ await toggleOwner(mi.dataset.ownTg); return; }
});

/* deep-link + history: open the project named in the URL on load, and let the browser
   back/forward buttons move between list and detail. */
window.addEventListener('popstate',()=>{
  const pid=_pidFromURL();
  if(pid) loadDetail(pid,false); else closeDetail(false);
});
(function(){
  const pid=_pidFromURL();
  if(!pid) return;
  // Canonicalize a legacy /projetos?p=<pid> link (Fila chip) to the path form, but PRESERVE the
  // ?registar=nota view-state (the legacy canonicalizer used to strip ALL query params).
  const params=new URLSearchParams(location.search);
  if(params.has('p')){
    const q=params.get('registar')==='nota'?'?registar=nota':'';
    try{history.replaceState(null,'','/projetos/'+encodeURIComponent(pid)+q);}catch(_){}
  }
  loadDetail(pid,false);
})();
"""


def build_html(projects: list[dict[str, Any]],
               nav_counts: dict[str, int] | None = None,
               roster: list[str] | None = None) -> str:
    return cockpit_ui.page(
        "Projetos", "projetos", _BODY,
        embeds={"projects": projects, "fields": _FIELDS, "roster": list(roster or [])},
        lens_js=_LENS_JS,
        nav_counts=nav_counts,
    )
