"""Phase 4 — the locate pass (ADR-054): validation, the incremental gate, and the privacy rule.

The pass exists to reach the values Phase 3's deterministic search CANNOT: measured on the live
corpus, 440 of 790 ledger rows are dark and 431 of those are absent from the email text in any form.
Everything here guards the two ways that value can be destroyed — by storing a sentence the email
does not contain, and by re-billing the model for work already done.
"""

import json
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

import pytest

from email2data import locate
from email2data.envelope import parse_eml

BODY_SECRET = "SEGREDO-CORPO-o-cliente-pediu-tres-replicas-do-candeeiro"
SUBJECT_SECRET = "SEGREDO-ASSUNTO-orcamento-candeeiro"

BODY = (
    "Boa tarde,\n\n"
    f"{BODY_SECRET}.\n"
    'Precisamos de construção de cenografia "Órfãos da \nLua" para a peça.\n'
    "Agradecia que enviassem a vossa melhor proposta até dia 30 de agosto.\n"
    "O valor combinado foi de 1.250,00 EUR.\n\n"
    "Cumprimentos,\nDaniel Genaro\n"
)


def _eml(local: str, subject: str, body: str) -> bytes:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = "cliente@example.pt"
    msg["To"] = "geral@lindoservico.pt"
    msg["Message-ID"] = f"<{local}@example.pt>"
    msg["Date"] = "Wed, 27 May 2026 09:00:00 +0100"
    msg.set_content(body)
    return msg.as_bytes()


@pytest.fixture
def proj(tmp_path):
    """config/ + corpus/ (2 entity-bearing emails) + out/results.jsonl, with real .eml filenames."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "locate_playbook.md").write_text("LOCATE PLAYBOOK", encoding="utf-8")
    (tmp_path / "out").mkdir()
    (tmp_path / "corpus").mkdir()

    settings_path = tmp_path / "config" / "settings.json"
    settings = {"llm": {"provider": "vertex_gemini", "model": "gemini-2.5-flash", "max_retries": 1},
                "__settings_path__": str(settings_path)}
    settings_path.write_text(json.dumps(settings), encoding="utf-8")

    from email2data.identity import safe_filename
    results = []
    for n in range(2):
        raw = _eml(f"loc{n}", SUBJECT_SECRET if n == 0 else f"Pedido {n}", BODY)
        mid = parse_eml(raw)["message_id"]
        (tmp_path / "corpus" / safe_filename(mid)).write_bytes(raw)
        results.append({"message_id": mid, "subject": f"Pedido {n}",
                        "entities": {"deadline": "2026-08-30",
                                     "action_requested": "enviar proposta",
                                     "money": "1250€"}})
    # A row with no entities at all: never sent to the model, never given a sidecar row.
    results.append({"message_id": "mid:barren@example.pt", "subject": "oi", "entities": {}})
    (tmp_path / "out" / "results.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in results), encoding="utf-8")

    return SimpleNamespace(base=tmp_path, settings=settings, results=results,
                           out=tmp_path / "out", sidecar=tmp_path / "out" / "evidence.jsonl",
                           audit=tmp_path / "out" / "audit.jsonl")


def _answer(payload, seen=None):
    def fake(client, cfg, system, user, **kw):
        if seen is not None:
            seen.append(user)
        return dict(payload)
    return fake


def _boom(msg="Vertex 400 INVALID_ARGUMENT"):
    def fake(client, cfg, system, user, **kw):
        raise RuntimeError(msg)
    return fake


# ── the whitespace-tolerant matcher ──────────────────────────────────────────────────────────────

def test_a_hard_wrapped_sentence_is_found_and_its_real_span_recovered():
    """The spike's three unrecoverable misses were all hard wraps. The span must come back pointing
    at the EMAIL's characters, newline included — not at the whitespace-collapsed copy."""
    spans = locate.find_spans(BODY, 'construção de cenografia "Órfãos da Lua"')
    assert len(spans) == 1
    s, e = spans[0]
    assert BODY[s:e] == 'construção de cenografia "Órfãos da \nLua"'
    assert "\n" in BODY[s:e]                       # the recovered span is the real, wrapped text


def test_a_quote_that_is_not_in_the_body_yields_no_span():
    assert locate.find_spans(BODY, "isto nunca foi escrito") == []


