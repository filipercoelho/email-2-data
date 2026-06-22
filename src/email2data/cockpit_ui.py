"""Shared shell for all cockpit lens pages (C0 — see docs/05-reference/cockpit-design.md).

Provides ``page()`` — the single assembler every lens calls. Bundles:
  CSS   — design tokens (identical to report.py) + the full component kit
  JS    — shared utilities, undo stack, command palette, density toggle
  HTML  — sticky nav with live counts, overlays (toast/palette/help), ARIA regions

Lens JS contract (each lens script must define these before shell event wiring runs):
  function render()         — re-renders the main content area
  function paletteItems(q)  — returns [{kind, label, sub?, run}] for the ⌘K palette
  function onKey(e)         — handles lens-specific keys (J/K/E/A/Z … delegated here)

Optional lens override:
  function onEsc()          — called on Esc in non-modal state (e.g. clear a filter);
                              defaults to a no-op defined by the shell.

Script ordering in the assembled page guarantees:
  1. Shell utilities  →  available when lens code runs
  2. Lens JS          →  defines render / paletteItems / onKey, calls render()
  3. Shell events     →  wires keydown + palette (calls the lens functions above)
"""

from __future__ import annotations

import json
from typing import Any


# ── nav items (order = visual order) ─────────────────────────────────────────
_NAV = [
    ("fila",         "Fila",          "/"),
    ("contrapartes", "Contrapartes",  "/contrapartes"),
    ("projetos",     "Projetos",      "/projetos"),
    ("para-ti",      "Para ti",       "/para-ti"),
    ("capturas",     "Capturas",      "/capturas"),
]


