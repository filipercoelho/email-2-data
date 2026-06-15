"""Canonical pt-PT display labels for the triage + project enums.

Single source of truth so the cockpit lenses (Fila / Projetos / Para-ti / Contrapartes) and the inbox
report stop carrying their own drifting JS copies. Python dicts here; pages embed the relevant ones as
JS consts via ``cockpit_ui.page(embeds=...)``. Keys MUST track the enums they label:
  * counterparty / purpose / priority / direction → ``schema.py`` (COUNTERPARTY / PURPOSE / PRIORITIES / DIRECTION)
  * stage / close-party → ``project.py`` (STAGES / CLOSE_PARTIES)
A missing key falls back to the raw enum value at render time, so an out-of-sync label degrades, never crashes.
"""

from __future__ import annotations

COUNTERPARTY_PT: dict[str, str] = {
    "CLIENT": "Cliente",
    "LEAD": "Lead",
    "SUPPLIER": "Fornecedor",
    "INTERNAL": "Interno",
    "BULK": "Newsletter",
    "OTHER": "Outro",
}

PURPOSE_PT: dict[str, str] = {
    "PO_FROM_CLIENT": "Encomenda de cliente",
    "ESTIMATE_REQUEST_FROM_CLIENT": "Pedido de orçamento",
    "OUTBOUND_INVOICE": "Fatura nossa",
    "OUR_ORDER_TO_SUPPLIER": "Encomenda a fornecedor",
    "SUPPLIER_REPLY_OR_CONFIRMATION": "Resposta de fornecedor",
    "INVOICE_OR_ACCOUNTING": "Fatura / contabilidade",
    "FOLLOW_UP": "Seguimento",
    "OWN_REJECTION": "Recusámos",
    "CLIENT_REJECTION": "Cliente recusou",
    "PUBLICITY": "Publicidade",
    "INTERNAL_OPS": "Operações internas",
    "OTHER": "Outro",
}

PRIORITY_PT: dict[str, str] = {
    "HIGH": "Alta", "MEDIUM": "Média", "LOW": "Baixa", "IGNORE": "Ignorar", "NEEDS_REVIEW": "Rever",
}

DIRECTION_PT: dict[str, str] = {"inbound": "entrada", "outbound": "saída", "internal": "interno"}


def fila_labels() -> dict[str, dict[str, str]]:
    """The label dicts the Fila needs in the browser (counterparty + purpose pickers)."""
    return {"counterparty": COUNTERPARTY_PT, "purpose": PURPOSE_PT, "priority": PRIORITY_PT}
