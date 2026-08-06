"""Phase B orchestration — failure surfacing, the scoped re-extract, and the atomic write.

A failed spec draft used to only print to stdout, which made a hard failure indistinguishable from an
email that genuinely had little to extract: the entry degraded to the fallback, the incremental gate
froze it, and the UI showed a thin project with no hint that anything broke. These pin the three
guarantees that were missing — the failure is recorded (in the entry AND the audit log, without
leaking content), one project can be re-extracted without re-billing the whole corpus, and a crash
mid-write can never truncate jobspecs.jsonl.
"""

import json
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

import pytest

from email2data import specbuild, specdraft
from email2data.envelope import parse_eml

SUBJECT_SECRET = "SEGREDO-ASSUNTO-orcamento-porticos"
BODY_SECRET = "SEGREDO-CORPO-cliente-confidencial"

DRAFTED = {"line_items": [{"item": "pórtico", "material": "corten", "thickness": "3 mm"}],
           "material_supplied_by": "us", "delivery": None}


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
    """A minimal project tree: config/ + corpus/ (3 job emails) + out/results.jsonl."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "spec_playbook.md").write_text("SPEC PLAYBOOK", encoding="utf-8")
    (tmp_path / "out").mkdir()
    (tmp_path / "corpus").mkdir()

    settings_path = tmp_path / "config" / "settings.json"
    settings = {"llm": {"provider": "vertex_gemini", "model": "gemini-2.5-flash", "max_retries": 1},
                "__settings_path__": str(settings_path)}
    settings_path.write_text(json.dumps(settings), encoding="utf-8")

    emls: list[Path] = []
    results: list[dict] = []
    for n in range(3):
        subject = SUBJECT_SECRET if n == 0 else f"Pedido {n}"
        raw = _eml(f"job{n}", subject, BODY_SECRET if n == 0 else f"corpo {n}")
        path = tmp_path / "corpus" / f"job{n}.eml"
        path.write_bytes(raw)
        emls.append(path)
        results.append({"message_id": parse_eml(raw)["message_id"], "subject": subject,
                        "counterparty": "CLIENT", "purpose": "ESTIMATE_REQUEST_FROM_CLIENT",
                        "entities": {"product_or_service": "corte laser", "deadline": None,
                                     "money": None}})
    (tmp_path / "out" / "results.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in results), encoding="utf-8")

    return SimpleNamespace(base=tmp_path, settings=settings, results=results, emls=emls,
                           out=tmp_path / "out", jobspecs=tmp_path / "out" / "jobspecs.jsonl",
                           audit=tmp_path / "out" / "audit.jsonl")


def _boom(msg="Vertex 400 INVALID_ARGUMENT: Provided image is not valid"):
    def fake(env, playbook, client, settings, *, tier=None):
        raise RuntimeError(msg)
    return fake


def _ok(seen=None):
    def fake(env, playbook, client, settings, *, tier=None):
        if seen is not None:
            seen.append(env["message_id"])
        return DRAFTED
    return fake


# ── build_entry: a failed draft is surfaced, not swallowed ───────────────────────────────────────

def test_build_entry_records_spec_error_instead_of_swallowing_the_failure(proj, monkeypatch):
    monkeypatch.setattr(specdraft, "draft", _boom())
    r = proj.results[0]
    entry, drafted = specbuild.build_entry(
        r, proj.emls[0], draft=True, reply=False, client=object(), settings=proj.settings,
        spec_pb="PB", reply_pb=None)
    assert drafted is False
    assert entry["spec_error"].startswith("RuntimeError:")
    assert "INVALID_ARGUMENT" in entry["spec_error"]
    assert entry["message_id"] == r["message_id"]           # the entry is still built (partial > none)


def test_build_entry_sets_no_spec_error_on_success(proj, monkeypatch):
    monkeypatch.setattr(specdraft, "draft", _ok())
    entry, drafted = specbuild.build_entry(
        proj.results[0], proj.emls[0], draft=True, reply=False, client=object(),
        settings=proj.settings, spec_pb="PB", reply_pb=None)
    assert drafted is True and "spec_error" not in entry
    assert entry["items"][0]["material"]["value"] == "corten"


def test_build_entry_audits_the_failure_without_leaking_any_content(proj, monkeypatch):
    """audit.jsonl carries counts/ids/types ONLY — never a subject, body, or model text."""
    monkeypatch.setattr(specdraft, "draft", _boom(f"model refused: {BODY_SECRET} / {SUBJECT_SECRET}"))
    specbuild.build_entry(
        proj.results[0], proj.emls[0], draft=True, reply=False, client=object(),
        settings=proj.settings, spec_pb="PB", reply_pb=None, tier="heavy", audit_log=proj.audit)

    recs = [json.loads(x) for x in proj.audit.read_text(encoding="utf-8").splitlines() if x]
    failures = [r for r in recs if r["event"] == "spec_draft_failed"]
    assert len(failures) == 1
    meta = failures[0]["meta"]
    assert meta["message_id"] == proj.results[0]["message_id"]
    assert meta["error"] == "RuntimeError"                  # the TYPE, never the message
    assert meta["tier"] == "heavy"
    assert failures[0]["target"] == "specbuild"

    written = proj.audit.read_text(encoding="utf-8")
    assert "SEGREDO-CORPO" not in written and "SEGREDO-ASSUNTO" not in written
    assert "cliente@example.pt" not in written


# ── rebuild_jobspecs: scoped re-extract, idempotency, counts ─────────────────────────────────────

def test_rebuild_only_rebuilds_the_named_mid_and_keeps_the_rest_byte_identical(proj, monkeypatch):
    """The scoped re-extract must not re-bill a Tier-1 pass for every job email in the corpus."""
    assert specbuild.rebuild_jobspecs(proj.settings, draft=False, incremental=False)["total"] == 3
    before = proj.jobspecs.read_text(encoding="utf-8").splitlines()

    target = proj.results[0]["message_id"]
    seen: list[str] = []
    monkeypatch.setattr(specdraft, "draft", _ok(seen))
    counts = specbuild.rebuild_jobspecs(proj.settings, draft=True, incremental=True,
                                        client=object(), only={target})

    assert seen == [target]                                  # the LLM ran for THAT mid and no other
    assert counts == {"built": 1, "drafted": 1, "kept": 2, "failed": 0, "total": 3}
    after = proj.jobspecs.read_text(encoding="utf-8").splitlines()
    assert after[1] == before[1] and after[2] == before[2]    # untouched, byte-for-byte
    assert after[0] != before[0]
    assert json.loads(after[0])["items"][0]["material"]["value"] == "corten"


def test_rebuild_without_only_is_byte_identical_and_reruns_are_idempotent(proj):
    specbuild.rebuild_jobspecs(proj.settings, draft=False, incremental=False)
    first = proj.jobspecs.read_bytes()

    specbuild.rebuild_jobspecs(proj.settings, draft=False, incremental=False)
    assert proj.jobspecs.read_bytes() == first                # full rebuild is deterministic

    counts = specbuild.rebuild_jobspecs(proj.settings, draft=False, incremental=True)
    assert counts["kept"] == 3 and counts["built"] == 0
    assert proj.jobspecs.read_bytes() == first                # incremental keeps every entry as-is


def test_counts_include_failed_and_it_increments_per_failed_draft(proj, monkeypatch):
    monkeypatch.setattr(specdraft, "draft", _boom())
    counts = specbuild.rebuild_jobspecs(proj.settings, draft=True, incremental=False, client=object())
    assert "failed" in counts
    assert counts["failed"] == 3 and counts["built"] == 3 and counts["drafted"] == 0
    entries = [json.loads(x) for x in proj.jobspecs.read_text(encoding="utf-8").splitlines() if x]
    assert all(e["spec_error"].startswith("RuntimeError:") for e in entries)


def test_tier_is_forwarded_to_the_spec_draft(proj, monkeypatch):
    tiers: list[str] = []

    def fake(env, playbook, client, settings, *, tier=None):
        tiers.append(tier)
        return DRAFTED
    monkeypatch.setattr(specdraft, "draft", fake)
    specbuild.rebuild_jobspecs(proj.settings, draft=True, incremental=False, client=object(),
                               tier="heavy")
    assert tiers == ["heavy", "heavy", "heavy"]


# ── the write is atomic ──────────────────────────────────────────────────────────────────────────

def test_rebuild_writes_through_a_temp_file_and_leaves_none_behind(proj, monkeypatch):
    replaced: list[tuple[str, str]] = []
    real_replace = specbuild.os.replace

    def spy(src, dst):
        replaced.append((str(src), str(dst)))
        return real_replace(src, dst)
    monkeypatch.setattr(specbuild.os, "replace", spy)

    specbuild.rebuild_jobspecs(proj.settings, draft=False, incremental=False)
    assert len(replaced) == 1 and replaced[0][0].endswith(".jsonl.building")
    assert replaced[0][1] == str(proj.jobspecs)
    assert list(proj.out.glob("*.building")) == []            # no temp file survives a clean run


def test_a_crash_mid_write_cannot_truncate_an_existing_jobspecs(proj, monkeypatch):
    """The lost entries are only recoverable by re-spending the LLM pass that produced them."""
    specbuild.rebuild_jobspecs(proj.settings, draft=False, incremental=False)
    good = proj.jobspecs.read_bytes()
    assert len(good) > 0

    real_write_text = Path.write_text

    def half_then_die(self, data, *args, **kwargs):
        real_write_text(self, data[: len(data) // 2], *args, **kwargs)
        raise OSError("no space left on device")
    monkeypatch.setattr(Path, "write_text", half_then_die)

    with pytest.raises(OSError):
        specbuild.rebuild_jobspecs(proj.settings, draft=False, incremental=False)
    monkeypatch.undo()
    assert proj.jobspecs.read_bytes() == good                 # the previous build survived intact


# ── ADR-053: the corpus filename IS the index ────────────────────────────────────────────────────

def test_rebuild_jobspecs_resolves_corpus_files_by_computed_name_not_a_full_scan(proj,
                                                                                 monkeypatch):
    """ADR-053: ``rebuild_jobspecs`` used to build a full ``mid2file`` map by globbing the corpus
    and parsing every .eml. The map is now computed per-mid via ``safe_filename(mid)``, so 15
    unrelated .eml files sitting in the corpus never get parsed."""
    from email2data import envelope, identity, specbuild as _sb

    # Rename each job's .eml to its computed safe_filename so the compute-first path finds it —
    # the fixture writes them under ``job0.eml`` / ``job1.eml`` / ``job2.eml``, which is what the
    # old scan-based ``_corpus_index`` looked up.
    for path in list(proj.emls):
        raw = path.read_bytes()
        target = path.parent / identity.safe_filename(identity.canonical_id_from_raw(raw))
        if target != path:
            path.rename(target)

    # Add noise files that MUST NOT be parsed once the compute-first fix lands.
    for i in range(15):
        raw = _eml(f"noise{i}", "not a job", "irrelevant")
        (proj.base / "corpus" / identity.safe_filename(identity.canonical_id_from_raw(raw))
         ).write_bytes(raw)

    monkeypatch.setattr(specdraft, "draft", _ok())

    calls = [0]
    real = envelope.parse_eml

    def counted(raw, *a, **kw):
        calls[0] += 1
        return real(raw, *a, **kw)

    monkeypatch.setattr(envelope, "parse_eml", counted)
    monkeypatch.setattr(_sb, "parse_eml", counted)

    counts = specbuild.rebuild_jobspecs(proj.settings, draft=True, incremental=False,
                                        client=object())
    assert counts["built"] == 3
    # Old code: 18 (scan of 3 jobs + 15 noise) + 3 (build_entry re-parse per job) = 21.
    # New code: 3 (build_entry per-mid) with the scan gone. No fallback because all files land on
    # their computed name.
    assert calls[0] == 3, (
        f"specbuild parsed {calls[0]} eml files for 3 jobs — the whole-corpus scan came back")