def _embed(obj: Any) -> str:
    """JSON for safe inlining in a <script> (``</`` escaped to prevent tag injection)."""
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def _esc_html(s: str) -> str:
    """Minimal HTML escaping for values inserted into tag content (e.g. <title>)."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def page(
    title: str,
    active: str,
    body_html: str,
    *,
    embeds: dict[str, Any] | None = None,
    lens_js: str = "",
    nav_counts: dict[str, int] | None = None,
    extra_css: str = "",
) -> str:
    """Assemble a full cockpit lens page.

    ``active``     — one of "fila" | "contrapartes" | "projetos" | "para-ti"
    ``body_html``  — the main content area (everything below the header)
    ``embeds``     — {NAME: value} → ``const NAME = <json>;`` injected before lens_js
    ``lens_js``    — lens-specific JS; must define render(), paletteItems(q), onKey(e)
    ``nav_counts`` — {key: n} → badge on nav items (e.g. {"fila": 54, "para-ti": 3})
    ``extra_css``  — lens-specific CSS appended to the kit (keep small)
    """
    counts = nav_counts or {}
    consts = "\n".join(
        f"const {k.upper()} = {_embed(v)};" for k, v in (embeds or {}).items()
    )
    return (
        _HEAD.replace("__TITLE__", _esc_html(title)).replace("__EXTRACSS__", extra_css)
        + _nav_html(active, counts)
        + body_html
        + _OVERLAYS
        + f"\n<script>\n{_SHELL_UTILS}\n</script>\n"
        + f"\n<script>\n{consts}\n{lens_js}\ntry{{render();}}catch(_e){{console.error(_e);}}\n</script>\n"
        + f"\n<script>\n{_SHELL_EVENTS}\n</script>\n"
        + "\n</body>\n</html>"
    )


def _nav_html(active: str, counts: dict[str, int]) -> str:
    links = []
    for key, label, href in _NAV:
        n = counts.get(key)
        badge = (
            f' <span class="nbadge">{n}</span>' if n else ""
        )
        cls = "nlink on" if key == active else "nlink"
        links.append(f'<a class="{cls}" href="{href}">{label}{badge}</a>')
    return (
        "<header>\n<div class='htop'>"
        + "<span class='logo'>email-2-data</span>"
        + "".join(links)
        + "<span class='grow'></span>"
        + "<button class='hbtn' id='_syncbtn'>Sincronizar</button>"
        + "<button class='hbtn' id='_denbtn'>densidade</button>"
        + "</div>\n</header>\n"
    )


# ── HTML fragments ────────────────────────────────────────────────────────────

_HEAD = """<!doctype html>
<html lang="pt">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>__TITLE__ · email-2-data</title>
<style>
  /* ── tokens (kept in sync with report.py) ─────────────────────────────── */
  :root{--bg:#eef0f3;--card:#fff;--bd:#e3e6ea;--bd2:#eef0f3;--tx:#15181c;--mut:#6b7280;--mut2:#9aa1ab;
    --ac:#3358d4;--int:#0d9488;--ext:#64748b;--red:#cf3a3a;--amber:#b9791c;--green:#1f9d57;--purple:#6b4fd1;
    --shadow:0 1px 2px rgba(20,24,28,.05),0 1px 3px rgba(20,24,28,.04);
    --rpad:12px;--rfont:13.5px;}
  body.compact{--rpad:7px;--rfont:13px}
  *{box-sizing:border-box} html,body{margin:0}
  body{font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:var(--tx);background:var(--bg)}
  /* ── a11y ─────────────────────────────────────────────────────────────── */
  .sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}
  :focus-visible{outline:2px solid var(--ac);outline-offset:2px;border-radius:6px}
  .hidden{display:none!important}
  /* ── nav / header ─────────────────────────────────────────────────────── */
  header{background:var(--card);border-bottom:1px solid var(--bd);padding:13px 26px;
    position:sticky;top:0;z-index:20;box-shadow:var(--shadow)}
  .htop{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .logo{font-weight:720;font-size:13px;letter-spacing:-.01em;color:var(--mut);margin-right:4px}
  .nlink{color:var(--mut);text-decoration:none;font-size:13px;font-weight:600;
    padding:5px 10px;border-radius:8px;display:inline-flex;align-items:center;gap:5px}
  .nlink:hover{background:var(--bg);color:var(--tx)}
  .nlink.on{background:var(--ac);color:#fff}
  .nlink.on:hover{background:#2a52c7}
  .nbadge{background:rgba(255,255,255,.25);border-radius:20px;padding:0 6px;font-size:10px;font-weight:700;font-variant-numeric:tabular-nums}
  .nlink:not(.on) .nbadge{background:#fbeaea;color:var(--red)}
  .grow{margin-left:auto}
  .hbtn{color:var(--mut);background:none;border:1px solid var(--bd);cursor:pointer;
    padding:5px 10px;border-radius:8px;font-size:12.5px;font-weight:600}
  .hbtn:hover{border-color:var(--ac);color:var(--ac)}
  /* ── layout ───────────────────────────────────────────────────────────── */
  .wrap{max-width:1000px;margin:0 auto;padding:16px 26px 60px}
  .bar{display:flex;align-items:center;gap:10px;color:var(--mut);font-size:12px;margin:2px 2px 12px;flex-wrap:wrap}
  .cmdk{margin-left:auto;color:var(--mut2)}
  .cmdk kbd{background:var(--bg);border:1px solid var(--bd);border-radius:5px;padding:0 5px;font-family:ui-monospace,monospace}
  /* ── component kit: list · row · counterparty badge ─────────────────── */
  .list{background:var(--card);border:1px solid var(--bd);border-radius:14px;overflow:hidden;box-shadow:var(--shadow)}
  .row{display:flex;align-items:center;gap:12px;padding:var(--rpad) 15px;border-bottom:1px solid var(--bd2);
    border-left:3px solid transparent;cursor:pointer;transition:opacity .16s ease,transform .16s ease,background .12s}
  .row:last-child{border-bottom:none}
  .row:hover{background:#f8f9fb}
  .row.on{background:#eef2ff;border-left-color:var(--ac)}
  .row.leaving{opacity:0;transform:translateX(10px)}
  .cp{flex:0 0 auto;display:inline-block;padding:2px 9px;border-radius:20px;font-size:10px;
    font-weight:700;letter-spacing:.03em;min-width:62px;text-align:center}
  .cp.CLIENT{background:#e7f6ee;color:var(--green)} .cp.LEAD{background:#efeafb;color:var(--purple)}
  .cp.SUPPLIER{background:#e8eefc;color:var(--ac)}
  .cp.INTERNAL,.cp.OTHER,.cp.BULK{background:#eef0f3;color:var(--mut)}
  /* ── component kit: row body ─────────────────────────────────────────── */
  .rmain{flex:1;min-width:0}
  .subj{font-weight:620;font-size:var(--rfont);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .rmeta{color:var(--mut);font-size:11.5px;margin-top:2px;display:flex;align-items:center;gap:7px;flex-wrap:wrap}
  .why{margin-top:6px;font-size:12px;color:#4a4326;background:#fffdf3;border:1px solid #f0e6c0;
    border-radius:8px;padding:6px 10px;line-height:1.5;white-space:normal}
  /* ── component kit: clock ────────────────────────────────────────────── */
  .clock{flex:0 0 auto;font-size:12px;font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap}
  .clock .d{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;
    vertical-align:middle;background:currentColor;aria-hidden:true}
  .clock.red{color:var(--red)} .clock.amber{color:var(--amber)}
  .clock.green{color:var(--green)} .clock.none{color:var(--mut2)}
  .clock.red .d{animation:beat 2s ease-in-out infinite}
  /* ── component kit: owner chip ───────────────────────────────────────── */
  .owner{flex:0 0 auto;font-size:12px;color:var(--int);background:#f0fdfa;
    border:1px solid #bfe6e0;border-radius:20px;padding:2px 10px;cursor:pointer;white-space:nowrap}
  .owner.empty{background:#f6f7f9;border-color:var(--bd);color:var(--mut2)}
  /* ── component kit: action buttons ──────────────────────────────────── */
  .acts{flex:0 0 auto;display:flex;gap:5px}
  .acts button,.act-btn{border:1px solid var(--bd);background:#fff;border-radius:8px;
    cursor:pointer;font-size:13px;color:var(--mut);line-height:1;padding:0 10px;height:30px}
  .acts button{width:30px;padding:0}
  .acts button:hover,.act-btn:hover{border-color:var(--ac);color:var(--ac);background:#f1f5ff}
  .act-btn.accept{border-color:var(--green);color:var(--green)}
  .act-btn.accept:hover{background:#e7f6ee}
  /* ── B5 trust grammar ─────────────────────────────────────────────────── */
  .trust{font-size:10.5px;font-weight:650;border-radius:20px;padding:1px 8px;cursor:pointer;
    font-variant-numeric:tabular-nums;background:#fff}
  .trust.proposed{border:1px dashed var(--mut2);color:var(--mut)}
  .trust.committed{border:1px solid var(--int);color:var(--int);background:#f0fdfa}
  .trust.committed::before{content:"✓ ";font-weight:700}
  /* ── readiness ring (C4 Projetos) ────────────────────────────────────── */
  .ring-wrap{flex:0 0 auto;position:relative;width:42px;height:42px}
  .ring-wrap svg{position:absolute;inset:0;transform:rotate(-90deg)}
  .ring-track{fill:none;stroke:var(--bd);stroke-width:4}
  .ring-fill{fill:none;stroke:var(--int);stroke-width:4;stroke-linecap:round;transition:stroke-dashoffset .3s ease}
  .ring-fill.done{stroke:var(--green)}
  .ring-pct{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
    font-size:10px;font-weight:700;font-variant-numeric:tabular-nums;color:var(--tx)}
  /* ── shared email thread rendering (Fila + Projetos) ─────────────────── */
  .texp{display:flex;flex-direction:column;gap:9px;white-space:normal;cursor:default}
  .thead{display:flex;align-items:center;flex-wrap:wrap;gap:10px;padding-bottom:2px}
  .tsum{color:var(--mut);font-size:12px;font-variant-numeric:tabular-nums}
  .tmsg{background:#f8f9fb;border:1px solid var(--bd2);border-radius:9px;padding:8px 11px}
  .tmeta{display:flex;align-items:baseline;flex-wrap:wrap;gap:6px;font-size:11px}
  .taddr{font-weight:650;font-size:12px;color:var(--tx)}
  .tarrow{color:var(--mut2)}
  .tdir{font-weight:700;text-transform:uppercase;font-size:9.5px;letter-spacing:.04em}
  .tdate{color:var(--mut2);margin-left:auto}
  .tatts{flex-basis:100%;margin-top:3px}
  .tbody{margin-top:6px;font-size:12.5px;line-height:1.5;color:var(--tx);white-space:pre-wrap;word-break:break-word;max-height:260px;overflow:auto}
  .qtoggle,.rawtoggle{margin-top:6px;font-size:11px;font-weight:600;color:var(--mut);background:none;border:none;cursor:pointer;padding:0;display:block}
  .qtoggle:hover,.rawtoggle:hover{color:var(--ac)}
  .rawbody{margin-top:4px;border-top:1px dashed var(--bd);padding-top:6px}
  .tquote{margin-top:5px;padding-left:9px;border-left:2px solid var(--bd);font-size:12px;line-height:1.45;color:var(--mut);white-space:pre-wrap;word-break:break-word;max-height:300px;overflow:auto}
  .tatt{display:inline-block;font-size:11px;background:#eef2ff;border:1px solid #cdd7ff;color:var(--ac);border-radius:6px;padding:1px 7px;margin:0 5px 3px 0;text-decoration:none}
  .tatt:hover{background:#dfe8ff}
  /* embedded messages (extracted from forwarded chains, not direct IMAP) */
  .tmsg.embedded{background:#fafbfc;border-style:dashed}
  .tembedded{font-size:9.5px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:var(--mut2);padding:1px 6px;border:1px solid var(--bd);border-radius:5px}
  /* provenance badges: which spec fields this message supplied */
  .tprov{margin-top:4px;display:flex;flex-wrap:wrap;gap:4px}
  .tprovbadge{font-size:10px;font-weight:700;background:#e7f6ee;border:1px solid #bfe6cf;color:var(--green);border-radius:5px;padding:1px 6px}
  /* ── timeline (C2 Contrapartes) ──────────────────────────────────────── */
  .timeline{list-style:none;margin:0;padding:0}
  .titem{display:flex;gap:12px;padding:10px 0;border-bottom:1px solid var(--bd2)}
  .titem:last-child{border-bottom:none}
  .titem .td{color:var(--mut2);font-size:11px;white-space:nowrap;min-width:64px;padding-top:2px}
  .titem .tc{flex:1;min-width:0}
  .titem .ttype{display:inline-block;font-size:9.5px;font-weight:700;text-transform:uppercase;
    letter-spacing:.05em;padding:1px 7px;border-radius:20px;margin-right:6px}
  .ttype.email{background:#e8eefc;color:var(--ac)}
  .ttype.projeto{background:#efeafb;color:var(--purple)}
  /* ── gate items (C3 Para ti) ─────────────────────────────────────────── */
  .gate{background:var(--card);border:1px solid var(--bd);border-radius:14px;
    padding:16px 18px;margin-bottom:10px;box-shadow:var(--shadow)}
  .gate .gkind{display:inline-block;font-size:10px;font-weight:700;text-transform:uppercase;
    letter-spacing:.05em;padding:2px 9px;border-radius:20px;margin-bottom:8px}
  .gkind.rever{background:#fff3f3;color:var(--red)}
  .gkind.projeto{background:#efeafb;color:var(--purple)}
  .gkind.identidade{background:#e8eefc;color:var(--ac)}
  .gate .gtitle{font-weight:640;font-size:14px;margin-bottom:4px}
  .gate .gwhy{font-size:12.5px;color:var(--mut);margin-bottom:10px;line-height:1.5}
  .gate .gacts{display:flex;gap:8px}
  /* ── cluster card (C2 list) ──────────────────────────────────────────── */
  .ccard{background:var(--card);border:1px solid var(--bd);border-left:3px solid transparent;
    border-radius:12px;padding:14px 16px;margin-bottom:8px;cursor:pointer;box-shadow:var(--shadow)}
  .ccard:hover{background:#f8f9fb} .ccard.on{border-left-color:var(--ac);background:#eef2ff}
  .ccard .ch{display:flex;align-items:center;gap:8px;margin-bottom:4px}
  .ccard .cname{font-weight:650;font-size:14px}
  .ccard .cstat{margin-left:auto;font-size:11.5px;color:var(--mut)}
  .ccard .cemails{font-size:11.5px;color:var(--mut2)}
  /* ── zero / hint ──────────────────────────────────────────────────────── */
  .zero{text-align:center;padding:70px 20px;color:var(--green);font-size:18px;font-weight:650;animation:zin .3s ease}
  .zero .s{display:block;color:var(--mut2);font-size:13px;font-weight:400;margin-top:8px}
  .hint{margin-top:14px;color:var(--mut2);font-size:11.5px;text-align:center}
  .hint b{color:var(--mut);font-weight:680}
  /* ── toast / menu / palette / help ──────────────────────────────────── */
  .toast{position:fixed;bottom:22px;left:50%%;transform:translateX(-50%%);background:var(--tx);color:#fff;
    padding:9px 16px;border-radius:9px;font-size:13px;box-shadow:var(--shadow);z-index:80}
  .menu{position:absolute;background:#fff;border:1px solid var(--bd);border-radius:10px;
    box-shadow:0 4px 16px rgba(20,24,28,.14);z-index:60;min-width:170px;padding:4px}
  .menu .mi{padding:7px 11px;border-radius:7px;cursor:pointer;font-size:13px}
  .menu .mi:hover,.menu .mi.on{background:#eef2ff;color:var(--ac)}
  .overlay{position:fixed;inset:0;background:rgba(20,24,28,.32);display:flex;align-items:flex-start;
    justify-content:center;z-index:70}
  .overlay.help{align-items:center}
  .card{background:#fff;border-radius:14px;padding:22px 26px;box-shadow:var(--shadow);max-width:340px}
  .card h3{margin:0 0 12px;font-size:14px}
  .card kbd{background:var(--bg);border:1px solid var(--bd);border-radius:5px;padding:1px 6px;
    font-family:ui-monospace,monospace;font-size:12px}
  .card .kr{display:flex;justify-content:space-between;gap:24px;padding:5px 0;font-size:13px;
    border-top:1px solid var(--bd2)}
  .card .kr:first-of-type{border-top:none}
  .pcard{background:#fff;border-radius:14px;box-shadow:0 10px 40px rgba(20,24,28,.22);
    width:min(560px,92vw);margin-top:12vh;overflow:hidden}
  #_pq{width:100%%;border:0;border-bottom:1px solid var(--bd);padding:15px 18px;font-size:15px;outline:none}
  #_presults{max-height:50vh;overflow:auto;padding:6px}
  .pi{display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:9px;cursor:pointer}
  .pi.on{background:#eef2ff}
  .pi .pik{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;
    color:var(--mut2);min-width:72px}
  .pi .pil{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13.5px}
  .pi .pis{color:var(--mut2);font-size:11.5px}
  /* ── B4 motion keyframes ─────────────────────────────────────────────── */
  @keyframes zin{from{opacity:0;transform:scale(.97)}to{opacity:1;transform:none}}
  @keyframes pop{0%{transform:scale(1)}40%{transform:scale(1.14)}100%{transform:scale(1)}}
  @keyframes beat{0%,100%{opacity:1}50%{opacity:.45}}
  @media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
  __EXTRACSS__
</style>
</head>
<body>
"""

_OVERLAYS = """
<div id="_live" class="sr" aria-live="polite" aria-atomic="true"></div>
<div id="_toast" class="toast hidden" role="status"></div>
<div id="_menu" class="menu hidden"></div>
<div id="_palette" class="overlay hidden"><div class="pcard" role="dialog" aria-label="Comandos">
  <input id="_pq" placeholder="comandos, contrapartes, assuntos…" autocomplete="off" aria-label="Procurar"/>
  <div id="_presults" role="listbox"></div>
</div></div>
<div id="_help" class="overlay help hidden"><div class="card" role="dialog" aria-label="Atalhos">
  <h3>Atalhos</h3>
  <div class="kr"><span>Navegar</span><span><kbd>J</kbd> <kbd>K</kbd></span></div>
  <div class="kr"><span>Ação principal</span><kbd>E</kbd></div>
  <div class="kr"><span>Atribuir dono</span><kbd>A</kbd></div>
  <div class="kr"><span>Desfazer</span><kbd>Z</kbd></div>
  <div class="kr"><span>Comandos</span><kbd>⌘K</kbd></div>
  <div class="kr"><span>Fechar / limpar</span><kbd>Esc</kbd></div>
</div></div>
"""

# ── shared JS utilities (available to all lens scripts) ──────────────────────
_SHELL_UTILS = r"""
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const $=s=>document.querySelector(s);
const reduceMotion=()=>window.matchMedia('(prefers-reduced-motion: reduce)').matches;
function announce(m){const el=$('#_live');if(el){el.textContent='';requestAnimationFrame(()=>{if(el)el.textContent=m;});}}
function toast(m){const t=$('#_toast');if(!t)return;t.textContent=m;t.classList.remove('hidden');clearTimeout(t._h);t._h=setTimeout(()=>t.classList.add('hidden'),2600);}
async function post(url,body){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!r.ok)throw new Error('HTTP '+r.status);return r.json();}
function decidedShort(d){d=(d||'').toLowerCase();if(!d)return '';if(d.startsWith('tier0'))return 'regra';if(d.includes('gemini'))return 'Gemini';if(d.includes('claude'))return 'Claude';if(d.startsWith('tier1'))return 'IA';return d.split(':').pop();}
const S={
  nadaDesfazer:'nada para desfazer',desfeito:'desfeito',revertido:'falhou — revertido',
  semResultados:'sem resultados',
  sincronizando:'a sincronizar…',sincronizado:'sincronizado',
  syncEmCurso:'sync já em curso',syncFalhou:'sync falhou',
  risk:n=>n+' em risco',threads:n=>n+(n===1?' thread':' threads'),
  semDados:'fila vazia',tratado:'tratado',
  actSync:'Sincronizar agora',actUndo:'Desfazer',actDensity:'Alternar densidade',actInbox:'Abrir inbox',
};
const undo=[];
function doUndo(){const u=undo.pop();if(!u){toast(S.nadaDesfazer);return;}u.revert();toast(S.desfeito);announce(S.desfeito);}

/* ── shared email-thread rendering ─────────────────────────────────────
   Used by both the Fila inline thread view and the Projetos source panel.
   Single source of truth: fix once here, both pages benefit.           */
function msgDirTag(d){
  if(d==='inbound') return {t:'recebido',c:'var(--ac)'};
  if(d==='internal') return {t:'interno',c:'var(--mut)'};
  return {t:'enviado',c:'var(--int)'};
}
function msgThreadSummary(msgs){
  const us=msgs.filter(m=>m.direction!=='inbound').length, them=msgs.length-us;
  const ds=msgs.map(m=>(m.date||'').slice(0,10)).filter(Boolean);
  const range=ds.length?(ds[0]===ds[ds.length-1]?ds[0]:ds[0]+' → '+ds[ds.length-1]):'';
  const p=[msgs.length+' '+(msgs.length===1?'mensagem':'mensagens')];
  if(us)p.push(us+' de nós'); if(them)p.push(them+' recebida'+(them===1?'':'s'));
  if(range)p.push(range);
  return p.join(' · ');
}
function msgSplitQuote(raw){
  const body=(raw||'').replace(/\r\n/g,'\n');
  const pats=[
    /^>.*/m,
    /^\s*-{2,}\s*(original message|mensagem original)\s*-{2,}/im,
    /^_{5,}\s*$/m,
    /^No dia .+/m,
    /^Em .+escreveu:/im,
    /^On .+wrote:$/im,
    /^\s*De:\s.+\n(?:.*\n){0,3}?\s*(Enviad[ao]|Para):/im,
    /^\s*From:\s.+\n(?:.*\n){0,3}?\s*(Sent|To):/im,
  ];
  let idx=-1;
  for(const re of pats){const m=re.exec(body); if(m&&(idx<0||m.index<idx)) idx=m.index;}
  if(idx<0) return {visible:body.trim(), quoted:''};
  return {visible:body.slice(0,idx).trim(), quoted:body.slice(idx).trim()};
}
/* Render one message. opts: { provenance: {addr: message_id} } lets the Projetos panel
   highlight which fields came from which message. */
function msgHTML(m, opts){
  opts=opts||{};
  const tag=msgDirTag(m.direction);
  const to=(m.to||[]);
  const toStr=to.length?(esc(to[0])+(to.length>1?' +'+(to.length-1):'')):'—';
  const atts=(m.attachments||[]).map((a,idx)=>
    '<a class="tatt" href="/api/attachment/'+encodeURIComponent(m.message_id)+'/'+idx
    +'" target="_blank" rel="noopener">📎 '+esc(a.name)+'</a>').join('');
  // Use the cleaned body by default; fall back to raw if no clean version available.
  const cleanBody = (m.body_clean !== undefined ? m.body_clean : m.body) || '';
  const rawBody   = m.body || '';
  const sp=msgSplitQuote(cleanBody);
  // If nothing remains after cleaning + splitting, try the raw body as fallback.
  const spRaw=msgSplitQuote(rawBody);
  const noVisible=!sp.visible && !sp.quoted;
  const vis=noVisible?(spRaw.visible||spRaw.quoted):(sp.visible||sp.quoted||'');
  const visHTML=vis?'<div class="tbody">'+esc(vis.slice(0,2000))+(vis.length>2000?'\n…':'')+'</div>':'';
  const quoteHTML=(sp.quoted&&!noVisible)
    ?'<button class="qtoggle">▸ mensagem citada</button>'
     +'<div class="tquote hidden">'+esc(sp.quoted.slice(0,3000))+'</div>'
    :'';
  // "ver original" toggle — only show when clean differs from raw
  const hasNoise = rawBody.length > cleanBody.length + 60;
  const rawToggle = hasNoise
    ? '<button class="rawtoggle">ver original</button>'
      +'<div class="rawbody hidden"><div class="tbody">'+esc(rawBody.slice(0,2000))+'</div></div>'
    : '';
  // field provenance: which spec fields did this message supply?
  // Uses fieldLabels() if a FIELDS registry is available (injected by Projetos lens).
  const prov=opts.provenance||{};
  const fromFields=Object.entries(prov).filter(([,mid])=>mid===m.message_id).map(([addr])=>addr);
  let provBadges='';
  if(fromFields.length){
    // dedupe by base key (item#0,item#1 → one "peça" badge), then map to PT label
    const seen=new Set();
    const labels=fromFields.map(addr=>{
      const base=addr.split('#')[0];
      if(seen.has(base)) return null; seen.add(base);
      // try the FIELDS registry if available (defined by Projetos lens as byKey)
      const label=(typeof byKey!=='undefined'&&byKey[base]&&byKey[base].label)||base;
      return label;
    }).filter(Boolean);
    provBadges='<div class="tprov">'+labels.map(l=>'<span class="tprovbadge" title="campo extraído desta mensagem">'+esc(l)+'</span>').join('')+'</div>';
  }
  const embeddedBadge=m.embedded?'<span class="tembedded">via reencaminhamento</span>':'';
  return '<div class="tmsg'+(m.embedded?' embedded':'')+'">'
    +'<div class="tmeta">'
    +'<span class="taddr">'+esc(m.from_email||'?')+'</span>'
    +'<span class="tarrow">→</span>'
    +'<span class="taddr">'+toStr+'</span>'
    +'<span class="tdir" style="color:'+tag.c+'">'+tag.t+'</span>'
    +embeddedBadge
    +'<span class="tdate">'+esc((m.date||'').slice(0,16).replace('T',' '))+'</span>'
    +(atts?'<span class="tatts">'+atts+'</span>':'')
    +'</div>'
    +provBadges
    +visHTML+quoteHTML+rawToggle
    +'</div>';
}
/* Render a full thread panel (summary line + all messages). */
function msgThreadHTML(msgs, opts){
  const head='<div class="thead"><span class="tsum">'+esc(msgThreadSummary(msgs))+'</span></div>';
  return '<div class="texp">'+head+msgs.map(m=>msgHTML(m,opts)).join('')+'</div>';
}
/* Quote + raw-toggle wiring — attach once to a container. */
function msgWireQuoteToggles(container){
  container.addEventListener('click',function(e){
    const qt=e.target.closest('.qtoggle');
    if(qt){
      const q=qt.nextElementSibling;
      if(q&&q.classList.contains('tquote')){
        const hid=q.classList.toggle('hidden');
        qt.textContent=(hid?'▸':'▾')+' mensagem citada';
      }
      e.stopPropagation(); return;
    }
    const rt=e.target.closest('.rawtoggle');
    if(rt){
      const rb=rt.nextElementSibling;
      if(rb&&rb.classList.contains('rawbody')){
        const hid=rb.classList.toggle('hidden');
        rt.textContent=hid?'ver original':'ver limpo';
      }
      e.stopPropagation();
    }
  });
}
let _pi=[],_pf=0;
function openPalette(){_pi=paletteItems('');_pf=0;$('#_palette').classList.remove('hidden');_rp();const q=$('#_pq');q.value='';q.focus();}
function closePalette(){$('#_palette').classList.add('hidden');}
function _rp(){_pf=Math.max(0,Math.min(_pf,_pi.length-1));$('#_presults').innerHTML=_pi.slice(0,40).map((it,i)=>'<div class="pi'+(i===_pf?' on':'')+'" data-i="'+i+'" role="option"><span class="pik">'+esc(it.kind||'')+'</span><span class="pil">'+esc(it.label||'')+(it.sub?' <span class="pis">'+esc(it.sub)+'</span>':'')+'</span></div>').join('')||'<div class="pi"><span class="pil pis">'+esc(S.semResultados)+'</span></div>';}
function _runP(i){const it=_pi[i];if(!it)return;closePalette();it.run();}
function toggleDensity(){document.body.classList.toggle('compact');try{localStorage.setItem('fila-density',document.body.classList.contains('compact')?'compact':'');}catch(e){}}
function onEsc(){}  /* lens may override */
async function syncNow(){toast(S.sincronizando);try{const r=await fetch('/api/sync',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});if(r.status===409){toast(S.syncEmCurso);return;}if(!r.ok){toast(S.syncFalhou);return;}await r.json();toast(S.sincronizado);setTimeout(()=>location.reload(),700);}catch(e){toast(S.syncFalhou);}}
"""

# ── shell event wiring (runs after lens JS, calls lens functions) ─────────────
_SHELL_EVENTS = r"""
$('#_pq').addEventListener('input',e=>{_pi=paletteItems(e.target.value);_pf=0;_rp();});
$('#_presults').addEventListener('click',e=>{const el=e.target.closest('.pi');if(el)_runP(parseInt(el.dataset.i,10));});
$('#_palette').addEventListener('click',e=>{if(e.target.id==='_palette')closePalette();});
$('#_help').addEventListener('click',e=>{if(e.target.id==='_help')$('#_help').classList.add('hidden');});
const _sb=$('#_syncbtn');if(_sb)_sb.addEventListener('click',syncNow);
const _db=$('#_denbtn');if(_db)_db.addEventListener('click',toggleDensity);
document.addEventListener('click',e=>{const m=$('#_menu');if(m&&!e.target.closest('#_menu')&&!e.target.closest('[data-act="owner"]'))m.classList.add('hidden');});
document.addEventListener('keydown',e=>{
  if((e.metaKey||e.ctrlKey)&&(e.key==='k'||e.key==='K')){e.preventDefault();$('#_palette').classList.contains('hidden')?openPalette():closePalette();return;}
  if(!$('#_palette').classList.contains('hidden')){
    if(e.key==='Escape')closePalette();
    else if(e.key==='ArrowDown'){_pf=Math.min(_pi.length-1,_pf+1);_rp();e.preventDefault();}
    else if(e.key==='ArrowUp'){_pf=Math.max(0,_pf-1);_rp();e.preventDefault();}
    else if(e.key==='Enter'){_runP(_pf);e.preventDefault();}
    return;
  }
  const tag=(e.target.tagName||'').toLowerCase();
  if(tag==='input'||tag==='textarea'){if(e.key==='Escape')e.target.blur();return;}
  if(e.key==='/'){ openPalette();e.preventDefault();return;}
  if(e.key==='?'){$('#_help').classList.toggle('hidden');return;}
  if(e.key==='Escape'){$('#_help').classList.add('hidden');closePalette();onEsc();return;}
  if(!$('#_help').classList.contains('hidden'))return;
  if(e.key==='z'||e.key==='Z'){doUndo();return;}
  onKey(e);
});
try{if(localStorage.getItem('fila-density')==='compact')document.body.classList.add('compact');}catch(e){}
"""
