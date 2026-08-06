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
    # ADR-044 moved the Fila off "/" — "/" is Início, the landing page. The Fila keeps every URL it
    # had except that one. Its OWN deep links (?tab=, ?thread=, …) survived because syncURL() builds
    # them from location.pathname — but that is only true of links the Fila writes for itself, and
    # reasoning from it is what broke six CROSS-page links, which hard-coded "/?thread=" and so
    # landed on Início, a page that reads no query parameter at all. The rule for anything added
    # here or on any other page: a link leaving the page it is written on has no location.pathname
    # to inherit and MUST name its route in full ("/fila?thread=", never "/?thread=").
    ("fila",         "Fila",          "/fila"),
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
    person: dict[str, Any] | None = None,
) -> str:
    """Assemble a full cockpit lens page.

    ``active``     — one of "fila" | "contrapartes" | "projetos" | "para-ti" | "capturas" | "admin"
    ``body_html``  — the main content area (everything below the header)
    ``embeds``     — {NAME: value} → ``const NAME = <json>;`` injected before lens_js
    ``lens_js``    — lens-specific JS; must define render(), paletteItems(q), onKey(e)
    ``nav_counts`` — {key: n} → badge on nav items (e.g. {"fila": 54, "para-ti": 3})
    ``extra_css``  — lens-specific CSS appended to the kit (keep small)
    ``person``     — the signed-in person row (``request.state.person``), or None.

    ``person=None`` is **default-deny**, matching the gate: no account menu and no Administração
    entry. A builder that forgets to forward it therefore costs an *admin* their link — visible and
    self-reporting — rather than offering a member a door ADR-040 already locked, which nobody
    would notice. `test_every_lens_forwards_the_signed_in_person_to_the_shell` closes that gap.

    Every lens also gets ``const IS_ADMIN`` for free, injected here rather than passed by each
    builder. The nav's «Administração» has been gated since ADR-040, but the ⌘K palettes are JS and
    could not see `person` at all, so two of them kept offering doors the gate refuses — `/admin` on
    Projetos and `/inbox` on the Fila (both in `_ADMIN_EXACT`). Deriving it once means a new palette
    entry is gated by asking `IS_ADMIN`, not by remembering that a page needed a new parameter — the
    same reason the auth gate is middleware rather than a decorator (non-negotiable #6).
    """
    counts = nav_counts or {}
    # Default-deny, and `is_admin` may arrive as 0/1 from SQLite — bool() so the embed is a real
    # JS boolean and `if(IS_ADMIN)` cannot read a truthy string.
    consts = f"const IS_ADMIN = {_embed(bool(person and person.get('is_admin')))};\n" + "\n".join(
        f"const {k.upper()} = {_embed(v)};" for k, v in (embeds or {}).items()
    )
    return (
        _HEAD.replace("__TITLE__", _esc_html(title)).replace("__EXTRACSS__", extra_css)
        + _nav_html(active, counts, person)
        + body_html
        + _OVERLAYS
        + f"\n<script>\n{_SHELL_UTILS}\n</script>\n"
        # A lens whose render() throws used to log to a console nobody has open and leave the nav
        # sitting above an empty page — indistinguishable from "there is nothing here", which is a
        # claim the app has no basis for. bootFailed() says a screen is missing rather than
        # letting an empty one speak for it.
        + f"\n<script>\n{consts}\n{lens_js}\n"
          f"try{{render();}}catch(_e){{bootFailed(_e);}}\n</script>\n"
        + f"\n<script>\n{_SHELL_EVENTS}\n</script>\n"
        + "\n</body>\n</html>"
    )


def forbidden_page(nav_counts: dict[str, int] | None = None,
                   person: dict[str, Any] | None = None) -> str:
    """The HTML body for an admin-only path reached by a signed-in non-admin (ADR-040).

    Rendered through ``page()`` — the same shell as every lens — for two reasons. It keeps the nav
    on screen, so a wrong turn is a dead end of one click rather than a dead end full stop; and it
    keeps the app's second "you cannot see this" screen out of ``auth_page.py``, which is already a
    parallel design system slated for consolidation. One more page there is one more page to merge.

    Says *what* was refused and *why*, not merely "403": the refusal is a fact about this account,
    and a screen that hides its own reason trains people to reload rather than to ask for access.
    """
    body = (
        "<div class='wrap'><div class='forbid'>"
        "<h1>Área reservada a administradores</h1>"
        "<p>A tua conta está ativa e autenticada — só não tem permissão de administrador, "
        "que é o que esta página exige.</p>"
        "<p class='fmut'>Se precisas de acesso, pede a um administrador para te promover.</p>"
        "<p><a class='fback' href='/'>← Voltar ao início</a></p>"
        "</div></div>"
    )
    css = (".forbid{background:var(--card);border:1px solid var(--bd);border-radius:14px;"
           "box-shadow:var(--shadow);padding:26px 28px;max-width:560px;margin:32px auto}"
           ".forbid h1{font-size:19px;margin:0 0 12px}"
           ".forbid p{margin:0 0 10px;color:var(--mut);font-size:13.5px;line-height:1.6}"
           ".forbid .fmut{color:var(--mut2)}"
           ".forbid .fback{color:var(--ac);text-decoration:none;font-weight:600;font-size:13px}"
           ".forbid .fback:hover{text-decoration:underline}")
    return page("Sem acesso", "", body, lens_js=_STATIC_LENS, nav_counts=nav_counts or {},
                extra_css=css, person=person)


# A no-op lens for the shell's own form pages. page() calls render() and the shell event wiring calls
# paletteItems()/onKey(); without all three the shell throws on a page whose whole point is to not be
# broken. Shared, so the next such page cannot forget one of them.
_STATIC_LENS = ("function render(){}\n"
                "function paletteItems(q){return [];}\n"
                "function onKey(e){}\n")


def _short_ts(iso: str) -> str:
    """"2026-07-25T14:03:12+00:00" → "2026-07-25 14:03". UTC, and the page says so — a session list
    that silently prints UTC as if it were local time makes "was that me?" unanswerable."""
    return (iso or "").replace("T", " ")[:16]


