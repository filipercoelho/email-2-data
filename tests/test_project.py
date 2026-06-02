"""Project layer: store CRUD, cross-thread merge policy, seeding, stage lifecycle, export.

In-memory SQLite (workspace.SCHEMA) — fast, isolated, no corpus. A tiny fake Workspace exposes the
``merge`` method the orchestration helpers call; the real one is exercised in the webapp test.
"""

from __future__ import annotations

import sqlite3

from email2data import export as exp, jobspec as js, project as p
from email2data.workspace import SCHEMA, Workspace


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def _spec(mid: str, *, job=None, items=None, date_subject="") -> js.JobSpec:
    """A JobSpec with explicit SpecFields (value, source) for merge tests."""
    job_fields = {k: js.SpecField() for k in js.JOB_KEYS}
    for k, (v, src) in (job or {}).items():
        job_fields[k] = js.SpecField(v, src, src == "user")
    it_list = []
    for it in (items or [{}]):
        d = {k: js.SpecField() for k in js.ITEM_KEYS}
        for k, (v, src) in it.items():
            d[k] = js.SpecField(v, src, src == "user")
        it_list.append(d)
    return js.JobSpec(message_id=mid, subject=date_subject, job_fields=job_fields, items=it_list)


# ---------------------------------------------------------------------------
# Store CRUD
# ---------------------------------------------------------------------------

def test_create_and_ids_increment():
    store = p.ProjectStore(_conn())
    assert store.create("A") == "p-0001"
    assert store.create("B") == "p-0002"
    assert [pr["title"] for pr in store.list()] == ["B", "A"]  # updated_ts DESC


def test_thread_attach_detach_roundtrip():
    store = p.ProjectStore(_conn())
    pid = store.create("A")
    store.attach_thread(pid, "root-1")
    store.attach_thread(pid, "root-2")
    store.attach_thread(pid, "root-1")  # idempotent
    assert store.threads_for(pid) == ["root-1", "root-2"]
    store.detach_thread(pid, "root-1")
    assert store.threads_for(pid) == ["root-2"]


def test_set_clear_field_and_provenance():
    store = p.ProjectStore(_conn())
    pid = store.create("A")
    store.set_field(pid, "deadline", "2026-07-01", source_mid="m1")
    assert store.fields_for(pid) == {"deadline": ("2026-07-01", "m1")}
    store.clear_field(pid, "deadline")
    assert store.fields_for(pid) == {}


# ---------------------------------------------------------------------------
# Cross-thread merge policy
# ---------------------------------------------------------------------------

def test_job_field_precedence_user_beats_llm():
    # two messages: m1 LLM deadline, m2 user deadline -> user wins regardless of order
    specs = [_spec("m1", job={"deadline": ("2026-07-01", "llm")}),
             _spec("m2", job={"deadline": ("2026-08-15", "user")})]
    job, prov, conflicts = p.merge_job_fields(specs)
    assert job["deadline"].value == "2026-08-15" and job["deadline"].source == "user"
    assert prov["deadline"] == "m2"


def test_job_field_recency_breaks_ties():
    # equal source rank (both llm), oldest->newest order => later message wins
    specs = [_spec("m1", job={"budget": ("100", "llm")}),
             _spec("m2", job={"budget": ("200", "llm")})]
    job, prov, conflicts = p.merge_job_fields(specs)
    assert job["budget"].value == "200" and prov["budget"] == "m2"
    assert "budget" in conflicts and {v for v, _ in conflicts["budget"]} == {"100", "200"}


def test_project_field_overrides_auto_merge():
    specs = [_spec("m1", job={"deadline": ("2026-07-01", "user")})]
    spec, rd, prov, _c = p.canonical_spec(
        "p-1", "T", "Cliente", 1, specs, {"deadline": ("2026-09-09", "")})
    assert spec.job_fields["deadline"].value == "2026-09-09"  # project decision is final
    assert spec.job_fields["deadline"].confirmed is True
    assert prov["deadline"] == "user"


def test_items_are_project_owned_not_unioned():
    # two messages each list one (different) item; canonical items come ONLY from project_fields
    specs = [_spec("m1", items=[{"item": ("placas", "llm")}]),
             _spec("m2", items=[{"item": ("expositores", "llm")}])]
    spec, _rd, _prov, _c = p.canonical_spec(
        "p-1", "T", "C", 1, specs, {"item#0": ("placas acrílico", "m1")})
    assert len(spec.items) == 1                                   # NOT unioned to 2
    assert spec.items[0]["item"].value == "placas acrílico"


# ---------------------------------------------------------------------------
# Orchestration: seeding + canonical via a real Workspace
# ---------------------------------------------------------------------------

