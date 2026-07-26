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
    {"key": k, "label": lbl, "tier": tier, "q": q, "scope": scope,
     "input": _js.INPUT_TYPE.get(k, "text")}
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
  .prename{border:1px solid var(--bd);background:var(--card);color:var(--mut);border-radius:8px;
    width:26px;height:26px;cursor:pointer;font-size:12px;line-height:1;vertical-align:3px}
  .prename:hover{border-color:var(--ac);color:var(--ac)}
  .pstage{display:inline-flex;gap:4px;align-items:center;flex-wrap:wrap}
  .pstage .st{padding:2px 8px;border-radius:20px;font-size:10px;font-weight:700;border:1px solid var(--bd);color:var(--mut);cursor:pointer}
  .pstage .st.on{background:var(--int);color:var(--card);border-color:var(--int)}
  .pstage .st.terminal{background:var(--green);color:var(--card);border-color:var(--green)}
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
  .finput{flex:1;min-width:0;border:1px solid var(--bd);border-radius:8px;padding:6px 10px;font-size:13px;font-family:inherit;background:var(--card);color:var(--tx)}
  .finput:focus{outline:none;border-color:var(--ac);box-shadow:0 0 0 3px var(--ac-soft)}
  .frow.miss-must .finput{border-color:var(--red-line);background:var(--surface2)}
  .frow.miss-opt .finput{border-color:var(--bd);background:var(--surface2);border-style:dashed}
  .finput::placeholder{color:var(--mut2);font-style:italic}
  /* Native date picker: WebKit gives date inputs their own intrinsic width/height and inner padding,
     which breaks the row rhythm next to the text inputs — pin them back to the .finput box. */
  .finput[type=date],.finput[type=datetime-local]{-webkit-appearance:none;appearance:none;height:30px;line-height:16px}
  .finput[type=date]::-webkit-datetime-edit,.finput[type=datetime-local]::-webkit-datetime-edit{padding:0}
  .finput[type=date]::-webkit-calendar-picker-indicator,
  .finput[type=datetime-local]::-webkit-calendar-picker-indicator{cursor:pointer;opacity:.55}
  .finput[type=date]:hover::-webkit-calendar-picker-indicator,
  .finput[type=datetime-local]:hover::-webkit-calendar-picker-indicator{opacity:1}
  /* An empty picker shows the browser's own dd/mm/yyyy hint instead of our ::placeholder — mute it to
     match. Keyed off the row's empty-state class, NOT :invalid: the field isn't `required`, so an
     empty picker is perfectly valid and :invalid would never match. */
  .frow.miss-must .finput[type=date]:not(:focus)::-webkit-datetime-edit,
  .frow.miss-opt  .finput[type=date]:not(:focus)::-webkit-datetime-edit,
  .frow.miss-must .finput[type=datetime-local]:not(:focus)::-webkit-datetime-edit,
  .frow.miss-opt  .finput[type=datetime-local]:not(:focus)::-webkit-datetime-edit{color:var(--mut2)}
  /* brief confirmation that an inline edit committed (data-entry feedback) */
  .frow.saved .finput{animation:savedflash .9s ease}
  @keyframes savedflash{0%{box-shadow:0 0 0 3px var(--green-bg)}100%{box-shadow:none}}
  /* required-vs-optional divider inside a field group */
  .fopt-sep{font-size:10px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut2);font-weight:700;
    margin:9px 0 3px;padding-top:7px;border-top:1px dashed var(--bd2)}
  /* clickable gap-count in the section header → jumps to the first missing must field */
  .gapjump{border:none;background:none;cursor:pointer;color:var(--red);font-weight:700;font-size:11px;
    text-transform:none;letter-spacing:0;padding:0;text-decoration:underline dotted}
  .gapjump.done{color:var(--green);cursor:default;text-decoration:none}
  .fsrc{font-size:9.5px;font-weight:700;text-transform:uppercase;letter-spacing:.03em;padding:2px 6px;border-radius:6px;flex:0 0 auto}
  .fsrc.s-offline{background:var(--ac-soft);color:var(--ext)} .fsrc.s-llm{background:var(--purple-bg);color:var(--purple)} .fsrc.s-user{background:var(--green-bg);color:var(--green)}
  /* line-item cards */
  .item-card{border:1px solid var(--bd);border-radius:12px;padding:12px 14px;margin:10px 0;background:var(--surface2)}
  .item-card .ih{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}
  .item-card .ih b{font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em}
  .item-rm{border:1px solid var(--bd);background:var(--card);border-radius:7px;font-size:11px;padding:2px 9px;cursor:pointer;color:var(--mut)}
  .item-rm:hover{border-color:var(--red);color:var(--red)}
  .addbtn{border:1px dashed var(--bd);background:var(--card);border-radius:9px;padding:7px 13px;cursor:pointer;font-size:12.5px;color:var(--mut);font-weight:600;margin-top:4px}
  .addbtn:hover{border-color:var(--ac);color:var(--ac);background:var(--ac-soft)}
  /* origem (source emails) — thread CSS comes from cockpit_ui shared styles */
  .origem{max-height:420px;overflow:auto;border:1px solid var(--bd2);border-radius:10px;padding:8px 12px;background:var(--surface2)}
  .origem .texp{margin:0}
  .origem .tmsg{border-bottom:1px solid var(--bd2);border-radius:0;border-left:none;border-right:none;border-top:none;background:transparent;padding:10px 0}
  .origem .tmsg:last-child{border-bottom:none}
  .hint2{color:var(--mut2);font-size:12.5px;padding:9px 2px}
  .dwarn{background:var(--amber-bg);border:1px solid var(--amber-line);color:var(--amber);border-radius:8px;padding:6px 10px;font-size:12px;margin-top:8px}
  /* re-extração (ADR-025 §4) */
  .rexbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:2px}
  .rexbar select{border:1px solid var(--bd);border-radius:8px;padding:4px 8px;font-size:12.5px;
    color:var(--tx);background:var(--card);font-family:inherit}
  .rexbar select:focus{border-color:var(--ac);outline:none}
  .rexbar button[disabled]{opacity:.55;cursor:default}
  .rexnote{padding-top:2px}
  .rexres{border:1px solid var(--bd);background:var(--surface2);border-radius:10px;padding:9px 12px;margin:6px 0 10px}
  .rexres.bad{border-color:var(--red-line);background:var(--red-bg)}
  .rexres .rexh{font-size:12.5px;font-weight:680}
  .rexres.bad .rexh{color:var(--red)}
  .rexres .rexsub{font-size:11.5px;color:var(--mut);margin-top:3px}
  .rexerrs{margin:6px 0 0;padding-left:18px;font-size:11.5px;color:var(--red)}
  .rexerrs code{font-family:ui-monospace,monospace;font-size:11px;word-break:break-all}
  /* perguntas / ask */
  .qs li{margin-bottom:6px;font-size:13px;color:var(--mut)}
  .qmail{display:flex;gap:8px;margin-top:14px;flex-wrap:wrap}
  .ready{color:var(--green);font-size:12.5px;font-weight:600}
  /* ── client-email composer ──────────────────────────────────────────── */
  .cmp .hdr{display:flex;flex-direction:column;gap:8px;margin-bottom:10px}
  .cmp .to{font-size:12.5px;color:var(--mut)}
  .cmp .to b{color:var(--tx);font-weight:600}
  .cmp .subj{display:flex;align-items:center;gap:8px}
  .cmp .subj label{flex:0 0 auto;font-size:12px;color:var(--mut)}
  .cmp .subj input{flex:1;min-width:0;border:1px solid var(--bd);border-radius:8px;padding:6px 10px;font-size:13px;font-family:inherit;background:var(--card);color:var(--tx)}
  .cmp .subj input:focus{outline:none;border-color:var(--ac);box-shadow:0 0 0 3px var(--ac-soft)}
  .askgrp{margin:10px 0}
  .askgrp .gl{font-size:10.5px;text-transform:uppercase;letter-spacing:.04em;font-weight:700;color:var(--mut2);margin-bottom:5px}
  .askgrp.must .gl{color:var(--red)}
  .ask-opt{display:flex;align-items:flex-start;gap:8px;padding:3px 0;font-size:13px;color:var(--mut);cursor:pointer}
  .ask-opt input{margin-top:2px;accent-color:var(--ac);cursor:pointer}
  .ask-opt.intern{color:var(--mut2);cursor:default}
  .custq{display:flex;align-items:center;gap:8px;padding:3px 0;font-size:13px;color:var(--mut)}
  .custq .rm{cursor:pointer;color:var(--mut2);border:none;background:none;font-size:15px;line-height:1;padding:0}
  .custq .rm:hover{color:var(--red)}
  .addq{border:1px dashed var(--bd);background:var(--card);border-radius:8px;padding:5px 11px;cursor:pointer;font-size:12px;color:var(--mut);font-weight:600;margin-top:4px}
  .addq:hover{border-color:var(--ac);color:var(--ac);background:var(--ac-soft)}
  /* ── purpose selector + per-purpose inputs (ADR-031) ── */
  .psel{display:flex;align-items:center;gap:8px;margin:10px 0}
  .psel label{flex:0 0 auto;font-size:12px;color:var(--mut)}
  .psel select{flex:1;min-width:0;border:1px solid var(--bd);border-radius:8px;padding:6px 10px;font-size:13px;font-family:inherit;background:var(--card);color:var(--tx);cursor:pointer}
  .psel select:focus{outline:none;border-color:var(--ac);box-shadow:0 0 0 3px var(--ac-soft)}
  .reasonsel{width:100%;box-sizing:border-box;border:1px solid var(--bd);border-radius:8px;padding:6px 10px;font-size:13px;font-family:inherit;background:var(--card);color:var(--tx);cursor:pointer;margin-bottom:8px}
  .reasonsel:focus{outline:none;border-color:var(--ac);box-shadow:0 0 0 3px var(--ac-soft)}
  .notebox{width:100%;box-sizing:border-box;min-height:90px;border:1px solid var(--bd);border-radius:10px;padding:10px 12px;font-size:13px;line-height:1.5;font-family:inherit;background:var(--card);color:var(--tx);resize:vertical}
  .notebox:focus{outline:none;border-color:var(--ac);box-shadow:0 0 0 3px var(--ac-soft)}
  .factschip{font-size:11.5px;color:var(--mut);margin:8px 0;min-height:1px}
  .factschip .fv{display:inline-block;background:var(--ac-soft);color:var(--ac);border:1px solid var(--ac-line);border-radius:6px;padding:1px 7px;margin:2px 3px 0 0;font-weight:600}
  .draftbox{margin-top:14px}
  .draftbox .dl{display:flex;align-items:center;justify-content:space-between;margin-bottom:5px;min-height:22px}
  .draftbox .dl h4{margin:0;font-size:10.5px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut2);font-weight:700}
  .draftbox .dirty{font-size:11px;color:var(--amber);display:flex;align-items:center;gap:8px}
  .draftbox .dirty .regen{border:1px solid var(--amber-line);background:var(--amber-bg);color:var(--amber);border-radius:7px;padding:2px 9px;cursor:pointer;font-size:11px;font-weight:600}
  .draftbox .dirty .regen:hover{background:var(--amber-bg)}
  .draftbox textarea{width:100%;box-sizing:border-box;min-height:200px;border:1px solid var(--bd);border-radius:10px;padding:11px 13px;font-size:13px;line-height:1.5;font-family:inherit;background:var(--card);color:var(--tx);resize:vertical}
  .draftbox textarea:focus{outline:none;border-color:var(--ac);box-shadow:0 0 0 3px var(--ac-soft)}
  /* ── AI polish (ADR-027) — visually SUBORDINATE to the draft above it: the deterministic text is
     the product, this is an offer sitting beside it until the user adopts it. ── */
  .aibar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:10px}
  .aibar select{border:1px solid var(--bd);border-radius:8px;padding:4px 8px;font-size:12.5px;
    background:var(--card);color:var(--tx);cursor:pointer}
  .aibar select:focus{border-color:var(--ac);outline:none}
  .aibar .hint2{margin:0}
  .act-btn.ai{border-color:var(--ac-line);color:var(--ac);background:var(--ac-soft)}
  .act-btn.ai:hover{background:var(--ac-soft)}
  .act-btn.ai[disabled]{opacity:.55;cursor:default}
  .airesult{margin-top:12px;border:1px solid var(--ac-line);border-radius:10px;padding:11px 13px;background:var(--ac-soft)}
  .airesult.bad{border-color:var(--red-line);background:var(--red-bg)}
  .airesult .aih{font-size:10.5px;text-transform:uppercase;letter-spacing:.04em;color:var(--ac);font-weight:700;margin-bottom:7px}
  .airesult.bad .aih{color:var(--red)}
  .airesult .aih .c{text-transform:none;letter-spacing:0;color:var(--mut2);font-weight:500}
  .airesult textarea{width:100%;box-sizing:border-box;min-height:200px;border:1px solid var(--bd);border-radius:10px;
    padding:11px 13px;font-size:13px;line-height:1.5;font-family:inherit;background:var(--card);color:var(--tx);resize:vertical}
  .airesult textarea:focus{outline:none;border-color:var(--ac);box-shadow:0 0 0 3px var(--ac-soft)}
  .airesult .qmail{margin-top:9px}
  .aiok{font-size:11.5px;color:var(--gr,var(--green));margin-bottom:7px}
  .aiwarn{font-size:11.5px;color:var(--amber);background:var(--amber-bg);border:1px solid var(--amber-line);border-radius:8px;
    padding:7px 10px;margin-bottom:7px}
  .aiwarn ul{margin:5px 0 0;padding-left:18px}
  /* ── tab strip (ADR-015 — only the active panel shows; keeps the page from being one long wall) ── */
  .ptabs{display:flex;gap:4px;flex-wrap:wrap;border-bottom:1px solid var(--bd);margin:14px 0 0}
  .ptab-btn{border:none;background:none;padding:8px 12px;font-size:12.5px;font-weight:600;color:var(--mut);
    cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px}
  .ptab-btn:hover{color:var(--tx)}
  .ptab-btn.on{color:var(--ac);border-bottom-color:var(--ac)}
  .ptab-btn .bdg{display:inline-block;min-width:16px;padding:0 5px;margin-left:5px;border-radius:9px;
    background:var(--bd);color:var(--mut);font-size:10px;font-weight:700;text-align:center}
  .ptab-btn .bdg.warn{background:var(--amber-bg);color:var(--amber)}
  .ppanel{padding-top:6px}
  .ppanel.hidden{display:none}
  /* provenance + conflict chips on a field row */
  .pchan{font-size:9.5px;font-weight:700;padding:2px 6px;border-radius:6px;background:var(--ac-soft);color:var(--ac);
    flex:0 0 auto;cursor:default}
  .frow.conflict .finput{border-color:var(--amber-line);background:var(--amber-bg)}
  .cwarn{font-size:11px;flex:0 0 auto;cursor:help}
  /* contested-on-top banner */
  .contested{background:var(--amber-bg);border:1px solid var(--amber-line);border-radius:10px;padding:9px 12px;margin:10px 0;font-size:12.5px;color:var(--amber)}
  .contested b{color:var(--amber)}
  .contested .cv{display:inline-block;margin:2px 6px 0 0;padding:1px 7px;border-radius:6px;background:var(--card);border:1px solid var(--amber-line);font-size:11px}
  /* custom fields */
  .custf{display:flex;align-items:center;gap:8px;padding:4px 0}
  .custf label{flex:0 0 158px;font-size:12.5px;color:var(--mut);text-align:right;font-style:italic}
  .addcust{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
  .addcust input{border:1px solid var(--bd);border-radius:8px;padding:6px 10px;font-size:13px;font-family:inherit}
  .addcust .cn{flex:0 0 158px} .addcust .cv2{flex:1;min-width:0}
  /* ── Registar (capture) surface ─────────────────────────────────────── */
  .cap{max-width:620px}
  .cap .chips{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px}
  .chip{border:1px solid var(--bd);background:var(--card);border-radius:20px;padding:4px 12px;font-size:12px;
    cursor:pointer;color:var(--mut);font-weight:600}
  .chip.on{background:var(--ac);border-color:var(--ac);color:var(--card)}
  .cap .meta{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}
  .cap .meta input{border:1px solid var(--bd);border-radius:8px;padding:6px 10px;font-size:13px;font-family:inherit}
  .cap textarea{width:100%;box-sizing:border-box;min-height:90px;border:1px solid var(--bd);border-radius:10px;
    padding:10px 12px;font-size:13px;line-height:1.5;font-family:inherit;resize:vertical}
  .cap textarea:focus{outline:none;border-color:var(--ac);box-shadow:0 0 0 3px var(--ac-soft)}
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
    background:var(--int-bg);border:1px solid var(--int-line);border-radius:20px;padding:2px 4px 2px 9px}
  .ochip .ox{border:none;background:none;color:var(--mut2);cursor:pointer;font-size:11px;padding:0 2px;line-height:1}
  .ochip .ox:hover{color:var(--red)}
  .oadd{font-size:11.5px;font-weight:600;color:var(--mut);background:var(--card);border:1px solid var(--bd);
    border-radius:20px;padding:2px 10px;cursor:pointer}
  .oadd:hover{border-color:var(--ac);color:var(--ac)}
  .menu .mhdr{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:var(--mut2);padding:5px 11px 3px}
  .menu .mi.reset{color:var(--int);border-top:1px solid var(--bd2);margin-top:3px}
  /* ── close-out (cancel / lost) ──────────────────────────────────────── */
  .closed{background:var(--red-bg);border:1px solid var(--red-line);color:var(--red);border-radius:9px;
    padding:7px 12px;font-size:12.5px;font-weight:600;margin:4px 0 8px}
  .cof{background:var(--card);border:1px solid var(--red-line);border-radius:11px;padding:13px 15px;margin:6px 0 10px}
  .cof .lbl{font-size:12.5px;font-weight:650;color:var(--tx);margin-bottom:8px}
  .cof textarea{width:100%;min-height:54px;border:1px solid var(--bd);border-radius:8px;padding:8px 10px;
    font:13px/1.5 inherit;color:var(--tx);resize:vertical;outline:none;margin-top:8px}
  .cof textarea:focus{border-color:var(--ac)}
  .cofacts{display:flex;justify-content:flex-end;gap:8px;margin-top:9px}
  .act-btn.danger{border-color:var(--red);color:var(--red)} .act-btn.danger:hover{background:var(--red-bg)}
  /* ── participants (who contributed) ─────────────────────────────────── */
  .parts{display:flex;align-items:center;flex-wrap:wrap;gap:7px;margin:2px 0 8px;font-size:11.5px}
  .parts .plbl{color:var(--mut);font-weight:600}
  .pcontrib{color:var(--purple);background:var(--purple-bg);border:1px solid var(--ac-line);border-radius:20px;padding:1px 9px;cursor:default}
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