def account_page(person: dict[str, Any],
                 *,
                 sessions: list[dict[str, Any]] | None = None,
                 scopes: list[str] | None = None,
                 must_change: bool = False,
                 signature: str = "",
                 signature_preview: str = "",
                 error: str = "",
                 ok: str = "",
                 nav_counts: dict[str, int] | None = None) -> str:
    """«A minha conta» (ADR-041) — the one surface a person owns outright.

    Before this, changing a password required an admin at a terminal minting a fresh invite, so the
    realistic response to "I think someone saw my password" was to do nothing. Everything here acts
    only on the signed-in person; there is no id in the URL and nothing to tamper with.

    ``must_change`` renders the forced-change state: the gate is holding every other page, and the
    page has to say why, or it looks like the app broke on the way to the Fila.

    ``signature`` is the person's raw template (what the editor edits) and ``signature_preview`` is
    that template RENDERED with their values (ADR-047). Both, because they answer different
    questions: the template is what you change, the preview is what the client reads — and with the
    empty-line rule in play you genuinely cannot infer the second from the first.
    """
    name = _esc_html(str(person.get("name") or ""))
    role = "Administrador" if person.get("is_admin") else "Membro"
    # Stated as "atribuídas", never as "o que vês": ADR-040 records that per-person visibility is NOT
    # enforced — every signed-in person still sees every thread. Printing these as a restriction would
    # be the UI asserting something the app does not do.
    caixas = ("todas (administrador)" if person.get("is_admin")
              else (", ".join(scopes or []) or "nenhuma atribuída"))
    rows = "".join(
        f"<tr><td>{_esc_html(_short_ts(s.get('created_ts', '')))}</td>"
        f"<td>{_esc_html(_short_ts(s.get('last_seen', '')))}</td>"
        f"<td class='ua'>{_esc_html((s.get('user_agent') or '—')[:70])}</td></tr>"
        for s in (sessions or []))
    banner = ""
    if must_change:
        banner = ("<div class='abox warn'><b>Tens de definir uma nova palavra-passe.</b>"
                  " A atual foi definida por um administrador e é temporária — o resto da aplicação"
                  " fica em espera até a mudares.</div>")
    elif error:
        banner = f"<div class='abox bad'>{_esc_html(error)}</div>"
    elif ok:
        banner = f"<div class='abox good'>{_esc_html(ok)}</div>"
    body = (
        "<div class='wrap'>"
        "<h1 class='ah1'>A minha conta</h1>"
        + banner
        + "<div class='acard'>"
          "<div class='arow'><span class='ak'>Nome</span>"
          f"<span class='av'>{name}</span></div>"
          "<div class='arow'><span class='ak'>Perfil</span>"
          f"<span class='av'>{role}</span></div>"
          "<div class='arow'><span class='ak'>Caixas atribuídas</span>"
          f"<span class='av'>{_esc_html(caixas)}</span></div>"
          "<p class='amut' style='margin:6px 0 0'>As caixas atribuídas ainda não filtram o que vês — "
          "por agora servem só de registo.</p>"
          "</div>"
        # The signature sits ABOVE the password card: it is the thing someone comes here to change
        # more than once, and burying self-service behind a security form is how a feature stays
        # unused. Sign-off wording is also the one thing on this page a client ever sees.
        + "<div class='acard'>"
          "<h2 class='ah2'>A minha assinatura</h2>"
          "<p class='amut'>O fecho dos teus rascunhos de resposta. Em branco usa o fecho da"
          " empresa. A aplicação <b>nunca envia</b> — o rascunho é para reveres e enviares.</p>"
          '<form method="post" action="/a-minha-conta/assinatura" class="aform wide">'
          f'<label for="_sig_role">Função<input id="_sig_role" type="text" name="job_title"'
          f' value="{_esc_html(str(person.get("job_title") or ""))}" autocomplete="organization-title"'
          f' placeholder="Ex.: Produção" maxlength="80"></label>'
          f'<label for="_sig_tel">Telefone<input id="_sig_tel" type="text" name="phone"'
          f' value="{_esc_html(str(person.get("phone") or ""))}" autocomplete="tel"'
          f' placeholder="Ex.: +351 912 345 678" maxlength="40"></label>'
          '<label for="_sig_body">Assinatura'
          f'<textarea id="_sig_body" name="signature" rows="7" spellcheck="false"'
          f' placeholder="Em branco = fecho da empresa">{_esc_html(signature)}</textarea></label>'
          '<p class="amut sighelp">Podes usar <code>{nome}</code>, <code>{cargo}</code>,'
          " <code>{telefone}</code> e <code>{email}</code>. Uma linha cujos campos estejam todos"
          " vazios desaparece — por isso põe um campo por linha.<br>"
          # Said BEFORE the paste, not only after it: the person about to paste an Outlook block is
          # the person who most needs to know it will come back as text.
          "Podes colar a tua assinatura do Outlook/Gmail — reconhecemos o HTML e convertemos"
          " para texto (logótipos e ícones não passam; os rascunhos são texto simples).</p>"
          '<button class="abtn" type="submit">Guardar assinatura</button>'
          "</form>"
          '<div class="sigprev"><span class="ak">Como fica</span>'
          + (f"<pre>{_esc_html(signature_preview)}</pre>" if signature_preview.strip()
             else "<p class='amut' style='margin:4px 0 0'>Sem fecho — os rascunhos terminam no"
                  " texto.</p>")
          + "</div></div>"
        + "<div class='acard'>"
          "<h2 class='ah2'>Mudar a palavra-passe</h2>"
        "<p class='amut'>Todas as outras sessões terminam; esta continua aberta.</p>"
        '<form method="post" action="/a-minha-conta/palavra-passe" class="aform">'
        # THE ACCOUNT ANCHOR, and it is load-bearing. Three password boxes with no username field is
        # an ambiguous form: the username is what tells a password manager "this CHANGES the password
        # of account X" instead of "this CREATES one". Without it NordPass read «Palavra-passe atual»
        # as a new-password field, offered to generate one, and did not recognise the real ones — so a
        # generated string nobody ever saw became the account password, twice, each time a lockout
        # only `auth reset` could undo. `autocomplete` alone does not carry this and never will.
        # RENDERED, not `hidden`: managers are entitled to skip what is not displayed, and saying
        # which account you are about to change is worth a row on its own.
        f'<label for="_acct_user">Conta<input id="_acct_user" class="ro" type="text" name="username"'
        f' value="{name}" autocomplete="username" readonly tabindex="-1"></label>'
        '<label for="_pw_cur">Palavra-passe atual<input id="_pw_cur" type="password" name="current"'
        ' autocomplete="current-password" required></label>'
        '<label for="_pw_new">Nova palavra-passe<input id="_pw_new" type="password" name="new"'
        ' autocomplete="new-password" minlength="8" required></label>'
        '<label for="_pw_cnf">Confirmar<input id="_pw_cnf" type="password" name="confirm"'
        ' autocomplete="new-password" minlength="8" required></label>'
        # The safety net under all of that: whatever got filled in, you can READ it before committing.
        # An unreadable field is how a wrong autofill becomes the password of record unnoticed.
        '<label class="apeek" for="_pwshow"><input type="checkbox" id="_pwshow">'
        "Mostrar as palavras-passe</label>"
        "<button class='abtn' type='submit'>Mudar palavra-passe</button>"
        "</form></div>"
        "<div class='acard'>"
        "<h2 class='ah2'>Sessões abertas</h2>"
        "<p class='amut'>Datas em UTC.</p>"
        "<table class='atab'><thead><tr><th>Início</th><th>Última atividade</th>"
        "<th>Dispositivo</th></tr></thead><tbody>"
        + (rows or "<tr><td colspan='3' class='amut'>Nenhuma sessão registada.</td></tr>")
        + "</tbody></table>"
          '<form method="post" action="/a-minha-conta/sessoes">'
          "<button class='abtn ghost' type='submit'>Terminar as outras sessões</button>"
          "</form></div>"
          "</div>"
    )
    css = (".ah1{font-size:20px;margin:22px 2px 14px}"
           ".ah2{font-size:15px;margin:0 0 4px}"
           ".acard{background:var(--card);border:1px solid var(--bd);border-radius:14px;"
           "box-shadow:var(--shadow);padding:18px 20px;max-width:620px;margin:0 0 14px}"
           ".arow{display:flex;gap:14px;padding:6px 0;font-size:13.5px}"
           ".arow .ak{width:120px;color:var(--mut2);font-weight:600}"
           ".arow .av{color:var(--tx)}"
           ".amut{color:var(--mut2);font-size:12.5px;margin:0 0 12px}"
           ".aform{display:flex;flex-direction:column;gap:10px;max-width:320px}"
           ".aform label{display:flex;flex-direction:column;gap:4px;font-size:12.5px;"
           "font-weight:600;color:var(--mut)}"
           ".aform input{padding:8px 10px;border:1px solid var(--bd);border-radius:8px;"
           "background:var(--bg);color:var(--tx);font:inherit;font-size:13px}"
           ".aform input.ro{background:var(--bd2);color:var(--mut);cursor:default}"
           # The signature form is prose, not credentials — 320px would wrap every line of a real
           # sign-off and make the editor lie about how the block looks.
           ".aform.wide{max-width:100%}"
           ".aform textarea{padding:9px 11px;border:1px solid var(--bd);border-radius:8px;"
           "background:var(--bg);color:var(--tx);font:inherit;font-size:13px;line-height:1.5;"
           "resize:vertical;white-space:pre}"
           ".aform .sighelp{margin:0;font-weight:400}"
           ".aform .sighelp code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;"
           "font-size:11.5px;background:var(--bd2);border-radius:5px;padding:1px 5px;color:var(--tx)}"
           # The preview is the answer to "what does the client actually read", so it renders in the
           # message font at message size — a shrunken grey box would be a different question.
           ".sigprev{margin-top:14px;padding-top:12px;border-top:1px solid var(--bd2)}"
           ".sigprev .ak{display:block;color:var(--mut2);font-weight:600;font-size:12.5px;"
           "margin-bottom:6px}"
           ".sigprev pre{margin:0;padding:11px 13px;border:1px solid var(--bd);border-radius:9px;"
           "background:var(--bg);color:var(--tx);font:inherit;font-size:13px;line-height:1.5;"
           "white-space:pre-wrap;word-break:break-word}"
           ".aform .apeek{flex-direction:row;align-items:center;gap:7px;cursor:pointer;"
           "color:var(--mut2);font-weight:600;margin-top:2px}"
           ".aform .apeek input{width:auto;padding:0;margin:0;accent-color:var(--ac)}"
           # --card, not #fff: --ac is a dark blue in the light theme and a PALE one in the dark
           # theme, so a label pinned to white disappears in exactly one of them.
           ".abtn{align-self:flex-start;margin-top:4px;padding:8px 14px;border-radius:9px;border:none;"
           "background:var(--ac);color:var(--card);font:600 13px inherit;cursor:pointer}"
           ".abtn.ghost{background:none;border:1px solid var(--bd);color:var(--mut);margin-top:10px}"
           ".abtn.ghost:hover{border-color:var(--ac);color:var(--ac)}"
           ".atab{width:100%;border-collapse:collapse;font-size:12.5px}"
           ".atab th{text-align:left;color:var(--mut2);font-weight:600;padding:4px 8px 6px 0;"
           "border-bottom:1px solid var(--bd)}"
           ".atab td{padding:6px 8px 6px 0;border-bottom:1px solid var(--bd2);color:var(--tx)}"
           ".atab .ua{color:var(--mut2)}"
           ".abox{max-width:620px;padding:11px 14px;border-radius:11px;font-size:13px;"
           "line-height:1.55;margin:0 0 14px;border:1px solid var(--bd)}"
           ".abox.good{border-color:var(--green-line);color:var(--green)}"
           ".abox.bad{border-color:var(--red-line);color:var(--red)}"
           ".abox.warn{border-color:var(--amber-line);color:var(--amber)}")
    # The one behaviour this page has. Deliberately tiny and dependency-free: it runs on the screen
    # where a mistake costs you your own account.
    lens = _STATIC_LENS + (
        "var _pwb=document.getElementById('_pwshow');\n"
        "if(_pwb)_pwb.addEventListener('change',function(){\n"
        "  var t=_pwb.checked?'text':'password';\n"
        "  ['_pw_cur','_pw_new','_pw_cnf'].forEach(function(id){\n"
        "    var el=document.getElementById(id); if(el)el.type=t;});\n"
        "});\n")
    return page("A minha conta", "", body, lens_js=lens, nav_counts=nav_counts or {},
                extra_css=css, person=person)


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
_SIGNOUT_ICON = ('<svg viewBox="0 0 24 24"><path d="M15 5H6a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h9"/>'
                 '<path d="M18 15l3-3-3-3M21 12h-9"/></svg>')
