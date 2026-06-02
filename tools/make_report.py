"""Write the self-contained static report to out/report.html (the UI lives in email2data.report).

Run from the repo root:  .venv/bin/python tools/make_report.py
"""

from pathlib import Path

from email2data import report
from email2data.config import load_settings, paths

settings = load_settings("config/settings.json")
settings["__settings_path__"] = str(Path("config/settings.json").resolve())
out = paths(settings, settings["__settings_path__"])["out_dir"]

emails, contacts, cost = report.prepare(settings)
(out / "report.html").write_text(report.build_html(emails, contacts, cost, live=False), encoding="utf-8")
print(f"wrote {out / 'report.html'}  ({len(emails)} emails, {len(contacts)} contacts)")
