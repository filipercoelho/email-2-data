"""Classifier: coercion/guardrail (provider-agnostic) + Gemini path incl. retry-on-empty."""

import json
from types import SimpleNamespace

from email2data.classifier import _coerce, build_user_message, classify
from email2data.signals import Signals

ENV = {"message_id": "mid:x@y", "subject": "s", "from": {"email": "a@b.pt"},
       "date": "2026-05-27", "attachments": [], "body_text": "corpo"}
SIG = Signals(sender_domain="b.pt", direction="inbound", source_mailbox="", is_bulk=False, is_automated=False, is_forward=False)
SETTINGS = {"llm": {"provider": "vertex_gemini", "model": "gemini-2.5-flash",
                    "max_tokens": 256, "max_retries": 3, "ignore_confidence_floor": 0.85}}


def _raw(**over):
    base = {"counterparty": "CLIENT", "purpose": "ESTIMATE_REQUEST_FROM_CLIENT",
            "urgency": 80, "confidence": 0.9, "reason": "r", "entities": {}}
    base.update(over)
    return base


def test_coerce_happy_path_derives_priority_and_direction():
    r = _coerce(_raw(), ENV, SIG, 0.85)
    assert r.counterparty == "CLIENT" and r.purpose == "ESTIMATE_REQUEST_FROM_CLIENT"
    assert r.priority == "HIGH"            # client -> HIGH (derived)
    assert r.direction == "inbound"        # set from signals, not the model
    assert r.message_id == "mid:x@y" and r.from_addr == "a@b.pt"


def test_coerce_low_confidence_bulk_is_downgraded_to_review():
    r = _coerce(_raw(counterparty="BULK", purpose="PUBLICITY", urgency=5, confidence=0.5), ENV, SIG, 0.85)
    assert r.priority == "NEEDS_REVIEW" and "guardrail" in r.reason


def test_coerce_confident_bulk_is_ignored():
    r = _coerce(_raw(counterparty="BULK", purpose="PUBLICITY", urgency=5, confidence=0.95), ENV, SIG, 0.85)
    assert r.priority == "IGNORE"


def test_coerce_validates_enums_and_clamps():
    r = _coerce(_raw(counterparty="BOGUS", purpose="??", urgency=999, confidence=5), ENV, SIG, 0.85)
    assert r.counterparty == "OTHER" and r.purpose == "OTHER"
    assert r.urgency == 100 and r.confidence == 1.0


def test_coerce_fills_deterministic_nif_iban_from_body():
    env = {**ENV, "body_text": "Contribuinte 501442600, IBAN PT50 0002 0123 1234 5678 9015 4"}
    r = _coerce(_raw(), env, SIG, 0.85)
    assert r.entities.nif == "501442600"
    assert r.entities.iban == "PT50000201231234567890154"


def test_build_user_message_attaches_extracted_values():
    env = {**ENV, "subject": "Fatura", "body_text": "NIF 501442600, valor 10 €"}
    msg = build_user_message(env, SIG, None)
    assert "OFFLINE SIGNALS" in msg
    assert "nif=501442600" in msg
    assert "amounts_found=" in msg


class _FakeGemini:
    """Returns resp.text from a sequence (to simulate transient empty responses)."""

    def __init__(self, texts):
        self._texts, self.calls = list(texts), 0
        self.models = SimpleNamespace(generate_content=self._gen)

    def _gen(self, **kw):
        t = self._texts[min(self.calls, len(self._texts) - 1)]
        self.calls += 1
        return SimpleNamespace(text=t)


def test_classify_gemini_happy_path():
    c = _FakeGemini([json.dumps(_raw())])
    r = classify(ENV, SIG, None, "playbook", c, SETTINGS)
    assert r.counterparty == "CLIENT" and r.priority == "HIGH" and c.calls == 1


def test_classify_gemini_retries_on_empty_then_succeeds():
    c = _FakeGemini(["", "", json.dumps(_raw(counterparty="SUPPLIER", purpose="INVOICE_OR_ACCOUNTING"))])
    r = classify(ENV, SIG, None, "playbook", c, SETTINGS)
    assert r.counterparty == "SUPPLIER" and c.calls == 3   # retried past the empties
