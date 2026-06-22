"""Caixa de Capturas lens page (/capturas) — the conversational-intake validation queue.

Thin wrapper over ``cockpit_ui.page()`` (mirrors ``para_ti_page``/``fila_page``). The pending captures
are built server-side by ``CaptureStore.list_pending``; the JS renders each one with a project picker +
event-kind picker + Aplicar/Descartar, and POSTs to the M3 API. NOTHING is auto-applied — every capture
is a deliberate human action (ADR-019 §5 / R9). All capture text + project titles are UNTRUSTED and run
through ``esc()`` before they touch the DOM (the M3 XSS lens).
"""

from __future__ import annotations

from typing import Any

from . import cockpit_ui, jobspec as _js
from .workspace import EVENT_KINDS

# pt-PT labels for the off-email event kinds the capture is filed as (ADR-015). Keys track
# ``workspace.EVENT_KINDS``; a missing key falls back to the raw kind at render time.
_KIND_LABEL = {"note": "Nota", "decision": "Decisão", "opinion": "Opinião", "todo": "To-do"}

# pt-PT labels for the extracted job-spec fields (Increment 2), keyed by base field key (the address
# minus any ``#i``). Single source of truth = jobspec.FIELDS, so they never drift from the registry.
_FIELD_LABELS = {k: label for k, label, _t, _q, _s in _js.FIELDS}

_BODY = """
<div class="wrap">
  <div class="bar">
    <span id="_count"></span>
    <span class="cmdk"><kbd>⌘K</kbd> comandos</span>
  </div>
  <div id="_list"></div>
  <div id="_zero" class="zero hidden">✓ Caixa de Capturas vazia<span class="s">nada à espera de validação</span></div>
  <div id="_noproj" class="hint hidden">Não há projetos ativos — cria um em <b>Projetos</b> antes de associar uma captura.</div>
  <div class="hint"><b>J/K</b> navegar · <b>A</b> aplicar · <b>D</b> descartar · <b>?</b> ajuda</div>
</div>
<style>
  .capcard{background:var(--card);border:1px solid var(--bd);border-radius:14px;
    padding:14px 16px;margin-bottom:10px;box-shadow:var(--shadow);display:flex;gap:13px;align-items:flex-start}
  .capcard.on{border-color:var(--ac);box-shadow:0 0 0 2px rgba(51,88,212,.15),var(--shadow)}
  .capthumb{width:52px;height:52px;object-fit:cover;border-radius:9px;border:1px solid var(--bd);
    flex:0 0 auto;background:#f6f7f9;cursor:zoom-in}
  .capbody{flex:1;min-width:0}
  .captxt{font-weight:620;font-size:14px;line-height:1.45;white-space:pre-wrap;word-break:break-word}
  .captxt.empty{color:var(--mut2);font-weight:550;font-style:italic}
  .captag{display:inline-block;font-size:9.5px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;
    color:var(--int);background:#f0fdfa;border:1px solid #bfe6e0;border-radius:20px;padding:1px 7px;margin-right:7px;vertical-align:middle}
  .capaudio{display:block;margin-top:9px;height:34px;max-width:340px;width:100%}
  .capmeta{display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin-top:5px;font-size:11.5px;color:var(--mut)}
  .capclass{font-size:9.5px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;
    padding:1px 7px;border-radius:20px;background:#eef0f3;color:var(--mut)}
  .capclass.artifact{background:#efeafb;color:var(--purple)}
  /* extracted-field validation (Increment 2) — editable, individually-confirmable rows */
  .capfields{margin-top:11px;border:1px solid var(--bd);border-radius:10px;padding:9px 11px;background:#fbfcfe}
  .capfhdr{font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:var(--mut);
    margin-bottom:7px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  .capconf{font-weight:700;color:var(--int);background:#f0fdfa;border:1px solid #bfe6e0;border-radius:20px;padding:0 7px;font-size:10px}
  .capfnote{font-weight:500;text-transform:none;letter-spacing:0;color:var(--mut2);font-size:11px}
  .capfield{display:flex;align-items:center;gap:8px;margin:5px 0}
  .capflabel{flex:0 0 130px;font-size:12px;color:var(--mut);font-weight:600}
  .capfval{flex:1;min-width:90px;border:1px solid var(--bd);border-radius:7px;padding:4px 8px;font-size:12.5px;
    color:var(--tx);background:var(--card);font-family:inherit;outline:none}
  .capfval:focus{border-color:var(--ac)}
  .capfok{flex:0 0 auto;font-size:12px}
  .capfield.saved{opacity:.7}
  .capfield.saved .capfval{border-color:var(--green);background:#f3fbf6}
  .capfield.saved .capfok{border-color:var(--green);color:var(--green)}
  .capctl{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:11px}
  .capctl select{border:1px solid var(--bd);border-radius:8px;padding:5px 9px;font-size:12.5px;
    color:var(--tx);background:var(--card);font-family:inherit;max-width:280px}
  .capctl select:focus{border-color:var(--ac);outline:none}
  .capproj{flex:1;min-width:140px}
</style>
"""