/* ── scoped re-extraction (ADR-025 §4) ────────────────────────────────────
   Tier = which model re-reads THIS project's emails. Values are the llm.tiers keys in
   settings.json; the labels are pt-PT. Default 'standard' — same model as the normal sync,
   so the obvious click is not the expensive one. */
const TIERS = [['light','Leve · custo baixo'],['standard','Normal · custo médio'],['heavy','Profundo · custo alto']];
let reTier = 'standard', reBusy = false;
// The email polish (ADR-027) picks its tier independently: it is one short call, so 'standard' being
// the obvious click here costs nothing like a whole-project reprocess at the same tier.
let aiTier = 'standard';
// Output languages for the client email (ADR-032). PT is deterministic; EN/FR/ES are produced by the
// polish pass translating the PT draft (numbers stay verbatim; the result is marked review-required).
const LANGS = [['pt','Português'],['en','English'],['fr','Français'],['es','Español']];
function langLabel(c){const x=LANGS.find(l=>l[0]===c);return x?x[1]:(c||'Português');}

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
    /* WHO the project is for, never a bare enum: a client_name that is just the counterparty enum
       ("LEAD") produced the unreadable "LEAD · LEAD" meta line — suppress it like the detail view
       does, fall back to the email, and speak pt-PT for the stage. */
    const ENUM_NM={CLIENT:1,LEAD:1,SUPPLIER:1,INTERNAL:1,BULK:1,OTHER:1};
    const who=(p.client_name&&!ENUM_NM[p.client_name])?p.client_name:(p.client_email||'');
    return '<div class="row'+(i===focus?' on':'')+'" data-i="'+i+'" data-pid="'+esc(p.project_id)+'">'
      +ringHTML(cov,est)
      +'<div class="rmain"><div class="subj">'+esc(p.title)+'</div>'
      +'<div class="rmeta">'+(who?esc(who)+' · ':'')+esc(STAGEpt[p.stage]||p.stage)
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
    const d=await getJSON('/api/projects/'+encodeURIComponent(pid));
    if(d&&d.error){ toast(S.falhou); return; }   // unknown id (e.g. stale link) → stay on list
    selected=d;
    if(push){ try{history.pushState(null,'','/projetos/'+encodeURIComponent(pid));}catch(_){} }
    renderDetail();
  }catch(e){toast(S.falhou);}
}
/* Return to the list. Pushes /projetos so Back from the list leaves the lens cleanly. */
function closeDetail(push){
  selected=null;
  if(push!==false){ try{history.pushState(null,'','/projetos');}catch(_){} }
  render();
}