_USER_ICON = ('<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="3.4"/>'
              '<path d="M5 20c0-3.6 3.1-6.2 7-6.2s7 2.6 7 6.2"/></svg>')


def _initials(name: str) -> str:
    """Up to two initials for the avatar chip. Falls back to "?" — never to an empty circle, which
    would read as a rendering bug rather than as an unnamed account."""
    parts = [p for p in (name or "").split() if p]
    if not parts:
        return "?"
    letters = parts[0][:1] + (parts[-1][:1] if len(parts) > 1 else "")
    return letters.upper()


def _account_html(person: dict[str, Any] | None) -> str:
    """The identity control: who is signed in, in what role, and the way out.

    Sits between the sync pill (status) and the gear (config) because it is neither — on a shared
    workshop machine «whose session is this?» is the question you ask before you touch anything, and
    burying it in a menu means the honest answer costs a click nobody spends.

    Sign-out is a **form POST**: `/logout` revokes the session row server-side (ADR-039), so an
    anchor would 405 and leave the person signed in while looking like it had worked.
    """
    if not person:
        return ""
    name = _esc_html(str(person.get("name") or ""))
    role = "Administrador" if person.get("is_admin") else "Membro"
    return (
        "<div class='acctwrap'>"
        f"<button class='hbtn acct' id='_acctbtn' aria-haspopup='true' aria-label='Conta'>"
        f"<span class='avatar'>{_esc_html(_initials(str(person.get('name') or '')))}</span>"
        f"<span class='anm'>{name}</span></button>"
        "<div class='acctmenu hidden' id='_acctmenu' role='menu'>"
        f"<div class='amhd'><span class='amnm'>{name}</span><span class='amrl'>{role}</span></div>"
        f'<a class="gm" href="/a-minha-conta">{_USER_ICON}<span>A minha conta</span></a>'
        "<form method='post' action='/logout'>"
        f"<button class='gm' type='submit' id='_signoutbtn' role='menuitem'>{_SIGNOUT_ICON}"
        "<span>Terminar sessão</span></button>"
        "</form>"
        "</div></div>"
    )


