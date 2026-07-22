"""Deterministic composer for the DESCRIÇÃO block of a proposta (C100) / fatura (V001).

Assembles the **descritivo do produto/serviço** from the JobSpec fields a human already confirmed
and an editable skeleton in ``config/description_playbook.md``. Pure and deterministic — no LLM,
nothing invented: every value comes from :mod:`jobspec`, and a field with no confirmed value renders
a **visible gap marker** (``[[MATERIAL?]]``) rather than being guessed or silently dropped. A human
reviews, edits, and pastes into ARTSOFT; the system never issues a document.

Same shape as :mod:`clientdraft` (ADR-013/-027), for the same reason: the deterministic draft is the
artefact, and the optional polish (:func:`polish_description`) sits *on top* of it — the model may
rewrite prose but must carry every confirmed fact through verbatim, and :func:`missing_facts` checks
that it did. "The model quietly changed 12mm to 10mm" is a visible failure, not a silent one.

**The house style is the AVERAGE of the analysed corpus.** The 59-document corpus behind this
(``out/wording-analysis/``) contains three competing macro-styles; the module renders the modal
(most-frequent) form on each independent dimension, which is a single coherent shape:

* macro-structure **prose** (39/59 = 66%, vs 20 labelled) — one flowing sentence, not bullet lines;
* the mode is a **header + one body segment** (32/59);
* opener **``Produção de``** (the dominant verb, 15);
* material introduced by **``em``** (45 vs 10 ``de``);
* the header is **passed through as typed** — title/sentence case is the plurality (33 vs 12 UPPER),
  so forcing upper-case would be *less* representative, not more.

The deterministic skeleton keeps the joins simple (comma-separated clauses); the optional polish
(:func:`polish_description`) turns that into natural prose while carrying every confirmed fact through
verbatim, which is where connective smoothing belongs because it is fact-checked. The two minority
styles are deliberately not emitted — the module exists to converge future documents on the
representative average, not to reproduce the historical spread. See ADR-030.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import llm
from .jobspec import JobSpec, SpecField

# Template tokens, and the JobSpec key each resolves from. ``None`` means the caller supplies it.
TOKENS = ("titulo", "processo", "item", "material", "dimensoes", "acabamento", "observacoes")

# token -> (jobspec key, item-scoped?, gap-marker label). A token whose key is None is caller-supplied.
#
# ``process`` is deliberately NOT wired to {processo}. The registry field is an *internal* manufacturing
# note ("(interno) Definir o processo de fabrico") — pasting it in front of the item noun produces
# "Impressão Direta HQ - 1 Face placa". In the corpus a specific technique surfaces under Acabamento:
# ("Corte e gravação laser no formato"), and the opener stays generic. So {processo} is a style slot the
# caller may override, never an internal field promoted to client-facing prose.
_SLOTS: dict[str, tuple[Optional[str], bool, str]] = {
    "item":       ("item",          True,  "O QUÊ"),
    "material":   ("material",      True,  "MATERIAL"),
    "dimensoes":  ("dimensions",    True,  "DIMENSÕES"),
    "acabamento": ("colour_finish", True,  "ACABAMENTO"),
    "processo":   (None,            False, "PROCESSO"),
    "titulo":     (None,            False, "TÍTULO"),
    "observacoes": (None,           False, "OBSERVAÇÕES"),
}

# Thickness is not a slot of its own — the house form fuses it onto the material
# ("Acrílico foscado 30 mm", "MDF de 12mm"), so it is appended to {material}.
_THICKNESS_KEY = "thickness"

# Style default for {processo} (see the playbook). "Produção de" is true of everything the shop makes,
# so it is a style choice; a SPECIFIC process ("Corte Laser") is a factual claim and is only ever
# emitted when the JobSpec confirms it. Never widen this fallback.
DEFAULT_PROCESSO = "Produção de"

# Lines that vanish entirely when their token is empty, instead of rendering a gap marker. Only the
# commercial caveat qualifies: 11/17 July propostas carry an "Obs.:", 0/20 faturas do, so an absent
# one is normal — whereas an absent material is a hole in the spec. ``titulo`` joins this set only on
# the 2nd..nth block of a multi-item render (the corpus carries exactly one header per document).
_OPTIONAL_TOKENS = frozenset({"observacoes"})

# The AVERAGE shape: header line + one flowing prose sentence. Joins are deliberately simple
# (comma-separated clauses) — the deterministic layer supplies the representative STRUCTURE and the
# fact-checked polish naturalises the connectors. Mirrors the modal corpus form
# ("Produção de <item> em <material>, c/ <dims>, <acabamento>").
DEFAULT_TEMPLATE = (
    "{titulo}\n\n"
    "{processo} {item} em {material}, c/ {dimensoes}, {acabamento}.\n\n"
    "Obs.: {observacoes}"
)


def gap(label: str) -> str:
    """The visible marker for a fact the JobSpec does not have. Deliberately ugly and un-sendable:
    the drafter must resolve it, and it must not read as a deliberate omission."""
    return f"[[{label}?]]"


_GAP_RE = re.compile(r"\[\[[^\]]+\?\]\]")


def load_template(path: str | Path) -> str:
    """Read the block skeleton from the playbook: the text between the first and second ``---`` fence
    lines (above the first is an editor note, below the second is reference vocabulary). Falls back to
    :data:`DEFAULT_TEMPLATE` if the file is missing, unreadable, or has lost every token — so a botched
    edit degrades gracefully instead of shipping an empty descritivo."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return DEFAULT_TEMPLATE
    parts = raw.split("\n---\n")
    tmpl = (parts[1] if len(parts) >= 3 else (parts[1] if len(parts) == 2 else raw)).strip()
    return tmpl if any(f"{{{t}}}" in tmpl for t in TOKENS) else DEFAULT_TEMPLATE