/* A native date/datetime input can only *hold* a value it can parse: hand it "meados de agosto" (a
   vague deadline the client genuinely gave, or an LLM/legacy value) and the browser silently shows an
   empty box — on a required field that reads as "no deadline" when one exists. So a picker-typed
   field degrades to a plain text input whenever the stored value isn't ISO-parseable, and only offers
   the picker when it is (or when the field is empty). Never blank a value we can't render natively. */
const _PICKERS={'date':1,'datetime-local':1};
function _isoDate(v){
  if(!/^\d{4}-\d{2}-\d{2}$/.test(v)) return false;
  const d=new Date(v+'T00:00:00Z');
  return !isNaN(d.getTime()) && d.toISOString().slice(0,10)===v;   // rejects 2026-02-30
}
function _isoDateTime(v){
  const m=/^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})$/.exec(v);
  return !!m && _isoDate(m[1]) && +m[2]<24 && +m[3]<60;
}
/* `deadline` accepts BOTH stored shapes — date-only (the extractor, the LLM without a stated hour,
   and every deadline written before the clock existed) and date+time. A datetime-local field renders
   either; a date-only field renders only the former. */
function inputType(f, val){
  if(!_PICKERS[f.input]) return 'text';
  if(!val) return f.input;
  const ok = f.input==='datetime-local' ? (_isoDate(val)||_isoDateTime(val)) : _isoDate(val);
  return ok ? f.input : 'text';
}
/* A datetime-local input cannot hold a bare date, so a date-only value is widened to midnight FOR
   DISPLAY ONLY. The store is untouched: no change event fires unless the user actually edits, so we
   never write back a midnight nobody stated. Showing an invented 00:00 is the lesser evil — the
   alternative is an empty box, which claims the deadline is missing. */
function pickerValue(f, val){
  return (inputType(f,val)==='datetime-local' && _isoDate(val)) ? val+'T00:00' : val;
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
  // A picker renders its own dd/mm/yyyy hint and ignores placeholder, so carry the PT clarifying
  // question on title= instead — the question stays reachable on hover either way.
  const ityp=inputType(f,val);
  const hint=_PICKERS[ityp] ? 'title="'+esc(f.q||'')+'"'
                            : 'placeholder="'+esc(f.q||'…')+'"';
  return '<div class="frow '+stcls+(conflicted?' conflict':'')+'" data-addr="'+esc(addr)+'">'
    +'<label>'+esc(f.label)+'</label>'
    +'<div class="fctl"><input type="'+ityp+'" class="finput" data-addr="'+esc(addr)+'" '
    +'value="'+esc(pickerValue(f,val))+'" '
    +hint+' autocomplete="off" spellcheck="false"/>'+chanChip(addr)+badge+cw+'</div>'
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
    const d=await getJSON('/api/projects/'+selected.project_id+'/draft');
    const asks=d.askables||[];
    const reasons=d.reject_reasons||[];
    draft={to:d.to||'', subject:d.subject||'', askables:asks,
           purposes:d.purposes||[], reasons:reasons, purpose:d.purpose||'ask',
           selected:new Set(asks.filter(a=>a.default).map(a=>a.key)), custom:[],  // questions
           reason:reasons[0]||'', reasonNote:'',                                  // reason
           content:'', facts:[],                                                  // text + protected tokens
           lang:'pt',                                                             // output language (ADR-032)
           body:d.body||'', dirty:false,
           ai:null, aiBusy:false};   // ai = the polish result, only ever set by an explicit click
    renderComposer();
  }catch(e){ box.innerHTML='<div class="hint2" style="color:var(--red)">falhou ao preparar o email</div>'; }
}

