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
# Administração (/admin) is NOT a decision lens — it is a configuration surface. Since ADR-034 P5d it
# lives in the gear menu (with densidade + tema), never between the queues in the main strip.


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

    ``active``     — one of "fila" | "contrapartes" | "projetos" | "para-ti" | "capturas" | "admin"
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


# One stroke glyph per lens (ADR-034 P5b) so the nav scans by shape; `currentColor` tints each
# icon for its state for free. 24-unit grid, matched to the rail's icon family.
_NAV_ICON = {
    "fila": '<svg viewBox="0 0 24 24"><path d="M4 12h4l2 3h4l2-3h4M4 12l2-7h12l2 7M4 12v7h16v-7"/></svg>',
    "contrapartes": '<svg viewBox="0 0 24 24"><circle cx="9" cy="8" r="3"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6"/><circle cx="17.5" cy="9" r="2.3"/><path d="M16 14.4c2.6.5 4.5 2.8 4.5 5.6"/></svg>',
    "projetos": '<svg viewBox="0 0 24 24"><path d="M3 7h6l2 2h10v10H3z"/><path d="M3 7V5h5l2 2"/></svg>',
    "para-ti": '<svg viewBox="0 0 24 24"><path d="M12 4a6 6 0 0 1 6 6v3l2 3H4l2-3v-3a6 6 0 0 1 6-6z"/><path d="M10 19a2 2 0 0 0 4 0"/></svg>',
    "capturas": '<svg viewBox="0 0 24 24"><rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7l1.5-3h5L16 7"/><circle cx="12" cy="13" r="3.3"/></svg>',
    "admin": '<svg viewBox="0 0 24 24"><path d="M4 7h11M19 7h1M4 12h6M14 12h6M4 17h9M17 17h3"/><circle cx="17" cy="7" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="15" cy="17" r="2"/></svg>',
}
_GEAR_ICON = ('<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 13a7.6 7.6 0 0 0'
              ' 0-2l1.9-1.4-1.9-3.3-2.2.9a7.5 7.5 0 0 0-1.7-1l-.3-2.4H9.9l-.3 2.4a7.5 7.5 0 0 0-1.7 1'
              'l-2.2-.9L3.8 9.6 5.7 11a7.6 7.6 0 0 0 0 2l-1.9 1.4 1.9 3.3 2.2-.9a7.5 7.5 0 0 0 1.7 1l'
              '.3 2.4h4.2l.3-2.4a7.5 7.5 0 0 0 1.7-1l2.2.9 1.9-3.3z"/></svg>')


def _nav_html(active: str, counts: dict[str, int]) -> str:
    links = []
    for key, label, href in _NAV:
        n = counts.get(key)
        # The badge carries DEMAND, not inventory (ADR-034): the Fila count is what needs a reply
        # (WE_OWE red+amber), computed in webapp._nav_counts — never the total active count.
        badge = (
            f' <span class="nbadge">{n}</span>' if n else ""
        )
        cls = "nlink on" if key == active else "nlink"
        icon = _NAV_ICON.get(key, "")
        # data-nav lets a lens refresh its badges in place from a poll, without a page reload.
        links.append(
            f'<a class="{cls}" data-nav="{key}" href="{href}">{icon}<span class="nlbl">{label}</span>{badge}</a>')
    # Freshness-as-sync pill (ADR-034 P5d): «Sincronizar» and «correio há N min» were an action and
    # its own status shown as two strangers. Merged into one pill — a dot (green fresh / amber stale
    # / spinning while syncing) + the age — that you click to sync now. The lens (Fila/Para-ti) feeds
    # it via setSynced(); other lenses show just «Sincronizar».
    sync_pill = ("<button class='hbtn syncpill' id='_syncbtn' title='Sincronizar agora'>"
                 "<span class='sdot' id='_sdot'></span><span id='_synclbl'>Sincronizar</span></button>")
    # Gear: Admin + densidade + tema fold into one menu (config, not a lens). Active on the /admin page.
    gear_on = " on" if active == "admin" else ""
    gear = (
        "<div class='gearwrap'>"
        f"<button class='hbtn ic{gear_on}' id='_gearbtn' aria-haspopup='true' aria-label='Definições'>{_GEAR_ICON}</button>"
        "<div class='gearmenu hidden' id='_gearmenu' role='menu'>"
        f'<a class="gm" data-nav="admin" href="/admin">{_NAV_ICON["admin"]}<span>Administração</span></a>'
        "<button class='gm' id='_denbtn' role='menuitem'>Densidade</button>"
        "<button class='gm' id='_themebtn' role='menuitem'>Tema claro / escuro</button>"
        "</div></div>"
    )
    return (
        "<header>\n<div class='htop'>"
        + "<span class='logo'><span class='mark'>e2d</span>email-2-data</span>"
        + "".join(links)
        + "<span class='grow'></span>"
        + sync_pill
        + gear
        + "</div>\n</header>\n"
    )