def _nav_html(active: str, counts: dict[str, int], person: dict[str, Any] | None = None) -> str:
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
    # ADR-040 locked /admin and left its door in plain sight — a member clicked «Administração» and
    # got a 403, once per page, forever. The link now agrees with the gate. Densidade and tema stay:
    # they are preferences, not privileges.
    admin_item = (
        f'<a class="gm" data-nav="admin" href="/admin">{_NAV_ICON["admin"]}<span>Administração</span></a>'
        if person and person.get("is_admin") else ""
    )
    gear = (
        "<div class='gearwrap'>"
        f"<button class='hbtn ic{gear_on}' id='_gearbtn' aria-haspopup='true' aria-label='Definições'>{_GEAR_ICON}</button>"
        "<div class='gearmenu hidden' id='_gearmenu' role='menu'>"
        + admin_item
        + "<button class='gm' id='_denbtn' role='menuitem'>Densidade</button>"
        "<button class='gm' id='_themebtn' role='menuitem'>Tema claro / escuro</button>"
        "</div></div>"
    )
    # The logo is the way back to Início (ADR-044). It carries the active state on "/" because no
    # lens link does — a header where nothing is marked reads as "you are nowhere", which is exactly
    # the complaint the landing page exists to answer.
    logo_on = " on" if active == "inicio" else ""
    return (
        "<header>\n<div class='htop'>"
        + f"<a class='logo{logo_on}' href='/' title='Início'>"
          "<span class='mark'>e2d</span>email-2-data</a>"
        + "".join(links)
        + "<span class='grow'></span>"
        + sync_pill
        + _account_html(person)
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
    /* transient evidence highlight (fila-evidence §Phase 3) — the ONE colour in this palette that
       means nothing on its own. Every other hue is committed: bands are urgency, the trio is
       counterparty, --int is a checksum FACT, --ac is «selected». A highlight that reused any of
       them would read as a claim about the text it lands on. Body-text background only, and only
       while a ledger row is picked, so it never sits beside the counterparty trio. */
    --hl-bg:#FFDA47;--hl-tx:#1A1405;
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
    --hl-bg:#8A6A12;--hl-tx:#FFF6DF;
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
  .logo{display:inline-flex;align-items:center;gap:7px;font-weight:720;font-size:13px;letter-spacing:-.01em;color:var(--mut);margin-right:4px;
    text-decoration:none;padding:4px 8px 4px 4px;border-radius:9px}
  .logo:hover{background:var(--bg);color:var(--tx)}
  .logo.on{background:var(--ac-soft);color:var(--ac)}   /* on Início — see _nav_html */
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
  .gearmenu .gm,.acctmenu .gm{display:flex;align-items:center;gap:9px;width:100%;text-align:left;text-decoration:none;
    border:none;background:none;cursor:pointer;font:600 13px inherit;color:var(--tx);border-radius:8px;padding:8px 10px}
  .gearmenu .gm:hover,.acctmenu .gm:hover{background:var(--bd2)}
  .gearmenu .gm svg,.acctmenu .gm svg{width:15px;height:15px;stroke:currentColor;fill:none;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round;color:var(--mut2)}
  /* account menu (who is signed in · role · sign out) — ADR-041 */
  .acctwrap{position:relative;display:inline-flex}
  .acct{display:inline-flex;align-items:center;gap:7px;max-width:190px;padding:4px 9px 4px 4px;border-radius:20px}
  .acct .avatar{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;
    border-radius:50%;background:var(--bd2);color:var(--mut2);font-size:10.5px;font-weight:700;letter-spacing:.3px}
  .acct .anm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .acct:hover .avatar{color:var(--ac)}
  .acctmenu{position:absolute;top:38px;right:0;z-index:60;min-width:200px;padding:5px;
    background:var(--card);border:1px solid var(--bd);border-radius:11px;box-shadow:0 6px 22px rgba(0,0,0,.16)}
  .acctmenu .amhd{display:flex;flex-direction:column;gap:1px;padding:7px 10px 9px;margin-bottom:4px;
    border-bottom:1px solid var(--bd)}
  .acctmenu .amnm{font-size:13px;font-weight:700;color:var(--tx)}
  .acctmenu .amrl{font-size:11.5px;color:var(--mut2)}
  .acctmenu form{margin:0}
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
  .qtoggle,.rawtoggle,.stoggle{margin-top:6px;font-size:11px;font-weight:600;color:var(--mut);background:none;border:none;cursor:pointer;padding:0;display:block}
  .qtoggle:hover,.rawtoggle:hover,.stoggle:hover{color:var(--ac)}
  /* the evidence span for the picked ledger value (fila-evidence §Phase 3). ::highlight() paints
     over live Ranges — no element is inserted, so the nextElementSibling toggles below are safe. */
  ::highlight(evid){background:var(--hl-bg);color:var(--hl-tx)}
  /* the sender's closing block — collapsed, never deleted (fila-evidence §Phase 2) */
  .tsig{margin-top:5px;padding-left:9px;border-left:2px dashed var(--bd);font-size:12px;line-height:1.45;
    color:var(--mut);white-space:pre-wrap;word-break:break-word;max-height:260px;overflow:auto}
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
  /* ── the attachment funnel (ADR-046) ─────────────────────────────────── */
  .attf{background:var(--surface2);border:1px solid var(--bd2);border-radius:9px;padding:9px 11px}
  .attf-h{display:flex;align-items:baseline;flex-wrap:wrap;gap:8px;font-size:11px;font-weight:700;
    text-transform:uppercase;letter-spacing:.04em;color:var(--mut)}
  .attf-h .attf-sub{font-weight:500;text-transform:none;letter-spacing:0;color:var(--mut2);
    font-variant-numeric:tabular-nums}
  .attf-band{margin-top:8px}
  .attf-bl{display:flex;align-items:baseline;gap:6px;font-size:10px;font-weight:700;
    text-transform:uppercase;letter-spacing:.05em;color:var(--mut2);margin-bottom:5px}
  .attf-bl b{color:var(--tx);font-variant-numeric:tabular-nums}
  /* Every band is an INFERENCE (nothing in MIME says "logo") — the badge says so, and each tile
     carries the deterministic reason in its own caption + title. */
  .attf-inf{font-size:9px;font-weight:700;letter-spacing:.05em;color:var(--mut2);
    border:1px solid var(--bd);border-radius:4px;padding:0 4px;cursor:help}
  .attf-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(132px,1fr));gap:7px}
  .atti{display:flex;flex-direction:column;gap:3px;text-decoration:none;background:var(--card);
    border:1px solid var(--bd);border-radius:8px;padding:6px;overflow:hidden}
  .atti:hover{border-color:var(--ac-line);background:var(--ac-soft)}
  .atti-t{height:74px;display:flex;align-items:center;justify-content:center;border-radius:5px;
    background:var(--bd2);overflow:hidden}
  .atti-t img{max-width:100%;max-height:100%;object-fit:contain;display:block}
  .atti-g{font-size:24px;line-height:1}
  .atti-n{font-size:11px;font-weight:650;color:var(--tx);overflow:hidden;text-overflow:ellipsis;
    white-space:nowrap}
  .atti-m{font-size:9.5px;color:var(--mut2);font-variant-numeric:tabular-nums;overflow:hidden;
    text-overflow:ellipsis;white-space:nowrap}
  .atti-ev{font-size:9px;color:var(--mut2);opacity:.85;overflow:hidden;text-overflow:ellipsis;
    white-space:nowrap;font-style:italic}
  .atti-n8{position:absolute}
  /* A file whose bytes are gone from the sole-copy capture store (ADR-020) — listed, never hidden. */
  .atti.gone{border-style:dashed;opacity:.72}
  /* Provenance line (ADR-052). Its own element OUTSIDE the tile anchor — it carries a link of its
     own, and an <a> inside an <a> is invalid HTML the browser silently un-nests. */
  .attw{display:flex;flex-direction:column;min-width:0}
  .attw>.atti{border-bottom-left-radius:0;border-bottom-right-radius:0;border-bottom:none}
  .atti-src{font-size:9.5px;color:var(--mut2);background:var(--surface2);border:1px solid var(--bd);
    border-radius:0 0 8px 8px;padding:3px 6px;display:flex;align-items:baseline;gap:5px;
    overflow:hidden;white-space:nowrap}
  /* min-width:0 is the load-bearing half — a flex item defaults to min-width:auto and refuses to
     shrink below its text, which is what pushed the jump link out of the tile. */
  .atti-who{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis}
  .atti-src.cap{color:var(--ext,var(--mut2))}
  .atti-jump{flex:0 0 auto;color:var(--ac);text-decoration:none;font-weight:600}
  .atti-jump:hover{text-decoration:underline}
  .attf-sig>summary{cursor:pointer;list-style:none;font-size:10px;font-weight:700;
    text-transform:uppercase;letter-spacing:.05em;color:var(--mut2);margin-bottom:5px}
  .attf-sig>summary::-webkit-details-marker{display:none}
  .attf-sig>summary:hover{color:var(--ac)}
  .attf-sig>summary b{color:var(--tx);font-variant-numeric:tabular-nums}
  .attf-empty{font-size:11.5px;color:var(--mut2)}
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
  /* Session-ended curtain. Opaque, not translucent like the others, and z-index above every one of
     them: what is behind it is a screenful of data from a session that no longer exists, and the
     whole point is to stop the reader treating it as current. Esc does not close it — there is
     nothing to go back to. */
  .overlay.gone{align-items:center;background:rgba(16,21,27,.86);z-index:120;backdrop-filter:blur(2px)}
  .gone .card{max-width:420px;text-align:left}
  .gone .card h3{font-size:15.5px;margin-bottom:8px}
  .gone .card p{margin:0 0 14px;color:var(--mut);font-size:13.5px;line-height:1.6}
  .gone .card .gbtn{display:inline-block;background:var(--ac);color:#fff;border:0;border-radius:9px;
    padding:9px 16px;font-size:13.5px;font-weight:650;cursor:pointer;text-decoration:none}
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
<div id="_gone" class="overlay gone hidden"><div class="card" role="alertdialog" aria-modal="true"
     aria-labelledby="_gonet" aria-describedby="_goned">
  <h3 id="_gonet">Sessão terminada</h3>
  <p id="_goned">A tua sessão expirou ou foi terminada noutro sítio. <b>O que está por trás desta
  janela é o estado anterior — pode já não ser verdade.</b> Entra outra vez para continuares.</p>
  <a class="gbtn" id="_gonebtn" href="/login">Entrar outra vez</a>
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
/* ── the network seam ──────────────────────────────────────────────────────────────────────────
   EVERY call to the API goes through fetchJSON. Not a style preference — before this, GET sites
   were written `const d = await (await fetch(url)).json()` and then read `d.rows||[]`. On a 401
   the body is {"error":"autenticação necessária"}, `.rows` is undefined, `||[]` turns it into an
   empty list, and the Fila renders «✓ Tudo tratado · 0 a responder». The app asserted an empty
   queue it knew nothing about — a zero-hallucination violation in the UI layer, and the most
   dangerous kind, because "nothing needs you" is the one answer a person acts on by walking away.

   Three rules, and they only hold if there is exactly one door:
     1. A non-2xx NEVER returns a body. It throws. `||[]` can then only ever soften a real
        empty list, which is what it was written for.
     2. 401/403 raise the curtain. The session is gone (or was never enough); a toast that fades
        after 2.6s is not an honest way to say "everything you are looking at is stale".
     3. A network failure is distinguishable from a refusal, so callers can say which happened. */
