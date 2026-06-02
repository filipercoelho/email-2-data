"""Project layer — group MANY email threads into ONE canonical job spec.

The rest of the pipeline works per-message (``JobSpec`` keyed by ``message_id``); a real estimating
job, though, is born when a LEAD arrives and then accretes information across several threads before
it is ready to cost. A **Project** is that first-class entity: it owns an explicit set of
``thread_root``s, merges everything known across them into one canonical spec, tracks a lifecycle
stage, and is eventually offloaded to an external estimating system (see ``export.py``).

State lives in the precious ``workspace.db`` (tables in ``workspace.SCHEMA``) so it survives triage
re-runs, exactly like ``decisions``/``reclassifications``. ``ProjectStore`` is pure CRUD over those
tables sharing the ``Workspace`` connection; the merge/seed/lifecycle logic is module functions.

**Cross-thread merge policy** (deliberate, see design):
  * job-level fields (deadline, budget, …) are scalar → auto-merged across messages by source
    precedence (user > llm > offline), later message winning ties.
  * line items are **project-owned**: ``n_items`` and every per-item value come ONLY from
    ``project_fields``. They are *seeded once* from a source message, then hand-curated — never
    auto-unioned across threads (that reliably mis-aligns distinct pieces).
``project_fields`` always overrides the auto-merge (the human's project-level decision is final).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from . import jobspec as js

# Lifecycle. LEAD arrives → GATHERING info → ESTIMABLE (Gate-1 passes) → QUOTED → WON|LOST; ARCHIVED
# is a manual retire. Terminal stages are human verdicts the auto-suggester must never overwrite.
STAGES = ["LEAD", "GATHERING", "ESTIMABLE", "QUOTED", "WON", "LOST", "ARCHIVED"]
TERMINAL_STAGES = frozenset({"QUOTED", "WON", "LOST", "ARCHIVED"})

# Source precedence for the job-level auto-merge. Higher wins; ties broken by message recency.
_SOURCE_RANK = {"user": 3, "llm": 2, "offline": 1, "": 0}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def suggest_stage(current: str, readiness: dict[str, Any], exported: bool) -> str:
    """Nudge the stage forward from signals, but NEVER overwrite a human-set terminal stage.

    Estimable + already pushed out → QUOTED; estimable → ESTIMABLE; anything with a thread in
    progress → GATHERING; otherwise leave as-is (a fresh project stays LEAD).
    """
    if current in TERMINAL_STAGES:
        return current
    if exported:
        return "QUOTED"
    if readiness.get("estimable"):
        return "ESTIMABLE"
    if current == "LEAD":
        return "GATHERING"
    return current


class ProjectStore:
    """CRUD over the projects / project_threads / project_fields tables.

    Wraps an EXISTING connection (share the one from a connected ``Workspace``): single-user, serial
    access, no locking concerns. Tests pass an in-memory connection that has run ``workspace.SCHEMA``.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # -- projects -------------------------------------------------------------

    def _next_id(self) -> str:
        rows = self._conn.execute("SELECT project_id FROM projects").fetchall()
        n = 0
        for r in rows:
            pid = r["project_id"] if isinstance(r, sqlite3.Row) else r[0]
            if isinstance(pid, str) and pid.startswith("p-") and pid[2:].isdigit():
                n = max(n, int(pid[2:]))
        return f"p-{n + 1:04d}"

    def create(self, title: str, client_email: Optional[str] = None,
               client_name: Optional[str] = None, stage: str = "LEAD") -> str:
        ts = _now()
        pid = self._next_id()
        self._conn.execute(
            "INSERT INTO projects(project_id, title, client_email, client_name, stage, n_items,"
            " created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?)",
            (pid, title, client_email, client_name, stage, 1, ts, ts),
        )
        self._conn.commit()
        return pid

    def get(self, pid: str) -> Optional[dict[str, Any]]:
        row = self._conn.execute("SELECT * FROM projects WHERE project_id=?", (pid,)).fetchone()
        return dict(row) if row else None

    def list(self, include_archived: bool = False) -> list[dict[str, Any]]:
        """Projects newest-first. ARCHIVED ones are hidden by default (soft-retire); pass
        ``include_archived=True`` to see them too."""
        where = "" if include_archived else "WHERE stage != 'ARCHIVED'"
        rows = self._conn.execute(
            f"SELECT * FROM projects {where} ORDER BY updated_ts DESC, project_id DESC").fetchall()
        return [dict(r) for r in rows]

    def delete(self, pid: str) -> bool:
        """Hard-delete a project and every row it owns (threads + canonical fields + field history).
        For removing mistakes/duplicates; ARCHIVED is the soft-retire that keeps the record but hides
        it from lists. Returns False if the project did not exist."""
        if self.get(pid) is None:
            return False
        self._conn.execute("DELETE FROM project_field_history WHERE project_id=?", (pid,))
        self._conn.execute("DELETE FROM project_fields WHERE project_id=?", (pid,))
        self._conn.execute("DELETE FROM project_threads WHERE project_id=?", (pid,))
        self._conn.execute("DELETE FROM projects WHERE project_id=?", (pid,))
        self._conn.commit()
        return True

    def _touch(self, pid: str) -> None:
        self._conn.execute("UPDATE projects SET updated_ts=? WHERE project_id=?", (_now(), pid))

    def set_stage(self, pid: str, stage: str) -> None:
        assert stage in STAGES, f"unknown stage: {stage}"
        self._conn.execute("UPDATE projects SET stage=?, updated_ts=? WHERE project_id=?",
                           (stage, _now(), pid))
        self._conn.commit()

    def set_item_count(self, pid: str, n: int) -> None:
        self._conn.execute("UPDATE projects SET n_items=?, updated_ts=? WHERE project_id=?",
                           (max(1, int(n)), _now(), pid))
        self._conn.commit()

    def set_external(self, pid: str, external_id: str, ts: str = "") -> None:
        self._conn.execute(
            "UPDATE projects SET external_id=?, external_ts=?, updated_ts=? WHERE project_id=?",
            (external_id, ts or _now(), _now(), pid))
        self._conn.commit()

    # -- threads --------------------------------------------------------------

    def attach_thread(self, pid: str, thread_root: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO project_threads(project_id, thread_root, added_ts) VALUES (?,?,?)",
            (pid, thread_root, _now()))
        self._touch(pid)
        self._conn.commit()

    def detach_thread(self, pid: str, thread_root: str) -> None:
        self._conn.execute(
            "DELETE FROM project_threads WHERE project_id=? AND thread_root=?", (pid, thread_root))
        self._touch(pid)
        self._conn.commit()

    def threads_for(self, pid: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT thread_root FROM project_threads WHERE project_id=? ORDER BY added_ts ASC",
            (pid,)).fetchall()
        return [r["thread_root"] for r in rows]

    # -- canonical fields -----------------------------------------------------

    def _field_value(self, pid: str, addr: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT value FROM project_fields WHERE project_id=? AND field=?", (pid, addr)).fetchone()
        if row is None:
            return None
        return row["value"] if isinstance(row, sqlite3.Row) else row[0]

    def _log_field(self, pid: str, addr: str, op: str, old: Optional[str],
                   new: Optional[str], source_mid: str) -> None:
        self._conn.execute(
            "INSERT INTO project_field_history(project_id, field, op, old_value, new_value, source_mid, ts)"
            " VALUES (?,?,?,?,?,?,?)", (pid, addr, op, old, new, source_mid, _now()))

    def set_field(self, pid: str, addr: str, value: str, source_mid: str = "") -> None:
        old = self._field_value(pid, addr)
        self._conn.execute(
            "INSERT INTO project_fields(project_id, field, value, source_mid, ts) VALUES (?,?,?,?,?)"
            " ON CONFLICT(project_id, field) DO UPDATE SET"
            "  value=excluded.value, source_mid=excluded.source_mid, ts=excluded.ts",
            (pid, addr, value, source_mid, _now()))
        self._log_field(pid, addr, "set", old, value, source_mid)
        self._touch(pid)
        self._conn.commit()

    def clear_field(self, pid: str, addr: str) -> None:
        old = self._field_value(pid, addr)
        self._conn.execute("DELETE FROM project_fields WHERE project_id=? AND field=?", (pid, addr))
        if old is not None:  # only log a real removal, not a clear of an absent field
            self._log_field(pid, addr, "clear", old, None, "")
        self._touch(pid)
        self._conn.commit()

    def field_history(self, pid: str, addr: Optional[str] = None) -> list[dict[str, Any]]:
        """Append-only audit of canonical edits, oldest-first. Filter to one address with ``addr``."""
        sql = ("SELECT field, op, old_value, new_value, source_mid, ts FROM project_field_history"
               " WHERE project_id=?")
        params: list[Any] = [pid]
        if addr is not None:
            sql += " AND field=?"
            params.append(addr)
        sql += " ORDER BY ts ASC, rowid ASC"
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def fields_for(self, pid: str) -> dict[str, tuple[str, str]]:
        """{address: (value, source_mid)} for this project's canonical decisions."""
        rows = self._conn.execute(
            "SELECT field, value, source_mid FROM project_fields WHERE project_id=?", (pid,)).fetchall()
        return {r["field"]: (r["value"], r["source_mid"] or "") for r in rows}

    def has_item_fields(self, pid: str) -> bool:
        for addr in self.fields_for(pid):
            if js.parse_address(addr)[1] is not None:
                return True
        return False

    def remove_item(self, pid: str, index: int) -> None:
        """Drop line item ``index`` from the canonical spec: delete its per-item fields, shift higher
        indices down by one (keep addresses contiguous), and decrement n_items. Mirrors
        ``Workspace.remove_item`` but on ``project_fields``."""
        fields = self.fields_for(pid)
        per_item: dict[int, dict[str, str]] = {}
        for addr, (value, _src) in fields.items():
            base, i = js.parse_address(addr)
            if i is not None:
                per_item.setdefault(i, {})[base] = value
        for i in sorted(per_item):
            for base in per_item[i]:
                self.clear_field(pid, js.address(base, i))
        for i, kv in per_item.items():
            if i == index:
                continue
            new_i = i - 1 if i > index else i
            for base, value in kv.items():
                self.set_field(pid, js.address(base, new_i), value)
        proj = self.get(pid)
        n = (proj["n_items"] if proj else 1) or 1
        self.set_item_count(pid, max(1, n - 1))


# -----------------------------------------------------------------------------
# Cross-thread merge (pure)
# -----------------------------------------------------------------------------

def merge_job_fields(specs: list[js.JobSpec]) -> tuple[dict[str, js.SpecField], dict[str, str],
                                                       dict[str, list[tuple[str, str]]]]:
    """Auto-merge job-level fields across per-message specs (ordered oldest→newest).

    Returns (job_fields, provenance, conflicts):
      * job_fields — winning SpecField per JOB_KEY (preserves the winner's source/confirmed).
      * provenance — {key: message_id that supplied the winning value}.
      * conflicts  — {key: [(value, message_id), …]} when ≥2 DISTINCT non-empty values tie at the
        winning precedence rank (surfaced, never silently dropped).
    """
    job: dict[str, js.SpecField] = {k: js.SpecField() for k in js.JOB_KEYS}
    provenance: dict[str, str] = {}
    best_rank: dict[str, int] = {k: 0 for k in js.JOB_KEYS}
    candidates: dict[str, list[tuple[str, str]]] = {k: [] for k in js.JOB_KEYS}
    for spec in specs:  # oldest→newest: later message wins ties (>=)
        for k in js.JOB_KEYS:
            fld = spec.job_fields.get(k)
            if not (fld and fld.value):
                continue
            rank = _SOURCE_RANK.get(fld.source, 0)
            candidates[k].append((fld.value, spec.message_id))
            if rank >= best_rank[k]:
                best_rank[k] = rank
                job[k] = js.SpecField(fld.value, fld.source, fld.confirmed)
                provenance[k] = spec.message_id
    conflicts = {
        k: cands for k, cands in candidates.items()
        if len({v for v, _ in cands}) > 1
    }
    return job, provenance, conflicts


def canonical_spec(pid: str, title: str, client_name: str, n_items: int,
                   msg_specs: list[js.JobSpec],
                   project_fields: dict[str, tuple[str, str]]
                   ) -> tuple[js.JobSpec, dict[str, Any], dict[str, str], dict[str, list]]:
    """Assemble the project's one canonical spec + Gate-1 readiness from all its message specs.

    ``msg_specs`` must be ordered oldest→newest. ``project_fields`` (the authoritative human layer)
    overrides the auto-merge for every address it names. Line items are project-owned: ``n_items``
    empty items, filled ONLY from ``project_fields`` ``#i`` addresses. Returns
    (spec, readiness, provenance, conflicts).
    """
    job, provenance, conflicts = merge_job_fields(msg_specs)
    items = [{k: js.SpecField() for k in js.ITEM_KEYS} for _ in range(max(1, n_items))]
    spec = js.JobSpec(
        message_id=pid, subject=title,
        counterparty=client_name or "",
        has_attachment=any(s.has_attachment for s in msg_specs),
        attachment_names=[n for s in msg_specs for n in s.attachment_names],
        job_fields=job, items=items,
    )
    # Authoritative overlay: project-level human decisions win over the auto-merge, for job AND items.
    for addr, (value, source_mid) in project_fields.items():
        js.confirm(spec, addr, value)
        provenance[addr] = source_mid or "user"
    return spec, js.readiness(spec), provenance, conflicts


# -----------------------------------------------------------------------------
# Orchestration — ties ProjectStore + Workspace + jobspecs + CrmStore together
# -----------------------------------------------------------------------------

def _merged_spec_for(ws, jobspecs: dict[str, Any], mid: str) -> Optional[js.JobSpec]:
    """Per-message spec with its own human confirmations overlaid (reuses Workspace.merge)."""
    j = jobspecs.get(mid)
    if j is None:
        return None
    spec, _ = ws.merge(j)
    return spec


def message_ids_for(store: ProjectStore, pid: str, crm_store=None) -> list[str]:
    """All message_ids across the project's attached threads, oldest→newest.

    With a CRM store, each thread_root expands to its sibling messages (crm.thread is date-ordered).
    Without one, the attached roots ARE the message_ids (degraded single-thread mode).
    """
    roots = store.threads_for(pid)
    if crm_store is None:
        return roots
    mids: list[str] = []
    seen: set[str] = set()
    for root in roots:
        for row in crm_store.thread(root):
            mid = row["message_id"]
            if mid not in seen:
                seen.add(mid)
                mids.append(mid)
    return mids


def build_canonical(store: ProjectStore, ws, jobspecs: dict[str, Any], pid: str, crm_store=None,
                    *, mids: Optional[list[str]] = None):
    """Full canonical spec for a project: gather messages → per-message merge → cross-thread merge.

    Pass ``mids`` to reuse an already-computed message-id list (the project view needs it for its
    response anyway) and skip a second CRM thread-expansion pass."""
    proj = store.get(pid)
    if proj is None:
        raise KeyError(pid)
    if mids is None:
        mids = message_ids_for(store, pid, crm_store)
    specs = [s for s in (_merged_spec_for(ws, jobspecs, m) for m in mids) if s is not None]
    return canonical_spec(
        pid, proj["title"], proj.get("client_name") or "", proj.get("n_items") or 1,
        specs, store.fields_for(pid),
    )


def dangling_threads(store: ProjectStore, pid: str, crm_store=None) -> list[str]:
    """Attached thread_roots that have NO interaction in the current CRM — i.e. precious→regenerable
    references that broke when crm.db was last rebuilt. These are silently dropped by
    ``message_ids_for``, so surfacing them is the only signal the project quietly lost messages.

    Returns [] when ``crm_store`` is None: in degraded single-thread mode the attached roots ARE the
    message_ids, so nothing can dangle and we must not raise a false alarm."""
    if crm_store is None:
        return []
    return [root for root in store.threads_for(pid) if not crm_store.thread(root)]


def seed_items_from(store: ProjectStore, ws, jobspecs: dict[str, Any], pid: str, mid: str) -> bool:
    """Seed the canonical line items from one source message (seed + curate).

    No-op if the project already has any per-item project_fields — items are curated from then on.
    Returns True if seeding happened.
    """
    if store.has_item_fields(pid):
        return False
    spec = _merged_spec_for(ws, jobspecs, mid)
    if spec is None or not spec.items:
        return False
    for i, item in enumerate(spec.items):
        for k in js.ITEM_KEYS:
            fld = item.get(k)
            if fld and fld.value:
                store.set_field(pid, js.address(k, i), fld.value, source_mid=mid)
    store.set_item_count(pid, len(spec.items))
    return True


def resolve_thread_root(crm_store, ref: str) -> str:
    """Map a message_id to its thread_root via the CRM; fall back to the ref itself (also handles
    the case where ``ref`` is already a thread_root)."""
    if crm_store is None:
        return ref
    root = crm_store.thread_root_for(ref)
    return root or ref
