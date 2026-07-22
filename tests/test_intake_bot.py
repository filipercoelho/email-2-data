"""M2 — the conversational-intake Telegram worker handler. Telegram is faked; this pins the
persist-then-scrub ordering (ADR-020), the default-deny allowlist (ADR-019 §6), no-auto-apply (R9),
and the offset / skip-backlog plumbing."""

from __future__ import annotations

import sqlite3
import threading

import pytest

from email2data import project as p
from email2data.captures import CaptureStore
from email2data.intake import (
    IntakeBot, TransientPersistError, _initial_offset, _read_offset, _write_offset, poll_forever)
from email2data.workspace import SCHEMA


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


class FakeClient:
    """Records every Telegram call so a test can assert what was sent/scrubbed and in what order."""

    def __init__(self, *, file_bytes: bytes = b"img", fail_delete: bool = False) -> None:
        self.sent: list = []
        self.edited: list = []
        self.deleted: list = []
        self.answered: list = []
        self.order: list = []
        self._next = 1000
        self._file_bytes = file_bytes
        self._fail_delete = fail_delete

    def send_message(self, chat_id, text, **kw):
        self.order.append("send")
        self.sent.append((chat_id, text, kw.get("reply_markup")))
        self._next += 1
        return self._next

    def edit_message_text(self, chat_id, message_id, text, **kw):
        self.order.append("edit")
        self.edited.append(
            (chat_id, message_id, text, kw.get("reply_markup"), kw.get("parse_mode", "Markdown")))

    def delete_message(self, chat_id, message_id):
        self.order.append("delete")
        if self._fail_delete:
            raise RuntimeError("delete failed")
        self.deleted.append((chat_id, message_id))

    def get_file(self, file_id):
        self.order.append("get_file")
        if "voice" in file_id:
            ext = "oga"
        elif "doc" in file_id:
            ext = "pdf"
        else:
            ext = "jpg"
        return {"file_path": f"files/{file_id}.{ext}"}

    def set_my_commands(self, commands):
        self.order.append("set_commands")
        self.commands = commands

    def download_file(self, file_path, **kw):
        self.order.append("download")
        return self._file_bytes

    def answer_callback_query(self, cb_id, **kw):
        self.answered.append(cb_id)


def _bot(conn, client, captures_dir, **kw):
    captures = CaptureStore(conn)
    projects = p.ProjectStore(conn)
    bot = IntakeBot(client=client, captures=captures, projects=projects,
                    captures_dir=captures_dir, **kw)
    return bot, captures, projects


def _msg(*, chat_id=10, sender_id=7, message_id=100, text="", photo=False, caption="", voice=False,
         document=None):
    m = {"message_id": message_id, "chat": {"id": chat_id, "type": "private"},
         "from": {"id": sender_id, "first_name": "Diogo"}}
    if text:
        m["text"] = text
    if caption:
        m["caption"] = caption
    if photo:
        m["photo"] = [{"file_id": "small"}, {"file_id": "big"}]
    if voice:
        m["voice"] = {"file_id": "voicebig", "mime_type": "audio/ogg", "duration": 4}
    if document is not None:
        # document=True -> a plain PDF; document="name.ext" -> that file_name (attacker-controlled).
        name = "desenho.pdf" if document is True else document
        m["document"] = {"file_id": "docbig", "mime_type": "application/pdf"}
        if name:
            m["document"]["file_name"] = name
    return m


def test_unauthorized_sender_is_rejected_and_nothing_persisted(tmp_path):
    client = FakeClient()
    bot, captures, _ = _bot(_conn(), client, tmp_path)
    bot.handle({"update_id": 1, "message": _msg(text="ola")})
    assert client.sent and "autorizado" in client.sent[0][1]   # F1 rejection sent
    assert client.deleted == []                                # nothing scrubbed
    assert captures.list_pending() == []                       # nothing persisted


def test_text_capture_persists_then_scrubs_then_offers_pick_list(tmp_path):
    client = FakeClient()
    bot, captures, projects = _bot(_conn(), client, tmp_path)
    captures.allow(7, display_name="Diogo", roster_owner="Diogo Costa")
    pid = projects.create("Estante Sousa", stage="LEAD")
    bot.handle({"update_id": 1, "message": _msg(text="prazo 30 jun", message_id=100)})

    cap = captures.get("c-10-100")
    assert cap is not None and cap["raw_text"] == "prazo 30 jun"
    assert cap["asserted_by"] == "Diogo Costa"              # roster_owner -> provenance
    assert client.deleted == [(10, 100)] and cap["telegram_scrubbed_at"] is not None
    assert client.order.index("delete") < client.order.index("edit")  # scrub before the pick-list
    pick = client.edited[-1]
    assert "projeto pertence" in pick[2]
    assert any(pid in b[0]["callback_data"] for b in pick[3]["inline_keyboard"])


