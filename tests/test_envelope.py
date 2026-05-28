from email2data.envelope import MAX_BODY_CHARS, parse_eml

ENCODED = (
    b"Subject: =?utf-8?Q?Pedido_de_or=C3=A7amento?=\r\n"
    b"From: =?utf-8?Q?Jo=C3=A3o?= Silva <joao@example.pt>\r\n"
    b"To: geral@lindoservico.pt\r\n"
    b"Date: Wed, 27 May 2026 09:00:00 +0100\r\n"
    b"Message-ID: <x1@example.pt>\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
    b"Bom dia, preciso de corte laser.\r\n"
)

HTML_ONLY = (
    b"Subject: promo\r\nFrom: a@b.pt\r\nMessage-ID: <h@b.pt>\r\n"
    b"Content-Type: text/html; charset=utf-8\r\n\r\n"
    b"<html><body><p>Ol&aacute; <b>50%</b></p></body></html>\r\n"
)


def test_rfc2047_subject_and_name_decoded():
    env = parse_eml(ENCODED)
    assert env["subject"] == "Pedido de orçamento"
    assert env["from"] == {"name": "João Silva", "email": "joao@example.pt"}
    assert env["date"].startswith("2026-05-27")
    assert "corte laser" in env["body_text"]


def test_html_only_is_stripped_to_text():
    env = parse_eml(HTML_ONLY)
    assert env["has_html"] is True
    assert "<" not in env["body_text"]
    assert "Olá" in env["body_text"] and "50%" in env["body_text"]


def test_body_is_truncated():
    big = b"Subject: x\r\nMessage-ID: <big@b>\r\n\r\n" + b"a" * (MAX_BODY_CHARS + 5000)
    assert len(parse_eml(big)["body_text"]) <= MAX_BODY_CHARS