/* Lens polls register here instead of calling setInterval directly, so the shell can stop every one
   of them at once. Without this the curtain goes up and the page keeps firing a doomed request every
   30s (every 2s on Admin) for as long as the tab stays open. */
const _timers=[];
function everyMs(fn,ms){const id=setInterval(fn,ms);_timers.push(id);return id;}
function stopPolling(){while(_timers.length)clearInterval(_timers.pop());}
function sessionEnded(){
  const el=$('#_gone'); if(!el||!el.classList.contains('hidden'))return;
  const btn=$('#_gonebtn');
  if(btn)btn.href='/login?next='+encodeURIComponent(location.pathname+location.search);
  el.classList.remove('hidden');
  stopPolling();
  if(btn)btn.focus();
  announce('Sessão terminada. Entra outra vez.');
}
class HttpError extends Error{
  constructor(status,url){super('HTTP '+status+' '+url);this.status=status;this.url=url;this.detail='';}
}
/* A refusal's REASON is not data. Rule 1 above is that a non-2xx never RETURNS a body — it still
   doesn't: the message rides on the thrown error, where only a catch block can reach it and no
   renderer can mistake it for a result. Without this a considered 400 («Rita já existe», «essa caixa
   não é desta instalação») reached the user as «falhou», which is the app knowing why and not saying. */
async function _httpError(r,url){
  const e=new HttpError(r.status,url);
  try{ const b=await r.json(); if(b&&typeof b.error==='string') e.detail=b.error; }catch(_e){}
  return e;
}
async function fetchJSON(url,opts){
  let r;
  try{ r=await fetch(url,opts); }
  catch(e){ throw new HttpError(0,url); }        /* offline / DNS / aborted — status 0, never a body */
  if(r.status===401||r.status===403){ sessionEnded(); throw new HttpError(r.status,url); }
  if(!r.ok) throw await _httpError(r,url);
  try{ return await r.json(); }
  catch(e){ throw new HttpError(r.status,url); } /* 200 with a truncated/HTML body is not success */
}
function getJSON(url,opts){ return fetchJSON(url,Object.assign({cache:'no-store'},opts||{})); }
async function post(url,body){
  return fetchJSON(url,{method:'POST',headers:{'Content-Type':'application/json'},
                        body:JSON.stringify(body)});
}
function del(url){ return fetchJSON(url,{method:'DELETE'}); }
/* What to show a person when a call failed: the server's own reason when it gave one, and an honest
   fallback when it did not. Never invents a cause. */
function failMsg(e,fallback){ return (e&&e.detail) ? e.detail : (fallback||'falhou'); }
/* A lens that failed to render must SAY so. The old handler was console.error, which on a page with
   a sticky nav produces a header above nothing — visually identical to an empty queue, and read as
   one. Kept deliberately dumb (string concat, no helpers) because it runs precisely when the lens's
   own code is what broke. */
function bootFailed(err){
  try{ console.error(err); }catch(_e){}
  if(document.getElementById('_bootfail'))return;
  var d=document.createElement('div');
  d.id='_bootfail';
  d.setAttribute('role','alert');
  d.style.cssText='max-width:640px;margin:32px auto;padding:18px 20px;border-radius:12px;'
    +'border:1px solid var(--red-line);background:var(--red-bg);color:var(--tx);font-size:13.5px;'
    +'line-height:1.6';
  d.innerHTML='<b>Esta vista não conseguiu abrir.</b><br>'
    +'Não é uma lista vazia — os dados não chegaram a ser desenhados, por isso <b>não sabemos</b> '
    +'o que está aqui. Recarrega a página; se persistir, o detalhe técnico está na consola.'
    +'<div style="margin-top:12px"><button id="_bootreload" style="background:var(--ac);color:#fff;'
    +'border:0;border-radius:8px;padding:8px 14px;font-size:13px;font-weight:650;cursor:pointer">'
    +'Recarregar</button></div>'
    +'<div style="margin-top:12px;color:var(--mut2);font-size:11.5px;font-family:ui-monospace,monospace;'
    +'word-break:break-word">'+esc(String((err&&err.message)||err||'erro desconhecido'))+'</div>';
  document.body.appendChild(d);
  var b=document.getElementById('_bootreload');
  if(b)b.addEventListener('click',function(){location.reload();});
}
function decidedShort(d){d=(d||'').toLowerCase();if(!d)return '';if(d.startsWith('tier0'))return 'regra';if(d.includes('gemini'))return 'Gemini';if(d.includes('claude'))return 'Claude';if(d.startsWith('tier1'))return 'IA';return d.split(':').pop();}
const S={
  /* THREE failure strings, honestly distinct. Which one you reach for is a factual claim about what
     happened to the data, so picking the wrong one is a small lie the user acts on:
       `revertido`  — the optimistic change WAS rolled back in this same catch block. Screen and
                      server agree, and they agree on the old value.
       `falhou`     — the action never happened. Nothing local changed, so nothing was reverted.
                      This is the common case and it was routinely mislabelled `revertido`, which
                      told people a write had been undone when no write was ever attempted.
       `undoFalhou` — the nasty one: undo restored the row on screen, then FAILED to tell the
                      server. Screen and server now disagree, and neither of the other two strings
                      admits that. Says so, and says what to do about it. */
  nadaDesfazer:'nada para desfazer',desfeito:'desfeito',revertido:'falhou — revertido',falhou:'falhou',
  undoFalhou:'desfeito aqui, mas o servidor não aceitou — recarrega a página',
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
  /* The closing block. `clean_email_body` used to DELETE it, which is where a sender's name, role
     and NIF live — so it now arrives as its own field and renders one click away. Its own field,
     not folded into body_clean, for three measured reasons: `hasNoise` below compares the two
     lengths and would lose «ver original»; `noVisible` above would stop falling back; and the
     server's cut would start landing inside the signature instead of the message. */
  const sigHTML=(m.body_sig&&!noVisible)
    ?'<button class="stoggle">▸ assinatura</button>'
     +'<div class="tsig hidden">'+esc(String(m.body_sig).slice(0,1500))+'</div>'
    :'';
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
  /* data-tmid, not data-mid: `.trbtn[data-mid]` is read with closest()-style lookups by the
     translate handler, and giving the WRAPPER the same attribute would make every click inside a
     message resolve to it. This one exists so «Evolução da conversa» can scroll to the message a
     narrative step cites (ADR-054) — provenance you can follow, not just claim. */
  return '<div class="tmsg dir-'+esc(tag.k)+(m.embedded?' embedded':'')
    +'" data-tmid="'+esc(m.message_id||'')+'">'
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
    +visHTML+sigHTML+quoteHTML+rawToggle+trHTML
    +'</div>';
}
/* ── the attachment funnel (ADR-046) ───────────────────────────────────────
   ONE deduped, banded list of a thread's files, built server-side (/api/thread → .attachments).
   Lives here, in the shared kit, so Para Ti and the Projetos origem panel inherit it instead of
   the Fila forking its own copy — the same reason msgHTML is shared.

   Three bands, and NOTHING is dropped: the signature band is collapsed behind a visible count,
   one click away, which keeps this inside the "never silently bin" rule rather than beside it.
   Every band is an INFERENCE — each tile shows the deterministic reason it landed there. */