# ── HTML fragments ────────────────────────────────────────────────────────────

_HEAD = """<!doctype html>
<html lang="pt">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>__TITLE__ · email-2-data</title>
<script>/* stamp the theme before first paint — no flash (ADR-035) */(function(){try{var t=localStorage.getItem('e2d-theme');if(t!=='light'&&t!=='dark')t=matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light';document.documentElement.setAttribute('data-theme',t);}catch(e){}})();</script>
<style>
  /* ── tokens (kept in sync with report.py) ───────────────────────────────
     The ADR-033 «Mesa com Foco» palette, ported from the design-proposal artifact: cool graphite
     neutrals, a steel-blue accent, and a CVD-VALIDATED counterparty trio (cliente teal ·
     fornecedor blue · lead amber — worst adjacent pair ΔE 19.4 deutan / 21.0 normal, all ≥3:1 on
     white; lead-purple was REJECTED: ΔE 2.9 protan against supplier blue). Semantic sub-tokens
     (-bg/-line) exist so component CSS never scatters raw hexes again. */
  :root{--bg:#F1F3F6;--card:#fff;--surface2:#F7F9FB;--bd:#DCE2E9;--bd2:#EAEEF2;--tx:#182027;--mut:#46525E;--mut2:#7C8894;
    --ac:#2C5E80;--ac-soft:#E3EDF4;--ac-line:#BDD3E2;--int:#0d9488;--int-bg:#EAF7F5;--int-line:#BFE6E0;--ext:#64748b;
    --red:#B3392E;--red-bg:#F9E9E7;--red-line:#EDCBC7;
    --amber:#96660F;--amber-bg:#F7EFDC;--amber-line:#E9DBB4;
    --green:#2E7D4F;--green-bg:#E4F1E9;--green-line:#C6E0D0;
    --purple:#6b4fd1;--purple-bg:#EFEAFB;
    --cli:#0A8F72;--cli-bg:#DFF1EC;--forn:#3B5FC0;--forn-bg:#E5EAF9;--lead:#A16207;--lead-bg:#F6ECD7;
    --shadow:0 1px 2px rgba(20,28,36,.05),0 1px 3px rgba(20,28,36,.04);
    --rpad:12px;--rfont:13.5px;}
  /* ── dark theme (ADR-035) ───────────────────────────────────────────────
     The validated dark palette from the design-proposal artifact: dark graphite surfaces, a lighter
     steel accent, and the CVD-checked dark counterparty trio (cliente #219980 · fornecedor #6E85DE
     · lead #BA8628 — the trio passes the validator on the dark surface). Token-level, so every
     component that already speaks in var(--…) recolours for free. An early inline script (below)
     stamps data-theme from the saved choice or the OS preference before first paint (no flash), so
     the light `:root` default and this override are all that's needed — no duplicated @media block. */
  :root[data-theme="light"]{color-scheme:light}
  :root[data-theme="dark"]{color-scheme:dark;
    --bg:#10151B;--card:#171E26;--surface2:#1C242D;--bd:#2A343F;--bd2:#232D37;--tx:#E6EBF0;--mut:#A9B4BF;--mut2:#71808C;
    --ac:#7FB0D0;--ac-soft:#1E3140;--ac-line:#2E495C;--int:#4CC2B4;--int-bg:#12332C;--int-line:#2E4A44;--ext:#8B98A8;
    --red:#E2685C;--red-bg:#3A2320;--red-line:#5A342E;
    --amber:#D9A441;--amber-bg:#33290F;--amber-line:#4A3C1C;
    --green:#58B282;--green-bg:#1C3226;--green-line:#2E4A3A;
    --purple:#9C86E8;--purple-bg:#241E3A;
    --cli:#219980;--cli-bg:#12332C;--forn:#6E85DE;--forn-bg:#1F2942;--lead:#BA8628;--lead-bg:#332810;
    --shadow:0 1px 2px rgba(0,0,0,.34),0 1px 3px rgba(0,0,0,.28);}
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
  .logo{display:inline-flex;align-items:center;gap:7px;font-weight:720;font-size:13px;letter-spacing:-.01em;color:var(--mut);margin-right:4px}
  .logo .mark{display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;
    border-radius:7px;background:var(--ac);color:#fff;font:800 10.5px ui-monospace,monospace}
  .nlink{color:var(--mut);text-decoration:none;font-size:13px;font-weight:600;
    padding:5px 10px;border-radius:8px;display:inline-flex;align-items:center;gap:6px}
  .nlink svg{width:15px;height:15px;stroke:currentColor;fill:none;stroke-width:1.7;
    stroke-linecap:round;stroke-linejoin:round;flex:0 0 auto;opacity:.85}
  .nlink:hover{background:var(--bg);color:var(--tx)}
  .nlink.on{background:var(--ac);color:#fff}
  .nlink.on svg{opacity:1}
  .nlink.on:hover{filter:brightness(1.08)}
  .nbadge{background:rgba(255,255,255,.25);border-radius:20px;padding:0 6px;font-size:10px;font-weight:700;font-variant-numeric:tabular-nums}
  .nlink:not(.on) .nbadge{background:var(--red-bg);color:var(--red)}
  .grow{margin-left:auto}
  .hbtn.ic{padding:5px 8px;display:inline-flex;align-items:center}
  .hbtn.ic.on{border-color:var(--ac);color:var(--ac)}
  .hbtn svg{width:15px;height:15px;stroke:currentColor;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
  /* freshness-as-sync pill (ADR-034 P5d) */
  .syncpill{display:inline-flex;align-items:center;gap:7px;border-radius:20px}
  .syncpill .sdot{width:8px;height:8px;border-radius:50%;background:var(--green);flex:0 0 auto}
  .syncpill.stale .sdot{background:var(--amber)}
  .syncpill.syncing .sdot{background:var(--ac);animation:beat 1s ease-in-out infinite}
  .syncpill.stale{color:var(--amber);border-color:var(--amber-line)}
  /* gear menu (Admin + densidade + tema) */
  .gearwrap{position:relative;display:inline-flex}
  .gearmenu{position:absolute;top:38px;right:0;z-index:60;min-width:180px;padding:5px;
    background:var(--card);border:1px solid var(--bd);border-radius:11px;box-shadow:0 6px 22px rgba(0,0,0,.16)}
  .gearmenu .gm{display:flex;align-items:center;gap:9px;width:100%;text-align:left;text-decoration:none;
    border:none;background:none;cursor:pointer;font:600 13px inherit;color:var(--tx);border-radius:8px;padding:8px 10px}
  .gearmenu .gm:hover{background:var(--bd2)}
  .gearmenu .gm svg{width:15px;height:15px;stroke:currentColor;fill:none;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round;color:var(--mut2)}
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
  .row:hover{background:var(--surface2)}
  .row.on{background:var(--ac-soft);border-left-color:var(--ac)}
  .row.leaving{opacity:0;transform:translateX(10px)}
  /* Counterparty identity: the CVD-validated trio — cliente teal · fornecedor blue · lead amber. */
  .cp{flex:0 0 auto;display:inline-block;padding:2px 9px;border-radius:20px;font-size:10px;
    font-weight:700;letter-spacing:.03em;min-width:62px;text-align:center}
  .cp.CLIENT{background:var(--cli-bg);color:var(--cli)} .cp.LEAD{background:var(--lead-bg);color:var(--lead)}
  .cp.SUPPLIER{background:var(--forn-bg);color:var(--forn)}
  .cp.INTERNAL,.cp.OTHER,.cp.BULK{background:var(--bd2);color:var(--mut)}
  /* ── component kit: row body ─────────────────────────────────────────── */
  .rmain{flex:1;min-width:0}
  .subj{font-weight:620;font-size:var(--rfont);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .rmeta{color:var(--mut);font-size:11.5px;margin-top:2px;display:flex;align-items:center;gap:7px;flex-wrap:wrap}
  .why{margin-top:6px;font-size:12px;color:var(--amber);background:var(--amber-bg);border:1px solid var(--amber-line);
    border-radius:8px;padding:6px 10px;line-height:1.5;white-space:normal}
  /* ── component kit: clock ────────────────────────────────────────────── */
  .clock{flex:0 0 auto;font-size:12px;font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap}
  .clock .d{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;
    vertical-align:middle;background:currentColor;aria-hidden:true}
  .clock.red{color:var(--red)} .clock.amber{color:var(--amber)}
  .clock.green{color:var(--green)} .clock.none{color:var(--mut2)}
  /* Only the CRITICAL tier pulses. When most of a real queue is red (email latency ≥ a day is
     normal here), animating every red dot destroys the signal — reserve motion for the oldest. */
  .clock.red.crit .d{animation:beat 2s ease-in-out infinite}
  /* ── component kit: owner chip ───────────────────────────────────────── */
  .owner{flex:0 0 auto;font-size:12px;color:var(--int);background:var(--int-bg);
    border:1px solid var(--int-line);border-radius:20px;padding:2px 10px;cursor:pointer;white-space:nowrap}
  .owner.empty{background:var(--surface2);border-color:var(--bd);color:var(--mut2)}
  /* ── component kit: action buttons ──────────────────────────────────── */
  .acts{flex:0 0 auto;display:flex;gap:5px}
  .acts button,.act-btn{border:1px solid var(--bd);background:var(--card);border-radius:8px;
    cursor:pointer;font-size:13px;color:var(--mut);line-height:1;padding:0 10px;height:30px}
  .acts button{width:30px;padding:0}
  .acts button:hover,.act-btn:hover{border-color:var(--ac);color:var(--ac);background:var(--ac-soft)}
  .act-btn.accept{border-color:var(--green);color:var(--green)}
  .act-btn.accept:hover{background:var(--green-bg)}
  /* ── B5 trust grammar ─────────────────────────────────────────────────── */
  .trust{font-size:10.5px;font-weight:650;border-radius:20px;padding:1px 8px;cursor:pointer;
    font-variant-numeric:tabular-nums;background:var(--card)}
  .trust.proposed{border:1px dashed var(--mut2);color:var(--mut)}
  .trust.committed{border:1px solid var(--int);color:var(--int);background:var(--int-bg)}
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
  .tmsg{background:var(--surface2);border:1px solid var(--bd2);border-radius:9px;padding:8px 11px}
  .tmeta{display:flex;align-items:baseline;flex-wrap:wrap;gap:6px;font-size:11px}
  .taddr{font-weight:650;font-size:12px;color:var(--tx)}
  .tarrow{color:var(--mut2)}
  .tdir{display:inline-flex;align-items:center;gap:3px;font-weight:700;text-transform:uppercase;font-size:9.5px;letter-spacing:.04em}
  .tdir .dicon{width:12px;height:12px;stroke:currentColor;fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
  .tdate{color:var(--mut2);margin-left:auto}
  .tatts{flex-basis:100%;margin-top:3px}
  .tatts-row{display:flex;flex-wrap:wrap;gap:4px;margin-top:3px}
  .tatts-d{margin-top:3px} .tatts-d>summary{cursor:pointer;font-size:11px;font-weight:650;color:var(--ac);list-style:none}
  .tatts-d>summary::-webkit-details-marker{display:none}
  .tatts-d[open]>summary{margin-bottom:4px}
  .tbody{margin-top:6px;font-size:12.5px;line-height:1.5;color:var(--tx);white-space:pre-wrap;word-break:break-word;max-height:260px;overflow:auto}
  .qtoggle,.rawtoggle{margin-top:6px;font-size:11px;font-weight:600;color:var(--mut);background:none;border:none;cursor:pointer;padding:0;display:block}
  .qtoggle:hover,.rawtoggle:hover{color:var(--ac)}
  .rawbody{margin-top:4px;border-top:1px dashed var(--bd);padding-top:6px}
  /* translate-to-English reading aid (ADR-032) */
  .tract{margin-top:6px}
  .trbtn{font-size:11px;font-weight:600;color:var(--mut);background:none;border:none;cursor:pointer;padding:0}
  .trbtn:hover{color:var(--ac)} .trbtn[disabled]{opacity:.55;cursor:default}
  .trbody{margin-top:6px;font-size:12.5px;line-height:1.5;color:var(--tx);white-space:pre-wrap;word-break:break-word;max-height:260px;overflow:auto;border-left:2px solid var(--ac);padding-left:8px}
  .trbody.trerr{border-left-color:var(--red,#dc2626);color:var(--red,#dc2626)}
  .tquote{margin-top:5px;padding-left:9px;border-left:2px solid var(--bd);font-size:12px;line-height:1.45;color:var(--mut);white-space:pre-wrap;word-break:break-word;max-height:300px;overflow:auto}
  .tatt{display:inline-block;max-width:170px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
    vertical-align:middle;font-size:10.5px;background:var(--ac-soft);border:1px solid var(--ac-line);
    color:var(--ac);border-radius:6px;padding:1px 6px;text-decoration:none}
  .tatt:hover{filter:brightness(.97)}
  /* embedded messages (extracted from forwarded chains, not direct IMAP) */
  .tmsg.embedded{background:var(--bd2);border-style:dashed}
  .tembedded{font-size:9.5px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:var(--mut2);padding:1px 6px;border:1px solid var(--bd);border-radius:5px}
  /* provenance badges: which spec fields this message supplied */
  .tprov{margin-top:4px;display:flex;flex-wrap:wrap;gap:4px}
  .tprovbadge{font-size:10px;font-weight:700;background:var(--green-bg);border:1px solid var(--green-line);color:var(--green);border-radius:5px;padding:1px 6px}
  /* ── timeline (C2 Contrapartes) ──────────────────────────────────────── */
  .timeline{list-style:none;margin:0;padding:0}
  .titem{display:flex;gap:12px;padding:10px 0;border-bottom:1px solid var(--bd2)}
  .titem:last-child{border-bottom:none}
  .titem .td{color:var(--mut2);font-size:11px;white-space:nowrap;min-width:64px;padding-top:2px}
  .titem .tc{flex:1;min-width:0}
  .titem .ttype{display:inline-block;font-size:9.5px;font-weight:700;text-transform:uppercase;
    letter-spacing:.05em;padding:1px 7px;border-radius:20px;margin-right:6px}
  .ttype.email{background:var(--ac-soft);color:var(--ac)}
  .ttype.projeto{background:var(--purple-bg);color:var(--purple)}
  /* ── gate items (C3 Para ti) ─────────────────────────────────────────── */
  .gate{background:var(--card);border:1px solid var(--bd);border-radius:14px;
    padding:16px 18px;margin-bottom:10px;box-shadow:var(--shadow)}
  .gate .gkind{display:inline-block;font-size:10px;font-weight:700;text-transform:uppercase;
    letter-spacing:.05em;padding:2px 9px;border-radius:20px;margin-bottom:8px}
  .gkind.rever{background:var(--red-bg);color:var(--red)}
  .gkind.projeto{background:var(--purple-bg);color:var(--purple)}
  .gkind.identidade{background:var(--ac-soft);color:var(--ac)}
  .gate .gtitle{font-weight:640;font-size:14px;margin-bottom:4px}
  .gate .gwhy{font-size:12.5px;color:var(--mut);margin-bottom:10px;line-height:1.5}
  .gate .gacts{display:flex;gap:8px}
  /* ── cluster card (C2 list) ──────────────────────────────────────────── */
  .ccard{background:var(--card);border:1px solid var(--bd);border-left:3px solid transparent;
    border-radius:12px;padding:14px 16px;margin-bottom:8px;cursor:pointer;box-shadow:var(--shadow)}
  .ccard:hover{background:var(--surface2)} .ccard.on{border-left-color:var(--ac);background:var(--ac-soft)}
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
  .toast{position:fixed;bottom:22px;left:50%;transform:translateX(-50%);background:var(--tx);color:#fff;
    padding:9px 16px;border-radius:9px;font-size:13px;box-shadow:var(--shadow);z-index:80}
  .menu{position:absolute;background:var(--card);border:1px solid var(--bd);border-radius:10px;
    box-shadow:0 4px 16px rgba(20,24,28,.14);z-index:60;min-width:170px;padding:4px}
  .menu .mi{padding:7px 11px;border-radius:7px;cursor:pointer;font-size:13px}
  .menu .mi:hover,.menu .mi.on{background:var(--ac-soft);color:var(--ac)}
  .overlay{position:fixed;inset:0;background:rgba(20,24,28,.32);display:flex;align-items:flex-start;
    justify-content:center;z-index:70}
  .overlay.help{align-items:center}
  .card{background:var(--card);border-radius:14px;padding:22px 26px;box-shadow:var(--shadow);max-width:340px}
  .card h3{margin:0 0 12px;font-size:14px}
  .card kbd{background:var(--bg);border:1px solid var(--bd);border-radius:5px;padding:1px 6px;
    font-family:ui-monospace,monospace;font-size:12px}
  .card .kr{display:flex;justify-content:space-between;gap:24px;padding:5px 0;font-size:13px;
    border-top:1px solid var(--bd2)}
  .card .kr:first-of-type{border-top:none}
  .pcard{background:var(--card);border-radius:14px;box-shadow:0 10px 40px rgba(20,24,28,.22);
    width:min(560px,92vw);margin-top:12vh;overflow:hidden}
  #_pq{width:100%;border:0;border-bottom:1px solid var(--bd);padding:15px 18px;font-size:15px;outline:none}
  #_presults{max-height:50vh;overflow:auto;padding:6px}
  .pi{display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:9px;cursor:pointer}
  .pi.on{background:var(--ac-soft)}
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
  /* Two failure strings, honestly distinct: `revertido` ONLY where the optimistic change was in fact
     rolled back; `falhou` where the action simply did not happen (nothing was reverted). */
  nadaDesfazer:'nada para desfazer',desfeito:'desfeito',revertido:'falhou — revertido',falhou:'falhou',
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
/* Direction is the primary axis of a thread — tag it with a colour AND an arrow icon (ADR-034 P5c):
   ↓ recebido (from them), ↑ enviado (from us), · interno. */
const _DIR_DOWN='<svg class="dicon" viewBox="0 0 24 24"><path d="M12 5v13M7 13l5 5 5-5"/></svg>';
const _DIR_UP='<svg class="dicon" viewBox="0 0 24 24"><path d="M12 19V6M7 11l5-5 5 5"/></svg>';
const _DIR_INT='<svg class="dicon" viewBox="0 0 24 24"><path d="M6 12h12"/></svg>';
function msgDirTag(d){
  if(d==='inbound') return {t:'recebido',c:'var(--forn)',i:_DIR_DOWN,k:'inbound'};
  if(d==='internal') return {t:'interno',c:'var(--mut)',i:_DIR_INT,k:'internal'};
  return {t:'enviado',c:'var(--cli)',i:_DIR_UP,k:'outbound'};
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
  // Attachments compact (ADR-034 P5c-fix): chips truncate long names (full name in the title), and
  // a thread with many (a real 14-attachment email exists) collapses behind a «N anexos» summary so
  // it never eats the pane. Native <details> — no JS, and no data-act so the dossier click ignores it.
  const _attL=(m.attachments||[]);
  const _attChips=_attL.map((a,idx)=>
    '<a class="tatt" href="/api/attachment/'+encodeURIComponent(m.message_id)+'/'+idx
    +'" target="_blank" rel="noopener" title="'+esc(a.name||'')+'">📎 '+esc(a.name||'anexo')+'</a>').join('');
  const atts=!_attL.length ? ''
    : (_attL.length<=4 ? '<div class="tatts-row">'+_attChips+'</div>'
       : '<details class="tatts-d"><summary>📎 '+_attL.length+' anexos</summary><div class="tatts-row">'+_attChips+'</div></details>');
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
  // Translate-to-English reading aid (ADR-032): button-only, only where there is a visible body.
  // The delegated handler (translateMsg) reads this message's .tbody text and fills .trbody.
  const trHTML=vis
    ?'<div class="tract"><button class="trbtn" type="button" data-mid="'+esc(m.message_id||'')
       +'">traduzir (EN)</button></div><div class="trbody hidden"></div>'
    :'';
  return '<div class="tmsg dir-'+esc(tag.k)+(m.embedded?' embedded':'')+'">'
    +'<div class="tmeta">'
    +'<span class="tdir" style="color:'+tag.c+'">'+tag.i+tag.t+'</span>'
    +'<span class="taddr">'+esc(m.from_email||'?')+'</span>'
    +'<span class="tarrow">→</span>'
    +'<span class="taddr">'+toStr+'</span>'
    +embeddedBadge
    +'<span class="tdate">'+esc((m.date||'').slice(0,16).replace('T',' '))+'</span>'
    +(atts?'<span class="tatts">'+atts+'</span>':'')
    +'</div>'
    +provBadges
    +visHTML+quoteHTML+rawToggle+trHTML
    +'</div>';
}
/* Render a full thread panel (summary line + all messages). */
function msgThreadHTML(msgs, opts){
  const head='<div class="thead"><span class="tsum">'+esc(msgThreadSummary(msgs))+'</span></div>';
  return '<div class="texp">'+head+msgs.map(m=>msgHTML(m,opts)).join('')+'</div>';
}
/* Translate-to-English reading aid (ADR-032). Reads THIS message's visible .tbody, POSTs it to
   /api/translate, and shows the English in the sibling .trbody. Once translated, re-clicking just
   toggles between the original and the translation (no second call). Never sends, never stored. */
async function translateMsg(btn){
  const msg=btn.closest('.tmsg'); if(!msg) return;
  const slot=msg.querySelector('.trbody'); if(!slot) return;
  if(slot.dataset.done){                         // already have it — just toggle original/translation
    const hid=slot.classList.toggle('hidden');
    btn.textContent=hid?'traduzir (EN)':'ver original';
    return;
  }
  const src=((msg.querySelector('.tbody')||{}).textContent||'').trim();
  if(!src) return;
  btn.disabled=true; const orig=btn.textContent; btn.textContent='a traduzir…';
  slot.classList.remove('trerr');
  try{
    const r=await fetch('/api/translate',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({message_id:btn.dataset.mid||'', text:src})});
    const d=await r.json().catch(()=>({}));
    if(r.ok){ slot.textContent=d.text||''; slot.dataset.done='1'; slot.classList.remove('hidden');
      btn.textContent='ver original'; }
    else { slot.textContent='tradução falhou: '+esc(d.error||('HTTP '+r.status));
      slot.classList.remove('hidden'); slot.classList.add('trerr'); btn.textContent=orig; }
  }catch(err){ slot.textContent='tradução falhou: sem resposta do servidor';
    slot.classList.remove('hidden'); slot.classList.add('trerr'); btn.textContent=orig; }
  finally{ btn.disabled=false; }
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
/* ── freshness-as-sync pill (ADR-034 P5d) ────────────────────────────────
   One control shows the sync status (dot: green fresh / amber stale / spinning) + the age, and
   clicking it syncs. Lenses feed the time via setSynced(iso); the shared shell owns the label. */
function _agoLabel(iso){if(!iso)return'';var s=Math.max(0,(Date.now()-Date.parse(iso))/1000);if(s<90)return'agora mesmo';var m=Math.round(s/60);if(m<60)return'há '+m+' min';var h=Math.round(m/60);return'há '+h+(h===1?' hora':' horas');}
let _syncedIso=null;
function setSynced(iso,syncing){var p=$('#_syncbtn'),l=$('#_synclbl');if(!p)return;if(iso)_syncedIso=iso;if(syncing){p.classList.add('syncing');p.classList.remove('stale');if(l)l.textContent='a sincronizar…';return;}p.classList.remove('syncing');if(!_syncedIso){if(l)l.textContent='Sincronizar';return;}var age=(Date.now()-Date.parse(_syncedIso))/1000;p.classList.toggle('stale',age>45*60);if(l)l.textContent='correio '+_agoLabel(_syncedIso);}
/* Post-sync: a lens that defines onSynced() refreshes ITSELF in place (ADR-023/§7 — a reload throws
   away the user's position mid-decision); lenses without the hook keep the legacy reload. */
async function syncNow(){setSynced(null,true);toast(S.sincronizando);try{const r=await fetch('/api/sync',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});if(r.status===409){toast(S.syncEmCurso);setSynced();return;}if(!r.ok){toast(S.syncFalhou);setSynced();return;}await r.json();if(typeof onSynced==='function'){onSynced();}else{toast(S.sincronizado);setTimeout(()=>location.reload(),700);}}catch(e){toast(S.syncFalhou);setSynced();}}
/* Nav badge refresh from any lens poll (shared; a lens-local copy may shadow this harmlessly). */
function setNavCounts(counts){document.querySelectorAll('.nlink[data-nav]').forEach(a=>{const n=(counts||{})[a.dataset.nav]||0;let b=a.querySelector('.nbadge');if(n){if(!b){b=document.createElement('span');b.className='nbadge';a.appendChild(b);}b.textContent=n;}else if(b){b.remove();}});}
"""

