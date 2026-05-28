"""Append-only JSONL audit log.

Privacy non-negotiable (red-team S3): this log carries counts / ids / timings ONLY — never subjects,
bodies, headers, or addresses. Subjects and extracted PII live in out/results.jsonl, not here.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(
    audit_log_path: str | Path,
    event: str,
    target: str,
    meta: dict[str, Any] | None = None,
    ts: str | None = None,
) -> None:
    """Append one audit record. ``ts`` is injectable for testability."""
    rec = {"ts": ts or now_iso(), "event": event, "target": target, "meta": meta or {}}
    path = Path(audit_log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
