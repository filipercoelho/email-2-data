"""Caixa de Capturas lens page (/capturas) — the conversational-intake validation queue.

Thin wrapper over ``cockpit_ui.page()`` (mirrors ``para_ti_page``/``fila_page``). The pending captures
are built server-side by ``CaptureStore.list_pending``; the JS renders each one with a project picker +
event-kind picker + Aplicar/Descartar, and POSTs to the M3 API. NOTHING is auto-applied — every capture
is a deliberate human action (ADR-019 §5 / R9). All capture text + project titles are UNTRUSTED and run
through ``esc()`` before they touch the DOM (the M3 XSS lens).
"""

from __future__ import annotations

from typing import Any

from . import cockpit_ui
from .workspace import EVENT_KINDS

# pt-PT labels for the off-email event kinds the capture is filed as (ADR-015). Keys track
# ``workspace.EVENT_KINDS``; a missing key falls back to the raw kind at render time.
_KIND_LABEL = {"note": "Nota", "decision": "Decisão", "opinion": "Opinião", "todo": "To-do"}

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
  .capmeta{display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin-top:5px;font-size:11.5px;color:var(--mut)}
  .capclass{font-size:9.5px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;
    padding:1px 7px;border-radius:20px;background:#eef0f3;color:var(--mut)}
  .capclass.artifact{background:#efeafb;color:var(--purple)}
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
function chosenProj(c){ return ('_proj' in c) ? c._proj : (c.inferred_project_id || ''); }
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

function renderCard(c, i){
  const txt = (c.raw_text || '').trim();
  const media = c.media_paths || [];
  const hasMedia = media.length > 0;
  const cid = c.capture_id;
  const isFocused = i === focus;
  // text, or a placeholder when the staffer sent only a photo/voice memo
  const bodyTxt = txt
    ? '<div class="captxt">'+esc(txt)+'</div>'
    : '<div class="captxt empty">'+(hasMedia ? '📷 foto sem legenda' : '📎 captura sem texto')+'</div>';
  // thumbnail of the first media file (the sole copy once Telegram is scrubbed — ADR-020)
  const thumb = hasMedia
    ? '<img class="capthumb" src="/api/captures/'+encodeURIComponent(cid)+'/media/0" alt="captura"'
      + ' loading="lazy" onclick="window.open(this.src)">'
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
    + '<div class="capbody">'+bodyTxt+meta+ctl+'</div>'
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

/* Persist a project/kind pick onto its capture so a later render() restores it (not just the DOM). */
$('#_list').addEventListener('change', e=>{
  const card = e.target.closest('.capcard'); if(!card) return;
  const c = caps.find(x => x.capture_id === card.dataset.cid); if(!c) return;
  if(e.target.classList.contains('capproj')) c._proj = e.target.value;
  else if(e.target.classList.contains('capkind')) c._kind = e.target.value;
});

$('#_list').addEventListener('click', e=>{
  if(e.target.closest('select')) return;              // let the native picker open without re-render
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
        embeds={"captures": captures, "projects": projects, "labels": labels},
        lens_js=_LENS_JS,
        nav_counts=nav_counts,
    )
