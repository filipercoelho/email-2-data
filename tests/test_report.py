"""Fresh-volume boot safety.

Repro of the Docker first-run crash: ``create_app`` calls ``report.prepare()`` BEFORE the lifespan
boot-sync writes ``out/results.jsonl``, so an unguarded read bricked ``docker compose up`` on a clean
``out/`` volume (FileNotFoundError, then a crash-loop under ``restart: unless-stopped``). prepare() must
degrade to empty on a fresh out/, exactly like its contacts/cost/jobspecs siblings.
"""

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
