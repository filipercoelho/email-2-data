"""Per-person email signatures — the closing of a reply belongs to the person sending it (ADR-047).

Every reply draft used to end in the same three words, because the closing was a literal block inside
``config/reply_playbook.md``::

    > Com os melhores cumprimentos,
    > Lindo Serviço

That is a company footer, not a signature: the human who reviews and sends the mail is invisible in
it, and the client cannot tell which of five people they are talking to. This module makes the
closing a property of the **signed-in person**, rendered from a template they own.

Three rules, and each is here rather than in a route because a rule that lives in one route is a rule
the next route forgets:

**1. The vocabulary is closed.** :data:`PLACEHOLDERS` is the whole of it, and every value resolves
from the person's OWN profile row — never from the thread, the counterparty, or the LLM. A signature
is identity, so an invented value here is a lie told in someone else's name. :func:`unknown_tokens`
is the save-time gate; the editor refuses a template it cannot fill rather than shipping a client
email with a literal ``{telemovel}`` in it.

**2. A line whose placeholders are all empty is dropped, not left dangling.** Someone with no phone
number on file gets a signature with no phone line — not ``Tel.:`` followed by nothing, which is what
naive substitution produces and what a client actually reads as sloppiness. A line mixing a filled
and an empty placeholder keeps the filled one (documented in the editor: one field per line).

**3. Rendering is deterministic and the model never touches it.** :func:`sign` appends the rendered
block AFTER the draft has been produced, and first removes any closing the model wrote on its own
(:func:`strip_closing`). The playbook now tells the model not to write one, but the playbook is a
bind-mounted file anybody can edit back, so the strip is the guard that keeps a double sign-off from
reaching a client. If the person's signature renders EMPTY, nothing is stripped and the draft is
returned untouched — removing a closing and replacing it with nothing is worse than leaving it.
"""

from __future__ import annotations

import html as _html
import re
from pathlib import Path
from typing import Any

# The closed placeholder vocabulary: token -> (people-row key, pt-PT label for the editor's help).
# Adding one means adding a real column someone can fill in -- an unfillable placeholder is a
# guaranteed blank line, which is rule 2 arriving as a bug instead of as a decision.
PLACEHOLDERS: dict[str, tuple[str, str]] = {
    "{nome}":     ("name",      "o teu nome"),
    "{cargo}":    ("job_title", "a tua função"),
    "{telefone}": ("phone",     "o teu contacto telefónico"),
    "{email}":    ("email",     "o teu endereço (definido por um administrador)"),
}

_TOKEN_RE = re.compile(r"\{[a-z_]+\}")

# The install-wide fallback, used when config/signature_template.md is missing or has lost its way.
# It is deliberately the SAME closing the reply playbook used to hard-code, plus the person -- so an
# install that upgrades and changes nothing sees its existing wording with a name under it.
DEFAULT_TEMPLATE = (
    "Com os melhores cumprimentos,\n"
    "{nome}\n"
    "{cargo}\n"
    "Lindo Serviço\n"
    "{telefone}\n"
    "{email}"
)

# Closing salutations we may cut before appending a signature. NARROWER than ``envelope._CLOSING`` on
# purpose: that one also matches "Obrigado.", which is the last line of the deterministic
# ask/follow-up templates and is body text, not a sign-off. Cutting it would silently delete the
# thanks from every client email.
_CLOSING_RE = re.compile(
    r"^\s*(?:"
    r"com\s+os\s+melhores\s+cumprimentos|melhores\s+cumprimentos|os\s+melhores\s+cumprimentos|"
    r"cumprimentos|atenciosamente|atentamente|"
    r"best\s+regards|kind\s+regards|warm\s+regards|regards|sincerely(?:\s+yours)?|"
    r"yours\s+(?:sincerely|faithfully|truly)|cheers"
    r")\s*[,;:!.]?\s*$",
    re.I,
)

# How far back from the end a closing may start and still be read as a sign-off rather than as a
# sentence in the body. A signature block is a handful of short lines; a match 20 lines up is prose.
_CLOSING_TAIL_LINES = 6