def test_persist_failure_never_scrubs(tmp_path, monkeypatch):
    client = FakeClient()
    bot, captures, projects = _bot(_conn(), client, tmp_path)
    captures.allow(7)
    projects.create("X", stage="LEAD")

    def _boom(**kw):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(captures, "add", _boom)
    # the handler re-raises so the loop holds the offset + retries — but FIRST it must have told the
    # user and, critically, NOT scrubbed Telegram (persist-then-scrub: never scrub on a failed persist).
    with pytest.raises(TransientPersistError):
        bot.handle({"update_id": 1, "message": _msg(text="hi")})
    assert client.deleted == []                                # never scrub on a failed persist
    assert any("Erro" in e[2] for e in client.edited)          # the user is told it failed


def test_scrub_failure_is_nonfatal_and_capture_kept(tmp_path):
    client = FakeClient(fail_delete=True)
    bot, captures, projects = _bot(_conn(), client, tmp_path)
    captures.allow(7)
    projects.create("X", stage="LEAD")
    bot.handle({"update_id": 1, "message": _msg(text="hi", message_id=100)})
    cap = captures.get("c-10-100")
    assert cap is not None and cap["telegram_scrubbed_at"] is None  # delete failed, capture safe


def test_photo_capture_downloads_persists_and_scrubs(tmp_path):
    client = FakeClient(file_bytes=b"JPEGDATA")
    bot, captures, projects = _bot(_conn(), client, tmp_path)
    captures.allow(7)
    projects.create("X", stage="LEAD")
    bot.handle({"update_id": 1, "message": _msg(photo=True, message_id=100)})
    cap = captures.get("c-10-100")
    assert cap is not None and cap["content_class"] == "artifact"
    assert cap["media_paths"] == ["c-10-100/photo.jpg"]
    assert (tmp_path / "c-10-100" / "photo.jpg").read_bytes() == b"JPEGDATA"
    assert client.deleted == [(10, 100)]
    assert "get_file" in client.order and "download" in client.order


def test_voice_capture_persists_then_scrubs_then_transcribes(tmp_path, monkeypatch):
    # Increment 1: a voice memo is downloaded + persisted (transcript NULL) -> Telegram scrubbed ->
    # transcribed best-effort (LLM mocked). The transcription runs strictly AFTER the scrub.
    import email2data.intake as intake_mod
    client = FakeClient(file_bytes=b"OGGDATA")
    bot, captures, projects = _bot(_conn(), client, tmp_path, llm_client=object(),
                                   llm_cfg={"provider": "vertex_gemini", "model": "x"})
    captures.allow(7)
    projects.create("X", stage="LEAD")

    seen = {}

    def fake_call(cl, cfg, system, user, **kw):
        imgs = kw.get("images")
        if imgs:                                            # the transcription call (carries the audio)
            seen["deleted_before"] = list(client.deleted)   # was the scrub already done?
            seen["audio_mime"] = imgs[0].get("mime")
            seen["text_mode"] = kw.get("text")
            return "o cliente quer mais duas estantes"
        return {}                                           # the (no-image) extract_fields call -> no fields
    monkeypatch.setattr(intake_mod.llm, "call", fake_call)

    bot.handle({"update_id": 1, "message": _msg(voice=True, message_id=100)})
    cap = captures.get("c-10-100")
    assert cap is not None and cap["content_class"] == "conversation"
    assert cap["media_paths"] and cap["media_paths"][0].startswith("c-10-100/voice")
    assert (tmp_path / cap["media_paths"][0]).read_bytes() == b"OGGDATA"   # audio persisted (precious)
    assert client.deleted == [(10, 100)]                                  # scrubbed
    assert cap["transcript"] == "o cliente quer mais duas estantes"       # transcribed
    assert seen["deleted_before"] == [(10, 100)]    # transcription ran AFTER the scrub (persist→scrub→transcribe)
    assert seen["audio_mime"] == "audio/ogg" and seen["text_mode"] is True


