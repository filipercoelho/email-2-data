"""Knowledge store (SQLite) — Phase 2/4 (SCAFFOLD: schema defined, methods stubbed).

Persists what we learn so each run is cheaper and better than the last (VISION tenet 5):
- sender_reputation: domain -> counterparty PRIOR. Human-confirmed is authoritative; LLM-derived is
  provisional and strengthens with repeated agreement. ALWAYS overridable by the body.
- verdict_cache: content_hash -> prior verdict, for repeat/templated mail (zero-token reuse).
- exemplars: past (email -> correct verdict) for few-shot retrieval on hard cases.
- thread_state: a thread already known to be a client PO stays a client.

Local-first, single file under out/. No PII leaves the machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS sender_reputation (
    domain        TEXT PRIMARY KEY,
    counterparty  TEXT NOT NULL,          -- schema.COUNTERPARTY
    confidence    REAL NOT NULL,
    n_obs         INTEGER NOT NULL DEFAULT 1,
    source        TEXT NOT NULL,          -- 'human' (authoritative) | 'llm' (provisional)
    last_seen     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS verdict_cache (
    content_hash      TEXT PRIMARY KEY,
    verdict_json      TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    created_at        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS exemplars (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    embedding    BLOB,                    -- float32 vector of subject+body
    subject      TEXT,
    body_excerpt TEXT,
    verdict_json TEXT NOT NULL,
    source       TEXT NOT NULL,           -- 'human' | 'llm'
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS thread_state (
    thread_id     TEXT PRIMARY KEY,
    counterparty  TEXT,
    last_verdict  TEXT,
    updated_at    TEXT NOT NULL
);
"""


@dataclass
class Reputation:
    counterparty: str
    confidence: float
    source: str  # 'human' | 'llm'


class KnowledgeStore:
    """SQLite-backed. CONTRACT below; all methods stubbed for Phase 2/4."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def connect(self) -> "KnowledgeStore":
        """CONTRACT: open the db, run SCHEMA (idempotent), return self."""
        raise NotImplementedError("Phase 2")

    # --- sender reputation (PRIOR, never a hard rule; body overrides) ---
    def get_reputation(self, domain: str) -> Optional[Reputation]:
        raise NotImplementedError("Phase 2")

    def observe_reputation(self, domain: str, counterparty: str, *, source: str) -> None:
        """CONTRACT: upsert. 'human' source is authoritative (pins counterparty, confidence high).
        'llm' source increments n_obs and nudges confidence toward the observed value only while it
        agrees; conflicting observations decay confidence rather than flip silently."""
        raise NotImplementedError("Phase 4")

    # --- verdict cache (zero-token reuse for repeat/templated mail) ---
    def get_cached_verdict(self, content_hash: str, extractor_version: str) -> Optional[dict[str, Any]]:
        """CONTRACT: return the cached verdict only if extractor_version matches (else miss, so a
        playbook bump invalidates stale cache)."""
        raise NotImplementedError("Phase 4")

    def put_verdict(self, content_hash: str, verdict: dict[str, Any], extractor_version: str) -> None:
        raise NotImplementedError("Phase 4")

    # --- exemplar retrieval (few-shot on hard cases) ---
    def find_exemplars(self, embedding: bytes, k: int = 3) -> list[dict[str, Any]]:
        raise NotImplementedError("Phase 4")

    def add_exemplar(self, *, embedding: bytes, subject: str, body_excerpt: str,
                     verdict: dict[str, Any], source: str) -> None:
        raise NotImplementedError("Phase 4")

    # --- feedback loop: a human correction updates reputation + exemplars ---
    def record_correction(self, *, domain: str, corrected_verdict: dict[str, Any],
                          subject: str, body_excerpt: str, embedding: bytes | None = None) -> None:
        raise NotImplementedError("Phase 4")