# ── the HTML paste ───────────────────────────────────────────────────────────
#
# A real signature does not get typed, it gets COPIED — out of Outlook or Gmail, where it is an HTML
# table of logos, social icons and inline styles. Pasting that into a plain-text field and storing it
# verbatim puts `<td style="padding:12px 0px 8px 12px;">` in a client's inbox. Every surface this
# module feeds is plain text end to end (the readonly textarea, the clipboard, and the `mailto:` body,
# which has no HTML at all), so HTML is never something we can honour as-is. It IS something we can
# RECOGNISE and convert -- and then say we did, because silently rewriting what someone pasted is the
# other half of the same failure.

# A closed list of tag names, not a generic `<[a-z]+`: a plain signature legitimately contains
# "<filipe@lindoservico.pt>", and treating that as markup would convert a perfectly good signature.
_HTML_TAG_RE = re.compile(
    r"<\s*/?\s*(?:!doctype|html|head|body|meta|title|table|tbody|thead|tfoot|tr|td|th|div|p|br|hr"
    r"|span|img|a|b|i|u|em|strong|font|style|script|ul|ol|li|h[1-6]|center|small|blockquote)\b",
    re.I)
_DROP_BLOCK_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.I | re.S)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
# Tags that end a visual line. `</a>` and `</span>` deliberately absent: they are inline, and breaking
# on them turns "Tel.: +351 912 345 678" into two lines.
_BREAK_RE = re.compile(
    r"<\s*br\s*/?\s*>|<\s*/\s*(?:p|div|tr|li|h[1-6]|table|blockquote|center)\s*>|<\s*hr\s*/?\s*>",
    re.I)
_IMG_RE = re.compile(r"<\s*img\b[^>]*>", re.I)
_ANY_TAG_RE = re.compile(r"<[^>]+>")


def looks_like_html(text: str) -> bool:
    """True when ``text`` is an HTML signature pasted out of a mail client."""
    return bool(_HTML_TAG_RE.search(text or ""))


def html_to_text(markup: str) -> str:
    """Flatten a pasted HTML signature to the plain-text block the drafts actually use.

    Deterministic and dependency-free — no parser, because the input is a signature fragment and not
    a document, and a hand-rolled pass we can read beats a dependency we cannot audit for something
    that touches client mail.

    Images are DROPPED rather than replaced by their ``alt``: a mail-client signature's images are
    the logo and the social icons, so alt text contributes "AUTO", "Facebook", "Instagram", "LinkedIn"
    — noise that reads like a bug. A link keeps its visible text; a link that wrapped only an image
    has none left and disappears with it.

    **Every blank line is dropped**, not merely collapsed. A signature's visual spacing in HTML comes
    from CSS padding, which plain text cannot carry, while the *markup* nesting (``</div></td></tr>``)
    emits a break per level — so preserving blank lines preserves the one thing that is an artefact
    of the markup and none of the thing that was actually designed. A tight block is what a client
    reads as a signature; the person can add spacing back by hand.
    """
    text = _COMMENT_RE.sub(" ", _DROP_BLOCK_RE.sub(" ", markup or ""))
    text = _IMG_RE.sub("", text)
    text = _BREAK_RE.sub("\n", text)
    text = _ANY_TAG_RE.sub("", text)
    text = _html.unescape(text)
    # NBSP is what Outlook pads with; left alone it survives every strip() and leaves lines that look
    # blank but are not, which then defeat the empty-line rule below.
    text = text.replace(" ", " ").replace("​", "")
    return "\n".join(
        stripped for line in text.splitlines() if (stripped := " ".join(line.split())))


def normalize_signature(text: str) -> tuple[str, bool]:
    """Normalise a signature as typed OR as pasted. Returns ``(plain_text, was_html)``.

    The single entry point every write path uses, so "what does the stored signature look like" has
    one answer regardless of how it arrived.
    """
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if looks_like_html(raw):
        return html_to_text(raw), True
    return raw.strip(), False


def unknown_tokens(template: str) -> list[str]:
    """Placeholder-shaped tokens in ``template`` that are not in :data:`PLACEHOLDERS`, in order.

    The save-time gate. Returning the offending tokens (rather than a bool) is what lets the editor
    say *which* one is wrong -- "assinatura inválida" sends someone hunting through their own text.
    """
    seen: list[str] = []
    for token in _TOKEN_RE.findall(template or ""):
        if token not in PLACEHOLDERS and token not in seen:
            seen.append(token)
    return seen


