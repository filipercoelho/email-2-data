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


# ── build_store: a missing gazetteer CSV must never be silent (see test_store.py) ──


def _base(tmp_path):
    """A minimal project layout: build_store derives config/ and out/ from the settings path."""
    (tmp_path / "config").mkdir()
    (tmp_path / "out").mkdir()
    sp = tmp_path / "config" / "settings.json"
    sp.write_text(json.dumps({"paths": {"out_dir": "out", "corpus_dir": "corpus",
                                        "captures_dir": "captures"}}), encoding="utf-8")
    return {"paths": {"out_dir": "out", "corpus_dir": "corpus", "captures_dir": "captures"},
            "__settings_path__": str(sp)}


def test_gazetteer_csv_points_at_the_editable_source_of_truth(tmp_path):
    s = _base(tmp_path)
    assert cascade.gazetteer_csv(s) == tmp_path / "config" / "gazetteer.csv"


def test_build_store_warns_instead_of_silently_serving_frozen_priors(tmp_path, capsys):
    """The live defect this fixes: knowledge.db kept serving 15 rows for three days after
    config/gazetteer.csv went missing, with no warning, no error and no log line. Seed once, delete
    the CSV, rebuild — the second build must say so."""
    s = _base(tmp_path)
    gaz = cascade.gazetteer_csv(s)
    gaz.write_text("domain,counterparty,note\ncliente.pt,CLIENT,a real client\n", encoding="utf-8")
    store = cascade.build_store(s)
    assert store.lookup("cliente.pt") == "CLIENT"
    store.close()
    capsys.readouterr()

    gaz.unlink()                       # the CSV vanishes; the table does not
    store = cascade.build_store(s)
    err = capsys.readouterr().err
    assert "MISSING" in err and "frozen" in err
    assert store.lookup("cliente.pt") == "CLIENT"   # still vetoes an offline IGNORE, still uneditable
    store.close()


def test_build_store_is_quiet_on_a_first_run_with_no_gazetteer_yet(tmp_path, capsys):
    store = cascade.build_store(_base(tmp_path))
    assert capsys.readouterr().err == ""
    assert store.count() == 0
    store.close()


def test_open_store_does_not_seed(tmp_path):
    """`gazetteer status` must report the table as it STANDS, not as the CSV would leave it —
    otherwise opening the status page would repair the drift it is meant to report."""
    s = _base(tmp_path)
    cascade.gazetteer_csv(s).write_text(
        "domain,counterparty,note\ncliente.pt,CLIENT,x\n", encoding="utf-8")
    store = cascade.open_store(s)
    assert store.count() == 0          # the CSV was NOT loaded
    store.close()