def _value(f: Optional[SpecField], *, require_confirmed: bool) -> Optional[str]:
    """The usable string in a SpecField, or None.

    ``require_confirmed`` is the zero-hallucination dial: with it set, an LLM-drafted value that no
    human has ticked does NOT reach the document — it renders a gap marker instead. That is the
    conservative default, because this text goes to a client with a price attached."""
    if f is None or not (f.value or "").strip():
        return None
    if require_confirmed and not f.confirmed:
        return None
    return f.value.strip()


@dataclass
class Description:
    """A rendered descritivo plus what is wrong with it.

    ``text`` is never withheld — a draft with holes is more useful than no draft, which is why the
    holes are marked in-band. ``gaps`` and ``unconfirmed`` are what the UI surfaces alongside it."""
    text: str
    gaps: list[str] = field(default_factory=list)          # labels with no usable value
    unconfirmed: list[str] = field(default_factory=list)   # values used that no human has ticked
    facts: list[tuple[str, str]] = field(default_factory=list)  # (label, value) actually rendered

    @property
    def complete(self) -> bool:
        """True when nothing is missing — i.e. no gap marker survives in the text."""
        return not self.gaps


def _render_item(tmpl: str, spec: JobSpec, idx: int, *, titulo: Optional[str],
                 observacoes: Optional[str], require_confirmed: bool,
                 processo: Optional[str] = None, with_title: bool = True) -> Description:
    """Render the template for one line item of ``spec``.

    ``with_title=False`` drops the header line entirely (2nd..nth block of a multi-item render)."""
    item = spec.items[idx] if 0 <= idx < len(spec.items) else {}
    gaps: list[str] = []
    unconfirmed: list[str] = []
    facts: list[tuple[str, str]] = []
    values: dict[str, str] = {}

    for token, (key, item_scoped, label) in _SLOTS.items():
        if key is None:
            continue
        f = item.get(key) if item_scoped else spec.job_fields.get(key)
        val = _value(f, require_confirmed=require_confirmed)
        if val is None:
            # A value the model drafted but nobody ticked is reported, not used.
            loose = _value(f, require_confirmed=False)
            if loose is not None:
                unconfirmed.append(label)
            values[token] = gap(label)
            gaps.append(label)
            continue
        values[token] = val
        facts.append((label, val))

    # {processo} is style, not a fact: "Produção de" is true of everything the shop makes, so it never
    # renders a gap. A SPECIFIC opener is a factual claim and only appears when the caller passes one.
    values["processo"] = (processo or "").strip() or DEFAULT_PROCESSO

    # Thickness fuses onto the material, per the house form ("MDF de 12mm").
    thick = _value(item.get(_THICKNESS_KEY), require_confirmed=require_confirmed)
    if thick and not _GAP_RE.search(values.get("material", "")):
        values["material"] = f"{values['material']} {thick}"
        facts.append(("ESPESSURA", thick))
    elif not thick and not _GAP_RE.search(values.get("material", "")):
        loose = _value(item.get(_THICKNESS_KEY), require_confirmed=False)
        if loose is not None:
            unconfirmed.append("ESPESSURA")
        values["material"] = f"{values['material']} {gap('ESPESSURA')}"
        gaps.append("ESPESSURA")

    if with_title:
        # Passed through as typed — title/sentence case is the corpus plurality (33 vs 12 upper), so
        # forcing upper-case would be LESS representative of the average, not more.
        values["titulo"] = (titulo or spec.subject or "").strip() or gap("TÍTULO")
        if _GAP_RE.search(values["titulo"]):
            gaps.append("TÍTULO")
        else:
            facts.append(("TÍTULO", values["titulo"]))
    else:
        values["titulo"] = ""
    values["observacoes"] = (observacoes or "").strip()

    optional = _OPTIONAL_TOKENS if with_title else _OPTIONAL_TOKENS | {"titulo"}
    text = _splice(tmpl, values, optional)
    return Description(text=text, gaps=gaps, unconfirmed=unconfirmed, facts=facts)