def test_the_matcher_is_case_sensitive_because_the_model_was_told_to_copy_exactly():
    """Folding here would let a quote the model actually REWROTE pass validation as if it had been
    copied. The client is fold-tolerant; the server, which decides what gets stored, is not."""
    assert locate.find_spans(BODY, "BOA TARDE") == []
    assert locate.find_spans(BODY, "Boa tarde") != []


# ── the validation stack, one test per rule ──────────────────────────────────────────────────────

def test_an_echo_of_the_value_is_rejected():
    """quote == value carries no evidence beyond the value, and Phase 3 already paints the value
    wherever it is present — so this can only ever discard a duplicate."""
    assert locate.validate_quote("money", "1.250,00 EUR", "1.250,00 EUR", BODY) == (None, "echo")
    # …including when it differs only by whitespace, which is how the model usually echoes.
    assert locate.validate_quote("money", "1.250,00 EUR", " 1.250,00   EUR ", BODY)[1] == "echo"


def test_a_fabricated_quote_is_rejected_rather_than_stored():
    assert locate.validate_quote(
        "action_requested", "enviar proposta",
        "Por favor enviem a proposta com urgência absoluta.", BODY) == (None, "not_in_body")


def test_a_quote_appearing_twice_is_rejected():
    body = "Confirmo.\nmais texto\nConfirmo."
    assert locate.validate_quote("action_requested", "confirmar", "Confirmo.", body)[1] == "not_unique"


def test_the_value_in_quote_gate_is_per_field_not_global():
    """Applied globally this gate would ship a feature that never once highlights a prazo: the
    deadline value is ISO and its sentence never contains it (0/4 in the spike)."""
    sentence = "Agradecia que enviassem a vossa melhor proposta até dia 30 de agosto."
    assert locate.validate_quote("deadline", "2026-08-30", sentence, BODY)[0] == sentence
    # money is gated ON — the same sentence does not contain the amount, so it cannot justify it
    assert locate.validate_quote("money", "1250€", sentence, BODY)[1] == "value_not_in_quote"


def test_an_absent_or_runaway_answer_is_rejected():
    assert locate.validate_quote("money", "1250€", None, BODY) == (None, "absent")
    assert locate.validate_quote("money", "1250€", "   ", BODY) == (None, "absent")
    assert locate.validate_quote("money", "1250€", "x" * 500, BODY) == (None, "too_long")


def test_the_stored_quote_is_the_emails_own_text_never_the_models():
    """The model is asked to copy character by character and mostly does — but 'mostly' is not a
    contract. Storing the matched span means nothing downstream can paint a character the sender
    did not write."""
    typed = "Precisamos de construção de cenografia  \"Órfãos da Lua\"  para a peça."
    got, reason = locate.validate_quote("product_or_service", "cenografia", typed, BODY)
    assert reason == "ok"
    assert got != typed
    assert got in BODY


# ── the pipeline ─────────────────────────────────────────────────────────────────────────────────

def test_a_run_writes_one_row_per_entity_bearing_message_and_none_for_the_barren_one(proj, monkeypatch):
    monkeypatch.setattr(locate.llm, "call", _answer({
        "deadline": "Agradecia que enviassem a vossa melhor proposta até dia 30 de agosto.",
        "action_requested": "Agradecia que enviassem a vossa melhor proposta",
        "money": "O valor combinado foi de 1.250,00 EUR.",
    }))
    counts = locate.rebuild_evidence(proj.settings, client=object())
    rows = [json.loads(x) for x in proj.sidecar.read_text(encoding="utf-8").splitlines() if x]
    assert counts["located"] == 2 and counts["total"] == 2
    assert {r["message_id"] for r in rows} == {r["message_id"] for r in proj.results[:2]}
    assert all(r["quotes"]["deadline"].startswith("Agradecia") for r in rows)