/* Per-purpose hints for the free-text box (ADR-031). The user writes the substance (costs, dates);
   the AI polish only improves the prose and is barred from touching any number (missing_values). */
const CONTENT_PLACEHOLDER = {
  quote:'Ex.: 2x placa inox 2mm, corte laser — 120€\ngravação logótipo — 40€\nTOTAL — 160€\nValidade 30 dias · Prazo 10 dias úteis',
  payment:'Ex.: Sinal de 50% — 80€\nIBAN PT50 …\nRestante 80€ na entrega',
  approval:'Ex.: Segue a arte final em anexo. Confirmam as cores e o texto antes de produzirmos?',
  deadline:'Ex.: Novo prazo previsto: 30/09. Motivo: rutura de material no fornecedor.',
  ready:'Ex.: O trabalho está pronto. Recolha na oficina de 2ª a 6ª, das 9h às 18h.',
};

function purposeKind(id){ const p=(draft.purposes||[]).find(x=>x.id===id); return p?p.input_kind:'questions'; }

/* the missing must-haves checklist (the original `ask` input) + custom questions */
function askInputHTML(){
  const d=draft;
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
  const empty=(!d.askables.length&&!d.custom.length)
    ? '<div class="hint2"><span class="ready">✓ sem obrigatórios em falta</span> — adiciona uma pergunta ou muda o tipo de email.</div>' : '';
  return empty
    +grp('must','Em falta',must)+grp('should','Opcionais',should)+custom+grp('intern','Internos',intern)
    +'<button class="addq" id="_addq">+ pergunta personalizada</button>';
}

/* reject: a reason chosen from the editable list + an optional free note */
function reasonInputHTML(){
  const d=draft;
  const opts=(d.reasons||[]).map(r=>'<option value="'+esc(r)+'"'+(r===d.reason?' selected':'')+'>'+esc(r)+'</option>').join('');
  return '<div class="askgrp"><div class="gl">Motivo da recusa</div>'
    +'<select id="_reason" class="reasonsel">'+opts+'</select>'
    +'<textarea id="_reasonnote" class="notebox" spellcheck="false" placeholder="Nota opcional para o cliente (ex.: alternativa, prazo futuro)…">'+esc(d.reasonNote||'')+'</textarea></div>';
}

/* quote / payment / approval / deadline / ready: a free-text box the user writes; the AI refines it */
function contentInputHTML(){
  const d=draft, ph=CONTENT_PLACEHOLDER[d.purpose]||'Escreve o conteúdo do email…';
  return '<div class="askgrp"><div class="gl">Conteúdo — escreves tu; a IA melhora o texto mas nunca altera os números</div>'
    +'<textarea id="_content" class="notebox" spellcheck="false" placeholder="'+esc(ph)+'">'+esc(d.content||'')+'</textarea></div>';
}

/* the money/number/date tokens the server extracted, echoed so the user sees exactly what the
   AI polish is barred from changing. Own container id so typing can patch it without a re-render. */
function factsInner(){
  const f=draft.facts||[];
  return f.length?('valores protegidos: '+f.map(v=>'<span class="fv">'+esc(v)+'</span>').join(' ')):'';
}

function composerHTML(){
  const d=draft;
  const kind=purposeKind(d.purpose);
  const inputArea=kind==='reason'?reasonInputHTML():kind==='text'?contentInputHTML():askInputHTML();
  const chip=(kind==='reason'||kind==='text')?'<div id="_factschip" class="factschip">'+factsInner()+'</div>':'';
  const psel='<div class="psel"><label>Tipo de email</label><select id="_purpose">'
    +(d.purposes||[]).map(p=>'<option value="'+esc(p.id)+'"'+(p.id===d.purpose?' selected':'')+'>'+esc(p.label)+'</option>').join('')
    +'</select></div>';
  const dirty=d.dirty?'<span class="dirty">✎ editado <button class="regen" id="_regenq">Regenerar</button></span>':'';
  return '<div class="psec"><h3>Email para o cliente <span class="c">escolhe o tipo, revê e copia</span></h3>'
    +'<div class="cmp">'
    +'<div class="hdr"><div class="to">Para: <b>'+esc(d.to||'sem email')+'</b></div>'
    +'<div class="subj"><label>Assunto</label><input id="_subj" value="'+esc(d.subject)+'" autocomplete="off" spellcheck="false"/></div></div>'
    +psel
    +inputArea
    +chip
    +'<div class="draftbox"><div class="dl"><h4>Rascunho</h4>'+dirty+'</div>'
    +'<textarea id="_draftbody" spellcheck="false">'+esc(d.body)+'</textarea></div>'
    +aiBarHTML()+aiResultHTML()
    +'<div class="qmail"><button class="act-btn" id="_copyq">Copiar email</button>'
    +'<button class="act-btn" id="_openq">Abrir no email</button></div>'
    +'</div></div>';
}

/* ── AI polish (ADR-027) ───────────────────────────────────────────────────
   Sits ON TOP of the deterministic draft, never replacing it: the server rewrites the same body
   through the LLM using the email thread + confirmed facts, and re-checks that every ticked question
   survived. The result is shown BESIDE the draft — adopting it is a second, separate click, so the
   deterministic text is never silently swapped out from under the user (ADR-013). Button only: no
   page-load, checkbox-toggle or keystroke path reaches this. */
function aiBarHTML(){
  const d=draft, busy=d.aiBusy, nonPt=(d.lang&&d.lang!=='pt');
  const hint=nonPt
    ? 'traduz para '+esc(langLabel(d.lang))+' + melhora (os números ficam intactos; gasta tokens, 1 chamada)'
    : 'lê a thread do cliente + os factos confirmados (gasta tokens, 1 chamada)';
  return '<div class="aibar">'
    +'<select id="_ailang" aria-label="Idioma do email"'+(busy?' disabled':'')+'>'
    +  LANGS.map(l=>'<option value="'+l[0]+'"'+(l[0]===d.lang?' selected':'')+'>'+esc(l[1])+'</option>').join('')
    +'</select>'
    +'<select id="_aitier" aria-label="Custo do melhoramento"'+(busy?' disabled':'')+'>'
    +  TIERS.map(t=>'<option value="'+t[0]+'"'+(t[0]===aiTier?' selected':'')+'>'+esc(t[1])+'</option>').join('')
    +'</select>'
    +'<button class="act-btn ai" id="_aibtn"'+(busy?' disabled':'')+'>'
    +  (busy?'A escrever…':(nonPt?'✨ Traduzir e melhorar':'✨ Melhorar com IA'))+'</button>'
    +'<span class="hint2">'+hint+'</span>'
    +'</div>';
}

function aiResultHTML(){
  const a=draft.ai;
  if(!a) return '';
  if(a.error) return '<div class="airesult bad"><div class="aih">✕ não deu</div>'
    +'<div class="hint2">'+esc(a.error)+'</div></div>';
  // The one failure that matters: for a question-email a dropped question means the client is never
  // asked; for a money/text-email an altered number is a wrong commitment. Both block the version.
  const isVal=(purposeKind(draft.purpose)!=='questions');
  const n=(a.missing||[]).length, one=(n===1);
  const kept=isVal?(a.n_facts||0):(a.n_questions||0);
  // For a translated (non-PT) email the server checks ONLY the numbers — a translated sentence can't
  // be verified word-for-word — so we mark it review-required and never claim question coverage.
  const trans=a.translated
    ? '<div class="aiwarn">🌐 traduzido para <b>'+esc(langLabel(a.lang))+'</b> — <b>revê o texto</b>: '
      +'a tradução das frases não é verificada palavra a palavra (só os números e datas são).</div>'
    : '';
  let miss;
  if(a.translated){
    miss=n
      ? '<div class="aiwarn">⚠ o modelo alterou ou removeu '+n+' valor'+(one?'':'es')
        +' (preços/números/datas) — <b>não uses esta versão</b>:<ul>'
        +a.missing.map(q=>'<li>'+esc(q)+'</li>').join('')+'</ul></div>'
      : '<div class="aiok">✓ '+(kept===0?'tradução pronta':(kept===1?'o valor foi mantido'
          :('os '+kept+' valores foram mantidos')))+'</div>';
  } else {
    miss=n
      ? '<div class="aiwarn">⚠ o modelo '+(isVal?'alterou ou removeu ':'não manteve ')+n+' '
        +(isVal?('valor'+(one?'':'es')+' (preços/números/datas)'):('pergunta'+(one?'':'s')))
        +' — <b>não uses esta versão</b>'+(isVal?'':' sem as acrescentar')+':<ul>'
        +a.missing.map(q=>'<li>'+esc(q)+'</li>').join('')+'</ul></div>'
      : '<div class="aiok">✓ '+(isVal
          ?(kept===0?'versão pronta (sem números a proteger)':kept===1?'o valor foi mantido':('os '+kept+' valores foram mantidos'))
          :(kept===1?'a pergunta foi mantida':('as '+kept+' perguntas foram mantidas')))+'</div>';
  }
  return '<div class="airesult"><div class="aih">Versão da IA'
    +'<span class="c"> · leu '+a.used_thread+' mensagem'+(a.used_thread===1?'':'s')
    +' e '+a.used_facts+' facto'+(a.used_facts===1?'':'s')+(a.tier?' · tier '+esc(a.tier):'')
    +(a.translated?' · '+esc(langLabel(a.lang)):'')+'</span></div>'
    +trans+miss
    +'<textarea id="_aibody" spellcheck="false">'+esc(a.body)+'</textarea>'
    +'<div class="qmail"><button class="act-btn accept" id="_aiuse">Usar esta versão</button>'
    +'<button class="act-btn" id="_aidrop">Descartar</button></div></div>';
}