const _ATT_GLYPH={pdf:'📕',image:'🖼',sheet:'📊',doc:'📄',slides:'📽',archive:'🗜',cad:'📐',mail:'✉',file:'📎'};
const _ATT_BANDLABEL={FICHEIROS:'Ficheiros', IMAGENS:'Imagens no corpo', ASSINATURAS:'Assinaturas e logótipos'};
function _attSize(n){
  n=n||0;
  if(n>=1048576) return (n/1048576).toFixed(1).replace('.',',')+' MB';
  if(n>=1024)    return Math.round(n/1024)+' KB';
  return n+' B';
}
/* Where the bytes live. TWO sources share this tile (ADR-052): a MIME part addressed by
   {message_id,index}, and an intake capture's media addressed by {capture_id,index}. The item says
   which; the tile never guesses from the shape of a string. */
function _attURL(it){
  const s=it.src||{};
  return s.capture_id
    ? '/api/captures/'+encodeURIComponent(s.capture_id)+'/media/'+(s.index||0)
    : '/api/attachment/'+encodeURIComponent(s.message_id||'')+'/'+(s.index||0);
}
/* «Quem trouxe este ficheiro?» — rendered only where a lens asks for it (o.showSource), because it
   is only TRUE where the merge is chronological. See attMerge: first-carrier-wins is meaningless
   unless the list was sorted by first_seen before the dedup ran. */
function _attSrcHTML(it){
  const when=(it.first_seen||'').slice(0,10);
  if(it.source==='capture'){
    const bits=['captura'+(it.channel?' · '+it.channel:'')];
    if(it.asserted_by) bits.push(it.asserted_by);
    if(when) bits.push(when);
    return '<span class="atti-src cap">'+esc(bits.join(' · '))+'</span>';
  }
  const bits=[];
  if(it.from_email) bits.push(it.from_email);
  if(when) bits.push(when);
  /* '/fila?thread=' written in full — never the bare root. The root stopped being the Fila in
     ADR-044, so a rootward query lands on Início, which reads no query parameter at all. A guard in
     tests/test_fila.py greps every lens for the old shape, so do not reintroduce it even in prose. */
  const jump=it.thread_root
    ? ' <a class="atti-jump" href="/fila?thread='+encodeURIComponent(it.thread_root)+'">ver na fila →</a>'
    : '';
  /* The address goes in its OWN span so flexbox can shrink and ellipsise it while the jump link
     never shrinks. Looking at the render is what caught this: with the whole line as one nowrap
     span, a real address («ana@espumas.pt · 2026-07-20») filled a 132 px tile and pushed «ver na
     fila →» clean off the right edge — the one action the line offers, invisible on every tile. */
  return '<span class="atti-src"><span class="atti-who">'
    +esc(bits.length?bits.join(' · '):'origem desconhecida')+'</span>'+jump+'</span>';
}
function _attTile(it, o){
  o=o||{};
  const url=_attURL(it);
  /* Size-gated preview: only a small image gets a real <img>, and it is lazy so a collapsed band
     costs nothing until opened. Everything else shows its kind glyph — no byte is fetched to draw
     an icon. The server decides (item.preview); the client never guesses from the size.

     o.bigPreviews is the ONE opt-in, and it is a decision about the SURFACE, not a guess about the
     file: in a panel whose entire purpose is looking at the project's files, iconising the 6 MB
     client photo while previewing the 6 KB supplier logo inverts the view. Still lazy, so only what
     scrolls into frame is fetched. Every other lens keeps the gate. (ADR-052 §Preview) */
  const wantImg=it.preview||(o.bigPreviews&&it.kind==='image'&&!it.missing);
  const thumb=wantImg
    ? '<span class="atti-t"><img loading="lazy" decoding="async" src="'+url+'" alt=""></span>'
    : '<span class="atti-t"><span class="atti-g">'+(_ATT_GLYPH[it.kind]||_ATT_GLYPH.file)+'</span></span>';
  const bits=[_attSize(it.size)];
  if(it.px&&it.px.length===2) bits.push(it.px[0]+'×'+it.px[1]);
  if(it.pages!=null) bits.push(it.pages+(it.pages===1?' pág.':' págs.'));
  if(it.n_copies>1) bits.push('×'+it.n_copies);
  const name=it.name||'(sem nome)';
  const ev=it.band_evidence||'';
  const tile='<a class="atti'+(it.missing?' gone':'')+'" href="'+url+'" target="_blank" rel="noopener" title="'
    +esc(name+' — '+bits.join(' · ')+(ev?('\nporquê: '+ev):''))+'">'
    +thumb
    +'<span class="atti-n">'+esc(name)+'</span>'
    +'<span class="atti-m">'+esc(bits.join(' · '))+'</span>'
    +(ev?'<span class="atti-ev">'+esc(ev)+'</span>':'')
    +'</a>';
  /* The source line carries its own link, so it CANNOT live inside the tile's <a>: nested anchors
     are invalid HTML and the browser silently splits the DOM around them. Wrapper instead. */
  return o.showSource ? '<div class="attw">'+tile+_attSrcHTML(it)+'</div>' : tile;
}
function attFunnelHTML(att, o){
  o=o||{};
  if(!att) return '';
  const items=att.items||[];
  if(!items.length) return '';
  const counts=att.counts||{};
  const by={};
  items.forEach(it=>{(by[it.band]=by[it.band]||[]).push(it);});
  const sub=[];
  if(counts.FICHEIROS)   sub.push(counts.FICHEIROS+(counts.FICHEIROS===1?' ficheiro':' ficheiros'));
  if(counts.IMAGENS)     sub.push(counts.IMAGENS+(counts.IMAGENS===1?' imagem no corpo':' imagens no corpo'));
  if(counts.ASSINATURAS) sub.push(counts.ASSINATURAS+(counts.ASSINATURAS===1?' assinatura':' assinaturas'));
  /* The heading names the scope it actually folded. It was the literal «Ficheiros da conversa» for
     a list that, in a Projetos panel, spans every conversation of the project — a heading that lies
     about its own scope. Existing callers pass one argument and keep the thread wording. */
  const title=(o&&o.title)||'Ficheiros da conversa';
  let out='<div class="attf"><div class="attf-h">📎 '+esc(title)
    +'<span class="attf-sub">'+esc(sub.join(' · '))+'</span></div>';
  const INF='<span class="attf-inf" title="INFERÊNCIA — nada no MIME diz «isto é um logótipo». '
    +'A banda é deduzida por uma regra determinista; cada ficheiro mostra a sua evidência.">INFERÊNCIA</span>';
  ['FICHEIROS','IMAGENS'].forEach(band=>{
    const lst=by[band]||[];
    if(!lst.length) return;
    /* Always an arrow — never a bare reference to the tile helper. Array.map passes
       (item, index, array), so passing the function itself hands it the ARRAY INDEX as its options
       object: falsy for tile 0 and truthy for every tile after it. The first tile looks perfect and
       the rest sprout a source line built from a number. Same for the signature band below, and
       tests/test_attachments.py greps for the bare shape — do not write it, even in prose. */
    out+='<div class="attf-band"><div class="attf-bl">'+esc(_ATT_BANDLABEL[band])
      +' <b>'+lst.length+'</b>'+INF+'</div>'
      +'<div class="attf-grid">'+lst.map(it=>_attTile(it,o)).join('')+'</div></div>';
  });
  const sig=by.ASSINATURAS||[];
  if(sig.length){
    /* Collapsed, but the COUNT is visible — a human can see there are 15 and open them. */
    out+='<details class="attf-band attf-sig"><summary>'+esc(_ATT_BANDLABEL.ASSINATURAS)
      +' <b>'+sig.length+'</b> — mostrar</summary>'
      +'<div class="attf-grid">'+sig.map(it=>_attTile(it,o)).join('')+'</div></details>';
  }
  return out+'</div>';
}
/* Merge several sources' funnels into one — a Projetos panel spans every thread of the project, plus
   its intake captures (ADR-052). Dedup stays on the item id (the content hash), so the same drawing
   quoted in two threads — or mailed AND re-sent through Telegram — is ONE file, and the band order
   the server chose is re-applied after the merge. */
