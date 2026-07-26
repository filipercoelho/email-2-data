"""Login, first-run setup, and invite-redemption pages (ADR-039). Server-rendered, pt-PT.

Deliberately self-contained: no nav, no app shell, no embedded data. A signed-out visitor must not
receive a byte of triage data, so these pages import nothing from the cockpit renderers. That is the
server-rendered advantage over the sibling SPA — there is no client-side gate to bypass, because the
data was never sent.

Theme tokens mirror ADR-035 so the pages sit in the same visual language in light and dark, but they
are inlined rather than shared: a stylesheet import would be one more thing served before auth.
"""

from __future__ import annotations

from html import escape

_CSS = """
:root {
  --bg: #f6f7f9; --surface: #ffffff; --ink: #16191d; --muted: #5b636e;
  --line: #dfe3e8; --accent: #3d5a80; --accent-ink: #ffffff; --danger: #a3232b;
  --danger-bg: #fdf0f0; --ok: #1f7a5c;
}
:root[data-theme="dark"] {
  --bg: #14171a; --surface: #1c2024; --ink: #e8eaed; --muted: #9aa3ad;
  --line: #2c3238; --accent: #6E85DE; --accent-ink: #10131a; --danger: #e08a8f;
  --danger-bg: #2a1c1e; --ok: #219980;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #14171a; --surface: #1c2024; --ink: #e8eaed; --muted: #9aa3ad;
    --line: #2c3238; --accent: #6E85DE; --accent-ink: #10131a; --danger: #e08a8f;
    --danger-bg: #2a1c1e; --ok: #219980;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
  background: var(--bg); color: var(--ink); padding: 24px;
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
.card {
  width: 100%; max-width: 380px; background: var(--surface); border: 1px solid var(--line);
  border-radius: 12px; padding: 28px; box-shadow: 0 1px 3px rgba(0,0,0,.06);
}
h1 { margin: 0 0 4px; font-size: 19px; letter-spacing: -0.01em; }
.sub { margin: 0 0 20px; color: var(--muted); font-size: 13px; }
label { display: block; margin: 14px 0 5px; font-size: 13px; font-weight: 600; }
input {
  width: 100%; padding: 9px 11px; font-size: 15px; color: var(--ink);
  background: var(--bg); border: 1px solid var(--line); border-radius: 7px;
}
input:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
button {
  width: 100%; margin-top: 20px; padding: 10px; font-size: 15px; font-weight: 600;
  color: var(--accent-ink); background: var(--accent); border: 0; border-radius: 7px; cursor: pointer;
}
button:hover { filter: brightness(1.08); }
.error {
  margin: 0 0 16px; padding: 9px 11px; border-radius: 7px; font-size: 13px;
  color: var(--danger); background: var(--danger-bg); border: 1px solid var(--danger);
}
.hint { margin-top: 18px; color: var(--muted); font-size: 12px; line-height: 1.45; }
.who { font-weight: 600; color: var(--ink); }
.alt { margin-top: 16px; text-align: center; font-size: 13px; }
.alt a { color: var(--accent); text-decoration: none; }
.alt a:hover { text-decoration: underline; }
.ok {
  margin: 0 0 16px; padding: 9px 11px; border-radius: 7px; font-size: 13px;
  color: var(--ok); background: var(--bg); border: 1px solid var(--ok);
}
/* The address a reset was requested for, echoed back. Wraps rather than overflowing the card:
   an address long enough to clip is exactly the one someone needs to read to spot their typo. */
.sent { overflow-wrap: anywhere; }
"""

_THEME_SCRIPT = (
    "<script>try{var t=localStorage.getItem('theme');"
    "if(t){document.documentElement.setAttribute('data-theme',t);}}catch(e){}</script>"
)


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=\"pt-PT\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<meta name=\"robots\" content=\"noindex\">"
        f"<title>{escape(title)}</title>{_THEME_SCRIPT}<style>{_CSS}</style></head>"
        f"<body><main class=\"card\">{body}</main></body></html>"
    )