# ── shell event wiring (runs after lens JS, calls lens functions) ─────────────
_SHELL_EVENTS = r"""
/* Translate-to-English (ADR-032): one delegated, CAPTURE-phase handler covers every page that renders
   msgHTML (Fila, Projetos-Origem, Para-ti) without per-page wiring. Capture + stopPropagation so a
   click on the button never also triggers the ancestor row/detail click handlers underneath it. */
document.addEventListener('click',e=>{
  const b=e.target.closest('.trbtn'); if(!b) return;
  e.preventDefault(); e.stopPropagation(); translateMsg(b);
}, true);
$('#_pq').addEventListener('input',e=>{_pi=paletteItems(e.target.value);_pf=0;_rp();});
$('#_presults').addEventListener('click',e=>{const el=e.target.closest('.pi');if(el)_runP(parseInt(el.dataset.i,10));});
$('#_palette').addEventListener('click',e=>{if(e.target.id==='_palette')closePalette();});
$('#_help').addEventListener('click',e=>{if(e.target.id==='_help')$('#_help').classList.add('hidden');});
const _sb=$('#_syncbtn');if(_sb)_sb.addEventListener('click',syncNow);
const _db=$('#_denbtn');if(_db)_db.addEventListener('click',toggleDensity);
/* gear menu (Admin + densidade + tema) — toggle + close on outside click */
const _gb=$('#_gearbtn');if(_gb)_gb.addEventListener('click',e=>{e.stopPropagation();const m=$('#_gearmenu');if(m)m.classList.toggle('hidden');});
document.addEventListener('click',e=>{const m=$('#_gearmenu');if(m&&!m.classList.contains('hidden')&&!e.target.closest('.gearwrap'))m.classList.add('hidden');});
/* sync pill: seed from the lens's SYNCED_AT embed (if any) and keep the «há N min» fresh */
try{if(typeof SYNCED_AT!=='undefined'&&SYNCED_AT)setSynced(SYNCED_AT);}catch(e){}
setInterval(()=>{const p=$('#_syncbtn');if(p&&!p.classList.contains('syncing'))setSynced();},60000);
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
  /* '/' dispatches through an optional lens hook (ADR-033 P0): the Fila focuses its visible search
     box — the natural gesture — while every lens that defines no onSlash keeps '/' = palette. */
  if(e.key==='/'){ if(typeof onSlash==='function'){onSlash();}else{openPalette();} e.preventDefault();return;}
  if(e.key==='?'){$('#_help').classList.toggle('hidden');return;}
  if(e.key==='Escape'){$('#_help').classList.add('hidden');closePalette();onEsc();return;}
  if(!$('#_help').classList.contains('hidden'))return;
  if(e.key==='z'||e.key==='Z'){doUndo();return;}
  onKey(e);
});
try{if(localStorage.getItem('fila-density')==='compact')document.body.classList.add('compact');}catch(e){}
/* theme toggle (ADR-035): flip data-theme, persist, swap the icon. The pre-paint <head> script set
   the initial theme from the saved choice / OS preference. Moon when light (→ go dark), sun when dark. */
const _MOON='<svg viewBox="0 0 24 24"><path d="M20 14.5A8 8 0 0 1 9.5 4 7 7 0 1 0 20 14.5z"/></svg>';
const _SUN='<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2.2M12 19.3v2.2M4.6 4.6l1.6 1.6M17.8 17.8l1.6 1.6M2.5 12h2.2M19.3 12h2.2M4.6 19.4l1.6-1.6M17.8 6.2l1.6-1.6"/></svg>';
function _paintTheme(){const dk=document.documentElement.getAttribute('data-theme')==='dark';const b=$('#_themebtn');if(b){b.innerHTML=(dk?_SUN:_MOON)+'<span>'+(dk?'Tema claro':'Tema escuro')+'</span>';}}
_paintTheme();
const _thb=$('#_themebtn');if(_thb)_thb.addEventListener('click',()=>{const dk=document.documentElement.getAttribute('data-theme')==='dark';const nx=dk?'light':'dark';document.documentElement.setAttribute('data-theme',nx);try{localStorage.setItem('e2d-theme',nx);}catch(e){}_paintTheme();});
"""
