"""Phase 4 — the LOCATE pass: which sentence of the email justifies each extracted value.

A **separate** LLM call, deliberately: the triage prompt and schema are untouched, so no verdict
churns and no ``EXTRACTOR_VERSION`` bump is owed. Given a message's already-extracted values, the
model returns the verbatim justifying sentence; everything it returns is then validated against the
real body before anything is stored (ADR-054).

Why this exists at all, measured rather than assumed (2026-08-06, ``corpus/`` = 1271):

  * Phase 3's deterministic client-side search already paints **350 of 790 ledger rows (44%)**.
  * Of the 440 it leaves dark, **431 are absent from the email text in any form** — ISO-normalised
    deadlines and model paraphrases of the ask. No search can ever reach those.
  * On exactly that dark population, the model supplies a genuine, literal, reachable, unique
    sentence **70% of the time** (`action_requested` 14/16, `deadline` 3/3). On the rows Phase 3
    already paints it supplies a useless echo 89% of the time — which is why the echo rule below
    discards nothing: see :func:`validate_quote`.

Storage is ``out/evidence.jsonl``, keyed by ``message_id`` (ADR-054 §1). Not ``crm.db`` — rebuilt
whole on every sync. Not ``results.jsonl`` — body-free by contract.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from . import audit, classifier, llm
from .config import paths
from .envelope import parse_eml
from .identity import safe_filename

# The ledger keys, in the order «Registo do fio» renders them. One locate call covers whichever of
# these a message actually has — the model is only ever asked about values that exist.
LOCATE_KEYS = ["money", "deadline", "product_or_service", "action_requested",
               "client_name", "nif", "iban"]

# The value-in-quote gate, per field (plan §3.3(b), measured on the spike). ON where a hallucinated
# value would be dangerous and the value is always written out in full; OFF where the value is
# legitimately a normalisation or a paraphrase of the sentence — applied globally this gate would
# ship a feature that never once highlights a prazo (deadline scored 0/4).
VALUE_IN_QUOTE_KEYS = {"money", "nif", "iban", "client_name"}

# How much body the model sees. The spike used 12000; median cleaned body is ~2 KB, p90 ~9 KB.
BODY_CHARS = 12000
# A quote longer than this is a paragraph, not a sentence — the model was asked for one sentence and
# a runaway answer paints half the message. Rejected as `too_long` rather than silently truncated.
MAX_QUOTE_CHARS = 400
# Raised for this call only: up to seven quotes come back in one object and the live default of 1024
# would truncate the JSON mid-object — which `llm.call` cannot distinguish from a transient failure,
# so it would retry `max_retries` times and bill every one of them.
MAX_TOKENS = 2048

SIDECAR_NAME = "evidence.jsonl"

# Gemini's OpenAPI subset and the Anthropic tool dialect — written twice, next to each other, because
# `llm.call` hands `schema=` only to Gemini and `tool=` only to Anthropic and silently drops the one
# the active provider does not use.
GEMINI_LOCATE_SCHEMA = {
    "type": "object",
    "properties": {k: {"type": "string", "nullable": True} for k in LOCATE_KEYS},
}
LOCATE_TOOL = {
    "name": "locate_evidence",
    "description": "Return the verbatim sentence of the email that justifies each extracted value.",
    "input_schema": {
        "type": "object",
        "properties": {k: {"type": ["string", "null"]} for k in LOCATE_KEYS},
        "required": [],
    },
}

_WS = re.compile(r"\s+")


def collapse(s: str) -> str:
    """Whitespace-collapsed form. The one lossless difference between what the model copies and what
    the email holds: hard wraps, ``\\r\\n``, and runs of spaces. Normalising it recovers a known
    difference rather than guessing at one."""
    return _WS.sub(" ", str(s or "")).strip()


def _collapse_map(s: str) -> tuple[str, list[int]]:
    """``(collapsed, index_map)`` where ``index_map[i]`` is the offset in ``s`` of collapsed char i.

    Carries one extra entry for the end position, so a match at the very end of the string can be
    closed without a bounds check.
    """
    out: list[str] = []
    idx: list[int] = []
    i, n = 0, len(s)
    # Leading whitespace is dropped, matching collapse()'s strip().
    while i < n and s[i].isspace():
        i += 1
    while i < n:
        c = s[i]
        if c.isspace():
            j = i
            while j < n and s[j].isspace():
                j += 1
            if j < n:                      # a trailing run collapses to nothing, not to a space
                out.append(" ")
                idx.append(i)
            i = j
            continue
        out.append(c)
        idx.append(i)
        i += 1
    idx.append(n)
    return "".join(out), idx


def find_spans(haystack: str, needle: str) -> list[tuple[int, int]]:
    """Every whitespace-tolerant occurrence of ``needle`` in ``haystack``, as offsets into
    ``haystack``. Case- and accent-SENSITIVE on purpose: the model was told to copy character by
    character, and folding here would let a quote it actually rewrote pass validation."""
    hay, imap = _collapse_map(haystack)
    ndl = collapse(needle)
    if not hay or not ndl:
        return []
    out: list[tuple[int, int]] = []
    i = hay.find(ndl)
    while i >= 0 and len(out) < 50:
        out.append((imap[i], imap[i + len(ndl)]))
        i = hay.find(ndl, i + max(1, len(ndl)))
    return out


def validate_quote(key: str, value: Any, quote: Any, body: str) -> tuple[Optional[str], str]:
    """``(accepted_quote, reason)``. ``accepted_quote`` is the EMAIL's own text, never the model's.

    The stack, in order, each step earned from the spike:

    1. **Reject ``quote == value``** (an *echo*). This looks like it discards half of every run, and
       on the pooled spike it does — but an echo can only occur when the value is present in the
       text, and when it is, the Phase-3 client already paints it by searching for the value itself.
       Searching for an echoed quote is *the same search*. Rejecting it removes a duplicate, never a
       result. Measured: on rows Phase 3 leaves dark the echo rate is 27%, not 50%.
    2. **Match whitespace-tolerantly against the real body** — recovers hard-wrap rewraps, which are
       a lossless difference. A quote that is not in the body is a fabrication and is discarded.
    3. **Reject a quote occurring more than once**: the highlight would paint every copy, including
       the one inside a quoted reply, and the reader cannot tell which sentence was the evidence.
    4. **Value-in-quote, per field only** (:data:`VALUE_IN_QUOTE_KEYS`).

    On success the stored quote is the body's **own substring** at the matched span, not the string
    the model typed. They differ only by whitespace here, but storing the email's text means nothing
    downstream can ever paint a character the sender did not write.
    """
    q = "" if quote is None else str(quote)
    if not q.strip():
        return None, "absent"
    if len(q) > MAX_QUOTE_CHARS:
        return None, "too_long"
    if collapse(q) == collapse(value):
        return None, "echo"
    spans = find_spans(body, q)
    if not spans:
        return None, "not_in_body"
    if len(spans) > 1:
        return None, "not_unique"
    s, e = spans[0]
    exact = body[s:e]
    if key in VALUE_IN_QUOTE_KEYS and collapse(value).lower() not in collapse(exact).lower():
        return None, "value_not_in_quote"
    return exact, "ok"


def load_evidence(out_dir: Path) -> dict[str, Any]:
    """``message_id -> row`` from ``out/evidence.jsonl``. Tolerant by design, unlike the three
    jobspecs loaders it is modelled on: a malformed line is skipped, not raised on.

    That is not stylistic. ``_load_jobspecs`` runs at ``create_app`` time, i.e. **before** the
    lifespan, so an unparseable sidecar there makes the app unconstructable and ``/healthz`` never
    answers — the container crash-loops. This store is written by an LLM pass and carries free text;
    it is the last file that should be able to take the app down.
    """
    out: dict[str, Any] = {}
    p = out_dir / SIDECAR_NAME
    if not p.exists():
        return out
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        if not line.strip():
            continue
        try:
            j = json.loads(line)
        except ValueError:
            continue
        mid = j.get("message_id") if isinstance(j, dict) else None
        if mid:
            out[mid] = j
    return out


def corpus_resolver(corpus_dir: Path) -> Callable[[str], Optional[Path]]:
    """``message_id -> .eml`` the ADR-053 way: compute first, scan once, never scan again.

    A deliberate second copy of ``specbuild``'s private closure rather than a refactor of it —
    ``specbuild``'s is pinned by an ``os.replace`` spy that asserts exactly one atomic write per
    rebuild, and this pass must not appear anywhere inside that call.
    """
    fallback: dict[str, Path] | None = None

    def _for(mid: str) -> Optional[Path]:
        nonlocal fallback
        cand = corpus_dir / safe_filename(mid)
        if cand.exists():
            return cand
        if fallback is None:
            fallback = {}
            for eml in corpus_dir.glob("*.eml"):
                try:
                    fallback[parse_eml(eml.read_bytes())["message_id"]] = eml
                except Exception:  # noqa: BLE001 — one unparseable .eml must not abort the pass
                    pass
        return fallback.get(mid)

    return _for


def build_message(subject: str, values: dict[str, Any], body: str) -> str:
    return (f"ASSUNTO: {subject}\n\nVALORES JÁ EXTRAÍDOS (localiza cada um):\n"
            + "\n".join(f"  {k} = {v}" for k, v in values.items())
            + f"\n\n--- CORPO DO EMAIL ---\n{body[:BODY_CHARS]}")


def values_of(r: dict[str, Any]) -> dict[str, Any]:
    """The extracted values worth locating for one ``results.jsonl`` row."""
    ents = r.get("entities") or {}
    if not isinstance(ents, dict):
        return {}
    return {k: ents[k] for k in LOCATE_KEYS if ents.get(k)}


def locate_message(r: dict[str, Any], eml: Optional[Path], *, client: Any,
                   cfg: dict[str, Any], playbook: str,
                   audit_log: Optional[Path] = None) -> dict[str, Any]:
    """One message -> one ``evidence.jsonl`` row. Never raises: an LLM failure is recorded on the row.

    A row is written even when nothing is accepted. Storing nothing on failure — which the plan's
    item 4.5 asked for — collides with the incremental gate, which keys on the *presence* of a row:
    a message with no row is indistinguishable from one never attempted, so every sync would re-bill
    the model for every message that ever failed, forever.
    """
    mid = r["message_id"]
    values = values_of(r)
    row: dict[str, Any] = {"message_id": mid, "quotes": {}, "rejected": {}}
    if not values:
        return row
    if eml is None:
        row["rejected"] = {k: "no_eml" for k in values}
        return row
    try:
        env = parse_eml(eml.read_bytes())
    except Exception as exc:  # noqa: BLE001
        row["error"] = f"{type(exc).__name__}"
        return row
    body = env.get("body_text") or ""
    if not body.strip():
        row["rejected"] = {k: "empty_body" for k in values}
        return row
    try:
        got = llm.call(client, cfg, playbook,
                       build_message(env.get("subject") or r.get("subject") or "", values, body),
                       schema=GEMINI_LOCATE_SCHEMA, tool=LOCATE_TOOL, temperature=0.0)
    except Exception as exc:  # noqa: BLE001 — one bad locate must not sink the whole pass
        row["error"] = f"{type(exc).__name__}"
        if audit_log is not None:
            # ADR-054: ids, types and counts ONLY. Never the quote, never the value, never the body —
            # out/audit.jsonl is served by no route, is outside the ADR-045 visibility gate, is not
            # backed up, and nothing prunes it.
            audit.log(audit_log, "locate_failed", "locate",
                      {"message_id": mid, "error": type(exc).__name__, "keys": len(values)})
        return row
    if not isinstance(got, dict):
        row["error"] = "BadShape"
        return row
    for k, v in values.items():
        quote, reason = validate_quote(k, v, got.get(k), body)
        if quote is None:
            row["rejected"][k] = reason
        else:
            row["quotes"][k] = quote
    return row


def rebuild_evidence(settings: dict[str, Any], *, incremental: bool = True, client: Any = None,
                     log: Optional[Callable[[str], None]] = None,
                     only: Optional[set[str]] = None,
                     tier: Optional[str] = None) -> dict[str, int]:
    """(Re)build ``out/evidence.jsonl``. Returns ``{located, kept, quotes, rejected, failed, total}``.

    Shaped like :func:`specbuild.rebuild_jobspecs` — incremental gate, ``only=`` scoping, temp file +
    ``os.replace`` — with two deliberate departures, each stated because copying the precedent blindly
    would be wrong here:

    * **This is a top-level function, never called from inside ``rebuild_jobspecs``.**
      ``tests/test_specbuild.py`` spies ``os.replace`` and asserts exactly ONE atomic write per
      rebuild, and ``specbuild.os`` *is* the stdlib module, so the spy is process-wide.
    * **The output is a union, not a projection.** ``rebuild_jobspecs`` rebuilds its list purely from
      the current ``results.jsonl``, so a row whose source stops matching is silently dropped. Every
      row here cost an LLM call; a re-triage that momentarily empties a message's entities would
      throw that money away. Rows for out-of-scope ids are carried through unchanged, in their
      existing order, after the in-scope ones.

    Never raises on a missing LLM client — with no credentials the pass is a no-op that keeps every
    existing row, rather than an error that fails the sync it hangs off.
    """
    p = paths(settings, settings["__settings_path__"])
    out_dir = p["out_dir"]
    base = Path(settings["__settings_path__"]).parents[1]
    counts = {"located": 0, "kept": 0, "quotes": 0, "rejected": 0, "failed": 0, "total": 0}

    results_path = out_dir / "results.jsonl"
    if not results_path.exists():
        return counts
    rows = [json.loads(x) for x in results_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    # `results.jsonl` is APPEND-only: a re-triage adds a line instead of replacing one, so a message
    # can appear several times. Fold to the freshest line per id — the same last-wins convention
    # `report.py` and `specbuild.py` use. Iterating the raw lines instead pays the LLM twice for one
    # message and writes two rows whose winner is then decided by file order; the first full backfill
    # did exactly that for 43 of 806 messages before this fold existed.
    latest: dict[str, dict[str, Any]] = {}
    for r in rows:
        mid = r.get("message_id")
        if mid and values_of(r):
            latest[mid] = r          # re-assignment keeps the FIRST position, so order stays stable
    targets = list(latest.values())
    existing = load_evidence(out_dir)

    todo = [r for r in targets
            if not (incremental and r["message_id"] in existing
                    and not (only and r["message_id"] in only))]
    if todo and client is None:
        try:
            client = classifier.make_client(settings)
        except Exception as exc:  # noqa: BLE001 — no creds → keep what we have, don't break the sync
            if log:
                log(f"locate skipped (no LLM client: {type(exc).__name__})")
            todo = []
    playbook = ""
    if todo:
        try:
            playbook = (base / "config" / "locate_playbook.md").read_text(encoding="utf-8")
        except OSError as exc:
            if log:
                log(f"locate skipped (no playbook: {type(exc).__name__})")
            todo = []
    # `with_tier` returns the SHARED settings dict on the no-op path, so a copy is taken here rather
    # than mutated in place — otherwise raising max_tokens would repoint every later call in the run.
    cfg = {**llm.with_tier(settings["llm"], tier), "max_tokens": MAX_TOKENS}
    todo_ids = {r["message_id"] for r in todo}
    file_for = corpus_resolver(p["corpus_dir"])

    out_rows: list[dict[str, Any]] = []
    for r in targets:
        mid = r["message_id"]
        if mid not in todo_ids:
            if mid in existing:
                out_rows.append(existing[mid])
                counts["kept"] += 1
            continue
        row = locate_message(r, file_for(mid), client=client, cfg=cfg, playbook=playbook,
                             audit_log=p["audit_log"])
        out_rows.append(row)
        counts["located"] += 1
        counts["quotes"] += len(row.get("quotes") or {})
        counts["rejected"] += len(row.get("rejected") or {})
        counts["failed"] += int(bool(row.get("error")))
        if log and counts["located"] % 25 == 0:
            log(f"locate {counts['located']}/{len(todo)} · {counts['quotes']} citações")

    seen = {r["message_id"] for r in out_rows}
    for mid, row in existing.items():                 # paid-for rows that left the source list
        if mid not in seen:
            out_rows.append(row)
    write_sidecar(out_dir / SIDECAR_NAME, out_rows)
    counts["total"] = len(out_rows)
    return counts


def write_sidecar(dest: Path, rows: Iterable[dict[str, Any]]) -> None:
    """Temp file + ``os.replace``. A crash mid-write used to truncate ``jobspecs.jsonl`` to a prefix,
    and the lost entries are only recoverable by re-spending the LLM pass that produced them — which
    is exactly as true here. The suffix is ``.writing``, NOT ``.building``: ``test_specbuild.py``
    asserts no ``*.building`` file survives a jobspec rebuild, and these passes run back-to-back with
    it on the same sync. Shared by both sidecars (``narrate`` imports it) so there is one atomic-write
    shape to get right, not two."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".jsonl.writing")
    tmp.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    os.replace(tmp, dest)
