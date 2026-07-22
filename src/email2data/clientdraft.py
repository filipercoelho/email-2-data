"""Deterministic client-email composer for the Projetos page.

Assembles the **email para o cliente** from the clarifying questions the user ticked and an
editable skeleton in ``config/client_email_template.md``. Pure and deterministic — no LLM,
nothing invented: the questions come from the Gate-1 gap analysis (``jobspec.askables``) and
this module only splices them into the template. A human reviews/edits and sends; the system
never sends.

The optional "melhorar tom" layer anticipated by ADR-013 is now built, at the bottom of this module
(:func:`polish_draft`, ADR-027). It sits *on top* of :func:`build_draft` exactly as ADR-013 required:
the deterministic body is assembled first and handed to the model, which may rewrite the prose around
the questions but must carry them through verbatim — and :func:`missing_questions` checks that it did,
deterministically, so "the model quietly dropped question 3" is a visible failure and not a silent one.
It runs on an explicit button click only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import llm

PLACEHOLDER = "{perguntas}"

# Fallback when the config file is missing/empty/malformed — keeps the composer working out of
# the box and mirrors the historical hard-coded JS template it replaces.
DEFAULT_TEMPLATE = (
    "Bom dia,\n\n"
    "Para conseguirmos avançar com o orçamento, precisávamos de confirmar:\n\n"
    f"{PLACEHOLDER}\n\n"
    "Obrigado."
)


def load_template(path: str | Path, token: str = PLACEHOLDER,
                  default: str = DEFAULT_TEMPLATE) -> str:
    """Read the body skeleton from a markdown file: everything after the first ``---`` fence
    line (the text above it is an editor note). Falls back to ``default`` if the file is
    missing, unreadable, or has lost the ``token`` placeholder (a guard so a botched edit
    degrades gracefully instead of shipping a token-less email).

    ``token``/``default`` default to the ``ask`` purpose so every existing caller is unchanged;
    the purpose-aware loader (:func:`load_purpose_template`) passes each purpose's own pair."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return default
    _note, sep, body = raw.partition("\n---\n")
    tmpl = (body if sep else raw).strip()
    return tmpl if token in tmpl else default


def build_draft(questions: list[str], template: str | None = None) -> str:
    """Render the email body: the ``questions`` as a 1-based numbered list spliced into the
    template at :data:`PLACEHOLDER`. Order is the caller's (the endpoint sorts by registry
    order so the list matches the on-screen checklist). With no questions the list collapses to
    empty — the caller decides whether to offer the email at all."""
    tmpl = template or DEFAULT_TEMPLATE
    numbered = "\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1))
    return tmpl.replace(PLACEHOLDER, numbered)


# ── purposes (ADR-031) — one composer, many client emails, still deterministic ───────────────────
# The composer used to write exactly one kind of email ("ask the missing must-haves"). A purpose is
# a template + the shape of what the user supplies for it. Three input kinds:
#   * ``questions`` — the jobspec.askables checklist (as today);           token {perguntas}
#   * ``reason``    — a reason chosen from an editable list + a free note; token {motivo}
#   * ``text``      — a free-text block the user writes (costs, dates, …); token {conteudo}
# Every purpose's base draft is still assembled with NO LLM (ADR-013); the optional polish below
# sits on top and is checked (ADR-027) — for money/text purposes the check is extended to the
# prices/numbers/dates the user typed (:func:`extract_values` / :func:`missing_values`).


@dataclass(frozen=True)
class Purpose:
    id: str            # stable wire id (the JS mirror is derived from the GET response)
    label: str         # pt-PT label for the selector
    token: str         # placeholder spliced in this purpose's template
    input_kind: str    # "questions" | "reason" | "text"


DEFAULT_PURPOSE = "ask"

PURPOSES: list[Purpose] = [
    Purpose("ask",       "Pedir detalhes em falta",                 PLACEHOLDER,  "questions"),
    Purpose("reject",    "Recusar o trabalho",                      "{motivo}",   "reason"),
    Purpose("quote",     "Aceitar / enviar custos (orçamento)",     "{conteudo}", "text"),
    Purpose("follow_up", "Seguimento / sem resposta",               PLACEHOLDER,  "questions"),
    Purpose("approval",  "Pedir aprovação de arte final / maquete", "{conteudo}", "text"),
    Purpose("payment",   "Pedir sinal / pagamento",                 "{conteudo}", "text"),
    Purpose("deadline",  "Atualização de prazo / atraso",           "{conteudo}", "text"),
    Purpose("ready",     "Pronto para entrega / recolha",           "{conteudo}", "text"),
]
PURPOSES_BY_ID: dict[str, Purpose] = {p.id: p for p in PURPOSES}