def test_a_re_triaged_message_is_located_once_from_its_freshest_line(proj, monkeypatch):
    """`results.jsonl` is APPEND-only, so a re-triage leaves two lines for one message. Iterating the
    raw lines bills the LLM twice and writes two rows whose winner `load_evidence` then picks by file
    order. Found in production, not by a test: the first full backfill reported 806 messages and
    1647 quotes while the sidecar held 763 distinct ids and 1543 quotes — 43 messages paid for twice.
    """
    stale = dict(proj.results[0])
    stale["entities"] = {"money": "999€"}               # the OLD triage, written first
    fresh = proj.results[0]                             # the re-triage, written last, must win
    (proj.out / "results.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in [stale] + proj.results),
        encoding="utf-8")

    calls = []
    monkeypatch.setattr(locate.llm, "call", _answer({
        "deadline": "Agradecia que enviassem a vossa melhor proposta até dia 30 de agosto.",
    }, calls))
    counts = locate.rebuild_evidence(proj.settings, client=object())

    rows = [json.loads(x) for x in proj.sidecar.read_text(encoding="utf-8").splitlines() if x]
    ids = [r["message_id"] for r in rows]
    assert len(calls) == 2 and counts["located"] == 2   # two messages, not three
    assert len(ids) == len(set(ids)) == 2               # one row per message, no duplicate
    # …and it was the FRESH line that was located: `deadline` is only in the re-triage's entities.
    row = next(r for r in rows if r["message_id"] == fresh["message_id"])
    assert row["quotes"]["deadline"].startswith("Agradecia")


def test_a_rejection_is_recorded_so_the_gate_cannot_re_bill_it_forever(proj, monkeypatch):
    """Item 4.5 said 'anything failing validation stores nothing'. Taken literally that collides with
    the incremental gate, which keys on the PRESENCE of a row: a message with no row is
    indistinguishable from one never attempted, so every sync would pay for it again."""
    calls = []
    monkeypatch.setattr(locate.llm, "call", _answer({"deadline": "2026-08-30"}, calls))
    locate.rebuild_evidence(proj.settings, client=object())
    assert len(calls) == 2
    rows = [json.loads(x) for x in proj.sidecar.read_text(encoding="utf-8").splitlines() if x]
    assert all(r["quotes"] == {} for r in rows)
    assert all(r["rejected"]["deadline"] == "echo" for r in rows)

    calls.clear()
    locate.rebuild_evidence(proj.settings, client=object())
    assert calls == []                              # the rejection is remembered, not re-purchased


def test_the_incremental_gate_keeps_existing_rows_and_only_rebuilds_the_named_one(proj, monkeypatch):
    monkeypatch.setattr(locate.llm, "call", _answer({"deadline": "até dia 30 de agosto"}))
    locate.rebuild_evidence(proj.settings, client=object())
    target = proj.results[0]["message_id"]

    seen = []
    monkeypatch.setattr(locate.llm, "call", _answer({"deadline": "proposta até dia 30 de agosto"}, seen))
    counts = locate.rebuild_evidence(proj.settings, client=object(), only={target})
    assert counts["located"] == 1 and counts["kept"] == 1
    assert len(seen) == 1
    rows = {r["message_id"]: r for r in
            (json.loads(x) for x in proj.sidecar.read_text(encoding="utf-8").splitlines() if x)}
    assert rows[target]["quotes"]["deadline"].startswith("proposta")
    assert rows[proj.results[1]["message_id"]]["quotes"]["deadline"].startswith("até")