const _ATT_ORDER={FICHEIROS:0,IMAGENS:1,ASSINATURAS:2};
function attMerge(blocks){
  const seen={}, order=[];
  /* Chronological BEFORE the dedup pass, not block order. fold_thread's contract is "the first
     occurrence wins: it supplies src, first_seen and from_email" — and this merge inherited that
     wording while feeding it project_threads.added_ts order, so the winning copy was whichever
     thread happened to be attached first. Harmless while nothing rendered a sender; a tile that
     names WHO SENT THIS FILE turns it into a confident lie, which is the worst failure mode in this
     codebase. An item with no date sorts LAST, so a dated carrier always outranks an undated one. */
  const flat=[];
  (blocks||[]).forEach(b=>{ if(b&&b.items) b.items.forEach(it=>flat.push(it)); });
  flat.sort((a,b)=>String(a.first_seen||'9999').localeCompare(String(b.first_seen||'9999')));
  flat.forEach(it=>{
    if(seen[it.id]){ seen[it.id].n_copies+=(it.n_copies||1); return; }
    seen[it.id]=Object.assign({},it); order.push(it.id);
  });
  const items=order.map(k=>seen[k]);
  items.sort((a,b)=>(_ATT_ORDER[a.band]-_ATT_ORDER[b.band])||(b.size-a.size)
                    ||String(a.name).localeCompare(String(b.name)));
  const counts={FICHEIROS:0,IMAGENS:0,ASSINATURAS:0};
  items.forEach(it=>{counts[it.band]=(counts[it.band]||0)+1;});
  return {items:items, counts:counts, bands:['FICHEIROS','IMAGENS','ASSINATURAS']};
}
/* Render a full thread panel (summary line + all messages). */
function msgThreadHTML(msgs, opts){
  opts=opts||{};
  const head='<div class="thead"><span class="tsum">'+esc(msgThreadSummary(msgs))+'</span></div>';
  return '<div class="texp">'+head+attFunnelHTML(opts.attachments)
    +msgs.map(m=>msgHTML(m,opts)).join('')+'</div>';
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
    const d=await post('/api/translate',{message_id:btn.dataset.mid||'', text:src});
    slot.textContent=d.text||''; slot.dataset.done='1'; slot.classList.remove('hidden');
    btn.textContent='ver original';
  }catch(err){
    /* 401 already raised the curtain inside fetchJSON; this only has to not claim a translation. */
    slot.textContent='tradução falhou: '+(err&&err.status===0?'sem resposta do servidor'
                                                             :('HTTP '+((err&&err.status)||'?')));
    slot.classList.remove('hidden'); slot.classList.add('trerr'); btn.textContent=orig;
  }
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
    const st=e.target.closest('.stoggle');
    if(st){
      const s=st.nextElementSibling;
      if(s&&s.classList.contains('tsig')){
        const hid=s.classList.toggle('hidden');
        st.textContent=(hid?'▸':'▾')+' assinatura';
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
/* ── evidence spans (fila-evidence §Phase 3) ───────────────────────────────────────────────────
   Click a ledger value, its evidence lights up in the message body. Deterministic and LLM-free.

   These MIRROR extract.py's patterns rather than reusing its output, and that is the whole design.
   `extract_values` folds first (NFKD → strip combining marks → casefold), so what it stores is NOT
   a substring of the body: a space-stripped upper-case IBAN does not occur in a body that writes it
   in groups of four, and a casefolded 'eur' amount does not occur in one that writes EUR — both
   verified by execution, see tests/test_cockpit_ui.py. Folding also changes string LENGTH on
   Portuguese text, so any offset computed server-side drifts silently on exactly the mail this app
   handles. The only safe place to locate a span is over the string that is on screen.

   No literal example values in this comment, deliberately: a 9-digit run here trips
   test_webapp.py::test_admin_page_does_not_embed_the_whole_settings_dict, which greps the rendered
   page for a Telegram user id. The examples live in the tests, where they belong.

   Keep in step with src/email2data/extract.py. */
const _EV_AMOUNT=/(?:€|eur\b|euros?\b)\s?\d[\d. ]*(?:,\d+)?|\d[\d. ]*(?:,\d+)?\s?(?:€|eur\b|euros?\b)/gi;
const _EV_NIF=/(?:nif|nipc|contribuinte)\D{0,12}(?<!\d)(\d{9})(?!\d)/gi;
const _EV_IBAN=/\bpt\d{2}(?:\s?\d){21}\b/gi;
/* PT NIF mod-11 — the check digit is what turns a noisy 9-digit run into a near-certain NIF (ADR-007).
   Dropping it here would highlight any phone number that happens to follow the word «contribuinte». */
function evValidNif(n){
  if(!/^\d{9}$/.test(n)) return false;
  let t=0; for(let i=0;i<8;i++) t+=parseInt(n[i],10)*(9-i);
  let c=11-(t%11); if(c>=10) c=0;
  return c===parseInt(n[8],10);
}
function evFold(s){
  return String(s||'').normalize('NFKD').replace(/[̀-ͯ]/g,'').toLowerCase();
}
/* Every match of `key`'s pattern in `text`, as {s,e,text} offsets into THAT string. */
function evMatches(text,key){
  const t=String(text||''), out=[];
  const re=key==='nif'?_EV_NIF:(key==='iban'?_EV_IBAN:(key==='money'?_EV_AMOUNT:null));
  if(!re) return out;
  re.lastIndex=0;
  let m;
  while((m=re.exec(t))!==null){
    if(m[0]==='') { re.lastIndex++; continue; }
    /* _EV_NIF captures the digits; the other two do not. Highlighting m[0] for NIF would paint the
       «NIF: » anchor as if it were the value — so the offset is re-derived from the group. */
    let s=m.index, e=m.index+m[0].length;
    if(key==='nif'){
      if(!evValidNif(m[1])) continue;
      s=m.index+m[0].indexOf(m[1]); e=s+m[1].length;
    }
    out.push({s:s,e:e,text:t.slice(s,e)});
  }
  return out;
}
/* Where in `text` is `value`'s evidence? Format-locked keys match by NORMALISED form (so a spaced
   IBAN still matches the folded stored one); everything else falls back to a fold-tolerant literal
   search. A value that is not there yields [] — 40% of extracted values are in the email in no form
   at all, and a wrong highlight is worse than none. */
function evLocate(text,key,value){
  const t=String(text||''), v=String(value==null?'':value).trim();
  if(!t||!v) return [];
  const norm=s=>evFold(s).replace(/[\s.]/g,'');
  if(key==='nif'||key==='iban'||key==='money'){
    const want=norm(v);
    return evMatches(t,key).filter(m=>norm(m.text)===want);
  }
  const hay=evFold(t), needle=evFold(v);   /* fold is 1:1 per char here — offsets stay aligned */
  const out=[]; let i=hay.indexOf(needle);
  while(i>=0&&out.length<50){ out.push({s:i,e:i+needle.length,text:t.slice(i,i+needle.length)});
    i=hay.indexOf(needle,i+Math.max(1,needle.length)); }
  return out;
}
/* ── the Phase-4 quote path (ADR-054) ──────────────────────────────────────────────────────────
   The locate pass stores the EMAIL's own sentence, but by the time that sentence reaches the DOM it
   has been through clean_email_body (whole lines dropped), msgSplitQuote (both halves TRIMMED), and
   three separate slices. Whitespace is the one difference that survives all of that, so it is the
   one difference tolerated here — and offsets are recovered through a map, never by arithmetic on
   the normalised string, because folding is NOT length-preserving (NFKD expands a ligature, and a
   lone combining mark folds away to nothing). That assumption is exactly what makes a server-side
   offset unusable; this is the client-side version of the same trap. */
function evNorm(t){
  const s=String(t||''), out=[], idx=[];
  let runAt=-1;                            /* where the pending whitespace run STARTED */
  for(let i=0;i<s.length;i++){
    const c=s[i];
    if(/\s/.test(c)){ if(out.length&&runAt<0) runAt=i; continue; }
    const f=evFold(c);
    if(!f) continue;                       /* a combining mark on its own folds to '' */
    /* The collapsed space maps to the START of its run, not to the character after it. That is what
       makes map[i+len] an EXCLUSIVE end: a match ending just before a space closes on the first
       whitespace character, not past it, so the painted span carries no trailing blank. */
    if(runAt>=0){ out.push(' '); idx.push(runAt); runAt=-1; }
    for(let k=0;k<f.length;k++){ out.push(f[k]); idx.push(i); }
  }
  idx.push(s.length);                      /* one past the end, so a match at the end can close */
  return {t:out.join(''), map:idx};
}
/* Every occurrence of `quote` in `text`, as {s,e} offsets into TEXT, tolerating any run of
   whitespace and the same folding evLocate uses. Returns [] when the sentence is not on screen —
   which is a real answer: the quote may sit in a region this message's render cut away. */
function evLocateQuote(text,quote){
  const q=String(quote==null?'':quote).trim();
  if(!text||!q) return [];
  const c=evNorm(text), n=evNorm(q);
  if(!c.t||!n.t) return [];
  const out=[]; let i=c.t.indexOf(n.t);
  while(i>=0&&out.length<50){ out.push({s:c.map[i], e:c.map[i+n.t.length]});
    i=c.t.indexOf(n.t,i+Math.max(1,n.t.length)); }
  return out;
}
/* The text nodes a highlight may paint: everything the user can actually reach in this dossier.
   Excludes the .rawbody copy — there are TWO .tbody per message when the raw body is noisier, and
   painting the hidden one shows nothing while making the match count lie. */
function evTextNodes(root){
  const nodes=[];
  root.querySelectorAll('.tbody,.tquote,.tsig').forEach(box=>{
    if(box.closest('.rawbody')) return;
    const w=document.createTreeWalker(box,NodeFilter.SHOW_TEXT);
    let n; while((n=w.nextNode())) nodes.push(n);
  });
  return nodes;
}
/* Open the collapsed block an element sits in, and correct its toggle's caret. Never inserts or
   removes an element — the toggles find their target with nextElementSibling. */
function evReveal(el){
  if(!el||!el.closest) return;
  const box=el.closest('.tquote,.tsig');
  if(!box||!box.classList.contains('hidden')) return;
  box.classList.remove('hidden');
  const t=box.previousElementSibling;
  if(t&&/toggle$/.test(t.className||'')) t.textContent=(t.textContent||'').replace('▸','▾');
}
let _evReg=null;
/* Paint `value`'s evidence inside `root`. Uses the CSS Custom Highlight API over live Ranges: it
   mutates NO DOM, which is required here — splicing <mark> into the escaped body would both index
   escaped text (esc() drifts 4 chars per &, and never escapes ') and repoint the nextElementSibling
   toggles. Returns the number of spans painted. */
function evHighlight(root,key,value,quote){
  evClear();
  if(!root||typeof CSS==='undefined'||!CSS.highlights||typeof Highlight==='undefined') return 0;
  const nodes=evTextNodes(root);
  const collect=fn=>{
    const rs=[];
    nodes.forEach(node=>{ fn(node.nodeValue).forEach(m=>{
      if(m.e<=m.s) return;
      const r=new Range(); r.setStart(node,m.s); r.setEnd(node,m.e); rs.push(r); }); });
    return rs;
  };
  let ranges=collect(t=>evLocate(t,key,value));
  /* The LOCATED sentence (ADR-054 Phase 4) is a fallback, never a replacement, and the order is the
     whole design. The deterministic search already paints 44% of ledger rows exactly — on those the
     model's quote is an echo of the value 89% of the time, so preferring it would swap a precise
     span for a whole sentence and lose nothing but precision. Phase 4 exists for the other 56%,
     where the value is in the email in NO form (an ISO-normalised prazo, a paraphrased pedido) and
     the sentence is the only thing there is to point at. */
  if(!ranges.length&&quote) ranges=collect(t=>evLocateQuote(t,quote));
  if(!ranges.length) return 0;
  /* If the evidence sits in a collapsed «assinatura» / «mensagem citada» block, open it — painting
     text nobody can see is indistinguishable from finding nothing. 41% of the model's evidence
     quotes and 31% of extracted values live behind exactly these two toggles. */
  ranges.forEach(r=>evReveal(r.startContainer.parentElement));
  _evReg=new Highlight(...ranges);
  CSS.highlights.set('evid',_evReg);
  /* .tbody/.tquote/.tsig are themselves scrollers (max-height + overflow:auto), so bringing a span
     into view scrolls a NESTED box — 'nearest' keeps the page still and moves only that box. */
  const first=ranges[0].startContainer.parentElement;
  if(first&&first.scrollIntoView) first.scrollIntoView({block:'nearest'});
  return ranges.length;
}
function evClear(){
  _evReg=null;
  if(typeof CSS!=='undefined'&&CSS.highlights) CSS.highlights.delete('evid');
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
async function syncNow(){
  setSynced(null,true); toast(S.sincronizando);
  try{
    await post('/api/sync',{});
    if(typeof onSynced==='function'){onSynced();}
    else{toast(S.sincronizado);setTimeout(()=>location.reload(),700);}
  }catch(e){
    /* 409 is not a failure — another sync holds the lock. It was the one non-2xx this function
       already read correctly, and fetchJSON turning every non-2xx into a throw must not flatten it
       back into «sync falhou». 401/403 stay silent: the curtain is already up and says more. */
    if(e&&e.status===409){ toast(S.syncEmCurso); }
    else if(!(e&&(e.status===401||e.status===403))){ toast(S.syncFalhou); }
    setSynced();
  }
}
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
/* account menu (name · role · terminar sessão) — same contract as the gear, and each closes the
   other: they are neighbours, so two open at once would overlap. */
const _ab=$('#_acctbtn');if(_ab)_ab.addEventListener('click',e=>{e.stopPropagation();const g=$('#_gearmenu');if(g)g.classList.add('hidden');const m=$('#_acctmenu');if(m)m.classList.toggle('hidden');});
if(_gb)_gb.addEventListener('click',()=>{const m=$('#_acctmenu');if(m)m.classList.add('hidden');});
document.addEventListener('click',e=>{const m=$('#_acctmenu');if(m&&!m.classList.contains('hidden')&&!e.target.closest('.acctwrap'))m.classList.add('hidden');});
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