/* the full per-purpose input set — only the fields the purpose uses are read server-side */
function draftPayload(){
  return {purpose:draft.purpose, selected:[...draft.selected], custom:draft.custom,
          reason:draft.reason, reason_note:draft.reasonNote, content:draft.content};
}

async function polishDraft(){
  if(!draft||!selected||draft.aiBusy) return;
  draft.aiBusy=true; draft.ai=null; renderComposer();
  announce('a melhorar o email com IA');
  try{
    draft.ai=await post('/api/projects/'+encodeURIComponent(selected.project_id)+'/draft/polish',
      Object.assign(draftPayload(),{tier:aiTier, lang:draft.lang}));
  }catch(e){ draft.ai={error:(e&&e.status===0)?'sem resposta do servidor'
                                             :('HTTP '+((e&&e.status)||'?'))}; }
  finally{
    draft.aiBusy=false; renderComposer();
    const a=draft.ai;
    toast(a&&!a.error?'versão da IA pronta':'melhoramento falhou');
    announce(a&&!a.error?'versão da IA pronta':'melhoramento falhou');
  }
}

/* Adopting the AI version = the user's own edit of the draft: it becomes the body and is marked
   dirty, so a later checkbox toggle offers Regenerar instead of silently overwriting their choice. */
function useAIDraft(){
  const a=draft.ai; if(!a||a.error) return;
  const ta=$('#_aibody');
  draft.body=(ta?ta.value:a.body); draft.dirty=true; draft.ai=null;
  renderComposer(); toast('versão da IA aplicada ao rascunho');
}

function renderComposer(){ const box=$('#_ask'); if(box) box.innerHTML=composerHTML(); }

/* ── Descritivo composer (ADR-030) ─────────────────────────────────────────
   The proposta/fatura DESCRIÇÃO text, assembled server-side from the CONFIRMED spec fields in the
   corpus-average style. Deterministic first; the optional AI polish sits on top and is fact-checked,
   exactly like the email composer. Loaded lazily when the tab opens (loadDescription). */
let descr = null;

async function loadDescription(){
  const box=$('#_desc'); if(!box||!selected) return;
  box.innerHTML='<div class="hint2">a preparar descritivo…</div>';
  try{
    const d=await getJSON('/api/projects/'+encodeURIComponent(selected.project_id)+'/description');
    descr={body:d.body||'', gaps:d.gaps||[], unconfirmed:d.unconfirmed||[],
           complete:!!d.complete, nFacts:d.n_facts||0, ai:null, aiBusy:false};
  }catch(e){ box.innerHTML='<div class="hint2">falhou a preparação do descritivo</div>'; return; }
  renderDescription();
}

function renderDescription(){ const box=$('#_desc'); if(box) box.innerHTML=descriptionHTML(); }

function descriptionHTML(){
  const d=descr; if(!d) return '';
  // Gaps are un-sendable holes; unconfirmed are candidates the model drafted but nobody ticked.
  const gapWarn=d.gaps.length
    ? '<div class="aiwarn">⚠ '+d.gaps.length+' campo'+(d.gaps.length===1?'':'s')+' por confirmar — '
      +'os marcadores <code>[[…?]]</code> têm de ser resolvidos na Especificação antes de usar:'
      +'<ul>'+d.gaps.map(g=>'<li>'+esc(g)+'</li>').join('')+'</ul></div>'
    : '<div class="aiok">✓ descritivo completo — '+d.nFacts+' facto'+(d.nFacts===1?'':'s')+' confirmado'+(d.nFacts===1?'':'s')+'</div>';
  const unconf=d.unconfirmed.length
    ? '<div class="hint2">há sugestões da IA por confirmar ('+d.unconfirmed.map(esc).join(', ')
      +') — confirma-as na Especificação para entrarem no descritivo.</div>' : '';
  return '<div class="psec"><h3>Descritivo <span class="c">o texto da coluna DESCRIÇÃO da proposta/fatura — estilo médio da casa</span></h3>'
    +'<div class="cmp">'+gapWarn+unconf
    +'<div class="draftbox"><div class="dl"><h4>Rascunho</h4></div>'
    +'<textarea id="_descbody" spellcheck="false">'+esc(d.body)+'</textarea></div>'
    +descAiBarHTML()+descAiResultHTML()
    +'<div class="qmail"><button class="act-btn" id="_desccopy">Copiar descritivo</button></div>'
    +'</div></div>';
}

function descAiBarHTML(){
  const busy=descr.aiBusy;
  return '<div class="aibar">'
    +'<select id="_descaitier" aria-label="Custo do melhoramento"'+(busy?' disabled':'')+'>'
    +  TIERS.map(t=>'<option value="'+t[0]+'"'+(t[0]===aiTier?' selected':'')+'>'+esc(t[1])+'</option>').join('')
    +'</select>'
    +'<button class="act-btn ai" id="_descaibtn"'+(busy?' disabled':'')+'>'
    +  (busy?'A escrever…':'✨ Melhorar com IA')+'</button>'
    +'<span class="hint2">redige a frase mantendo os factos palavra por palavra (gasta tokens, 1 chamada)</span>'
    +'</div>';
}

function descAiResultHTML(){
  const a=descr.ai; if(!a) return '';
  if(a.error) return '<div class="airesult bad"><div class="aih">✕ não deu</div>'
    +'<div class="hint2">'+esc(a.error)+'</div></div>';
  // A dropped/altered fact is the failure that matters — these are priced legal documents.
  const miss=(a.missing||[]).length
    ? '<div class="aiwarn">⚠ o modelo não manteve '+a.missing.length+' facto'+(a.missing.length===1?'':'s')
      +' — <b>não uses esta versão</b>:<ul>'+a.missing.map(q=>'<li>'+esc(q)+'</li>').join('')+'</ul></div>'
    : '<div class="aiok">✓ todos os factos foram mantidos palavra por palavra</div>';
  const dropped=(a.dropped_gaps||0)>0
    ? '<div class="aiwarn">⚠ o modelo apagou '+a.dropped_gaps+' marcador de lacuna — reveja, uma lacuna invisível é pior.</div>' : '';
  return '<div class="airesult"><div class="aih">Versão da IA'
    +(a.tier?'<span class="c"> · tier '+esc(a.tier)+'</span>':'')+'</div>'
    +miss+dropped
    +'<textarea id="_descaibody" spellcheck="false">'+esc(a.body)+'</textarea>'
    +'<div class="qmail"><button class="act-btn accept" id="_descaiuse">Usar esta versão</button>'
    +'<button class="act-btn" id="_descaidrop">Descartar</button></div></div>';
}