_LENS_JS = r"""
/* ── Caixa de Capturas lens state ───────────────────────────────────── */
let caps = CAPTURES.slice(), focus = 0, gone = new Set();
const KINDS = ['note','decision','opinion','todo'];
const _busy = new Set();   /* captures with an apply/discard POST in flight — blocks a double-fire */

function visible(){ return caps.filter(c => !gone.has(c.capture_id)); }

/* The user's project/kind picks live ON the capture object (c._proj / c._kind), not just in the DOM,
   so render() (J/K nav, a sibling apply) restores them instead of snapping back to the inferred
   default — without that, the advertised validate-many flow silently reverts or mis-files (M3 review). */
function chosenProj(c){ return ('_proj' in c) ? c._proj
  : (c.inferred_project_id || c.suggested_project_id || ''); }   /* suggested = deterministic resolve */
function chosenKind(c){ return c._kind || 'note'; }

/* The active-projects pick-list (PROJECTS = [{project_id,title,stage}]), pre-selected to the chosen/
   inferred project. A blank placeholder is prepended whenever there is no valid pre-selection, so
   Aplicar can't fire against an empty project. */
function projectOptions(sel){
  const known = PROJECTS.some(p => p.project_id === sel);
  const head = known ? '' : '<option value="" selected>— escolher projeto —</option>';
  return head + PROJECTS.map(p =>
    '<option value="'+esc(p.project_id)+'"'+(p.project_id===sel?' selected':'')+'>'
    + esc(p.title) + ' (' + esc(p.project_id) + ')</option>').join('');
}
function kindOptions(sel){
  return KINDS.map(k =>
    '<option value="'+k+'"'+(k===sel?' selected':'')+'>'+esc(LABELS[k]||k)+'</option>').join('');
}

/* The LLM-extracted job-spec field values (Increment 2). Shown as editable, individually-confirmable
   rows — NOTHING is bulk-applied: confirming one POSTs it to the SELECTED project's /field with the
   capture's provenance (R9; a wrong extracted value can feed the estimable gate, so each is a
   deliberate human action). The field address (e.g. "material#0") → its pt-PT label via FIELD_LABELS. */
function fieldLabel(addr){ const base = String(addr).split('#')[0]; return FIELD_LABELS[base] || base; }
function fieldVal(c, addr){ const o = c._fields || {}; return (addr in o) ? o[addr] : c.extracted_fields[addr]; }
function fieldsHTML(c){
  const f = c.extracted_fields || {};
  const addrs = Object.keys(f);
  if(!addrs.length) return '';
  const conf = (typeof c.confidence === 'number')
    ? '<span class="capconf" title="confiança da extração">'+Math.round(c.confidence*100)+'%</span>' : '';
  const rows = addrs.map(addr =>
    '<div class="capfield" data-addr="'+esc(addr)+'">'
    + '<span class="capflabel">'+esc(fieldLabel(addr))+'</span>'
    + '<input class="capfval" value="'+esc(fieldVal(c, addr))+'" aria-label="'+esc(fieldLabel(addr))+'">'
    + '<button class="act-btn capfok" data-act="field">✓ confirmar</button>'
    + '</div>').join('');
  return '<div class="capfields">'
    + '<div class="capfhdr">Campos extraídos '+conf
    + '<span class="capfnote">confirma um a um — nada é aplicado automaticamente</span></div>'
    + rows + '</div>';
}

function renderCard(c, i){
  const txt = (c.raw_text || '').trim();
  const transcript = (c.transcript || '').trim();
  const media = c.media_paths || [];
  const hasMedia = media.length > 0;
  const isPhoto = c.content_class === 'artifact' && hasMedia;   // photo artifact vs voice/audio memo
  const isAudio = c.content_class !== 'artifact' && hasMedia;
  const cid = c.capture_id;
  const isFocused = i === focus;
  // The body: the typed text, else the pt-PT transcript of a voice memo (Increment 1), else a
  // placeholder. A transcript is shown with a small hint so the user knows it came from audio.
  let bodyTxt;
  if(txt){ bodyTxt = '<div class="captxt">'+esc(txt)+'</div>'; }
  else if(transcript){ bodyTxt = '<div class="captxt"><span class="captag">🎙️ transcrição</span>'+esc(transcript)+'</div>'; }
  else { bodyTxt = '<div class="captxt empty">'
    +(isAudio ? '🎙️ memo de voz (sem transcrição)' : (isPhoto ? '📷 foto sem legenda' : '📎 captura sem texto'))+'</div>'; }
  // The media off the sole-copy endpoint (ADR-020): a photo is a clickable left-column thumbnail; a
  // voice/audio memo is a playable control inside the body (an <img> on audio would just break). The
  // src is read-only + path-traversal-guarded server-side.
  const thumb = isPhoto
    ? '<img class="capthumb" src="/api/captures/'+encodeURIComponent(cid)+'/media/0" alt="captura"'
      + ' loading="lazy" onclick="window.open(this.src)">'
    : '';
  const audioEl = isAudio
    ? '<audio class="capaudio" controls preload="none" src="/api/captures/'+encodeURIComponent(cid)+'/media/0"></audio>'
    : '';
  // provenance line: who said it, how, and when (real-world acquisition time)
  const cls = c.content_class === 'artifact' ? 'artifact' : '';
  const metaParts = ['<span class="capclass '+cls+'">'+esc(c.content_class||'conversation')+'</span>'];
  if(c.asserted_by) metaParts.push('<span>'+esc(c.asserted_by)+'</span>');
  const when = (c.acquired_at || c.created_ts || '').slice(0,10);
  if(when) metaParts.push('<span>'+esc(when)+'</span>');
  if(media.length > 1) metaParts.push('<span>+'+(media.length-1)+' anexo'+(media.length-1===1?'':'s')+'</span>');
  const meta = '<div class="capmeta">'+metaParts.join('')+'</div>';
  // controls: project picker (pre-selected) + kind picker + apply/discard
  const noProjects = PROJECTS.length === 0;
  const ctl = '<div class="capctl">'
    + '<select class="capproj" aria-label="Projeto"'+(noProjects?' disabled':'')+'>'+projectOptions(chosenProj(c))+'</select>'
    + '<select class="capkind" aria-label="Tipo">'+kindOptions(chosenKind(c))+'</select>'
    + '<button class="act-btn accept" data-i="'+i+'" data-act="apply"'+(noProjects?' disabled':'')+'>Aplicar</button>'
    + '<button class="act-btn" data-i="'+i+'" data-act="discard">Descartar</button>'
    + '</div>';
  return '<div class="capcard'+(isFocused?' on':'')+'" data-i="'+i+'" data-cid="'+esc(cid)+'">'
    + thumb
    + '<div class="capbody">'+bodyTxt+audioEl+meta+fieldsHTML(c)+ctl+'</div>'
    + '</div>';
}

function render(){
  const v = visible();
  const cnt = $('#_count');
  if(cnt) cnt.textContent = v.length ? v.length+' pendente'+(v.length===1?'':'s') : '';
  const zero = $('#_zero'); if(zero) zero.classList.toggle('hidden', v.length > 0);
  const np = $('#_noproj'); if(np) np.classList.toggle('hidden', !(v.length > 0 && PROJECTS.length === 0));
  if(focus >= v.length) focus = Math.max(0, v.length - 1);
  $('#_list').innerHTML = v.map((c,i) => renderCard(c,i)).join('');
}

/* Keep the Capturas nav badge in sync after an apply/discard (no full reload). */
function bumpNav(){
  const n = visible().length;
  const link = Array.from(document.querySelectorAll('header .nlink'))
    .find(a => a.getAttribute('href') === '/capturas');
  if(!link) return;
  let b = link.querySelector('.nbadge');
  if(n > 0){ if(!b){ b = document.createElement('span'); b.className='nbadge'; link.appendChild(b); } b.textContent = n; }
  else if(b){ b.remove(); }
}

async function applyCap(i){
  const v = visible(); const c = v[i]; if(!c) return;
  if(_busy.has(c.capture_id)) return;                 // a POST is already in flight — no double-fire
  const card = $('#_list').querySelector('.capcard[data-i="'+i+'"]');
  const pid = card ? ((card.querySelector('.capproj')||{}).value || '') : chosenProj(c);
  const kind = card ? ((card.querySelector('.capkind')||{}).value || 'note') : chosenKind(c);
  if(!pid){ toast('Escolhe um projeto'); return; }
  _busy.add(c.capture_id);
  try{
    await post('/api/captures/'+encodeURIComponent(c.capture_id)+'/apply', {project_id: pid, kind: kind});
    gone.add(c.capture_id); render(); bumpNav(); toast('aplicado'); announce('captura aplicada');
  }catch(e){ toast(S.revertido); }
  finally{ _busy.delete(c.capture_id); }
}
async function discardCap(i){
  const v = visible(); const c = v[i]; if(!c) return;
  if(_busy.has(c.capture_id)) return;
  _busy.add(c.capture_id);
  try{
    await post('/api/captures/'+encodeURIComponent(c.capture_id)+'/discard', {});
    gone.add(c.capture_id); render(); bumpNav(); toast('descartado'); announce('captura descartada');
  }catch(e){ toast(S.revertido); }
  finally{ _busy.delete(c.capture_id); }
}

function onKey(e){
  const v = visible(); if(!v.length) return;
  if(e.key==='j'||e.key==='ArrowDown'){ focus=Math.min(v.length-1,focus+1); render(); e.preventDefault(); }
  else if(e.key==='k'||e.key==='ArrowUp'){ focus=Math.max(0,focus-1); render(); e.preventDefault(); }
  else if(e.key==='a'||e.key==='A'||e.key==='e'||e.key==='E') applyCap(focus);
  else if(e.key==='d'||e.key==='D') discardCap(focus);
}

function paletteItems(q){
  q = (q||'').toLowerCase().trim();
  const base = [
    {kind:'ação', label:'Fila', run:()=>{ location.href='/'; }},
    {kind:'ação', label:'Contrapartes', run:()=>{ location.href='/contrapartes'; }},
    {kind:'ação', label:'Projetos', run:()=>{ location.href='/projetos'; }},
    {kind:'ação', label:'Para ti', run:()=>{ location.href='/para-ti'; }},
    {kind:'ação', label:S.actSync, run:syncNow},
  ];
  return q ? base.filter(it => (it.label+' '+it.kind).toLowerCase().includes(q)) : base;
}

/* Confirm ONE extracted field into the SELECTED project (R9 — never bulk-applied). POSTs to the
   existing /field endpoint carrying the capture's provenance; on success the row is marked saved. */
async function confirmField(card, row){
  const c = caps.find(x => x.capture_id === card.dataset.cid); if(!c || !row) return;
  const pid = (card.querySelector('.capproj')||{}).value || '';
  if(!pid){ toast('Escolhe um projeto primeiro'); return; }
  const addr = row.dataset.addr;
  const val = ((row.querySelector('.capfval')||{}).value || '').trim();
  if(!val){ toast('Valor vazio'); return; }
  try{
    await post('/api/projects/'+encodeURIComponent(pid)+'/field',
      {field: addr, value: val, channel: c.channel || 'manual',
       asserted_by: c.asserted_by || '', acquired_at: c.acquired_at || ''});
    row.classList.add('saved');
    const b = row.querySelector('.capfok'); if(b){ b.textContent = '✓ guardado'; b.disabled = true; }
    toast('campo guardado em '+pid); announce('campo confirmado');
  }catch(e){ toast(S.revertido); }
}

/* Persist project/kind picks AND field-value edits onto the capture so a re-render restores them. */
$('#_list').addEventListener('change', e=>{
  const card = e.target.closest('.capcard'); if(!card) return;
  const c = caps.find(x => x.capture_id === card.dataset.cid); if(!c) return;
  if(e.target.classList.contains('capproj')) c._proj = e.target.value;
  else if(e.target.classList.contains('capkind')) c._kind = e.target.value;
  else if(e.target.classList.contains('capfval')){
    const row = e.target.closest('.capfield');
    if(row){ c._fields = c._fields || {}; c._fields[row.dataset.addr] = e.target.value; }
  }
});

$('#_list').addEventListener('click', e=>{
  if(e.target.closest('select') || e.target.closest('input')) return;  // let native controls work
  const fb = e.target.closest('[data-act="field"]');                   // confirm one extracted field
  if(fb){ confirmField(fb.closest('.capcard'), fb.closest('.capfield')); return; }
  const btn = e.target.closest('[data-act]');
  if(btn){
    const i = parseInt(btn.dataset.i, 10);
    if(btn.dataset.act === 'apply') applyCap(i);
    else if(btn.dataset.act === 'discard') discardCap(i);
    return;
  }
  const card = e.target.closest('.capcard');          // click elsewhere on a card = focus it
  if(card){ const i=parseInt(card.dataset.i,10); if(!isNaN(i)){ focus=i; render(); } }
});
"""


def build_html(captures: list[dict[str, Any]], projects: list[dict[str, Any]],
               nav_counts: dict[str, int] | None = None) -> str:
    """Render the Caixa de Capturas queue. ``captures`` are pending rows from ``CaptureStore``;
    ``projects`` are the active projects (terminal stages filtered out) for the pick-list."""
    labels = {k: _KIND_LABEL.get(k, k) for k in EVENT_KINDS}
    return cockpit_ui.page(
        "Capturas", "capturas", _BODY,
        embeds={"captures": captures, "projects": projects, "labels": labels,
                "field_labels": _FIELD_LABELS},
        lens_js=_LENS_JS,
        nav_counts=nav_counts,
    )