class _CrmStub:
    """Minimal crm_store: thread(root) -> messages; thread_root_for(mid) -> root."""
    def __init__(self, threads, roots):
        self._threads = threads   # {root: [message_id, ...]}
        self._roots = roots       # {message_id: root}

    def thread(self, root):
        return [{"message_id": m} for m in self._threads.get(root, [])]

    def thread_root_for(self, mid):
        return self._roots.get(mid)


def test_seed_items_from_message_then_locked(tmp_path):
    ws = Workspace(tmp_path / "w.db").connect()
    store = p.ProjectStore(ws._conn)
    src = js.build_jobspec(
        {"message_id": "m1", "subject": "s", "counterparty": "CLIENT",
         "entities": {"product_or_service": "troféus"}},
        {"attachments": [], "subject": "s", "body_text": ""}).to_dict()
    jobspecs = {"m1": src, "m2": dict(src, message_id="m2")}
    pid = store.create("Troféus")
    assert p.seed_items_from(store, ws, jobspecs, pid, "m1") is True
    assert store.fields_for(pid).get("item#0", ("",))[0] == "troféus"
    # second seed is a no-op (items are curated from here on)
    assert p.seed_items_from(store, ws, jobspecs, pid, "m2") is False


def test_build_canonical_merges_across_threads(tmp_path):
    ws = Workspace(tmp_path / "w.db").connect()
    store = p.ProjectStore(ws._conn)
    j1 = js.build_jobspec({"message_id": "m1", "subject": "s1", "counterparty": "CLIENT",
                           "entities": {"deadline": "2026-07-01"}},
                          {"attachments": [], "subject": "s1", "body_text": ""}).to_dict()
    j2 = js.build_jobspec({"message_id": "m2", "subject": "s2", "counterparty": "CLIENT",
                           "entities": {"money": "500 EUR"}},
                          {"attachments": [], "subject": "s2", "body_text": ""}).to_dict()
    jobspecs = {"m1": j1, "m2": j2}
    crm = _CrmStub({"r1": ["m1"], "r2": ["m2"]}, {"m1": "r1", "m2": "r2"})
    pid = store.create("Job")
    store.attach_thread(pid, "r1")
    store.attach_thread(pid, "r2")
    spec, rd, prov, _c = p.build_canonical(store, ws, jobspecs, pid, crm)
    assert spec.job_fields["deadline"].value == "2026-07-01"   # from m1's thread
    assert spec.job_fields["budget"].value == "500 EUR"        # from m2's thread


# ---------------------------------------------------------------------------
# Stage lifecycle
# ---------------------------------------------------------------------------

def test_suggest_stage_advances_but_respects_terminal():
    assert p.suggest_stage("LEAD", {"estimable": False}, False) == "GATHERING"
    assert p.suggest_stage("GATHERING", {"estimable": True}, False) == "ESTIMABLE"
    assert p.suggest_stage("ESTIMABLE", {"estimable": True}, True) == "QUOTED"
    assert p.suggest_stage("WON", {"estimable": True}, True) == "WON"      # never overwritten
    assert p.suggest_stage("LOST", {"estimable": False}, False) == "LOST"


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def _estimable_project(tmp_path):
    ws = Workspace(tmp_path / "w.db").connect()
    store = p.ProjectStore(ws._conn)
    pid = store.create("Pergola", client_name="Sr. Silva")
    for k, v in {"deadline": "2026-07-01", "design_ready": "sim", "material_supplied_by": "us"}.items():
        store.set_field(pid, k, v)
    for k, v in {"item": "pergola", "dimensions": "3x4m", "material": "aço",
                 "thickness": "2mm", "process": "soldadura", "quantity": "1"}.items():
        store.set_field(pid, js.address(k, 0), v)
    store.set_item_count(pid, 1)
    return ws, store, pid


def test_build_payload_projectcreate_shape(tmp_path):
    ws, store, pid = _estimable_project(tmp_path)
    spec, rd, _p, _c = p.build_canonical(store, ws, {}, pid, None)
    payload = exp.build_payload(store.get(pid), spec, rd, ["r1"], ["m1"])
    assert payload["project_name"] == "Pergola"
    assert payload["cliente"] == "Sr. Silva"
    assert payload["status"] == "ATIVO" and payload["currency"] == "EUR"
    assert "pergola" in payload["descricao"]
    assert "Estimável (Gate-1): sim" in payload["notas"]


def test_json_adapter_writes_file(tmp_path):
    a = exp.JsonFileAdapter(tmp_path)
    res = a.export("p-0001", {"project_name": "X"})
    assert res.ok and (tmp_path / "exports" / "p-0001.json").exists()