async function polishDescription(){
  if(!descr||!selected||descr.aiBusy) return;
  descr.aiBusy=true; descr.ai=null; renderDescription();
  announce('a melhorar o descritivo com IA');
  try{
    descr.ai=await post('/api/projects/'+encodeURIComponent(selected.project_id)+'/description/polish',
      {tier:aiTier});
  }catch(e){ descr.ai={error:(e&&e.status===0)?'sem resposta do servidor'
                                             :('HTTP '+((e&&e.status)||'?'))}; }
  finally{
    descr.aiBusy=false; renderDescription();
    const a=descr.ai;
    toast(a&&!a.error?'versão da IA pronta':'melhoramento falhou');
    announce(a&&!a.error?'versão da IA pronta':'melhoramento falhou');
  }
}

/* Adopting the AI version = the user's own edit: it becomes the body. */
function useAIDescription(){
  const a=descr.ai; if(!a||a.error) return;
  const ta=$('#_descaibody');
  descr.body=(ta?ta.value:a.body); descr.ai=null;
  renderDescription(); toast('versão da IA aplicada ao descritivo');
}

/* Rebuild the generated body from the current purpose + inputs and re-render the whole composer.
   Used when the STRUCTURE changes (purpose switch, checkbox toggle, reason pick, custom question).
   While dirty we keep the user's manual edits and just re-render (Regenerar is their way back). */
async function resyncDraft(){
  if(!draft||!selected) return;
  if(draft.dirty){ renderComposer(); return; }
  try{
    const r=await post('/api/projects/'+selected.project_id+'/draft', draftPayload());
    draft.body=r.body; draft.facts=r.facts||[]; renderComposer();
  }catch(e){ toast(S.falhou); }
}

/* Rebuild only the draft body + the protected-values chip IN PLACE (no re-render), so the
   free-text box the user is typing into keeps focus/caret. Used by the debounced input handler. */
async function resyncBody(){
  if(!draft||!selected||draft.dirty) return;
  try{
    const r=await post('/api/projects/'+selected.project_id+'/draft', draftPayload());
    draft.body=r.body; draft.facts=r.facts||[];
    const ta=$('#_draftbody'); if(ta) ta.value=draft.body;
    const chip=$('#_factschip'); if(chip) chip.innerHTML=factsInner();
    // a live edit invalidates any AI version shown from a previous state — drop it, no re-render
    if(draft.ai){ draft.ai=null; const air=$('#_detail').querySelector('.airesult'); if(air) air.remove(); }
  }catch(e){ /* transient: the next keystroke retries */ }
}
let _resyncT=null;
function debouncedResyncBody(){ clearTimeout(_resyncT); _resyncT=setTimeout(resyncBody,300); }

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

/* Registar — deterministic capture of off-email knowledge. STORED verbatim, never rewritten by a
   model. Since ADR-026 a model may LATER *read* what is stored here, but only when the user clicks
   Reprocessar in Origem — so the placeholder promises "sem IA" about the write, not about all time,
   which is the honest version of the older "guardado tal e qual, sem IA". */
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
    +'<textarea id="_captext" placeholder="O que aconteceu? Conclusão da chamada, decisão, opinião… (guardado tal e qual — a IA só lê isto se pedires Reprocessar)" spellcheck="false"></textarea>'
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
  catch(e){ toast(S.falhou); }
}
async function toggleOwner(name){
  const ow=new Set(selected.owners||[]);
  ow.has(name)?ow.delete(name):ow.add(name);
  await setOwners([...ow]); ownerPicker();        // keep the picker open with refreshed checks
}
async function addRosterOwner(){
  const nm=prompt('Novo dono (nome):'); if(!nm||!nm.trim()) return;
  try{ const r=await post('/api/roster',{name:nm.trim()}); roster=r.roster||roster; await toggleOwner(nm.trim()); }
  catch(e){ toast(S.falhou); }
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
  catch(e){ toast(S.falhou); }
}

async function loadParticipants(){
  const box=$('#_participants'); if(!box||!selected) return;
  try{
    const d=await getJSON('/api/projects/'+encodeURIComponent(selected.project_id)+'/participants');
    const ps=d.participants||[];
    box.innerHTML=ps.length?('<span class="plbl">Contribuíram:</span>'
      +ps.map(p=>'<span class="pcontrib" title="'+p.contributions+' contribuição(ões)'+(p.channels&&p.channels.length?' · '+esc(p.channels.join(', ')):'')+'">@'+esc(p.name)+' <b>'+p.contributions+'</b></span>').join('')):'';
  }catch(e){ box.innerHTML=''; }
}

function detailHTML(){
  const p=selected.project, rd=selected.readiness||{};
  const job=selected.job_fields||{}, items=selected.items||[], customs=selected.custom_fields||{};
  const stages=STAGES.map(s=>'<span class="st'+(p.stage===s?' on':'')+(TERMINAL.has(s)&&p.stage===s?' terminal':'')+'" data-stage="'+s+'">'+esc(STAGEpt[s]||s)+'</span>').join('');
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
  const nev=selected.n_events||0;
  const scope=nthreads+' email'+(nthreads===1?'':'s')+(nev?' + '+nev+' registo'+(nev===1?'':'s')+' da linha do tempo':'');
  const origem='<div class="rexbar">'
    +'<select id="_retier" aria-label="Custo da re-extração">'
    +   TIERS.map(t=>'<option value="'+t[0]+'"'+(t[0]===reTier?' selected':'')+'>'+esc(t[1])+'</option>').join('')
    +'</select>'
    +'<button class="item-rm" id="_rexbtn">Reprocessar tudo com IA</button>'
    +'<span class="grow"></span>'
    +'<button class="item-rm" id="_attachbtn">+ ligar email</button></div>'
    +'<div class="hint2 rexnote">Volta a ler <b>'+esc(scope)+'</b> com o LLM (gasta tokens: '
    +'uma chamada por email + uma por registo). Nunca apaga nem substitui um campo que tu confirmaste.</div>'
    +'<div id="_rexres"></div>'
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

  /* Descritivo panel — the proposta/fatura DESCRIÇÃO text (ADR-030), a second distinct outbound task. */
  const descTab='<div class="ppanel hidden" data-panel="descritivo"><div id="_desc"><div class="hint2">a preparar descritivo…</div></div></div>';

  const tabs='<div class="ptabs">'
    +'<button class="ptab-btn on" data-tab="espec">Especificação</button>'
    +'<button class="ptab-btn" data-tab="origem">Origem'+(nthreads?' <span class="bdg">'+nthreads+'</span>':'')+'</button>'
    +'<button class="ptab-btn" data-tab="timeline">Linha do tempo</button>'
    +'<button class="ptab-btn" data-tab="email">Email ao cliente'+(nmiss?' <span class="bdg warn">'+nmiss+'</span>':'')+'</button>'
    +'<button class="ptab-btn" data-tab="descritivo">Descritivo</button>'
    +'<button class="ptab-btn" data-tab="registar">Registar</button></div>';

  return '<button class="hbtn" id="_backbtn" style="margin-bottom:14px">← Projetos</button>'
    +'<h2 style="margin:0 0 8px;font-size:20px;letter-spacing:-.01em">'+esc(p.title)
    +' <button id="_ptitle" class="prename" title="mudar o nome do projeto — o assunto do email é identidade, não um nome">✎</button></h2>'
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
    +descTab
    +'<div class="ppanel hidden" data-panel="registar">'+captureHTML()+'</div>';
}

/* ── tab strip (show/hide; lazy-load the timeline; reflect Registar in the URL) ─────────── */
function showTab(name){
  const root=$('#_detail'); if(!root) return;
  root.querySelectorAll('.ptab-btn').forEach(b=>b.classList.toggle('on', b.dataset.tab===name));
  root.querySelectorAll('.ppanel').forEach(pl=>pl.classList.toggle('hidden', pl.dataset.panel!==name));
  if(name==='timeline') loadTimeline();
  if(name==='descritivo') loadDescription();
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
    const d=await getJSON('/api/projects/'+encodeURIComponent(pid)+'/timeline');
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
  // a field save changes the gaps → refresh the composer's prompt list, but only for the gap-driven
  // `ask` purpose, and only when the user hasn't started hand-editing (never wipe their wording or an
  // in-progress quote/reject that doesn't depend on the gap list at all).
  if(draft&&!draft.dirty&&draft.purpose==='ask') loadDraft();
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
    const all=[]; const attBlocks=[];
    for(const root of roots){
      const d=await getJSON('/api/thread/'+encodeURIComponent(root));
      if(d&&d.messages) all.push(...d.messages);
      if(d&&d.attachments) attBlocks.push(d.attachments);
    }
    // provenance: {field_addr: message_id} — shows which message supplied each spec field
    const prov=selected.provenance||{};
    const html=all.length
      ? msgThreadHTML(all, {provenance: prov, attachments: attMerge(attBlocks)})
      : '<div class="hint2">sem mensagens neste projeto</div>';
    _srcCache[pid]=html;
    const b2=$('#_origem'); if(b2){b2.innerHTML=html; msgWireQuoteToggles(b2);}
  }catch(e){
    const b2=$('#_origem');
    if(b2) b2.innerHTML='<div class="hint2" style="color:var(--red)">falhou ao carregar contexto</div>';
  }
}

