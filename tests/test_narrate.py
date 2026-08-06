"""Phase 5 — «Evolução da conversa» (ADR-054): the watermark, the citation check, and absence.

Two things make this pass honest rather than decorative. The gate is a CONTENT watermark, so a
re-triage that changes what a thread means re-narrates it even though no message arrived. And every
beat must cite a message that was actually sent, with the date attached server-side from the real
row — a narrative that renders a date the model invented looks exactly like a correct one.
"""

import json
from email.message import EmailMessage
from types import SimpleNamespace

import pytest

from email2data import narrate
from email2data.envelope import parse_eml
from email2data.identity import safe_filename

BODY_SECRET = "SEGREDO-CORPO-pediram-tres-replicas"


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
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "narrative_playbook.md").write_text("NARRATIVE PLAYBOOK", encoding="utf-8")
    (tmp_path / "out").mkdir()
    (tmp_path / "corpus").mkdir()
    settings_path = tmp_path / "config" / "settings.json"
    settings = {"llm": {"provider": "vertex_gemini", "model": "gemini-2.5-flash", "max_retries": 1},
                "__settings_path__": str(settings_path)}
    settings_path.write_text(json.dumps(settings), encoding="utf-8")

    ints = []
    for n in range(3):
        raw = _eml(f"nar{n}", "Réplicas do candeeiro", f"{BODY_SECRET} · mensagem {n}")
        mid = parse_eml(raw)["message_id"]
        (tmp_path / "corpus" / safe_filename(mid)).write_bytes(raw)
        ints.append({"message_id": mid, "thread_root": "root-A",
                     "date": f"2026-07-0{n + 1}T09:00:00+01:00",
                     "direction": "inbound" if n % 2 == 0 else "outbound",
                     "subject": "Réplicas do candeeiro", "purpose": "ESTIMATE_REQUEST_FROM_CLIENT",
                     "speech_act": "ASK", "counterparty": "CLIENT", "entities": "{}"})
    # A lone message on its own thread: no evolution to describe, so never narrated.
    raw = _eml("solo", "Olá", "uma só mensagem")
    solo = parse_eml(raw)["message_id"]
    (tmp_path / "corpus" / safe_filename(solo)).write_bytes(raw)
    ints.append({"message_id": solo, "thread_root": "root-SOLO", "date": "2026-07-09T09:00:00+01:00",
                 "direction": "inbound", "subject": "Olá", "purpose": "OTHER",
                 "speech_act": "INFORM", "counterparty": "CLIENT", "entities": "{}"})

    return SimpleNamespace(base=tmp_path, settings=settings, ints=ints, mids=[i["message_id"] for i in ints],
                           out=tmp_path / "out", sidecar=tmp_path / "out" / "narratives.jsonl",
                           audit=tmp_path / "out" / "audit.jsonl")


def _answer(payload, seen=None):
    def fake(client, cfg, system, user, **kw):
        if seen is not None:
            seen.append(user)
        return json.loads(json.dumps(payload))
    return fake


GOOD = {"steps": [{"message_id": "m1", "text": "O cliente pediu três réplicas do candeeiro."},
                  {"message_id": "m2", "text": "Enviámos a proposta."}],
        "state": "À espera da confirmação do cliente."}


# ── who gets narrated ────────────────────────────────────────────────────────────────────────────

def test_a_single_message_thread_is_never_narrated(proj, monkeypatch):
    """It has no evolution; describing one would be inventing a story about a message."""
    monkeypatch.setattr(narrate.llm, "call", _answer(GOOD))
    counts = narrate.rebuild_narratives(proj.settings, client=object(), interactions=proj.ints)
    rows = [json.loads(x) for x in proj.sidecar.read_text(encoding="utf-8").splitlines() if x]
    assert counts["narrated"] == 1
    assert [r["thread_root"] for r in rows] == ["root-A"]


def test_the_steps_carry_the_real_message_ids_and_server_supplied_dates(proj, monkeypatch):
    monkeypatch.setattr(narrate.llm, "call", _answer(GOOD))
    narrate.rebuild_narratives(proj.settings, client=object(), interactions=proj.ints)
    row = json.loads(proj.sidecar.read_text(encoding="utf-8").splitlines()[0])
    assert [s["message_id"] for s in row["steps"]] == proj.mids[:2]
    assert [s["date"] for s in row["steps"]] == ["2026-07-01", "2026-07-02"]
    assert row["state"] == "À espera da confirmação do cliente."