def test_export_project_gates_then_records(tmp_path):
    ws, store, pid = _estimable_project(tmp_path)
    a = exp.JsonFileAdapter(tmp_path)
    res = exp.export_project(store, ws, {}, a, pid, crm_store=None)
    assert res.ok and store.get(pid)["external_id"] == res.external_id
    assert store.get(pid)["stage"] == "QUOTED"
    # re-export refused unless forced
    res2 = exp.export_project(store, ws, {}, a, pid, crm_store=None)
    assert not res2.ok and "already exported" in res2.detail
    assert exp.export_project(store, ws, {}, a, pid, crm_store=None, force=True).ok


def test_export_project_blocks_non_estimable(tmp_path):
    ws = Workspace(tmp_path / "w.db").connect()
    store = p.ProjectStore(ws._conn)
    pid = store.create("Incompleto")
    res = exp.export_project(store, ws, {}, exp.JsonFileAdapter(tmp_path), pid, crm_store=None)
    assert not res.ok and "not estimable" in res.detail


def test_materials_costing_adapter_posts(monkeypatch):
    captured = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"project_id": "PRJ-000007"}'

    def _fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["key"] = req.headers.get("X-api-key")
        captured["body"] = req.data
        return _Resp()

    monkeypatch.setattr(exp.urllib.request, "urlopen", _fake_urlopen)
    a = exp.MaterialsCostingAdapter("http://host:8080", "APK-X.sk_live_abc")
    res = a.export("p-0001", {"project_name": "X"})
    assert res.ok and res.external_id == "PRJ-000007"
    assert captured["url"] == "http://host:8080/api/projects"
    assert captured["key"] == "APK-X.sk_live_abc"


# ---------------------------------------------------------------------------
# Lifecycle maintenance: delete + archive-hide (Phase 3)
# ---------------------------------------------------------------------------

def test_delete_removes_project_and_owned_rows():
    store = p.ProjectStore(_conn())
    pid = store.create("A")
    store.attach_thread(pid, "root-1")
    store.set_field(pid, "deadline", "2026-07-01")
    assert store.delete(pid) is True
    assert store.get(pid) is None
    assert store.threads_for(pid) == []
    assert store.fields_for(pid) == {}
    assert store.delete(pid) is False           # already gone


def test_list_hides_archived_by_default():
    store = p.ProjectStore(_conn())
    a = store.create("A")
    store.create("B")
    store.set_stage(a, "ARCHIVED")
    assert [pr["title"] for pr in store.list()] == ["B"]                      # A hidden
    assert {pr["title"] for pr in store.list(include_archived=True)} == {"A", "B"}


# ---------------------------------------------------------------------------
# Durability: schema version + canonical-edit history (Phase 4)
# ---------------------------------------------------------------------------

def test_field_edits_are_audited():
    store = p.ProjectStore(_conn())
    pid = store.create("A")
    store.set_field(pid, "deadline", "2026-07-01", source_mid="m1")
    store.set_field(pid, "deadline", "2026-08-01")          # overwrite — prior value must survive in history
    store.clear_field(pid, "deadline")
    store.clear_field(pid, "deadline")                       # no-op clear of an absent field: not logged
    hist = store.field_history(pid, "deadline")
    assert [(h["op"], h["old_value"], h["new_value"]) for h in hist] == [
        ("set", None, "2026-07-01"),
        ("set", "2026-07-01", "2026-08-01"),
        ("clear", "2026-08-01", None),
    ]
    assert hist[0]["source_mid"] == "m1"


def test_workspace_stamps_schema_version(tmp_path):
    from email2data import workspace as wsmod
    ws = wsmod.Workspace(tmp_path / "w.db").connect()
    v = ws._conn.execute("PRAGMA user_version").fetchone()[0]
    assert v == wsmod.SCHEMA_VERSION >= 1
    ws.close()


# ---------------------------------------------------------------------------
# Integrity: dangling thread_roots (precious -> regenerable refs) (Phase 5)
# ---------------------------------------------------------------------------

class _FakeCrm:
    """Minimal CRM double: only the thread_roots in ``known`` resolve to interactions."""
    def __init__(self, known): self.known = set(known)
    def thread(self, root): return [{"message_id": root}] if root in self.known else []
    def thread_root_for(self, mid): return mid

def test_dangling_threads_detection():
    store = p.ProjectStore(_conn())
    pid = store.create("A")
    store.attach_thread(pid, "live-root")
    store.attach_thread(pid, "stale-root")          # not in the rebuilt CRM
    crm = _FakeCrm(known={"live-root"})
    assert p.dangling_threads(store, pid, crm) == ["stale-root"]
    assert p.dangling_threads(store, pid, None) == []   # degraded mode: never false-alarm