/* ── scoped re-extraction (ADR-025 §4) ────────────────────────────────────
   Re-reads only this project's emails with the chosen tier. The whole point of the result block
   below is that a FAILURE stops being invisible: a message whose spec_error is set is listed by
   name, in red, instead of quietly looking like a thin email that had nothing to extract. */
function rexResultHTML(d){
  const msgs=d.messages||[], c=d.counts||{}, ev=d.events||{};
  const bad=msgs.filter(m=>m.spec_error), evbad=ev.failed||[], evapp=ev.applied||[];
  const nb=c.built||0, nk=c.kept||0, nev=ev.read||0;
  let head='<b>'+nb+'</b> '+(nb===1?'mensagem re-extraída':'mensagens re-extraídas')
    +' · <b>'+nk+'</b> '+(nk===1?'intacta':'intactas');
  // The timeline half (ADR-026) is reported separately: "read" is what was PAID for, "applied" is
  // what actually changed — a note can be read in full and still legitimately yield no spec field.
  if(nev) head+=' · <b>'+nev+'</b> registo'+(nev===1?'':'s')+' lido'+(nev===1?'':'s')
    +' → <b>'+evapp.length+'</b> campo'+(evapp.length===1?'':'s');
  head+=(d.tier?' · tier '+esc(d.tier):'');
  const errs=bad.length
    ? '<ul class="rexerrs">'+bad.map(m=>'<li><code>'+esc(m.message_id)+'</code> — '+esc(m.spec_error)+'</li>').join('')+'</ul>'
    : '';
  const everrs=evbad.length
    ? '<ul class="rexerrs">'+evbad.map(e=>'<li>registo '+esc(e.kind||'')+' #'+esc(String(e.rowid))+' — '+esc(e.error||'')+'</li>').join('')+'</ul>'
    : '';
  const nfail=bad.length+evbad.length;
  const cls=nfail?'rexres bad':'rexres';
  const title=nfail?('⚠ '+nfail+' falha'+(nfail===1?'':'s')+' na extração'):'✓ reprocessamento concluído';
  return '<div class="'+cls+'"><div class="rexh">'+title+'</div><div class="rexsub">'+head+'</div>'+errs+everrs+'</div>';
}

async function reextract(){
  if(!selected||reBusy) return;
  const pid=selected.project_id, box=$('#_rexres');
  reBusy=true;
  const btn=$('#_rexbtn'); if(btn){btn.disabled=true;btn.textContent='A reprocessar…';}
  if(box) box.innerHTML='<div class="rexres"><div class="rexsub">a re-ler os emails e os registos com o LLM…</div></div>';
  announce('re-extração iniciada');
  try{
    const d=await post('/api/projects/'+encodeURIComponent(pid)+'/reextract',{tier:reTier});
    if(d.project) selected=d.project;
    delete _srcCache[pid];          // provenance moved → the cached thread render is stale
    renderDetail();
    const b2=$('#_rexres'); if(b2) b2.innerHTML=rexResultHTML(d);
    showTab('origem');
    toast(d.ok?'re-extração concluída':'re-extração com falhas');
    announce(d.ok?'re-extração concluída':'re-extração com falhas');
  }catch(err){
    /* 409 = a sync holds the lock. Not a failure, and telling someone their re-extraction failed
       when it merely has to wait sends them to re-run an LLM job that would have worked. */
    const conflict=(err&&err.status===409);
    const b2=$('#_rexres');
    if(b2) b2.innerHTML='<div class="rexres bad"><div class="rexh">'
      +(conflict?'sync em curso':'falhou')+'</div><div class="rexsub">'
      +(conflict?'espera que a sincronização termine e tenta outra vez.'
               :esc((err&&err.status===0)?'sem resposta do servidor':('HTTP '+((err&&err.status)||'?'))))
      +'</div></div>';
    toast(conflict?S.syncEmCurso:S.falhou);
  }finally{
    reBusy=false;
    const b3=$('#_rexbtn'); if(b3){b3.disabled=false;b3.textContent='Reprocessar tudo com IA';}
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
    {kind:'ação',label:'Início',run:()=>{location.href='/';}},
    {kind:'ação',label:'Fila',run:()=>{location.href='/fila';}},
    {kind:'ação',label:'Contrapartes',run:()=>{location.href='/contrapartes';}},
    {kind:'ação',label:'Para ti',run:()=>{location.href='/para-ti';}},
    {kind:'ação',label:'Capturas',run:()=>{location.href='/capturas';}},
    {kind:'ação',label:'Novo projeto',run:promptNew},
    {kind:'ação',label:'Registar conhecimento',run:()=>{ if(selected) showTab('registar'); else toast('abre um projeto primeiro'); }},
    {kind:'ação',label:'Re-extrair este projeto',sub:'gasta tokens',run:()=>{ if(selected) reextract(); else toast('abre um projeto primeiro'); }},
    {kind:'ação',label:'Admin',run:()=>{location.href='/admin';}},
    {kind:'ação',label:S.actSync,run:syncNow},
  ];
  projects.forEach(p=>base.push({kind:'projeto',label:p.title,sub:p.stage,run:()=>loadDetail(p.project_id)}));
  return q?base.filter(it=>(it.label+' '+(it.sub||'')+' '+it.kind).toLowerCase().includes(q)):base;
}

function promptNew(){
  const t=prompt('Título do projeto:'); if(!t||!t.trim()) return;
  post('/api/projects',{title:t.trim()}).then(r=>{
    return getJSON('/api/projects').then(list=>{projects=list;renderList();toast('criado: '+t);});
  }).catch(()=>toast(S.falhou));
}

/* ── list selection ───────────────────────────────────────────────────── */
$('#_list').addEventListener('click',e=>{
  const row=e.target.closest('.row'); if(!row) return;
  focus=parseInt(row.dataset.i,10); loadDetail(row.dataset.pid);
});

/* ── detail: save a field on change (blur/Enter), keep the user's place ── */
$('#_detail').addEventListener('change', async e=>{
  // composer: the purpose selector — switching rebuilds the input area + draft for the new kind.
  // A hand-edited draft is confirmed first so we never wipe wording without asking (ADR-013 spirit).
  const psel=e.target.closest('#_purpose');
  if(psel&&draft){
    const np=psel.value; if(np===draft.purpose) return;
    if(draft.dirty && !confirm('Mudar o tipo de email substitui o rascunho atual. Continuar?')){
      psel.value=draft.purpose; return; }
    draft.purpose=np; draft.dirty=false; draft.ai=null; resyncDraft(); return; }
  // reject: a different reason → rebuild the body in place (keeps the note textarea's focus)
  const rsel=e.target.closest('#_reason');
  if(rsel&&draft){ draft.reason=rsel.value; resyncBody(); return; }
  // composer: a prompt checkbox toggled → update selection + re-assemble the draft
  const cb=e.target.closest('.ask-opt input[data-key]');
  if(cb&&draft){ const k=cb.dataset.key;
    if(cb.checked) draft.selected.add(k); else draft.selected.delete(k);
    resyncDraft(); return; }
  // tier pickers — view preferences; nothing is POSTed until the matching button is clicked
  const tsel=e.target.closest('#_retier');
  if(tsel){ reTier=tsel.value; return; }
  const asel=e.target.closest('#_aitier');
  if(asel){ aiTier=asel.value; return; }
  // output language for the email (ADR-032) — a view pref; nothing is POSTed until the button, but
  // re-render so the AI bar's button/hint reflect PT vs a translation.
  const lsel=e.target.closest('#_ailang');
  if(lsel&&draft){ draft.lang=lsel.value; renderComposer(); return; }
  const dsel=e.target.closest('#_descaitier');
  if(dsel){ aiTier=dsel.value; return; }
  const inp=e.target.closest('.finput'); if(!inp||!selected) return;
  const addr=inp.dataset.addr, value=inp.value.trim();
  try{
    const d=await post('/api/projects/'+selected.project_id+'/field',{field:addr,value});
    selected=d; markRow(addr,value); refreshSummary();
  }catch(err){ toast(S.falhou); }
});