# Built-in fallback body per purpose (used when the editable config file is absent/malformed).
# ``ask`` reuses DEFAULT_TEMPLATE so its historical behaviour is byte-identical.
DEFAULT_TEMPLATES: dict[str, str] = {
    "ask": DEFAULT_TEMPLATE,
    "reject": (
        "Bom dia,\n\n"
        "Agradecemos o vosso contacto e o interesse na Lindo Serviço.\n\n"
        "Depois de analisarmos o pedido, de momento não conseguimos avançar com este trabalho:\n\n"
        "{motivo}\n\n"
        "Ficamos ao dispor para futuros projetos.\n\n"
        "Com os melhores cumprimentos,\nLindo Serviço"
    ),
    "quote": (
        "Bom dia,\n\n"
        "Obrigado pelo pedido. Segue a nossa proposta:\n\n"
        "{conteudo}\n\n"
        "Ficamos a aguardar a vossa confirmação para avançarmos.\n\n"
        "Com os melhores cumprimentos,\nLindo Serviço"
    ),
    "follow_up": (
        "Bom dia,\n\n"
        "Voltamos ao contacto sobre este pedido. Para conseguirmos avançar, faltava-nos "
        "confirmar:\n\n"
        f"{PLACEHOLDER}\n\n"
        "Obrigado."
    ),
    "approval": (
        "Bom dia,\n\n"
        "Antes de avançarmos para produção, precisamos da vossa aprovação:\n\n"
        "{conteudo}\n\n"
        "Confirmam que podemos avançar assim?\n\n"
        "Com os melhores cumprimentos,\nLindo Serviço"
    ),
    "payment": (
        "Bom dia,\n\n"
        "Para agendarmos a produção, segue o pedido de pagamento:\n\n"
        "{conteudo}\n\n"
        "Obrigado.\n\n"
        "Com os melhores cumprimentos,\nLindo Serviço"
    ),
    "deadline": (
        "Bom dia,\n\n"
        "Uma atualização sobre o prazo deste trabalho:\n\n"
        "{conteudo}\n\n"
        "Pedimos desculpa por qualquer transtorno e ficamos ao dispor.\n\n"
        "Com os melhores cumprimentos,\nLindo Serviço"
    ),
    "ready": (
        "Bom dia,\n\n"
        "O vosso trabalho está pronto:\n\n"
        "{conteudo}\n\n"
        "Combinamos a entrega/recolha?\n\n"
        "Com os melhores cumprimentos,\nLindo Serviço"
    ),
}

# Editable per-purpose template file (in config/). ``ask`` keeps its historical filename so the
# existing file and its tests are untouched; the rest follow client_email_<id>_template.md.
CONFIG_FILES: dict[str, str] = {
    "ask":       "client_email_template.md",
    "reject":    "client_email_reject_template.md",
    "quote":     "client_email_quote_template.md",
    "follow_up": "client_email_follow_up_template.md",
    "approval":  "client_email_approval_template.md",
    "payment":   "client_email_payment_template.md",
    "deadline":  "client_email_deadline_template.md",
    "ready":     "client_email_ready_template.md",
}

# Fallback reject reasons (config/client_email_reject_reasons.md overrides). Never invent a reason:
# the user picks one of these (or writes a free note) — the email never bins a client silently.
DEFAULT_REJECT_REASONS: list[str] = [
    "Sem capacidade / agenda no período pedido",
    "Prazo pedido não é exequível",
    "Fora do âmbito técnico da oficina",
    "Quantidade abaixo do mínimo para produção",
    "Preço-alvo inviável para a margem necessária",
    "Material indisponível / lead time incompatível",
    "Ficheiros / desenho não executáveis como estão",
    "Informação essencial em falta, nunca fornecida",
]