def _splice(tmpl: str, values: dict[str, str], optional: frozenset[str] | set[str]) -> str:
    """Replace every ``{token}`` in ``tmpl``. A line carrying an OPTIONAL token with no value is
    removed whole — otherwise the document keeps a dangling label ("Obs.:") that reads as an
    unfinished thought."""
    out_lines: list[str] = []
    for line in tmpl.split("\n"):
        present = [t for t in TOKENS if f"{{{t}}}" in line]
        if present and all(t in optional and not values.get(t) for t in present):
            continue
        for t in present:
            line = line.replace(f"{{{t}}}", values.get(t, ""))
        out_lines.append(line)
    # Collapse the blank line an removed optional line leaves behind.
    text = "\n".join(out_lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def build_description(spec: JobSpec, template: str | None = None, *,
                      titulo: str | None = None, observacoes: str | None = None,
                      processo: str | None = None, item_index: int | None = None,
                      require_confirmed: bool = True) -> Description:
    """Render the DESCRIÇÃO block for ``spec``.

    With ``item_index`` set, renders that line item alone; otherwise every item in the spec is
    rendered and the blocks are joined by a blank line (a single lead routinely lists several distinct
    pieces — see :mod:`jobspec` "Multi-item"). The title is emitted once, on the first block only,
    because the corpus carries exactly one header per document (59/59).

    ``require_confirmed=True`` (the default) keeps LLM-drafted-but-unticked values OUT of the text and
    reports them in ``unconfirmed``. Pass ``False`` only for a preview the user cannot send.
    """
    tmpl = template or DEFAULT_TEMPLATE
    idxs = [item_index] if item_index is not None else list(range(len(spec.items) or 1))
    blocks = [
        _render_item(
            tmpl, spec, i,
            titulo=titulo,
            observacoes=observacoes if n == len(idxs) - 1 else None,
            require_confirmed=require_confirmed, processo=processo,
            with_title=(n == 0),   # exactly one header per document (59/59 in the corpus)
        )
        for n, i in enumerate(idxs)
    ]
    if len(blocks) == 1:
        return blocks[0]

    merged = Description(text="\n\n".join(b.text for b in blocks if b.text))
    for b in blocks:
        merged.gaps.extend(b.gaps)
        merged.unconfirmed.extend(b.unconfirmed)
        merged.facts.extend(b.facts)
    return merged


# ── optional AI polish (ADR-027) — sits ON TOP of the deterministic draft ────────────────────────

# Fallback when config/description_playbook.md carries no polish section. Deliberately carries the same
# hard rules: a missing config must not quietly become a permissive prompt.
DEFAULT_POLISH_PLAYBOOK = (
    "És um assistente da Lindo Serviço (corte laser, CNC, gravação, sinalética). Reescreve o "
    "DESCRITIVO para a coluna DESCRIÇÃO de uma proposta/fatura, em português de Portugal. "
    "REGRAS: mantém TODOS os valores de FACTOS palavra por palavra (materiais, espessuras, medidas, "
    "cores, acabamentos) — não os reformules nem converjas unidades; mantém os marcadores [[...?]] "
    "intactos, são lacunas por preencher; nunca inventes materiais, medidas, acabamentos, prazos ou "
    "preços — se não está em FACTOS, não existe; mantém o título tal como está, na primeira linha; "
    "mantém a ordem dos eixos L x A x P. Uma frase corrida por peça, seca e técnica, sem floreados."
)


def load_polish_playbook(path: str | Path) -> str:
    """Read the polish system prompt: the ``## Polish`` section of the playbook if present, else the
    whole file. Falls back to :data:`DEFAULT_POLISH_PLAYBOOK` if missing, unreadable, or empty."""
    try:
        raw = Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return DEFAULT_POLISH_PLAYBOOK
    return raw or DEFAULT_POLISH_PLAYBOOK


def _norm(s: str) -> str:
    """Whitespace-insensitive form for the coverage check: the model may legitimately re-wrap a long
    line, but a reworded measurement must still fail."""
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def missing_facts(polished: str, facts: list[tuple[str, str]]) -> list[str]:
    """The confirmed fact VALUES that did not survive into ``polished``, in the order given.

    This is the guarantee behind the polish button: ADR-027 requires the AI layer to sit on top of the
    deterministic draft, and "on top" is only meaningful if it is *checked*. Deterministic substring
    test over whitespace-normalised text — no second model call grading the first. A dropped or altered
    dimension is exactly the error that costs money here."""
    hay = _norm(polished)
    return [v for _lbl, v in facts if v.strip() and _norm(v) not in hay]


def dropped_gaps(polished: str, original: str) -> int:
    """How many gap markers the polish removed. A model that tidies ``[[ESPESSURA?]]`` away turns a
    visible hole into an invisible one, which is worse than not polishing at all."""
    return max(0, len(_GAP_RE.findall(original)) - len(_GAP_RE.findall(polished)))


def build_polish_message(draft: str, facts: list[tuple[str, str]]) -> str:
    """The user message for :func:`polish_description`: the deterministic block and the facts that must
    survive it verbatim. Every block is labelled and explicitly bounded, so an empty one reads as
    "nothing known" rather than inviting the model to fill the gap."""
    fact_lines = "\n".join(f"  - {k}: {v}" for k, v in facts) or "  (nada confirmado ainda)"
    return (f"DESCRITIVO (reescreve este texto):\n{draft}\n\n"
            f"FACTOS (mantém todos, palavra por palavra):\n{fact_lines}\n")


def polish_description(draft: str, facts: list[tuple[str, str]], playbook: str, client: Any,
                       cfg: dict[str, Any]) -> str:
    """Rewrite ``draft`` through the LLM, keeping every confirmed fact verbatim.

    Returns the polished text — a DRAFT the human reviews and edits. The caller must run
    :func:`missing_facts` and :func:`dropped_gaps` over the result and surface any loss, and must keep
    the deterministic draft available so the user can reject this outright. Raises ``llm.LLMError`` on
    failure: the user asked for this explicitly and paid for the call, so a failure is reported, never
    degraded into silently returning the unpolished text as if it had worked."""
    msg = build_polish_message(draft, facts)
    out = llm.call(client, cfg, playbook, msg, text=True, temperature=0.2)
    text = (out or "").strip()
    if not text:
        raise llm.LLMError("description polish returned empty text")
    return text