def test_a_step_citing_a_message_that_was_never_sent_is_discarded(proj, monkeypatch):
    """The ordinal check is what makes a citation exact — a fabricated one cannot survive it, and a
    beat nobody can trace back to a message is exactly what the zero-hallucination rule forbids."""
    monkeypatch.setattr(narrate.llm, "call", _answer({
        "steps": [{"message_id": "m1", "text": "Pediram três réplicas."},
                  {"message_id": "m99", "text": "O cliente telefonou a aprovar."},
                  {"message_id": "", "text": "Houve uma reunião."}],
        "state": None}))
    narrate.rebuild_narratives(proj.settings, client=object(), interactions=proj.ints)
    row = json.loads(proj.sidecar.read_text(encoding="utf-8").splitlines()[0])
    assert len(row["steps"]) == 1
    assert row["dropped"] == 2
    assert "telefonou" not in proj.sidecar.read_text(encoding="utf-8")


def test_the_date_is_never_taken_from_the_model(proj, monkeypatch):
    """A rendered date the model invented looks exactly like a correct one."""
    monkeypatch.setattr(narrate.llm, "call", _answer({
        "steps": [{"message_id": "m1", "text": "Pediram réplicas.", "date": "1999-01-01"}],
        "state": None}))
    narrate.rebuild_narratives(proj.settings, client=object(), interactions=proj.ints)
    assert "1999" not in proj.sidecar.read_text(encoding="utf-8")


def test_the_step_count_and_length_are_clamped_not_trusted(proj, monkeypatch):
    monkeypatch.setattr(narrate.llm, "call", _answer({
        "steps": [{"message_id": "m1", "text": f"passo {i}"} for i in range(20)],
        "state": "x" * 900}))
    narrate.rebuild_narratives(proj.settings, client=object(), interactions=proj.ints)
    row = json.loads(proj.sidecar.read_text(encoding="utf-8").splitlines()[0])
    assert len(row["steps"]) == narrate.MAX_STEPS
    assert row["state"] is None


# ── the watermark ────────────────────────────────────────────────────────────────────────────────

def test_a_second_run_over_an_unchanged_thread_spends_nothing(proj, monkeypatch):
    seen = []
    monkeypatch.setattr(narrate.llm, "call", _answer(GOOD, seen))
    narrate.rebuild_narratives(proj.settings, client=object(), interactions=proj.ints)
    assert len(seen) == 1
    seen.clear()
    counts = narrate.rebuild_narratives(proj.settings, client=object(), interactions=proj.ints)
    assert seen == [] and counts["kept"] == 1 and counts["narrated"] == 0


def test_a_new_message_in_the_thread_re_narrates_it(proj, monkeypatch):
    monkeypatch.setattr(narrate.llm, "call", _answer(GOOD))
    narrate.rebuild_narratives(proj.settings, client=object(), interactions=proj.ints)
    grown = proj.ints + [{**proj.ints[0], "message_id": "mid:new@example.pt",
                          "date": "2026-07-08T09:00:00+01:00"}]
    counts = narrate.rebuild_narratives(proj.settings, client=object(), interactions=grown)
    assert counts["narrated"] == 1


def test_a_re_triage_re_narrates_even_though_no_message_arrived(proj, monkeypatch):
    """CrmStore.record is INSERT OR REPLACE on message_id, so speech_act / purpose / counterparty /
    entities can all flip while the count and the last date stay identical. A count-and-date
    watermark would freeze a narrative describing verdicts that no longer exist."""
    monkeypatch.setattr(narrate.llm, "call", _answer(GOOD))
    narrate.rebuild_narratives(proj.settings, client=object(), interactions=proj.ints)

    retriaged = [dict(i) for i in proj.ints]
    retriaged[0]["speech_act"] = "OBLIGATION"
    retriaged[0]["counterparty"] = "SUPPLIER"
    assert len(retriaged) == len(proj.ints)
    assert retriaged[-1]["date"] == proj.ints[-1]["date"]      # nothing a count+date gate would see
    counts = narrate.rebuild_narratives(proj.settings, client=object(), interactions=retriaged)
    assert counts["narrated"] == 1


def test_the_watermark_does_not_move_when_only_the_row_order_changes(proj):
    """crm.thread() orders by a LEXICOGRAPHIC date sort over ISO strings that keep their original UTC
    offset, so row order is not a stable input. A watermark that moved with it would re-bill every
    thread on every sync."""
    rows = [i for i in proj.ints if i["thread_root"] == "root-A"]
    assert narrate.watermark(rows) == narrate.watermark(list(reversed(rows)))


def test_only_forces_a_rebuild_of_the_named_thread(proj, monkeypatch):
    monkeypatch.setattr(narrate.llm, "call", _answer(GOOD))
    narrate.rebuild_narratives(proj.settings, client=object(), interactions=proj.ints)
    seen = []
    monkeypatch.setattr(narrate.llm, "call", _answer(GOOD, seen))
    counts = narrate.rebuild_narratives(proj.settings, client=object(), interactions=proj.ints,
                                        only={"root-A"})
    assert len(seen) == 1 and counts["narrated"] == 1