/* ── date fields: the whole box opens the picker, not just the glyph ─────
   By default only the ~14px calendar indicator opens the picker; the rest of the input looks
   clickable but merely parks a cursor on a segment, which reads as broken. showPicker() requires
   user activation — a real click supplies it. Registered as its OWN listener (not folded into the
   main click handler below) because that one early-returns on a dozen branches before it would ever
   reach a field. Anything that can't open a picker (older browser, cross-origin frame, the text
   fallback for a non-ISO value) is left alone and stays keyboard-typeable. */
$('#_detail').addEventListener('click', e=>{
  const d=e.target.closest('.finput[type=date],.finput[type=datetime-local]');
  if(!d||d.disabled||d.readOnly||typeof d.showPicker!=='function') return;
  try{ d.showPicker(); }catch(_){}   // NotAllowedError / SecurityError → plain typing still works
});

/* composer: hand-editing the draft marks it dirty (keep edits; offer Regenerar). We mutate the
   DOM in place rather than re-render, so the textarea keeps focus while typing. */
$('#_detail').addEventListener('input', e=>{
  if(!draft) return;
  // the free-text inputs (reason note / content) are what the draft is BUILT from, not a hand-edit:
  // update state and rebuild the body in place (debounced), WITHOUT marking dirty.
  if(e.target.id==='_content'){ draft.content=e.target.value; debouncedResyncBody(); return; }
  if(e.target.id==='_reasonnote'){ draft.reasonNote=e.target.value; debouncedResyncBody(); return; }
  // hand-editing the assembled draft itself marks it dirty (keep edits; offer Regenerar). We mutate
  // the DOM in place rather than re-render, so the textarea keeps focus while typing.
  if(e.target.id!=='_draftbody') return;
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
  /* rename: the title a project is born with is the raw email subject — machine identity */
  if(e.target.closest('#_ptitle')){
    const nm=prompt('Nome do projeto:', selected.project.title||'');
    if(nm===null||!nm.trim()) return;
    try{
      await post('/api/projects/'+selected.project_id+'/rename',{title:nm.trim()});
      selected.project.title=nm.trim();
      const pl=projects.find(x=>x.project_id===selected.project_id); if(pl) pl.title=nm.trim();
      renderDetail(); toast('nome guardado');
    }catch(err){ toast(S.falhou); }
    return; }
  const st=e.target.closest('.pstage .st');
  if(st){ const stage=st.dataset.stage;
    // CANCELLED/LOST open an inline close-out form (party + reason) instead of posting immediately.
    if(CLOSED_STAGES.has(stage) && selected.project.stage!==stage){ openCloseout(stage); e.stopPropagation(); return; }
    try{await post('/api/projects/'+selected.project_id+'/stage',{stage});
      selected=await getJSON('/api/projects/'+selected.project_id); renderDetail();}
    catch(err){toast(S.falhou);} return; }
  /* owners (multi) */
  if(e.target.closest('#_ownadd')){ ownerPicker(); e.stopPropagation(); return; }
  const orm=e.target.closest('[data-own-rm]');
  if(orm){ await setOwners((selected.owners||[]).filter(o=>o!==orm.dataset.ownRm)); return; }
  /* close-out form */
  const coc=e.target.closest('#_coparty .chip');
  if(coc){ _coParty=coc.dataset.party; coc.parentElement.querySelectorAll('.chip').forEach(x=>x.classList.toggle('on',x===coc)); return; }
  if(e.target.closest('#_coconfirm')){ await confirmCloseout(); return; }
  if(e.target.closest('#_cocancel')){ const b=$('#_closeform'); if(b){b.classList.add('hidden');b.innerHTML='';} return; }
  if(e.target.closest('#_rexbtn')){ await reextract(); return; }
  if(e.target.closest('#_attachbtn')){
    const ref=prompt('Cola o thread_root ou message_id do email a ligar:'); if(!ref||!ref.trim()) return;
    try{ selected=await post('/api/projects/'+selected.project_id+'/attach',{ref:ref.trim()});
      delete _srcCache[selected.project_id]; renderDetail(); toast('email ligado'); }
    catch(err){ toast(S.falhou); } return; }
  if(e.target.closest('#_additem')){ try{
    selected=await post('/api/projects/'+selected.project_id+'/item/add',{}); renderDetail();}
    catch(err){toast(S.falhou);} return; }
  const rm=e.target.closest('.item-rm');
  if(rm){ try{ selected=await post('/api/projects/'+selected.project_id+'/item/remove',{index:parseInt(rm.dataset.idx,10)}); renderDetail();}
    catch(err){toast(S.falhou);} return; }
  /* ── composer actions ─────────────────────────────────────────────── */
  if(e.target.closest('#_addq')&&draft){
    const q=prompt('Pergunta para o cliente:'); if(!q||!q.trim()) return;
    draft.custom.push(q.trim()); resyncDraft(); return; }
  const crm=e.target.closest('.custq .rm');
  if(crm&&draft){ draft.custom.splice(parseInt(crm.dataset.ci,10),1); resyncDraft(); return; }
  if(e.target.closest('#_regenq')&&draft){
    draft.dirty=false;
    try{ const r=await post('/api/projects/'+selected.project_id+'/draft', draftPayload());
      draft.body=r.body; draft.facts=r.facts||[]; renderComposer(); }
    catch(err){ toast(S.falhou); } return; }
  /* AI polish (ADR-027) — the ONLY path that triggers the model for this email. */
  if(e.target.closest('#_aibtn')&&draft){ polishDraft(); return; }
  if(e.target.closest('#_aiuse')&&draft){ useAIDraft(); return; }
  if(e.target.closest('#_aidrop')&&draft){ draft.ai=null; renderComposer(); return; }
  if(e.target.closest('#_copyq')){
    const txt=(($('#_draftbody')||{}).value)||'';
    try{ await navigator.clipboard.writeText(txt); toast('email copiado'); }
    catch(err){ toast('copia manual: '+txt.slice(0,40)+'…'); } return; }
  if(e.target.closest('#_openq')&&draft){
    const subj=(($('#_subj')||{}).value)||draft.subject||'';
    const body=(($('#_draftbody')||{}).value)||'';
    location.href='mailto:'+encodeURIComponent(draft.to||'')
      +'?subject='+encodeURIComponent(subj)+'&body='+encodeURIComponent(body); return; }
  /* ── descritivo composer (ADR-030) — mirrors the email composer's AI-polish contract ── */
  if(e.target.closest('#_descaibtn')&&descr){ polishDescription(); return; }
  if(e.target.closest('#_descaiuse')&&descr){ useAIDescription(); return; }
  if(e.target.closest('#_descaidrop')&&descr){ descr.ai=null; renderDescription(); return; }
  if(e.target.closest('#_desccopy')){
    const txt=(($('#_descbody')||{}).value)||'';
    try{ await navigator.clipboard.writeText(txt); toast('descritivo copiado'); }
    catch(err){ toast('copia manual: '+txt.slice(0,40)+'…'); } return; }
  if(e.target.closest('#_exportbtn')){ try{
    const r=await post('/api/projects/'+selected.project_id+'/export',{adapter:'json'});
    toast(r.ok?'exportado: '+(r.external_id||'ok'):S.falhou);}
    catch(err){toast(S.falhou);} return; }
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
    catch(err){ toast(S.falhou); } return; }
  if(e.target.closest('#_cfadd')){
    const name=(($('#_cfname')||{}).value||'').trim(), val=(($('#_cfval')||{}).value||'').trim();
    if(!name||!val){ toast('nome e valor'); return; }
    try{ selected=await post('/api/projects/'+selected.project_id+'/custom-field',{name:name, value:val});
      renderDetail(); toast('campo adicionado'); }
    catch(err){ toast(S.falhou); } return; }
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
               roster: list[str] | None = None,
               person: dict[str, Any] | None = None) -> str:
    return cockpit_ui.page(
        "Projetos", "projetos", _BODY,
        embeds={"projects": projects, "fields": _FIELDS, "roster": list(roster or [])},
        lens_js=_LENS_JS,
        nav_counts=nav_counts,
        person=person,
    )
