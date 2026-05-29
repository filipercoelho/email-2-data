"""Forwarded/quoted original extraction — Phase 2 (SCAFFOLD: types defined, bodies stubbed).

The forwarded-order trap (VISION tenet 4): a colleague forwards a client PO, so ``From`` is internal
and a naive rule buries a client order as "internal". We defeat it by mining the ORIGINAL external
sender/subject out of the quoted block and classifying on that.
"""

from __future__ import annotations

from dataclasses import dataclass

# Markers that introduce a forwarded/quoted original, across PT/EN clients (Outlook, Gmail, Apple).
FORWARD_MARKERS = (
    "---------- Forwarded message",
    "Begin forwarded message",
    "-----Original Message-----",
    "-----Mensagem original-----",
    "Mensagem encaminhada",
)
# Quoted-original header lines to parse the original sender/subject from (PT + EN).
ORIGINAL_HEADER_KEYS = {
    "from": ("From:", "De:"),
    "subject": ("Subject:", "Assunto:"),
    "sent": ("Sent:", "Enviada:", "Date:", "Data:"),
}


@dataclass
class Original:
    from_addr: str | None
    subject: str | None
    present: bool  # True if a forwarded/quoted original was detected at all


def extract_original(body_text: str) -> Original:
    """CONTRACT: scan body_text for a FORWARD_MARKER; if found, parse the following quoted header
    block for the original From/Subject (ORIGINAL_HEADER_KEYS, PT+EN). Return Original(present=False)
    when no forwarded original is detected. Pure function of text — no I/O. Must be robust to HTML
    already stripped to text and to multiple nested quotes (take the OUTERMOST/first original)."""
    raise NotImplementedError("Phase 2")