def test_a_paid_for_row_survives_a_message_leaving_the_source_list(proj, monkeypatch):
    """Deliberately UNLIKE rebuild_jobspecs, which rebuilds its list purely from results.jsonl and
    silently drops anything no longer there. Every row here cost an LLM call."""
    monkeypatch.setattr(locate.llm, "call", _answer({"deadline": "até dia 30 de agosto"}))
    locate.rebuild_evidence(proj.settings, client=object())
    gone = proj.results[1]["message_id"]

    kept_rows = [r for r in proj.results if r["message_id"] != gone]
    (proj.out / "results.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in kept_rows), encoding="utf-8")
    locate.rebuild_evidence(proj.settings, client=object())

    rows = [json.loads(x) for x in proj.sidecar.read_text(encoding="utf-8").splitlines() if x]
    assert gone in {r["message_id"] for r in rows}


def test_an_llm_failure_is_recorded_on_the_row_and_never_raises(proj, monkeypatch):
    monkeypatch.setattr(locate.llm, "call", _boom())
    counts = locate.rebuild_evidence(proj.settings, client=object())
    assert counts["failed"] == 2
    rows = [json.loads(x) for x in proj.sidecar.read_text(encoding="utf-8").splitlines() if x]
    assert all(r["error"] == "RuntimeError" for r in rows)


def test_the_failure_audit_leaks_no_body_no_subject_and_no_address(proj, monkeypatch):
    """ADR-054: out/audit.jsonl is served by no route, sits outside the ADR-045 visibility gate, is
    not backed up and is never pruned. A quote written there is permanent and ungoverned."""
    monkeypatch.setattr(locate.llm, "call", _boom(f"modelo recusou: {BODY_SECRET}"))
    locate.rebuild_evidence(proj.settings, client=object())

    written = proj.audit.read_text(encoding="utf-8")
    assert "SEGREDO-CORPO" not in written and "SEGREDO-ASSUNTO" not in written
    assert "cliente@example.pt" not in written
    recs = [json.loads(x) for x in written.splitlines() if x]
    fails = [r for r in recs if r["event"] == "locate_failed"]
    assert len(fails) == 2
    assert fails[0]["meta"]["error"] == "RuntimeError"        # the TYPE, never the message
    assert set(fails[0]["meta"]) == {"message_id", "error", "keys"}


def test_no_quote_text_is_ever_written_for_a_rejected_key(proj, monkeypatch):
    """A reason code explains the decision and carries no body text; the rejected sentence does not
    reach the disk at all."""
    fabricated = f"O cliente disse {BODY_SECRET} numa reunião."
    monkeypatch.setattr(locate.llm, "call", _answer({"action_requested": fabricated}))
    locate.rebuild_evidence(proj.settings, client=object())
    written = proj.sidecar.read_text(encoding="utf-8")
    assert "numa reunião" not in written
    rows = [json.loads(x) for x in written.splitlines() if x]
    assert all(r["rejected"]["action_requested"] == "not_in_body" for r in rows)


def test_the_pass_is_a_no_op_without_an_llm_client_instead_of_breaking_the_sync(proj, monkeypatch):
    def no_client(_settings):
        raise RuntimeError("no ADC")
    monkeypatch.setattr(locate.classifier, "make_client", no_client)
    msgs = []
    counts = locate.rebuild_evidence(proj.settings, log=msgs.append)
    assert counts["located"] == 0 and counts["failed"] == 0
    assert any("no LLM client" in m for m in msgs)


def test_the_atomic_write_uses_its_own_suffix_and_leaves_nothing_behind(proj, monkeypatch):
    """NOT '.building': tests/test_specbuild.py asserts no *.building file survives a jobspec
    rebuild, and on a sync these two passes run back to back."""
    replaced = []
    real = locate.os.replace

    def spy(src, dst):
        replaced.append((str(src), str(dst)))
        return real(src, dst)
    monkeypatch.setattr(locate.os, "replace", spy)
    monkeypatch.setattr(locate.llm, "call", _answer({"deadline": "até dia 30 de agosto"}))

    locate.rebuild_evidence(proj.settings, client=object())
    assert len(replaced) == 1
    assert replaced[0][0].endswith(".jsonl.writing")
    assert replaced[0][1] == str(proj.sidecar)
    assert list(proj.out.glob("*.building")) == [] and list(proj.out.glob("*.writing")) == []


def test_a_crash_mid_write_cannot_truncate_an_existing_sidecar(proj, monkeypatch):
    monkeypatch.setattr(locate.llm, "call", _answer({"deadline": "até dia 30 de agosto"}))
    locate.rebuild_evidence(proj.settings, client=object())
    good = proj.sidecar.read_bytes()
    assert len(good) > 0

    real_write_text = Path.write_text

    def half_then_die(self, data, *args, **kwargs):
        real_write_text(self, data[: len(data) // 2], *args, **kwargs)
        raise OSError("no space left on device")
    monkeypatch.setattr(Path, "write_text", half_then_die)
    with pytest.raises(OSError):
        locate.rebuild_evidence(proj.settings, client=object(), only={proj.results[0]["message_id"]})
    assert proj.sidecar.read_bytes() == good


def test_a_malformed_line_is_skipped_rather_than_crashing_the_app(proj):
    """The three jobspecs loaders raise on a bad line, and one of them runs at create_app time —
    before the lifespan — so an unparseable sidecar makes the app unconstructable and the container
    crash-loops. This store is written by an LLM pass and carries free text."""
    proj.sidecar.write_text('{"message_id":"a","quotes":{}}\nNOT JSON\n{"no_id":1}\n'
                            '{"message_id":"b","quotes":{"money":"x"}}\n', encoding="utf-8")
    got = locate.load_evidence(proj.out)
    assert set(got) == {"a", "b"}


def test_a_missing_sidecar_is_an_empty_map_not_an_error(tmp_path):
    assert locate.load_evidence(tmp_path) == {}


def test_the_prompt_carries_the_values_and_the_body_and_asks_about_nothing_else(proj, monkeypatch):
    seen = []
    monkeypatch.setattr(locate.llm, "call", _answer({}, seen))
    locate.rebuild_evidence(proj.settings, client=object())
    user = seen[0]
    assert "2026-08-30" in user and "enviar proposta" in user
    assert BODY_SECRET in user                       # the model must see the body to quote from it
    # It is asked to LOCATE, never to reclassify: no verdict vocabulary reaches the prompt.
    assert "counterparty" not in user and "purpose" not in user and "priority" not in user


def test_the_locate_call_never_touches_the_triage_schema_or_playbook():
    """A separate call is the whole reason no verdict churns and no EXTRACTOR_VERSION is owed."""
    from email2data import schema
    assert locate.GEMINI_LOCATE_SCHEMA is not schema.GEMINI_TRIAGE_SCHEMA
    assert set(locate.GEMINI_LOCATE_SCHEMA["properties"]) == set(locate.LOCATE_KEYS)
    assert "counterparty" not in locate.GEMINI_LOCATE_SCHEMA["properties"]
    src = Path("src/email2data/locate.py").read_text(encoding="utf-8")
    assert "triage_playbook" not in src and "TRIAGE_SCHEMA" not in src


def test_the_python_validator_and_the_shipped_js_painter_agree_on_the_same_span():
    """The one drift that would ship silently. `locate.find_spans` decides what gets STORED and
    `evLocateQuote` decides what gets PAINTED — two implementations of one whitespace-tolerant rule,
    in two languages, and a disagreement means a sentence that validated on the server finds nothing
    on screen (or worse, paints a span shifted by one character). Executed, not compared by eye."""
    import json as _json
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available — the shipped JS cannot be executed")
    from email2data import cockpit_ui
    kit = cockpit_ui._SHELL_UTILS
    js = kit[kit.index("const _EV_AMOUNT="):kit.index("function evHighlight(")]

    cases = [
        (BODY, 'construção de cenografia "Órfãos da Lua"'),
        (BODY, "Agradecia que enviassem a vossa melhor proposta até dia 30 de agosto."),
        (BODY, "O valor combinado foi de 1.250,00 EUR."),
        (BODY, "Cumprimentos,\nDaniel Genaro"),
        (BODY, "Boa tarde"),
        ("a  b\n\nc", "a b c"),
        ("  leading   space  ", "leading space"),
        (BODY, "não está aqui"),
    ]
    prog = (js + "\nconst cases=" + _json.dumps(cases, ensure_ascii=False) + ";"
            "console.log(JSON.stringify(cases.map(c=>evLocateQuote(c[0],c[1]).map(m=>[m.s,m.e]))));")
    r = subprocess.run([node, "-e", prog], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    from_js = [[tuple(x) for x in spans] for spans in _json.loads(r.stdout)]
    from_py = [locate.find_spans(t, q) for t, q in cases]
    assert from_js == from_py
    # …and the spans are not all empty, or the agreement would be vacuous.
    assert sum(len(s) for s in from_py) >= 6


def test_max_tokens_is_raised_for_this_call_without_mutating_shared_settings(proj, monkeypatch):
    """with_tier returns the SHARED settings dict on the no-op path, so raising max_tokens in place
    would repoint every later LLM call in the run. And 1024 truncates a seven-quote answer mid-JSON,
    which llm.call cannot tell from a transient failure — it would retry and bill all five."""
    seen_cfg = {}

    def fake(client, cfg, system, user, **kw):
        seen_cfg.update(cfg)
        return {}
    monkeypatch.setattr(locate.llm, "call", fake)
    proj.settings["llm"]["max_tokens"] = 1024
    locate.rebuild_evidence(proj.settings, client=object())
    assert seen_cfg["max_tokens"] == locate.MAX_TOKENS > 1024
    assert proj.settings["llm"]["max_tokens"] == 1024
