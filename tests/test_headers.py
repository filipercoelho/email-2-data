"""Header decoding — RFC 2047 encoded-words, raw 8-bit headers, and the two mixed together.

The regression these pin: a raw 8-bit (non-RFC 2047) header used to reach the UI as one U+FFFD per
byte — "Pedido de or??amento ??? constru????o" — because compat32 sanitizes such a header into
``Header(charset="unknown-8bit")`` before any of our code can decode it. See ADR-043.
"""

from email.header import Header

import pytest

from email2data.headers import (decode_bytes, decode_value, header_text, parse_message,
                                repair_8bit)

# The real message that surfaced this (corpus/5d9b256a…): UTF-8 bytes sat raw in the header.
RAW_8BIT = (
    b"Subject: Pedido de or\xc3\xa7amento \xe2\x80\x93 constru\xc3\xa7\xc3\xa3o de cenografia\r\n"
    b"From: Hugo Matos <hugo@example.pt>\r\n\r\nbody\r\n"
)
SUBJECT = "Pedido de orçamento – construção de cenografia"


def test_raw_8bit_header_decodes_instead_of_becoming_replacement_chars():
    subject = decode_value(parse_message(RAW_8BIT).get("Subject"))
    assert subject == SUBJECT
    assert "�" not in subject


def test_default_parser_is_the_one_that_loses_the_bytes():
    """Documents *why* parse_message exists — if this ever stops being true, drop the policy."""
    from email import message_from_bytes

    assert "�" in str(message_from_bytes(RAW_8BIT).get("Subject"))


def test_rfc2047_encoded_words_still_decode():
    assert decode_value("=?utf-8?Q?Pedido_de_or=C3=A7amento?=") == "Pedido de orçamento"
    assert decode_value("=?iso-8859-1?Q?or=E7amento?=") == "orçamento"
    assert decode_value("=?utf-8?B?b3LDp2FtZW50bw==?=") == "orçamento"


def test_encoded_word_mixed_with_raw_8bit_in_one_header():
    """decode_header hands the plain run back as raw-unicode-escape bytes; both halves must survive."""
    raw = b"Subject: =?utf-8?q?RE=3A?= or\xc3\xa7amento \xe2\x80\x93 fim\r\n\r\nx"
    assert decode_value(parse_message(raw).get("Subject")) == "RE: orçamento – fim"


def test_raw_8bit_falls_back_to_cp1252_when_not_utf8():
    """Windows-1252 bytes (Outlook's dashes/quotes) are not valid UTF-8 — decode, don't mangle."""
    raw = b"Subject: or\xe7amento \x96 caro\r\n\r\nx"          # \x96 is an en dash in cp1252
    assert decode_value(parse_message(raw).get("Subject")) == "orçamento – caro"


def test_plain_ascii_and_empty_values_are_untouched():
    assert decode_value("RE: proposta") == "RE: proposta"
    assert decode_value("") == "" and decode_value(None) == ""
    assert repair_8bit("já decodificado") == "já decodificado"


def test_malformed_encoded_word_degrades_instead_of_aborting_the_parse():
    """`decode_header` raises HeaderParseError here; one bad subject must not kill a whole fetch."""
    assert decode_value("=?utf-8?b?a?=") == "=?utf-8?b?a?="


def test_bogus_charset_label_never_raises():
    assert decode_value("=?unknown-8bit?q?or=C3=A7amento?=") == "orçamento"
    assert decode_value("=?not-a-charset?q?ol=C3=A1?=") == "olá"
    assert decode_bytes(b"\xc3\xa7", "definitely-not-a-codec") == "ç"


def test_header_object_input_does_not_crash():
    """Defensive: a caller still using the default parser hands us a Header, not a str."""
    assert decode_value(Header("assunto", "utf-8")) == "assunto"


def test_header_text_repairs_but_does_not_decode_encoded_words():
    """Address/Message-ID headers must be split as tokens first, and carry no surrogates onward."""
    raw = b"To: Jo\xc3\xa3o <joao@example.pt>\r\nMessage-ID: <=?utf-8?q?x?=@b.pt>\r\n\r\nx"
    msg = parse_message(raw)
    assert header_text(msg, "To") == "João <joao@example.pt>"
    assert header_text(msg, "Message-ID") == "<=?utf-8?q?x?=@b.pt>"   # token, left intact
    assert header_text(msg, "Absent") == ""


@pytest.mark.parametrize("value", [
    "Pedido de or\udcc3\udca7amento",                       # surrogates from a raw 8-bit header
    "=?utf-8?q?RE=3A?= or\udcc3\udca7amento",               # mixed
    "=?utf-8?b?a?=",                                        # malformed
])
def test_no_surrogate_ever_escapes_the_decoder(value):
    """A surrogate reaching a store raises UnicodeEncodeError in sqlite3/json — it must not."""
    decode_value(value).encode("utf-8")                     # must not raise
