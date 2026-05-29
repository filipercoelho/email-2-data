"""Cascade: Tier-0 offline bulk-IGNORE (no LLM) vs Tier-1 escalation, and the client-hint veto."""

import json
from types import SimpleNamespace

from email2data import cascade

SETTINGS = {"llm": {"provider": "vertex_gemini", "model": "gemini-2.5-flash",
                    "max_tokens": 256, "max_retries": 2, "ignore_confidence_floor": 0.85}}

BULK = b"From: news@shop.com\r\nSubject: promo\r\nMessage-ID: <b@s>\r\nList-Unsubscribe: <https://x/u>\r\n\r\nbuy now\r\n"
CLIENT = b"From: Joao <joao@cliente.pt>\r\nSubject: orcamento\r\nMessage-ID: <c@s>\r\n\r\nPreciso de um corte laser, podem orcamentar?\r\n"


class _Store:
    def __init__(self, hint=None):
        self._hint = hint

    def lookup(self, domain):
        return self._hint


class _Client:
    def __init__(self, verdict):
        self._v, self.calls = verdict, 0
        self.models = SimpleNamespace(generate_content=self._gen)

    def _gen(self, **kw):
        self.calls += 1
        return SimpleNamespace(text=json.dumps(self._v))


_VERDICT = {"counterparty": "CLIENT", "purpose": "ESTIMATE_REQUEST_FROM_CLIENT",
            "urgency": 80, "confidence": 0.9, "reason": "r", "entities": {}}


def test_bulk_decided_offline_without_llm():
    c = _Client(_VERDICT)
    r = cascade.triage(BULK, "pb", _Store(hint=None), c, SETTINGS)
    assert r.priority == "IGNORE" and r.decided_by.startswith("tier0") and c.calls == 0


def test_non_bulk_escalates_to_llm():
    c = _Client(_VERDICT)
    r = cascade.triage(CLIENT, "pb", _Store(hint=None), c, SETTINGS)
    assert r.counterparty == "CLIENT" and r.decided_by.startswith("tier1") and c.calls == 1


def test_any_known_domain_vetoes_offline_ignore():
    # Even with a bulk header, ANY known domain (client OR supplier) must NOT be binned offline.
    # (Amazon/invoicing platforms set List-Unsubscribe; a known supplier must escalate, not bin.)
    for hint in ("CLIENT", "SUPPLIER"):
        c = _Client(_VERDICT)
        r = cascade.triage(BULK, "pb", _Store(hint=hint), c, SETTINGS)
        assert r.decided_by.startswith("tier1") and c.calls == 1, f"hint={hint}"