def values_for(person: dict[str, Any] | None) -> dict[str, str]:
    """The placeholder values for one person: ``{token: value}``, every token present.

    Missing/None columns resolve to ``''`` rather than raising, so a person row read from a DB that
    predates a column still renders (the line is simply dropped by rule 2).
    """
    row = person or {}
    return {token: str(row.get(key) or "").strip() for token, (key, _help) in PLACEHOLDERS.items()}


def render(template: str, person: dict[str, Any] | None) -> str:
    """Render a signature template for ``person``.

    Rule 2 lives here: a line is dropped when it contained placeholders and ALL of them resolved
    empty. Runs of blank lines collapse to one, and trailing whitespace goes -- an editor's stray
    newline should not become a ragged block at the bottom of a client email.
    """
    values = values_for(person)
    out: list[str] = []
    for line in (template or "").splitlines():
        tokens = [t for t in _TOKEN_RE.findall(line) if t in values]
        if tokens and not any(values[t] for t in tokens):
            continue                      # every placeholder on this line is empty -> drop the line
        for token in tokens:
            line = line.replace(token, values[token])
        out.append(line.rstrip())
    text: list[str] = []
    for line in out:                      # collapse blank runs, drop leading blanks
        if not line and (not text or not text[-1]):
            continue
        text.append(line)
    return "\n".join(text).strip()


def load_template(config_dir: str | Path | None, default: str = DEFAULT_TEMPLATE) -> str:
    """The install-wide default signature template from ``config/signature_template.md``.

    Same contract as the other editable playbooks (``clientdraft.load_template``): everything after
    the first ``---`` fence is the template, the note above it is for the human, and the file is read
    per call so an edit is live without a rebuild. Falls back to ``default`` when the file is
    missing, unreadable, empty, or carries a token this module cannot fill -- a botched edit must
    degrade to a working closing, never to a client email with ``{telemovel}`` printed in it.
    """
    if config_dir is None:
        return default
    try:
        raw = Path(config_dir).joinpath("signature_template.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return default
    _note, sep, body = raw.partition("\n---\n")
    tmpl = (body if sep else raw).strip()
    if not tmpl or unknown_tokens(tmpl):
        return default
    return tmpl


def for_person(person: dict[str, Any] | None, config_dir: str | Path | None = None) -> str:
    """The rendered signature block for ``person``: their own template, else the install default.

    ``person['signature']`` being blank is the ordinary case, not an error -- most people never open
    the editor. It means "use whatever the install closes with", which is exactly the pre-ADR-047
    behaviour with the person's name filled in.
    """
    own = str((person or {}).get("signature") or "").strip()
    return render(own or load_template(config_dir), person)


def strip_closing(body: str) -> str:
    """Remove a trailing sign-off block from ``body`` (the last closing salutation to the end).

    Only fires when the salutation sits in the last :data:`_CLOSING_TAIL_LINES` non-blank lines and
    is not the very first line -- so "Cumprimentos," opening a short note is body text and survives,
    while a model-written footer at the bottom is cut. Returns ``body`` right-stripped when there is
    nothing to cut.
    """
    lines = (body or "").rstrip().splitlines()
    tail_start = 0
    seen = 0
    for i in range(len(lines) - 1, -1, -1):      # walk back over the last N NON-BLANK lines
        if lines[i].strip():
            seen += 1
            if seen > _CLOSING_TAIL_LINES:
                tail_start = i + 1
                break
    for i in range(len(lines) - 1, tail_start - 1, -1):
        if i > 0 and _CLOSING_RE.match(lines[i]):
            return "\n".join(lines[:i]).rstrip()
    return "\n".join(lines).rstrip()


def sign(body: str, person: dict[str, Any] | None, config_dir: str | Path | None = None) -> str:
    """``body`` with the person's signature as its closing. The one call sites should use.

    Strip-then-append, so a draft that already ends in a sign-off (the model wrote one, or a
    deterministic template carries one) does not reach the client with two. An empty rendered
    signature short-circuits: the body comes back untouched, closing and all.
    """
    block = for_person(person, config_dir)
    if not block:
        return body
    return f"{strip_closing(body)}\n\n{block}" if (body or "").strip() else block
