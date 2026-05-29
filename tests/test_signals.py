from email import message_from_bytes

from email2data.signals import detect_forward, header_signals

INBOUND = b"From: Cliente <joao@cliente.pt>\r\nSubject: orcamento\r\n\r\nola\r\n"
INTERNAL = b"From: Pedro <pedro.ferreira@lindoservico.pt>\r\nSubject: RE: x\r\n\r\nola\r\n"
BULK = (b"From: news@shop.com\r\nSubject: promo\r\nList-Unsubscribe: <https://x/u>\r\n\r\nbuy\r\n")
AUTO = b"From: noreply@bank.pt\r\nSubject: aviso\r\nAuto-Submitted: auto-generated\r\n\r\nx\r\n"


def _sig(raw):
    return header_signals(message_from_bytes(raw))


def test_direction_from_domain():
    assert _sig(INBOUND).direction == "inbound"
    assert _sig(INTERNAL).direction == "internal"


def test_bulk_detected_from_list_header():
    s = _sig(BULK)
    assert s.is_bulk and s.ignorable_offline and s.bulk_evidence == "List-*"


def test_automated_is_feature_not_offline_ignorable():
    # Automated/transactional mail (supplier invoices from no-reply, etc.) must NOT be binned offline.
    s = _sig(AUTO)
    assert s.is_automated and not s.ignorable_offline


def test_plain_inbound_is_not_ignorable():
    assert not _sig(INBOUND).ignorable_offline


def test_detect_forward():
    assert detect_forward("FW: PO", "---------- Forwarded message ---------\nFrom: client@x")
    assert detect_forward("RE: x", "De: cliente\nMensagem reencaminhada")
    assert not detect_forward("orcamento", "bom dia, preciso de um corte")