def _error_block(error: str) -> str:
    return f'<p class="error">{escape(error)}</p>' if error else ""


def build_login_html(*, error: str = "", next_url: str = "/") -> str:
    """The sign-in page.

    ``next_url`` round-trips where the visitor was heading so a bookmarked deep link survives a
    login. It is written into a hidden field escaped, and re-validated server-side before use —
    never trusted as a redirect target on its own (an open redirect is how a login page leaks).
    """
    return _page("Entrar · email-2-data", f"""
      <h1>email-2-data</h1>
      <p class="sub">Triagem de correio — acesso restrito.</p>
      {_error_block(error)}
      <form method="post" action="/login">
        <input type="hidden" name="next" value="{escape(next_url)}">
        <label for="name">Nome</label>
        <input id="name" name="name" autocomplete="username" autofocus required>
        <label for="password">Palavra-passe</label>
        <input id="password" name="password" type="password" autocomplete="current-password" required>
        <button type="submit">Entrar</button>
      </form>
      <p class="alt"><a href="/recuperar">Esqueceste-te da palavra-passe?</a></p>
    """)


def build_forgot_html(*, error: str = "", sent: bool = False) -> str:
    """Request a reset link (ADR-042).

    ``sent`` renders the SAME confirmation whether or not the name matched anyone, whether or not
    that person has an address on file, and whether or not the mail actually left. This page must
    not become an account oracle: a distinguishable "no such person" turns the roster into something
    anyone on the LAN can enumerate, and the roster is people's names.
    """
    if sent:
        return _page("Link enviado · email-2-data", """
          <h1>Verifica o teu email</h1>
          <p class="ok sent">Se essa conta existir e tiver um endereço associado, foi enviado um
          link para definir uma nova palavra-passe.</p>
          <p class="hint">O link é válido durante pouco tempo e só funciona uma vez. Não recebeste
          nada? Pode não haver nenhum endereço associado à tua conta — nesse caso pede a um
          administrador que to defina, ou que te reponha a palavra-passe.</p>
          <p class="alt"><a href="/login">Voltar a entrar</a></p>
        """)
    return _page("Recuperar acesso · email-2-data", f"""
      <h1>Recuperar acesso</h1>
      <p class="sub">Indica o teu nome. Enviamos um link para o endereço associado à tua conta.</p>
      {_error_block(error)}
      <form method="post" action="/recuperar">
        <label for="name">Nome</label>
        <input id="name" name="name" autocomplete="username" autofocus required>
        <button type="submit">Enviar link</button>
      </form>
      <p class="alt"><a href="/login">Voltar a entrar</a></p>
    """)


def build_reset_html(*, person_name: str, token: str, error: str = "") -> str:
    """Set a new password from a mailed reset link.

    Carries the same ``autocomplete="username"`` anchor as the account page's change-password form.
    Its absence is the defect ADR-041 records: without a username field a password manager reads
    this as a *create*-password form and offers to save a new entry instead of updating the existing
    one — which is how the owner locked himself out twice. ``readonly`` because the person is already
    identified by the token; the field exists for the password manager, not for input.
    """
    return _page("Nova palavra-passe · email-2-data", f"""
      <h1>Nova palavra-passe</h1>
      <p class="sub">Define a palavra-passe para <span class="who">{escape(person_name)}</span>.</p>
      {_error_block(error)}
      <form method="post" action="/recuperar/definir">
        <input type="hidden" name="token" value="{escape(token)}">
        <label for="username">Nome</label>
        <input id="username" name="username" autocomplete="username" readonly
               value="{escape(person_name)}">
        <label for="password">Palavra-passe</label>
        <input id="password" name="password" type="password" autocomplete="new-password"
               autofocus required minlength="8">
        <label for="confirm">Confirmar palavra-passe</label>
        <input id="confirm" name="confirm" type="password" autocomplete="new-password"
               required minlength="8">
        <button type="submit">Definir e entrar</button>
      </form>
      <p class="hint">Este link só pode ser usado uma vez. Ao definires a nova palavra-passe,
      todas as sessões abertas terminam.</p>
    """)


