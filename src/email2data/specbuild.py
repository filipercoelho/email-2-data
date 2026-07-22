"""Phase B orchestration — (re)build ``out/jobspecs.jsonl`` for job-relevant emails.

Extracted from the CLI so the SAME pipeline runs on demand (``email2data spec``) AND automatically
after each webapp sync — otherwise jobspecs.jsonl silently goes stale (triage keeps writing
results.jsonl while the spec layer is frozen at its last manual build, so new leads create empty,
context-less projects).

Two modes:
  * ``incremental=True``  — keep already-built entries, only extract message_ids not yet present.
    Bounds LLM spend on every sync to genuinely-new leads. Used by the webapp.
  * ``incremental=False`` — rebuild every job-relevant email. Used by the CLI and the one-time
    backfill (e.g. after enabling attachment extraction).

The spec pass feeds the model the email body PLUS extracted attachment content (PDF text + drawing
images via ``envelope.attachment_media``), so forwarded-as-PDF/-image leads populate instead of
starting at 0%.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Optional

from . import audit, classifier, jobspec as js, replydraft, specdraft
from .config import paths
from .envelope import attachment_media, parse_eml

# Which emails earn a (costly) spec pass: client estimate requests / POs, plus anything tagged LEAD.
JOB_PURPOSES = {"ESTIMATE_REQUEST_FROM_CLIENT", "PO_FROM_CLIENT"}


def is_job(r: dict[str, Any]) -> bool:
    return r.get("purpose") in JOB_PURPOSES or r.get("counterparty") == "LEAD"


def _load_existing(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                j = json.loads(line)
                out[j["message_id"]] = j
    return out


def _corpus_index(corpus_dir: Path) -> dict[str, Path]:
    idx: dict[str, Path] = {}
    for eml in corpus_dir.glob("*.eml"):
        try:
            idx[parse_eml(eml.read_bytes())["message_id"]] = eml
        except Exception:  # noqa: BLE001 — skip an unparseable .eml, don't abort the build
            pass
    return idx


def build_entry(r: dict[str, Any], eml: Optional[Path], *, draft: bool, reply: bool,
                client: Any, settings: dict[str, Any], spec_pb: Optional[str],
                reply_pb: Optional[str], log: Optional[Callable[[str], None]] = None,
                tier: Optional[str] = None, audit_log: Optional[Path] = None
                ) -> tuple[dict[str, Any], bool]:
    """Build one jobspecs.jsonl entry (spec.to_dict + readiness [+ draft_reply]). Returns
    (entry, drafted) where ``drafted`` is True if the LLM spec pass ran.

    A failed spec pass sets ``entry["spec_error"]`` and records a ``spec_draft_failed`` audit event.
    It used to only print to stdout, which made a hard failure indistinguishable from an email that
    genuinely had little to extract: the entry degraded to the one-item entities fallback, the
    incremental gate then froze it, and the UI showed a thin project with no hint that anything broke
    (this is exactly what happened to the Corten lead — a 283MP attachment 400'd on every retry).
    """
    mid = r["message_id"]
    raw = eml.read_bytes() if eml else b""
    env = parse_eml(raw) if eml else {"attachments": [], "subject": r.get("subject", ""), "body_text": ""}
    drafted_obj = None
    did_draft = False
    spec_error: Optional[str] = None
    if draft and eml:
        media = attachment_media(raw)  # PDF text + drawing images for the model to read
        env["attachment_texts"] = media["texts"]
        env["attachment_images"] = media["images"]
        try:
            drafted_obj = specdraft.draft(env, spec_pb, client, settings, tier=tier)
            did_draft = True
        except Exception as exc:  # noqa: BLE001 — one bad draft must not sink the whole build
            spec_error = f"{type(exc).__name__}: {exc}"[:300]
            if log:
                log(f"draft failed {mid[:24]}: {spec_error}")
            if audit_log is not None:
                # counts/ids/types only — never the body or the model's text (audit.py privacy rule).
                audit.log(audit_log, "spec_draft_failed", "specbuild",
                          {"message_id": mid, "error": type(exc).__name__, "tier": tier or "default"})
    spec = js.build_jobspec(r, env, drafted_obj)
    rd = js.readiness(spec)
    entry = {**spec.to_dict(), "readiness": rd}
    if spec_error:
        entry["spec_error"] = spec_error
    if reply and eml and client:
        try:
            entry["draft_reply"] = replydraft.draft_reply(entry, rd, reply_pb, client, settings)
        except Exception as exc:  # noqa: BLE001
            if log:
                log(f"reply failed {mid[:24]}: {type(exc).__name__}: {exc}")
    return entry, did_draft


def rebuild_jobspecs(settings: dict[str, Any], *, draft: bool = True, reply: bool = False,
                     incremental: bool = True, client: Any = None,
                     log: Optional[Callable[[str], None]] = None,
                     only: Optional[set[str]] = None, tier: Optional[str] = None) -> dict[str, int]:
    """(Re)build ``out/jobspecs.jsonl``. Returns counts ``{built, drafted, kept, failed, total}``.

    Never raises on a missing LLM client — degrades to offline (regex-only) extraction so a sync on a
    box without credentials still refreshes the offline fields instead of breaking.

    ``only`` is a set of message_ids that must be rebuilt *even in incremental mode* — the scoped
    re-extract. It is deliberately narrower than ``incremental=False``: re-extracting one project must
    not re-bill a Tier-1 pass for every job email in the corpus. Everything outside ``only`` keeps its
    existing entry byte-for-byte, so normal-path idempotency is untouched.

    ``tier`` picks the model for this rebuild only (see :func:`llm.with_tier`).
    """
    base = Path(settings["__settings_path__"]).parents[1]
    p = paths(settings, settings["__settings_path__"])
    out_dir = p["out_dir"]
    counts = {"built": 0, "drafted": 0, "kept": 0, "failed": 0, "total": 0}

    results_path = out_dir / "results.jsonl"
    if not results_path.exists():
        return counts
    results = [json.loads(x) for x in results_path.read_text().splitlines() if x]
    jobs = [r for r in results if is_job(r)]
    existing = _load_existing(out_dir / "jobspecs.jsonl")

    need_llm = draft or reply
    if need_llm and client is None:
        try:
            client = classifier.make_client(settings)
        except Exception as exc:  # noqa: BLE001 — no creds/SDK → offline build, don't break the caller
            if log:
                log(f"spec draft disabled (no LLM client: {type(exc).__name__})")
            draft = reply = need_llm = False
    spec_pb = specdraft.load_playbook(base / "config" / "spec_playbook.md") if draft else None
    reply_pb = replydraft.load_playbook(base / "config" / "reply_playbook.md") if reply else None
    mid2file = _corpus_index(p["corpus_dir"])

    out_entries: list[dict[str, Any]] = []
    for r in jobs:
        mid = r["message_id"]
        if incremental and mid in existing and not (only and mid in only):
            out_entries.append(existing[mid])
            counts["kept"] += 1
            continue
        entry, did_draft = build_entry(
            r, mid2file.get(mid), draft=draft, reply=reply, client=client, settings=settings,
            spec_pb=spec_pb, reply_pb=reply_pb, log=log, tier=tier, audit_log=p["audit_log"])
        out_entries.append(entry)
        counts["built"] += 1
        counts["drafted"] += int(did_draft)
        counts["failed"] += int(bool(entry.get("spec_error")))

    # Temp + atomic replace: a crash mid-write used to truncate jobspecs.jsonl to a prefix, and the
    # lost entries are only recoverable by re-spending the LLM pass that produced them.
    dest = out_dir / "jobspecs.jsonl"
    tmp = dest.with_suffix(".jsonl.building")
    tmp.write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in out_entries), encoding="utf-8")
    os.replace(tmp, dest)
    counts["total"] = len(out_entries)
    return counts
