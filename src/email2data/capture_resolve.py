"""Deterministic capture→project resolver (ADR-019 §4 / R2 seed; Increment 1).

Ranks the ACTIVE projects against a capture's text WITHOUT an LLM — the cheap pre-filter that resolves
the certain cases so the model (WP4 / Increment 2) is invoked ONLY on genuine ambiguity (ADR-001:
compute ∝ uncertainty). Matching is by each project's own identity (title + client name + client email)
expanded with the editable ``config/capture_playbook.md`` alias table and the gazetteer. Deterministic,
offline, fully testable — no network, no model. The resolver only ever SUGGESTS; the human still
confirms every capture (ADR-019 §5 / R9).
"""

from __future__ import annotations

import csv
import re
import unicodedata
from pathlib import Path
from typing import Any, Optional

_WORD = re.compile(r"[0-9a-zà-ÿ]+", re.IGNORECASE)

# pt/en function words that carry no project signal — dropped so they never inflate a match score.
_STOP = frozenset({
    "para", "com", "uma", "uns", "umas", "dos", "das", "que", "por", "nos", "nas", "ao", "aos",
    "the", "and", "for", "with", "este", "esta", "isto", "como", "mais", "sem", "sobre", "pela",
    "pelo", "ja", "nao", "sim", "tem", "ter", "foi", "vai", "ser", "esta", "estao", "obra", "projeto",
    "cliente", "encomenda", "orcamento", "prazo", "preco", "euros", "eur",
})


def _fold(s: str) -> str:
    """Lowercase + strip accents (NFKD) so "Acrílico"/"acrilico" and "Sousa"/"sousa" compare equal."""
    nfkd = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _tokens(s: str) -> set[str]:
    """Significant word tokens of a string (folded, ≥3 chars, stopwords dropped)."""
    return {t for t in _WORD.findall(_fold(s)) if len(t) >= 3 and t not in _STOP}


def load_aliases(path: str | Path) -> dict[str, str]:
    """Parse the ``## Aliases`` section of ``config/capture_playbook.md`` into ``{alias: canonical}``.

    Each alias line is ``- <alias> -> <canonical>`` (``->`` / ``→`` / ``=`` accepted). The alias is a
    short form the staffer might say (e.g. ``VDH``), the canonical the project-facing term (e.g.
    ``Violaine d'Harcourt``); both are folded to tokens at match time. Best-effort: a missing file or a
    malformed line is skipped, so a bad playbook degrades to plain matching, never crashes the worker.
    """
    out: dict[str, str] = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    in_section = False
    for raw in lines:
        line = raw.strip()
        if line.startswith("#"):
            in_section = line.lstrip("#").strip().lower().startswith("alias")
            continue
        if not in_section or not line.startswith(("-", "*")):
            continue  # only list items are aliases — prose in the section is ignored
        body = line[1:].strip()
        for sep in ("->", "→", "=>", "="):
            if sep in body:
                alias, _, canon = body.partition(sep)
                alias, canon = alias.strip(), canon.strip()
                if alias and canon:
                    out[_fold(alias)] = canon
                break
    return out


def load_gazetteer(path: str | Path) -> dict[str, str]:
    """``{domain: note}`` from the gazetteer CSV — the note seeds extra needles for a project whose
    client sits at that domain (e.g. corticoenetos.com → "supplies cork rolls…"). Best-effort."""
    out: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return out
    for row in csv.reader(text.splitlines()):
        if not row or row[0].startswith("#") or row[0].strip().lower() == "domain":
            continue
        domain = row[0].strip().lower()
        note = row[2].strip() if len(row) > 2 else ""
        if domain:
            out[domain] = note
    return out


def _expand(tokens: set[str], aliases: dict[str, str]) -> set[str]:
    """Add the canonical's tokens whenever an alias token is present (alias expansion, both directions
    fold to tokens). ``{"vdh"}`` + alias ``vdh→Violaine d'Harcourt`` → ``{"vdh","violaine","harcourt"}``."""
    out = set(tokens)
    for alias, canon in aliases.items():
        if alias in tokens or _fold(alias) in tokens:
            out |= _tokens(canon)
    return out


def _project_needles(project: dict[str, Any], aliases: dict[str, str],
                     gazetteer: dict[str, str]) -> set[str]:
    """The token set that identifies a project: its title + client name + client-email local-part,
    plus the gazetteer note for the client's domain, all alias-expanded."""
    title = project.get("title") or ""
    name = project.get("client_name") or ""
    email = project.get("client_email") or ""
    local, _, domain = email.partition("@")
    needles = _tokens(title) | _tokens(name) | _tokens(local)
    if domain and domain.lower() in gazetteer:
        needles |= _tokens(gazetteer[domain.lower()]) | _tokens(domain.split(".")[0])
    return _expand(needles, aliases)


def rank_projects(text: str, projects: list[dict[str, Any]], *,
                  aliases: Optional[dict[str, str]] = None,
                  gazetteer: Optional[dict[str, str]] = None) -> list[dict[str, Any]]:
    """Rank ``projects`` by how strongly the capture ``text`` names them. Returns each project dict with
    an added ``score`` (count of distinct identifying tokens the text mentions) + ``matched`` (which
    ones), sorted by score desc; ties preserve the input order (newest-first), so a zero-signal capture
    keeps the default ordering. Pure function — no LLM, no I/O."""
    aliases = aliases or {}
    gazetteer = gazetteer or {}
    cap_tokens = _expand(_tokens(text), aliases)
    ranked: list[dict[str, Any]] = []
    for p in projects:
        needles = _project_needles(p, aliases, gazetteer)
        matched = sorted(needles & cap_tokens)
        ranked.append({**p, "score": len(matched), "matched": matched})
    # stable sort by descending score (Python's sort is stable → ties keep input order)
    ranked.sort(key=lambda r: r["score"], reverse=True)
    return ranked


def best_project(text: str, projects: list[dict[str, Any]], *,
                 aliases: Optional[dict[str, str]] = None,
                 gazetteer: Optional[dict[str, str]] = None) -> Optional[str]:
    """The single project id the deterministic resolver is CONFIDENT about, or ``None`` when the signal
    is absent or ambiguous (→ defer to the human / the LLM). Confident = the top match has a non-zero
    score AND strictly beats the runner-up (no tie). A ``None`` here is the ADR-001 hand-off to WP4."""
    ranked = rank_projects(text, projects, aliases=aliases, gazetteer=gazetteer)
    if not ranked or ranked[0]["score"] <= 0:
        return None
    if len(ranked) > 1 and ranked[1]["score"] >= ranked[0]["score"]:
        return None  # ambiguous tie — do not guess
    return ranked[0]["project_id"]