def build_reset_expired_html() -> str:
    """A dead reset link says so plainly, and offers the one action that still works."""
    return _page("Link inválido · email-2-data", """
      <h1>Link inválido</h1>
      <p class="sub">Este link já foi usado ou expirou.</p>
      <p class="hint">Pede um novo em «Esqueceste-te da palavra-passe?», ou fala com um
      administrador.</p>
      <p class="alt"><a href="/recuperar">Pedir um novo link</a></p>
    """)


def build_recovery_unavailable_html() -> str:
    """Shown when no outbound mail is configured at all.

    The honest refusal (ADR-040) applied to recovery: an install with no ``mail`` block cannot send
    anything, and saying "check your email" there would be a promise the app knows it is not
    keeping. It names the one route that does work instead.
    """
    return _page("Recuperação indisponível · email-2-data", """
      <h1>Recuperação por email indisponível</h1>
      <p class="sub">Esta instalação não tem envio de email configurado.</p>
      <p class="hint">Pede a um administrador que reponha a tua palavra-passe em
      Administração → Pessoas.</p>
      <p class="alt"><a href="/login">Voltar a entrar</a></p>
    """)


def build_setup_html(*, error: str = "") -> str:
    """First-run: no credentials exist yet, so the first administrator is created here.

    Reachable only while ``AuthStore.has_any_credentials()`` is False. Once one credential exists the
    route 404s, so this can never be used to mint a second admin.
    """
    return _page("Configuração inicial · email-2-data", f"""
      <h1>Configuração inicial</h1>
      <p class="sub">Ainda não existe nenhuma conta. Cria a conta de administrador.</p>
      {_error_block(error)}
      <form method="post" action="/setup">
        <label for="name">Nome</label>
        <input id="name" name="name" autocomplete="username" autofocus required
               placeholder="Filipe">
        <label for="password">Palavra-passe</label>
        <input id="password" name="password" type="password" autocomplete="new-password"
               required minlength="8">
        <label for="confirm">Confirmar palavra-passe</label>
        <input id="confirm" name="confirm" type="password" autocomplete="new-password"
               required minlength="8">
        <button type="submit">Criar administrador</button>
      </form>
      <p class="hint">Esta página desaparece assim que a primeira conta existir.</p>
    """)


def build_invite_html(*, person_name: str, token: str, error: str = "") -> str:
    """Invite redemption — the invited person chooses their own password.

    The token travels in the URL and is repeated in a hidden field; it is single-use and consumed
    atomically on submit, so a reloaded page cannot set a second password.
    """
    return _page("Definir palavra-passe · email-2-data", f"""
      <h1>Bem-vindo</h1>
      <p class="sub">Define a palavra-passe para <span class="who">{escape(person_name)}</span>.</p>
      {_error_block(error)}
      <form method="post" action="/aceitar-convite">
        <input type="hidden" name="token" value="{escape(token)}">
        <label for="password">Palavra-passe</label>
        <input id="password" name="password" type="password" autocomplete="new-password"
               autofocus required minlength="8">
        <label for="confirm">Confirmar palavra-passe</label>
        <input id="confirm" name="confirm" type="password" autocomplete="new-password"
               required minlength="8">
        <button type="submit">Definir e entrar</button>
      </form>
      <p class="hint">Este link só pode ser usado uma vez.</p>
    """)


def build_invite_expired_html() -> str:
    """A dead invite says so plainly rather than bouncing to a login the person cannot pass."""
    return _page("Convite inválido · email-2-data", """
      <h1>Convite inválido</h1>
      <p class="sub">Este link já foi usado ou expirou.</p>
      <p class="hint">Pede um novo convite a um administrador.</p>
    """)