# ── failure, privacy, degradation ────────────────────────────────────────────────────────────────

def test_an_llm_failure_is_recorded_and_leaks_nothing(proj, monkeypatch):
    def boom(client, cfg, system, user, **kw):
        raise RuntimeError(f"modelo recusou: {BODY_SECRET}")
    monkeypatch.setattr(narrate.llm, "call", boom)
    counts = narrate.rebuild_narratives(proj.settings, client=object(), interactions=proj.ints)
    assert counts["failed"] == 1

    written = proj.audit.read_text(encoding="utf-8")
    assert "SEGREDO-CORPO" not in written and "cliente@example.pt" not in written
    fails = [json.loads(x) for x in written.splitlines() if x]
    fails = [f for f in fails if f["event"] == "narrate_failed"]
    assert len(fails) == 1 and fails[0]["meta"]["error"] == "RuntimeError"
    assert set(fails[0]["meta"]) == {"thread_root", "error", "messages"}


def test_a_failed_thread_is_retried_next_run_because_its_watermark_never_matched(proj, monkeypatch):
    """Unlike the locate pass, whose gate is presence of a row, this gate is the watermark — and a
    failed row still records the CURRENT watermark, so a transient outage must not freeze the thread
    forever. It does not: the row is written with the watermark, so this pins the behaviour that
    actually ships rather than the one that sounds right."""
    def boom(client, cfg, system, user, **kw):
        raise RuntimeError("transient")
    monkeypatch.setattr(narrate.llm, "call", boom)
    narrate.rebuild_narratives(proj.settings, client=object(), interactions=proj.ints)
    row = json.loads(proj.sidecar.read_text(encoding="utf-8").splitlines()[0])
    assert row["error"] == "RuntimeError" and row["watermark"]

    seen = []
    monkeypatch.setattr(narrate.llm, "call", _answer(GOOD, seen))
    narrate.rebuild_narratives(proj.settings, client=object(), interactions=proj.ints, only={"root-A"})
    assert len(seen) == 1                              # --only is the documented way back


def test_the_prompt_uses_ordinals_and_the_frozen_body_cleaner(proj, monkeypatch):
    """The signature-keeping entry point is opt-in for /api/thread only; every LLM prompt must go
    through the frozen deleting one (ADR-047 / Phase 2's caller decision).

    Asserted against the MODULE NAMESPACE, not against the file text: the first cut of this grepped
    the source for the opt-in function's name and fired on the comment that explains why it is not
    used — the same way an explanatory comment tripped two guards in Phase 3.
    """
    seen = []
    monkeypatch.setattr(narrate.llm, "call", _answer(GOOD, seen))
    narrate.rebuild_narratives(proj.settings, client=object(), interactions=proj.ints)
    user = seen[0]
    assert "[m1]" in user and "[m2]" in user and "[m3]" in user
    assert proj.mids[0] not in user                    # real ids never reach the model

    from email2data import envelope
    assert narrate.clean_email_body is envelope.clean_email_body
    assert not any(n.endswith("_parts") for n in vars(narrate))


def test_the_human_decisions_reach_the_prompt_when_supplied(proj, monkeypatch):
    seen = []
    monkeypatch.setattr(narrate.llm, "call", _answer(GOOD, seen))
    narrate.rebuild_narratives(proj.settings, client=object(), interactions=proj.ints,
                               decisions_for=lambda root: ["dono da conversa: Rita"])
    assert "dono da conversa: Rita" in seen[0]


def test_the_pass_is_a_no_op_without_an_llm_client(proj, monkeypatch):
    def no_client(_settings):
        raise RuntimeError("no ADC")
    monkeypatch.setattr(narrate.classifier, "make_client", no_client)
    msgs = []
    counts = narrate.rebuild_narratives(proj.settings, interactions=proj.ints, log=msgs.append)
    assert counts["narrated"] == 0
    assert any("no LLM client" in m for m in msgs)


def test_a_malformed_line_is_skipped_rather_than_crashing_the_app(proj):
    proj.sidecar.write_text('{"thread_root":"a","steps":[]}\nNOT JSON\n{"steps":[]}\n'
                            '{"thread_root":"b","steps":[]}\n', encoding="utf-8")
    assert set(narrate.load_narratives(proj.out)) == {"a", "b"}


def test_a_missing_sidecar_is_an_empty_map_not_an_error(tmp_path):
    assert narrate.load_narratives(tmp_path) == {}


def test_with_no_crm_db_the_pass_returns_zero_instead_of_raising(proj):
    counts = narrate.rebuild_narratives(proj.settings, client=object())
    assert counts == {"narrated": 0, "kept": 0, "steps": 0, "failed": 0, "total": 0}