def load_purpose_template(purpose: str, config_dir: str | Path | None) -> str:
    """The editable template for ``purpose``: the per-purpose config file if a config dir is
    known, else the built-in default. Unknown purpose falls back to :data:`DEFAULT_PURPOSE`."""
    p = PURPOSES_BY_ID.get(purpose, PURPOSES_BY_ID[DEFAULT_PURPOSE])
    default = DEFAULT_TEMPLATES[p.id]
    if config_dir is None:
        return default
    return load_template(Path(config_dir) / CONFIG_FILES[p.id], token=p.token, default=default)


def load_reasons(path: str | Path) -> list[str]:
    """Read the editable reject-reason list: one reason per non-empty line after the first
    ``---`` fence (the text above it is an editor note). Falls back to
    :data:`DEFAULT_REJECT_REASONS` if the file is missing, unreadable, or has no reasons."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return DEFAULT_REJECT_REASONS
    _note, sep, body = raw.partition("\n---\n")
    lines = [ln.strip().lstrip("-*").strip() for ln in (body if sep else raw).splitlines()]
    reasons = [ln for ln in lines if ln]
    return reasons or DEFAULT_REJECT_REASONS


def build_purpose_draft(purpose: str, template: str, *,
                        questions: list[str] | None = None,
                        reason: str | None = None, reason_note: str | None = None,
                        content: str | None = None) -> str:
    """Assemble the base draft for ``purpose`` by splicing the user's input into ``template`` at
    its token. Deterministic — no LLM. Dispatches on the purpose's ``input_kind``:

    * ``questions`` → a 1-based numbered list (identical to :func:`build_draft`);
    * ``reason``    → the chosen reason, then the free note (blank-line separated, empties dropped);
    * ``text``      → the free-text content verbatim.

    ``build_purpose_draft("ask", tmpl, questions=qs)`` is byte-identical to
    ``build_draft(qs, tmpl)``."""
    p = PURPOSES_BY_ID.get(purpose, PURPOSES_BY_ID[DEFAULT_PURPOSE])
    if p.input_kind == "questions":
        filled = "\n".join(f"{i}. {q}" for i, q in enumerate(questions or [], 1))
    elif p.input_kind == "reason":
        parts = [s for s in [(reason or "").strip(), (reason_note or "").strip()] if s]
        filled = "\n\n".join(parts)
    else:  # text
        filled = (content or "").strip()
    return template.replace(p.token, filled)


# ── optional AI polish (ADR-027) — sits ON TOP of the deterministic draft ────────────────────────

# Fallback when config/client_email_polish_playbook.md is missing. Deliberately carries the same hard
# rules as the file: a missing config must not quietly become a permissive prompt.
DEFAULT_POLISH_PLAYBOOK = (
    "És um assistente da Lindo Serviço (corte laser, CNC, gravação, sinalética). Reescreve o "
    "RASCUNHO num email mais natural em português de Portugal, usando o HISTÓRICO para apanhar o tom "
    "do cliente. Consoante o email, recebes um bloco PERGUNTAS ou um bloco VALORES A MANTER. "
    "REGRAS: mantém TODAS as perguntas de PERGUNTAS palavra por palavra, numeradas; mantém TODOS os "
    "VALORES A MANTER (preços, números, datas) exatamente como estão — não os alteres, reformates "
    "nem arredondes; não acrescentes perguntas novas; nunca inventes nem prometas preços, prazos, "
    "medidas, quantidades ou materiais — se não está em FACTOS CONFIRMADOS ou HISTÓRICO, não existe. "
    "Curto e cordial. Assina 'Com os melhores cumprimentos,\nLindo Serviço'."
)

# Per-message cap on thread text handed to the model. The composer only needs tone and what the client
# already said; the full thread is what the spec pass is for, and sending it here would re-bill it.
_THREAD_CHARS = 1200
_THREAD_MAX_MSGS = 6


def load_polish_playbook(path: str | Path) -> str:
    """Read the polish system prompt (the whole file, like ``reply_playbook.md``). Falls back to
    :data:`DEFAULT_POLISH_PLAYBOOK` if the file is missing, unreadable, or empty."""
    try:
        raw = Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return DEFAULT_POLISH_PLAYBOOK
    return raw or DEFAULT_POLISH_PLAYBOOK


def _norm(s: str) -> str:
    """Whitespace-insensitive form for the coverage check: the model is told to keep each question
    verbatim, but it may legitimately re-wrap a long line. Collapsing runs of whitespace means a
    re-wrap passes while an actual reword still fails."""
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def missing_questions(polished: str, questions: list[str]) -> list[str]:
    """The questions that did NOT survive into ``polished``, in the order given.

    This is the guarantee behind the polish button: ADR-013 requires the AI layer to sit on top of the
    deterministic draft, and "on top" is only meaningful if it is *checked*. Deterministic substring
    test over whitespace-normalised text — no second model call grading the first."""
    hay = _norm(polished)
    return [q for q in questions if q.strip() and _norm(q) not in hay]


# ── verbatim-fact guard (ADR-031) — the zero-hallucination rule applied to money ──────────────────
# The money/text purposes (quote, payment, deadline, …) carry numbers the user typed: a price, a
# total, a date, a quantity. The AI polish may reword the prose around them, but it must NEVER
# invent, alter, reformat, or round one — a guessed commitment to a client is a costly error
# (VISION non-negotiable). We extract those tokens deterministically and check they survived the
# polish verbatim, exactly as :func:`missing_questions` does for questions.
#
# The pattern is deliberately conservative: it matches only tokens that carry a currency symbol,
# a unit, a percent, or an unambiguous date shape. So list markers ("1.", "2."), thousands
# ("1.250"), and phone runs ("912 345 678") do NOT match (avoiding false alarms), while a bare
# total with no €/unit ("Total: 160") is intentionally NOT guarded — the templates and playbook
# nudge the user to write "€", and this boundary is documented in the reference doc.
# A number core with internal (but not trailing) thousands/decimal separators, so "€160," keeps
# the comma out and "1.250,00" stays whole. Currency/percent alts need no trailing \b (€/% are
# symbols); the euro-word and unit alts use \b so "160 euros"/"2 m" match but "metros" does not.
_NUM = r"\d+(?:[.,]\d+)*"
_FACT_RE = re.compile(
    rf"€\s?{_NUM}"                                            # €160 / € 1.250,00
    rf"|{_NUM}\s?€"                                           # 160€ / 160 €
    rf"|{_NUM}\s?eur(?:os)?\b"                                # 160 euros / 160 eur
    rf"|{_NUM}\s?%"                                           # 50%
    rf"|{_NUM}\s?(?:unidades|semanas|meses|m[eê]s|dias|dia|horas|hora|"
    rf"mm|cm|dm|km|kg|min|pcs|und|un|p[cç]|m|g|h)\b"          # 2mm, 10 dias, 20 un (units longest-first)
    r"|\b\d{4}-\d{2}-\d{2}\b"                                 # ISO 2026-09-30
    r"|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b",                     # 30/09 or 30/09/2026
    re.IGNORECASE,
)


def extract_values(text: str) -> list[str]:
    """Money / number / date tokens in ``text`` that an AI polish must carry through verbatim, in
    order of first appearance, de-duplicated (whitespace/case-insensitively)."""
    out: list[str] = []
    seen: set[str] = set()
    for m in _FACT_RE.finditer(text or ""):
        tok = m.group(0).strip()
        key = _norm(tok)
        if key and key not in seen:
            seen.add(key)
            out.append(tok)
    return out


def missing_values(polished: str, values: list[str]) -> list[str]:
    """The values that did NOT survive verbatim into ``polished`` (whitespace-insensitive), in the
    order given. Same guarantee as :func:`missing_questions`, applied to numbers: a value here
    means the model altered a price/date/quantity, so the caller must block that version. Note the
    check is whitespace-insensitive but otherwise exact, so ``160€`` → ``160 €`` (a reformat) is
    reported — reformatting a money value is treated as an alteration, on purpose."""
    hay = _norm(polished)
    return [v for v in values if v.strip() and _norm(v) not in hay]


# ── output language (ADR-032) — the polish may translate; the number guard survives translation ───
# The deterministic base is always Portuguese (ADR-013). Choosing a non-PT language turns the polish
# into a translate+polish pass: numbers/dates are still checked verbatim (language-independent), but a
# translated question/prose cannot be, so the caller marks a non-PT result "traduzido — rever".
LANGUAGES: list[tuple[str, str]] = [
    ("pt", "Português"), ("en", "English"), ("fr", "Français"), ("es", "Español"),
]
LANGUAGES_BY_ID: dict[str, str] = {code: label for code, label in LANGUAGES}
DEFAULT_LANGUAGE = "pt"
# pt-PT name of each target language, for the directive handed to the model.
_LANG_PT_NAME: dict[str, str] = {"en": "inglês", "fr": "francês", "es": "espanhol"}


def build_polish_message(draft_body: str, questions: list[str] | None = None,
                         facts: list[tuple[str, str]] | None = None,
                         thread: list[dict[str, Any]] | None = None,
                         *, keep_values: list[str] | None = None,
                         language: str = DEFAULT_LANGUAGE) -> str:
    """The user message for :func:`polish_draft`: the deterministic draft, whatever must survive it
    verbatim (the ``questions`` for question-purposes, the money/date ``keep_values`` for the
    money/text ones), the confirmed facts it may restate, and what the client actually wrote.

    The must-keep block is labelled for what it is — ``PERGUNTAS`` for questions, ``VALORES A
    MANTER`` for numbers — so the model is told exactly what it may not touch. The FACTOS and
    HISTÓRICO blocks are always present and explicitly bounded so an empty one reads as "nothing
    known" rather than inviting the model to fill the gap — zero-hallucination applied to a prompt.

    A non-default ``language`` prepends an ``IDIOMA DE SAÍDA`` directive: translate the whole email to
    that language, but keep every VALORES A MANTER exactly as written (a number/date is never
    translated). PT (the default) adds nothing, so the message is byte-identical to before."""
    blocks: list[str] = []
    if language != DEFAULT_LANGUAGE and language in _LANG_PT_NAME:
        blocks.append(
            f"IDIOMA DE SAÍDA: escreve o email final em {_LANG_PT_NAME[language]}. Traduz todo o "
            "RASCUNHO fielmente para esse idioma, incluindo as perguntas, MAS mantém os VALORES A "
            "MANTER (preços, números, datas) exatamente como estão — nunca traduzas nem reformates "
            "um número.")
    blocks.append(f"RASCUNHO (reescreve este texto):\n{draft_body}")
    if questions:
        q_lines = "\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1))
        blocks.append("PERGUNTAS (mantém todas, palavra por palavra, numeradas):\n" + q_lines)
    if keep_values:
        v_lines = "\n".join(f"  - {v}" for v in keep_values)
        blocks.append("VALORES A MANTER (preços, números e datas — palavra por palavra, sem "
                      "reformatar nem arredondar):\n" + v_lines)
    fact_lines = "\n".join(f"  - {k}: {v}" for k, v in (facts or [])) or "  (nada confirmado ainda)"
    blocks.append("FACTOS CONFIRMADOS (só podes referir estes):\n" + fact_lines)
    msgs = (thread or [])[-_THREAD_MAX_MSGS:]
    if msgs:
        parts = []
        for m in msgs:
            who = (m.get("from_email") or "?").strip()
            when = (m.get("date") or "")[:10]
            text = (m.get("body") or "").strip()[:_THREAD_CHARS]
            parts.append(f"  [{when} · {who}]\n{text}")
        thread_block = "\n\n".join(parts)
    else:
        thread_block = "  (sem histórico disponível)"
    blocks.append("HISTÓRICO (o que o cliente escreveu):\n" + thread_block)
    return "\n\n".join(blocks) + "\n"


def polish_draft(draft_body: str, questions: list[str], playbook: str, client: Any,
                 cfg: dict[str, Any], *, facts: list[tuple[str, str]] | None = None,
                 thread: list[dict[str, Any]] | None = None,
                 keep_values: list[str] | None = None,
                 language: str = DEFAULT_LANGUAGE) -> str:
    """Rewrite ``draft_body`` into warmer prose through the LLM, keeping the questions (and any
    ``keep_values`` — prices/numbers/dates) verbatim. With a non-default ``language`` it also
    translates the email to that language while still keeping the numbers/dates verbatim.

    Returns the polished text — a DRAFT the human reviews, edits and sends; the system never sends.
    The caller is responsible for running :func:`missing_questions` / :func:`missing_values` over the
    result and surfacing any loss, and for keeping the deterministic draft available so the user can
    reject this outright. Raises ``llm.LLMError`` on failure: the user asked for this explicitly and
    paid for the call, so a failure is reported, never degraded into silently returning the unpolished
    text as if it worked.
    """
    msg = build_polish_message(draft_body, questions, facts, thread,
                               keep_values=keep_values, language=language)
    out = llm.call(client, cfg, playbook, msg, text=True, temperature=0.3)
    text = (out or "").strip()
    if not text:
        raise llm.LLMError("polish returned empty text")
    return text