def test_transcription_failure_keeps_the_capture_intact(tmp_path, monkeypatch):
    # Degradation: the LLM raises -> the capture survives (audio preserved, transcript empty), and the
    # scrub still happened (it is AFTER the durable persist; persist-then-scrub is never broken).
    import email2data.intake as intake_mod

    def boom(*a, **k):
        raise intake_mod.llm.LLMError("vertex down")
    monkeypatch.setattr(intake_mod.llm, "call", boom)
    client = FakeClient(file_bytes=b"OGGDATA")
    bot, captures, projects = _bot(_conn(), client, tmp_path, llm_client=object(),
                                   llm_cfg={"provider": "vertex_gemini", "model": "x"})
    captures.allow(7)
    projects.create("X", stage="LEAD")
    bot.handle({"update_id": 1, "message": _msg(voice=True, message_id=100)})
    cap = captures.get("c-10-100")
    assert cap is not None and cap["transcript"] is None       # transcription failed -> empty, intact
    assert (tmp_path / cap["media_paths"][0]).read_bytes() == b"OGGDATA"   # audio still preserved
    assert client.deleted == [(10, 100)]                       # scrub stood (it is after persist)


def test_voice_without_llm_client_is_stored_not_transcribed(tmp_path, monkeypatch):
    # No LLM wired -> the bot degrades to "stored, not transcribed": no llm.call, capture + audio kept.
    import email2data.intake as intake_mod
    calls = {"n": 0}
    monkeypatch.setattr(intake_mod.llm, "call",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
    client = FakeClient(file_bytes=b"OGGDATA")
    bot, captures, projects = _bot(_conn(), client, tmp_path)   # no llm_client passed
    captures.allow(7)
    projects.create("X", stage="LEAD")
    bot.handle({"update_id": 1, "message": _msg(voice=True, message_id=100)})
    cap = captures.get("c-10-100")
    assert cap is not None and cap["transcript"] is None and cap["media_paths"]
    assert client.deleted == [(10, 100)]
    assert calls["n"] == 0                                      # transcription never attempted


def test_text_capture_extracts_fields_best_effort(tmp_path, monkeypatch):
    # Increment 2: the worker extracts job-spec field VALUES from the text and STORES them (never
    # applies — R9). The extraction is the mocked LLM; the stored fields are coerced/addressed.
    import email2data.intake as intake_mod
    monkeypatch.setattr(intake_mod._infer, "extract_fields", lambda text, client, cfg:
                        {"fields": {"material#0": "inox 304", "deadline": "2026-07-01"}, "confidence": 0.88})
    client = FakeClient()
    bot, captures, projects = _bot(_conn(), client, tmp_path, llm_client=object(),
                                   llm_cfg={"provider": "vertex_gemini", "model": "x"})
    captures.allow(7)
    projects.create("X", stage="LEAD")
    bot.handle({"update_id": 1, "message": _msg(text="inox 304, prazo 1 jul", message_id=100)})
    cap = captures.get("c-10-100")
    assert cap["extracted_fields"] == {"material#0": "inox 304", "deadline": "2026-07-01"}
    assert cap["confidence"] == 0.88
    assert cap["status"] == "stored"        # extracted only — NOT applied/parsed (no auto-apply)


def test_extraction_failure_leaves_capture_without_fields(tmp_path, monkeypatch):
    # Degradation: extraction raises -> the capture survives with NO fields (nothing half-applied).
    import email2data.intake as intake_mod
    def boom(*a, **k):
        raise RuntimeError("model exploded")
    monkeypatch.setattr(intake_mod._infer, "extract_fields", boom)
    client = FakeClient()
    bot, captures, projects = _bot(_conn(), client, tmp_path, llm_client=object(),
                                   llm_cfg={"provider": "vertex_gemini", "model": "x"})
    captures.allow(7)
    projects.create("X", stage="LEAD")
    bot.handle({"update_id": 1, "message": _msg(text="inox 304", message_id=100)})
    cap = captures.get("c-10-100")
    assert cap is not None and cap["extracted_fields"] == {} and cap["confidence"] is None
    assert client.deleted == [(10, 100)]    # the capture still persisted + scrubbed normally


def test_extraction_is_skipped_without_an_llm_client(tmp_path, monkeypatch):
    import email2data.intake as intake_mod
    calls = {"n": 0}
    monkeypatch.setattr(intake_mod._infer, "extract_fields",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or {"fields": {}, "confidence": 0})
    client = FakeClient()
    bot, captures, projects = _bot(_conn(), client, tmp_path)   # no llm_client
    captures.allow(7)
    projects.create("X", stage="LEAD")
    bot.handle({"update_id": 1, "message": _msg(text="inox 304", message_id=100)})
    assert captures.get("c-10-100")["extracted_fields"] == {}
    assert calls["n"] == 0                   # extraction never attempted without a client


def test_pick_list_ranks_the_named_project_first(tmp_path):
    # Deterministic resolve (R2): the capture text reorders the pick-list so the named obra is the
    # FIRST button — beating the default newest-first order. Still buttons; no auto-apply (R9).
    client = FakeClient()
    bot, captures, projects = _bot(_conn(), client, tmp_path)
    captures.allow(7)
    p_acme = projects.create("Placas Acme", stage="LEAD")       # created first -> older
    projects.create("Estante Sousa", stage="LEAD")              # newer -> default first button
    bot.handle({"update_id": 1, "message": _msg(text="a Acme precisa de mais placas", message_id=100)})
    kb = client.edited[-1][3]["inline_keyboard"]
    assert kb[0][0]["callback_data"] == f"pick:c-10-100:{p_acme}"   # ranking beat newest-first


def test_empty_message_is_rejected(tmp_path):
    client = FakeClient()
    bot, captures, _ = _bot(_conn(), client, tmp_path)
    captures.allow(7)
    bot.handle({"update_id": 1, "message": _msg()})            # no text, no photo
    assert any("Envia" in s[1] for s in client.sent)
    assert captures.list_pending() == []


def test_no_active_projects_holds_capture_in_queue(tmp_path):
    client = FakeClient()
    bot, captures, _ = _bot(_conn(), client, tmp_path)
    captures.allow(7)
    bot.handle({"update_id": 1, "message": _msg(text="hi", message_id=100)})
    cap = captures.get("c-10-100")
    assert cap is not None and cap["status"] == "stored"       # held, never dropped (N2)
    assert any("Não há projetos ativos" in e[2] for e in client.edited)


def test_callback_pick_sets_project_and_confirms(tmp_path):
    client = FakeClient()
    bot, captures, projects = _bot(_conn(), client, tmp_path)
    captures.allow(7)
    pid = projects.create("Estante Sousa", stage="LEAD")
    cid, _ = captures.add(telegram_message_id=100, telegram_chat_id=10, raw_text="x")
    bot.handle({"update_id": 2, "callback_query": {
        "id": "cb1", "from": {"id": 7}, "data": f"pick:{cid}:{pid}",
        "message": {"message_id": 500, "chat": {"id": 10}}}})
    assert client.answered == ["cb1"]
    cap = captures.get(cid)
    assert cap["inferred_project_id"] == pid and cap["status"] == "parsed"
    assert any("Associado" in e[2] for e in client.edited)


def test_callback_from_stranger_is_ignored(tmp_path):
    client = FakeClient()
    bot, captures, projects = _bot(_conn(), client, tmp_path)
    captures.allow(7)
    pid = projects.create("X", stage="LEAD")
    cid, _ = captures.add(telegram_message_id=100, telegram_chat_id=10, raw_text="x")
    bot.handle({"update_id": 2, "callback_query": {
        "id": "cb1", "from": {"id": 999}, "data": f"pick:{cid}:{pid}",   # not allowlisted
        "message": {"message_id": 500, "chat": {"id": 10}}}})
    assert captures.get(cid)["status"] == "stored"             # the stranger's tap changed nothing


def test_offset_roundtrip(tmp_path):
    path = tmp_path / "intake_offset.json"
    assert _read_offset(path, "default") is None
    _write_offset(path, "default", 42)
    assert _read_offset(path, "default") == 42
    _write_offset(path, "default", 43)
    assert _read_offset(path, "default") == 43


def test_initial_offset_skips_backlog_on_fresh_bot(tmp_path):
    path = tmp_path / "intake_offset.json"

    class Tail:
        def get_updates(self, offset, *, timeout=25, allowed_updates=None):
            assert offset == -1 and timeout == 0
            return [{"update_id": 77}]
    assert _initial_offset(Tail(), path, "default") == 78      # last_update_id + 1, backlog skipped
    assert _read_offset(path, "default") == 78                 # persisted immediately


def test_initial_offset_resumes_from_persisted(tmp_path):
    path = tmp_path / "intake_offset.json"
    _write_offset(path, "default", 500)

    class Boom:
        def get_updates(self, *a, **k):
            raise AssertionError("must not poll when a persisted offset exists")
    assert _initial_offset(Boom(), path, "default") == 500


def test_poll_forever_handles_a_batch_and_advances_offset(tmp_path):
    conn = _conn()
    captures = CaptureStore(conn)
    projects = p.ProjectStore(conn)
    captures.allow(7)
    projects.create("X", stage="LEAD")
    off_path = tmp_path / "off.json"
    _write_offset(off_path, "default", 0)  # pre-seed so _initial_offset resumes (no tail poll)
    stop = threading.Event()
    calls = {"n": 0}

    class LoopClient(FakeClient):
        def get_updates(self, offset, *, timeout=25, allowed_updates=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return [{"update_id": 5, "message": _msg(text="hi", message_id=100)}]
            stop.set()
            return []
    lc = LoopClient()
    bot = IntakeBot(client=lc, captures=captures, projects=projects, captures_dir=tmp_path)
    poll_forever(client=lc, bot=bot, bot_name="default", offset_path=off_path, shutdown=stop)
    assert captures.get("c-10-100") is not None                # the batched update was handled
    assert _read_offset(off_path, "default") == 6              # offset advanced + persisted


def test_persist_lock_holds_offset_and_retries_until_committed(tmp_path, monkeypatch):
    # ADR-020 retry: a locked DB must NOT advance the offset or drop the capture — re-poll re-delivers
    # the update and the idempotent add() retries until it durably commits (no human re-send needed).
    import email2data.intake as intake_mod
    monkeypatch.setattr(intake_mod.time, "sleep", lambda s: None)  # skip the real backoff wait
    conn = _conn()
    captures = CaptureStore(conn)
    projects = p.ProjectStore(conn)
    captures.allow(7)
    projects.create("X", stage="LEAD")
    off_path = tmp_path / "off.json"
    _write_offset(off_path, "default", 5)  # resume from 5 (no tail poll)
    stop = threading.Event()

    real_add = captures.add
    calls = {"add": 0}

    def flaky_add(**kw):
        calls["add"] += 1
        if calls["add"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_add(**kw)
    monkeypatch.setattr(captures, "add", flaky_add)

    class RetryClient(FakeClient):
        def get_updates(self, offset, *, timeout=25, allowed_updates=None):
            if offset > 5:                  # the capture committed and the offset advanced -> done
                stop.set()
                return []
            return [{"update_id": 5, "message": _msg(text="hi", message_id=100)}]
    rc = RetryClient()
    bot = IntakeBot(client=rc, captures=captures, projects=projects, captures_dir=tmp_path)
    poll_forever(client=rc, bot=bot, bot_name="default", offset_path=off_path, shutdown=stop)

    assert calls["add"] == 2                               # retried after the lock
    assert captures.get("c-10-100") is not None            # the capture was NOT dropped (N2)
    assert _read_offset(off_path, "default") == 6          # offset advanced ONLY after the commit
    assert rc.deleted == [(10, 100)]                       # scrubbed after the successful persist


def test_callback_confirmation_is_plain_text_against_markdown_titles(tmp_path):
    # A project title with Markdown specials (* _ [) must not break the confirmation edit AFTER
    # set_project has committed — the edit is plain text (parse_mode=None).
    client = FakeClient()
    bot, captures, projects = _bot(_conn(), client, tmp_path)
    captures.allow(7)
    pid = projects.create("Loja 50% *promo* _verao_ [x]", stage="LEAD")
    cid, _ = captures.add(telegram_message_id=100, telegram_chat_id=10, raw_text="x")
    bot.handle({"update_id": 2, "callback_query": {
        "id": "cb1", "from": {"id": 7}, "data": f"pick:{cid}:{pid}",
        "message": {"message_id": 500, "chat": {"id": 10}}}})
    assert captures.get(cid)["status"] == "parsed"         # the DB committed
    conf = client.edited[-1]
    assert "Associado" in conf[2] and conf[4] is None      # confirmation rendered as PLAIN text


# ── UX review (2026-07-19): help/guidance, media coverage, and the pick-list ──────────────────────
# The bot is the ONLY surface a staffer sees from the phone, and a voice memo is scrubbed from
# Telegram once stored — so what the chat says back is load-bearing, not cosmetic.


def _llm_bot(conn, client, tmp_path, **kw):
    return _bot(conn, client, tmp_path, llm_client=object(),
                llm_cfg={"provider": "vertex_gemini", "model": "x"}, **kw)


def test_unauthorized_sender_gets_no_help_text(tmp_path):
    # The help text describes the internal workflow; default-deny must come FIRST so a stranger who
    # finds the bot learns nothing about it.
    client = FakeClient()
    bot, _, _ = _bot(_conn(), client, tmp_path)
    bot.handle({"update_id": 1, "message": _msg(text="/start")})
    assert len(client.sent) == 1
    assert "autorizado" in client.sent[0][1]
    assert "Envia uma nota" not in client.sent[0][1]      # the help body never leaked


def test_allowlisted_user_gets_help_listing_every_supported_kind(tmp_path):
    client = FakeClient()
    bot, captures, _ = _bot(_conn(), client, tmp_path)
    captures.allow(7)
    for cmd in ("/start", "/ajuda", "/help"):
        client.sent.clear()
        bot.handle({"update_id": 1, "message": _msg(text=cmd)})
        body = client.sent[0][1]
        # every kind the handler actually accepts must be advertised
        for kind in ("nota", "foto", "ficheiro", "voz"):
            assert kind in body, f"{cmd} help omits {kind}"


def test_empty_message_error_mentions_voice_and_files(tmp_path):
    # It used to say "texto ou uma foto" while voice was already supported.
    client = FakeClient()
    bot, captures, _ = _bot(_conn(), client, tmp_path)
    captures.allow(7)
    bot.handle({"update_id": 1, "message": _msg()})
    body = client.sent[-1][1]
    assert "voz" in body and "ficheiro" in body


# -- documents: PDFs/DXFs and photos sent "as file" ------------------------------------------------


def test_document_capture_downloads_persists_and_scrubs(tmp_path):
    client = FakeClient(file_bytes=b"%PDF-1.7")
    bot, captures, projects = _bot(_conn(), client, tmp_path)
    captures.allow(7)
    projects.create("X", stage="LEAD")
    bot.handle({"update_id": 1, "message": _msg(document=True, message_id=100)})
    cap = captures.get("c-10-100")
    assert cap is not None and cap["content_class"] == "artifact"
    assert cap["media_paths"] == ["c-10-100/desenho.pdf"]
    assert (tmp_path / "c-10-100" / "desenho.pdf").read_bytes() == b"%PDF-1.7"
    assert client.deleted == [(10, 100)]                       # persisted, then scrubbed


def test_document_with_caption_keeps_the_caption_as_text(tmp_path):
    client = FakeClient(file_bytes=b"%PDF")
    bot, captures, projects = _bot(_conn(), client, tmp_path)
    captures.allow(7)
    projects.create("X", stage="LEAD")
    bot.handle({"update_id": 1,
                "message": _msg(document=True, caption="desenho final do cliente", message_id=100)})
    assert captures.get("c-10-100")["raw_text"] == "desenho final do cliente"


@pytest.mark.parametrize("hostile", ["../../evil.sh", "/etc/passwd", "..\\..\\evil.bat", "....//x.sh"])
def test_document_filename_cannot_escape_the_captures_dir(tmp_path, hostile):
    # file_name is attacker-controlled: it must never write outside the capture folder.
    client = FakeClient(file_bytes=b"x")
    bot, captures, projects = _bot(_conn(), client, tmp_path)
    captures.allow(7)
    projects.create("X", stage="LEAD")
    bot.handle({"update_id": 1, "message": _msg(document=hostile, message_id=100)})
    cap = captures.get("c-10-100")
    assert cap is not None, "a hostile name must not lose the capture"
    rel = cap["media_paths"][0]
    assert ".." not in rel and not rel.startswith("/")
    written = (tmp_path / rel).resolve()
    assert tmp_path.resolve() in written.parents          # stayed inside the captures dir
    assert written.exists()


def test_document_without_filename_falls_back_to_the_telegram_extension(tmp_path):
    client = FakeClient(file_bytes=b"x")
    bot, captures, projects = _bot(_conn(), client, tmp_path)
    captures.allow(7)
    projects.create("X", stage="LEAD")
    bot.handle({"update_id": 1, "message": _msg(document="", message_id=100)})
    assert captures.get("c-10-100")["media_paths"] == ["c-10-100/document.pdf"]


def test_document_download_failure_says_file_and_never_persists(tmp_path, monkeypatch):
    client = FakeClient()
    bot, captures, projects = _bot(_conn(), client, tmp_path)
    captures.allow(7)
    projects.create("X", stage="LEAD")
    monkeypatch.setattr(client, "download_file", lambda *a, **k: (_ for _ in ()).throw(OSError("net")))
    bot.handle({"update_id": 1, "message": _msg(document=True, message_id=100)})
    assert captures.get("c-10-100") is None                # nothing persisted
    assert client.deleted == []                            # nothing scrubbed -> Telegram keeps it
    assert "ficheiro" in client.edited[-1][2]              # file wording, not "imagem"


def test_audio_download_failure_says_voice_not_image(tmp_path, monkeypatch):
    # The audio path reused the photo string, telling the user about an "imagem" they never sent.
    client = FakeClient()
    bot, captures, projects = _bot(_conn(), client, tmp_path)
    captures.allow(7)
    projects.create("X", stage="LEAD")
    monkeypatch.setattr(client, "download_file", lambda *a, **k: (_ for _ in ()).throw(OSError("net")))
    bot.handle({"update_id": 1, "message": _msg(voice=True, message_id=100)})
    body = client.edited[-1][2]
    assert "voz" in body and "imagem" not in body
    assert captures.get("c-10-100") is None and client.deleted == []


# -- the transcript echo: the staffer's only chance to check what was heard -------------------------


def test_voice_capture_echoes_the_transcript_back_to_the_chat(tmp_path, monkeypatch):
    import email2data.intake as intake_mod
    client = FakeClient(file_bytes=b"OGG")
    bot, captures, projects = _llm_bot(_conn(), client, tmp_path)
    captures.allow(7)
    projects.create("Estante Sousa", stage="LEAD")
    monkeypatch.setattr(intake_mod.llm, "call",
                        lambda cl, cfg, system, user, **kw: ("cliente quer 200 unidades"
                                                             if kw.get("images") else {}))
    bot.handle({"update_id": 1, "message": _msg(voice=True, message_id=100)})
    pick = client.edited[-1]
    assert "cliente quer 200 unidades" in pick[2]     # the staffer can verify before the audio is gone
    assert "Ouvi" in pick[2]
    assert "projeto pertence" in pick[2]              # still offers the pick-list
    assert pick[4] is None                            # plain text: model output is untrusted Markdown


def test_voice_without_a_transcript_tells_the_user_it_failed(tmp_path, monkeypatch):
    # Silence would be the worst outcome: Telegram is already scrubbed, so the staffer would believe
    # the note was captured verbatim.
    import email2data.intake as intake_mod
    monkeypatch.setattr(intake_mod.llm, "call",
                        lambda *a, **k: (_ for _ in ()).throw(intake_mod.llm.LLMError("down")))
    client = FakeClient(file_bytes=b"OGG")
    bot, captures, projects = _llm_bot(_conn(), client, tmp_path)
    captures.allow(7)
    projects.create("X", stage="LEAD")
    bot.handle({"update_id": 1, "message": _msg(voice=True, message_id=100)})
    body = client.edited[-1][2]
    assert "não consegui transcrev" in body.lower()
    assert captures.get("c-10-100")["media_paths"]         # the audio itself is still preserved


def test_voice_with_no_active_projects_still_reports_what_was_heard(tmp_path, monkeypatch):
    import email2data.intake as intake_mod
    client = FakeClient(file_bytes=b"OGG")
    bot, captures, _ = _llm_bot(_conn(), client, tmp_path)   # no projects created
    captures.allow(7)
    monkeypatch.setattr(intake_mod.llm, "call",
                        lambda cl, cfg, system, user, **kw: "nota solta" if kw.get("images") else {})
    bot.handle({"update_id": 1, "message": _msg(voice=True, message_id=100)})
    body = client.edited[-1][2]
    assert "nota solta" in body and "Caixa de Capturas" in body


def test_long_transcript_is_clipped_in_the_echo(tmp_path, monkeypatch):
    import email2data.intake as intake_mod
    long_text = "palavra " * 200
    client = FakeClient(file_bytes=b"OGG")
    bot, captures, projects = _llm_bot(_conn(), client, tmp_path)
    captures.allow(7)
    projects.create("X", stage="LEAD")
    monkeypatch.setattr(intake_mod.llm, "call",
                        lambda cl, cfg, system, user, **kw: long_text if kw.get("images") else {})
    bot.handle({"update_id": 1, "message": _msg(voice=True, message_id=100)})
    body = client.edited[-1][2]
    assert len(body) < 900 and "…" in body                       # clipped for a phone screen
    assert captures.get("c-10-100")["transcript"].strip() == long_text.strip()  # stored in FULL


def test_text_capture_does_not_claim_to_have_heard_anything(tmp_path):
    client = FakeClient()
    bot, captures, projects = _bot(_conn(), client, tmp_path)
    captures.allow(7)
    projects.create("X", stage="LEAD")
    bot.handle({"update_id": 1, "message": _msg(text="nota escrita", message_id=100)})
    assert "Ouvi" not in client.edited[-1][2]


# -- pick-list buttons: two identical labels are un-pickable ---------------------------------------


def test_button_label_disambiguates_duplicate_titles():
    from email2data.intake import _button_label
    a = {"project_id": "p-0002", "title": "Troféu KIA", "client_email": "ana@kia.pt",
         "created_ts": "2026-06-01T10:00:00+00:00"}
    b = {"project_id": "p-0003", "title": "Troféu KIA", "client_email": "jose@agencia.com",
         "created_ts": "2026-06-04T10:00:00+00:00"}
    la, lb = _button_label(a), _button_label(b)
    assert la != lb                       # the whole point: they must be tellable apart
    assert "kia.pt" in la and "agencia.com" in lb
    assert "p-0002" in la and "p-0003" in lb


def test_button_label_truncates_long_email_subject_titles():
    from email2data.intake import _button_label
    label = _button_label({"project_id": "p-0001",
                           "title": "Re: awards for MME needed before June 14th",
                           "client_email": "diogo@brand.com", "created_ts": "2026-06-01T10:00:00+00:00"})
    assert "…" in label and len(label) < 60
    assert "p-0001" in label and "brand.com" in label


def test_button_label_falls_back_to_the_date_without_a_client_email():
    from email2data.intake import _button_label
    label = _button_label({"project_id": "p-0009", "title": "Troféu croissant",
                           "client_email": "", "created_ts": "2026-06-20T10:00:00+00:00"})
    assert "20/06" in label and "p-0009" in label


def test_button_label_survives_missing_fields():
    from email2data.intake import _button_label
    assert "p-1" in _button_label({"project_id": "p-1"})            # no title, no email, no date
    assert _button_label({"project_id": "p-2", "title": None, "created_ts": None})


def test_pick_list_uses_the_disambiguated_labels(tmp_path):
    from email2data.intake import _button_label
    client = FakeClient()
    conn = _conn()
    bot, captures, projects = _bot(conn, client, tmp_path)
    captures.allow(7)
    pid = projects.create("Troféu KIA", stage="LEAD")
    bot.handle({"update_id": 1, "message": _msg(text="nota", message_id=100)})
    buttons = [b[0] for b in client.edited[-1][3]["inline_keyboard"]]
    picked = [b for b in buttons if b["callback_data"].endswith(pid)]
    assert picked and picked[0]["text"] == _button_label(
        [p for p in projects.list() if p["project_id"] == pid][0])


# -- the / command menu ----------------------------------------------------------------------------


def test_poll_forever_registers_the_command_menu(tmp_path):

    class OneShot(FakeClient):
        def __init__(self):
            super().__init__()
            self.polls = 0

        def get_updates(self, offset, *, timeout=25, allowed_updates=None):
            self.polls += 1
            stop.set()
            return []

    stop = threading.Event()
    c = OneShot()
    bot, _, _ = _bot(_conn(), c, tmp_path)
    poll_forever(client=c, bot=bot, bot_name="b", offset_path=tmp_path / "o.json", shutdown=stop)
    assert getattr(c, "commands", None), "the / menu was never registered"
    assert any(x["command"] == "ajuda" for x in c.commands)
    assert c.order.index("set_commands") < len(c.order)   # registered at start


def test_command_menu_failure_never_stops_the_worker(tmp_path):
    # Discoverability is not function: a bot that cannot register commands must still poll.
    class Broken(FakeClient):
        def __init__(self):
            super().__init__()
            self.polls = 0

        def set_my_commands(self, commands):
            raise RuntimeError("telegram down")

        def get_updates(self, offset, *, timeout=25, allowed_updates=None):
            self.polls += 1
            stop.set()
            return []

    stop = threading.Event()
    c = Broken()
    bot, _, _ = _bot(_conn(), c, tmp_path)
    poll_forever(client=c, bot=bot, bot_name="b", offset_path=tmp_path / "o.json", shutdown=stop)
    assert c.polls >= 1        # polled anyway
