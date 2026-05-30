"""Deterministic value extraction (Idea 2) — high-precision PT-PT structured values.

Format-anchored regex, in two tiers (see the red-team in the design notes):

  * AUTHORITATIVE — the code fills the entity, the LLM does not: ``nif``, ``iban``. Their
    format is specific enough that a match is almost certainly THE value (and NIF is checksum-
    validated), so deterministic beats the model.
  * CANDIDATES — attached to the FACTS block; the LLM stays authoritative: ``amounts``,
    ``dates``, ``doc_numbers``. A body may hold several and only the model knows which is
    relevant (which amount is the price, which date is the deadline).

Relative dates ("até sexta") are deliberately left to the LLM — it has the received date.
Deferred (not MVP): phone numbers (NIF collision), IBAN mod-97, doc-number as an authoritative
field.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# Amount: a number (PT thousands/decimal: 1.234,56) adjacent to a currency anchor. Requiring the
# anchor is what keeps precision high — bare "50 peças" / "3mm" / "2026" never match. No newline
# inside the number so two stacked figures aren't glued together.
_AMOUNT = re.compile(
    r"(?:€|eur\b|euros?\b)\s?\d[\d. ]*(?:,\d+)?|\d[\d. ]*(?:,\d+)?\s?(?:€|eur\b|euros?\b)", re.I
)
# Explicit dates only (yyyy-mm-dd, dd/mm/yyyy, dd-mm-yyyy). Relative dates -> LLM.
_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b")
# PT NIF: 9 digits near an anchor, not part of a longer number. Checksum-validated below.
_NIF = re.compile(r"(?:nif|nipc|contribuinte)\D{0,12}(?<!\d)(\d{9})(?!\d)", re.I)
# PT IBAN: PT + 2 check digits + 21 digits, spaces allowed.
_IBAN = re.compile(r"\bpt\d{2}(?:\s?\d){21}\b", re.I)
# Doc references: an anchor word followed by a token that contains a digit.
_DOC = re.compile(
    r"\b(fatura|factura|ft|fr|nota de encomenda|encomenda|ordem de compra|po)\b"
    r"[\s:.#/\-]*([a-z0-9][a-z0-9/_.\-]*\d[a-z0-9/_.\-]*)",
    re.I,
)


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).casefold()


def _valid_nif(n: str) -> bool:
    """PT NIF mod-11 check digit. Turns a noisy 9-digit match into a near-certain NIF."""
    if len(n) != 9 or not n.isdigit():
        return False
    total = sum(int(n[i]) * (9 - i) for i in range(8))
    check = 11 - (total % 11)
    check = 0 if check >= 10 else check
    return check == int(n[8])


def _plausible_date(d: str) -> bool:
    parts = re.split(r"[/-]", d)
    if len(parts) != 3:
        return False
    a, b, c = parts
    day, month = (b, a) if len(a) == 4 else (a, b)  # yyyy-mm-dd vs dd/mm/yyyy
    try:
        return 1 <= int(day) <= 31 and 1 <= int(month) <= 12
    except ValueError:
        return False


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _dedupe(items: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for it in items:
        seen.setdefault(it, None)
    return list(seen)


def extract_values(subject: str, body_text: str) -> dict[str, Any]:
    """Pure: subject+body -> {nif, iban (authoritative); amounts, dates, doc_numbers (candidates)}."""
    text = _fold(subject + "\n" + body_text)
    nifs = [m for m in _NIF.findall(text) if _valid_nif(m)]
    ibans = ["".join(m.split()).upper() for m in _IBAN.findall(text)]
    amounts = _dedupe(_clean(m) for m in _AMOUNT.findall(text))
    dates = _dedupe(d for d in _DATE.findall(text) if _plausible_date(d))
    docs = _dedupe(f"{a}:{b}" for a, b in _DOC.findall(text))
    return {
        "nif": nifs[0] if nifs else None,
        "iban": ibans[0] if ibans else None,
        "amounts": amounts,
        "dates": dates,
        "doc_numbers": docs,
    }


def render_candidates(vals: dict[str, Any]) -> str:
    """Compact facts string for the prompt; '' when nothing extracted."""
    parts: list[str] = []
    if vals.get("nif"):
        parts.append(f"nif={vals['nif']}")
    if vals.get("iban"):
        parts.append(f"iban={vals['iban']}")
    if vals.get("amounts"):
        parts.append("amounts_found=" + " | ".join(vals["amounts"]))
    if vals.get("dates"):
        parts.append("dates_found=" + " | ".join(vals["dates"]))
    if vals.get("doc_numbers"):
        parts.append("docs_found=" + " | ".join(vals["doc_numbers"]))
    return "values[" + "; ".join(parts) + "]" if parts else ""
