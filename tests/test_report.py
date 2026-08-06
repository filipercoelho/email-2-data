"""Fresh-volume boot safety + ADR-053 perf pins.

Repro of the Docker first-run crash: ``create_app`` calls ``report.prepare()`` BEFORE the lifespan
boot-sync writes ``out/results.jsonl``, so an unguarded read bricked ``docker compose up`` on a clean
``out/`` volume (FileNotFoundError, then a crash-loop under ``restart: unless-stopped``). prepare() must
degrade to empty on a fresh out/, exactly like its contacts/cost/jobspecs siblings.

ADR-053 additions pin the corpus-index perf invariants: prepare() computes each email's .eml path
via ``safe_filename(mid)`` instead of a whole-corpus scan, and memoizes parsed envelopes so a second
prepare() on the same corpus does no I/O.
"""

import json
from email.message import EmailMessage

from email2data import report


def _settings(tmp_path):
    return {"__settings_path__": str(tmp_path / "config" / "settings.json")}


def test_prepare_on_fresh_out_dir_returns_empty_without_crashing(tmp_path):
    # First-run state: out/ exists (paths() makes it) but holds no results.jsonl yet.
    emails, contacts, cost = report.prepare(_settings(tmp_path))
    assert emails == [] and contacts == [] and cost == {}


def test_prepare_still_reads_results_when_present(tmp_path):
    from email2data.config import paths

    p = paths(_settings(tmp_path), _settings(tmp_path)["__settings_path__"])
    (p["out_dir"] / "results.jsonl").write_text(
        '{"message_id": "mid:a@x.pt", "priority": "HIGH", "urgency": 9}\n', encoding="utf-8")
    emails, _contacts, _cost = report.prepare(_settings(tmp_path))
    assert [e["message_id"] for e in emails] == ["mid:a@x.pt"]


# ── ADR-053: the corpus filename IS the index ────────────────────────────────────────────────────

def _adr053_eml(local: str) -> bytes:
    m = EmailMessage()
    m["From"] = "cliente@exemplo.pt"
    m["To"] = "orcamentos@lindoservico.pt"
    m["Subject"] = f"Perf {local}"
    m["Message-ID"] = f"<{local}@exemplo.pt>"
    m["Date"] = "Mon, 20 Jul 2026 10:00:00 +0100"
    m.set_content(f"corpo {local}")
    return m.as_bytes()


def _adr053_seed(tmp_path, *, n_real: int, n_noise: int) -> list[str]:
    """Seed corpus with ``n_real`` real emails (recorded in results.jsonl) + ``n_noise`` decoys
    that must NEVER be parsed by prepare(). Returns the real message ids in order."""
    from email2data.config import paths
    from email2data.identity import canonical_id_from_raw, safe_filename

    p = paths(_settings(tmp_path), _settings(tmp_path)["__settings_path__"])
    mids: list[str] = []
    for i in range(n_real):
        raw = _adr053_eml(f"real{i}")
        mid = canonical_id_from_raw(raw)
        mids.append(mid)
        (p["corpus_dir"] / safe_filename(mid)).write_bytes(raw)
    for i in range(n_noise):
        raw = _adr053_eml(f"noise{i}")
        (p["corpus_dir"] / safe_filename(canonical_id_from_raw(raw))).write_bytes(raw)
    (p["out_dir"] / "results.jsonl").write_text(
        "\n".join(json.dumps({"message_id": m, "priority": "HIGH", "urgency": 5}) for m in mids),
        encoding="utf-8")
    return mids


def test_prepare_never_scans_the_whole_corpus(tmp_path, monkeypatch):
    """ADR-053: prepare() resolves each email's .eml with ``safe_filename(mid)`` — one filesystem
    stat per known email — and parses only those files. Old behavior globbed and parsed every .eml
    to build ``mid2file``, then re-parsed each match: **9.04s of pure scan** on a 1094-file corpus
    per the perf diagnosis."""
    from email2data import envelope
    real_mids = _adr053_seed(tmp_path, n_real=2, n_noise=20)
    report._env_cache.clear()

    calls = [0]
    real = envelope.parse_eml

    def counted(raw, *a, **kw):
        calls[0] += 1
        return real(raw, *a, **kw)

    monkeypatch.setattr(envelope, "parse_eml", counted)
    monkeypatch.setattr(report, "parse_eml", counted)

    emails, _c, _cost = report.prepare(_settings(tmp_path))
    assert [e["message_id"] for e in emails] == real_mids
    # 2 (one enrichment parse per email) — pre-ADR-053 this was 22 (scan) + 2 (re-parse) = 24.
    # Any number involving the noise files means the whole-corpus scan is still running.
    assert calls[0] == len(real_mids), (
        f"prepare parsed {calls[0]} eml files for {len(real_mids)} emails — the scan came back")


def test_prepare_is_incremental_across_re_runs(tmp_path, monkeypatch):
    """ADR-053: the envelope cache means a second prepare() on the same corpus parses **zero**
    envelopes. A typical sync brings ~5 new messages; the incremental gate parses 5, not len(corpus).
    Removes the other 9.6s of report.prepare's CPU burn per the perf diagnosis."""
    from email2data import envelope
    _adr053_seed(tmp_path, n_real=3, n_noise=10)
    report._env_cache.clear()

    report.prepare(_settings(tmp_path))                             # cold: parses 3

    calls = [0]
    real = envelope.parse_eml

    def counted(raw, *a, **kw):
        calls[0] += 1
        return real(raw, *a, **kw)

    monkeypatch.setattr(envelope, "parse_eml", counted)
    monkeypatch.setattr(report, "parse_eml", counted)

    report.prepare(_settings(tmp_path))                             # warm: parses 0
    assert calls[0] == 0, f"warm prepare() re-parsed {calls[0]} envelopes"
