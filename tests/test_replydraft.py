"""Phase C: clarifying-reply message build + draft (no network)."""

from types import SimpleNamespace

from email2data.replydraft import build_reply_message, draft_reply

SPEC = {"subject": "Pedido de orçamento", "has_attachment": True,
        "fields": {"item": {"value": "troféus", "source": "llm"},
                   "quantity": {"value": "20 unidades", "source": "llm"},
                   "material": {"value": None, "source": ""},
                   "client_identity": {"value": "CLIENT", "source": "offline"},
                   "process": {"value": None, "source": ""}}}
RD = {"questions": ["Em que material?", "Que dimensões?"], "missing": ["material", "dimensions"]}


def test_build_reply_message_grounds_facts_and_questions():
    m = build_reply_message(SPEC, RD)
    assert "troféus" in m and "20 unidades" in m            # confirmed facts included
    assert "Em que material?" in m and "Que dimensões?" in m  # missing -> questions
    assert "client_identity" not in m and "process" not in m  # internal flags excluded


def test_build_reply_message_handles_empty_spec():
    m = build_reply_message({"subject": "x", "has_attachment": False, "fields": {}}, {"questions": []})
    assert "nada confirmado ainda" in m and "nenhuma" in m


class _FakeGemini:
    def __init__(self, text):
        self.models = SimpleNamespace(generate_content=lambda **kw: SimpleNamespace(text=text))


def test_draft_reply_returns_text():
    c = _FakeGemini("Boa tarde, obrigado pelo seu pedido. Em que material pretendem?")
    settings = {"llm": {"provider": "vertex_gemini", "model": "gemini-2.5-flash", "max_retries": 2, "max_tokens": 256}}
    out = draft_reply(SPEC, RD, "pb", c, settings)
    assert "obrigado" in out.lower()
