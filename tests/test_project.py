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
    # A clean precedence win is NOT a conflict (supersession, not contradiction) — ADR-015.
    assert "deadline" not in conflicts


def test_cross_rank_supersession_is_not_a_conflict():
    # A user value (rank 3) cleanly supersedes a stale offline value (rank 1): precedence
    # resolves it, so it must NOT be flagged as a conflict — the live over-firing bug we fixed.
    specs = [_spec("m1", job={"deadline": ("2026-07-01", "offline")}),
             _spec("m2", job={"deadline": ("2026-08-15", "user")})]
    _job, _prov, conflicts = p.merge_job_fields(specs)
    assert conflicts == {}


def test_job_field_recency_breaks_ties():
    # equal source rank (both llm), oldest->newest order => later message wins
    specs = [_spec("m1", job={"budget": ("100", "llm")}),
             _spec("m2", job={"budget": ("200", "llm")})]
    job, prov, conflicts = p.merge_job_fields(specs)
    assert job["budget"].value == "200" and prov["budget"] == "m2"
    # Genuine contradiction: two equal-authority (llm vs llm) values disagree -> conflict,
    # enriched with value + source (ADR-015 conflict shape).
    assert "budget" in conflicts
    assert {c["value"] for c in conflicts["budget"]} == {"100", "200"}
    assert all(c["source"] == "llm" for c in conflicts["budget"])


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


def _seeded_project(tmp_path, product="troféus"):
    """A project whose item#0 was seeded by the MACHINE from message m1 (the p-0002 shape)."""
    ws = Workspace(tmp_path / "w.db").connect()
    store = p.ProjectStore(ws._conn)
    src = js.build_jobspec(
        {"message_id": "m1", "subject": "s", "counterparty": "CLIENT",
         "entities": {"product_or_service": product}},
        {"attachments": [], "subject": "s", "body_text": ""}).to_dict()
    pid = store.create("Troféus")
    assert p.seed_items_from(store, ws, {"m1": src}, pid, "m1") is True
    return ws, store, pid


def _richer_jobspec(mid="m1", **item_fields):
    """A re-extracted jobspec for ``mid`` carrying more per-item fields than the first pass."""
    spec = js.JobSpec(message_id=mid, subject="s",
                      job_fields={k: js.SpecField() for k in js.JOB_KEYS},
                      items=[{k: js.SpecField() for k in js.ITEM_KEYS}])
    for k, v in item_fields.items():
        spec.items[0][k] = js.SpecField(v, "llm", False)
    return {mid: spec.to_dict()}


def test_force_reseed_off_by_default_still_no_ops(tmp_path):
    # Pins the existing seed-once contract: without force=True a second seed must NOT touch a
    # project that already has item fields, however much better the new extraction is.
    ws, store, pid = _seeded_project(tmp_path)
    better = _richer_jobspec("m2", item="estrutura em aço Corten", material="Aço Corten")
    assert p.seed_items_from(store, ws, better, pid, "m2") is False
    assert store.fields_for(pid)["item#0"][0] == "troféus"      # untouched
    assert "material#0" not in store.fields_for(pid)
    ws.close()


def test_force_reseed_updates_machine_seeded_fields(tmp_path):
    # The bug this exists for: a correct re-extract could never reach the project view, because
    # seed_items_from no-ops the moment ONE machine-seeded item#0 row exists (p-0002).
    ws, store, pid = _seeded_project(tmp_path)
    better = _richer_jobspec("m1", item="estrutura em aço Corten", material="Aço Corten",
                             thickness="entre 5 e 8 mm", dimensions="Altura total: 200 cm",
                             colour_finish="Natural (sem pintura)")
    assert p.seed_items_from(store, ws, better, pid, "m1", force=True) is True
    fields = store.fields_for(pid)
    assert fields["item#0"][0] == "estrutura em aço Corten"     # machine value refreshed
    assert fields["material#0"][0] == "Aço Corten"              # and the new ones landed
    assert fields["colour_finish#0"][0] == "Natural (sem pintura)"
    ws.close()


def test_force_reseed_never_overwrites_a_human_set_field(tmp_path):
    # THE critical property (CLAUDE.md non-negotiable 6): a re-extract must never destroy a human
    # decision. The human's item#0 survives verbatim while the machine-only fields are refreshed.
    ws, store, pid = _seeded_project(tmp_path)
    store.set_field(pid, "item#0", "pórtico em Corten (confirmado ao telefone)",
                    channel="call", asserted_by="Bruno")
    store.set_field(pid, "material#0", "inox 304")             # workbench inline edit: no bundle at all
    better = _richer_jobspec("m1", item="estrutura em aço Corten", material="Aço Corten",
                             thickness="entre 5 e 8 mm")
    assert p.seed_items_from(store, ws, better, pid, "m1", force=True) is True
    fields = store.fields_for(pid)
    assert fields["item#0"][0] == "pórtico em Corten (confirmado ao telefone)"   # attributed: kept
    assert fields["material#0"][0] == "inox 304"               # unattributed hand edit: also kept
    assert fields["thickness#0"][0] == "entre 5 e 8 mm"        # untouched by a human: refreshed
    ws.close()


def test_force_reseed_does_not_resurrect_a_human_cleared_field(tmp_path):
    # A deliberate deletion is a decision too: re-seeding a field the human removed would silently
    # undo it. human_touched_fields reads the ledger, so the cleared address stays protected.
    ws, store, pid = _seeded_project(tmp_path)
    store.set_field(pid, "material#0", "inox 304", channel="manual", asserted_by="Bruno")
    store.clear_field(pid, "material#0", channel="manual", asserted_by="Bruno")
    better = _richer_jobspec("m1", item="troféus", material="Aço Corten")
    assert p.seed_items_from(store, ws, better, pid, "m1", force=True) is True
    assert "material#0" not in store.fields_for(pid)
    ws.close()


def test_force_reseed_is_idempotent_and_never_shrinks_items(tmp_path):
    # Re-running with the same jobspec must write nothing (no history churn), and a re-extract that
    # now sees FEWER line items must not shrink n_items — that would hide every field on the dropped
    # indices, human ones included, since canonical_spec only builds n_items items.
    ws, store, pid = _seeded_project(tmp_path)
    store.set_item_count(pid, 3)
    same = _richer_jobspec("m1", item="troféus")
    assert p.seed_items_from(store, ws, same, pid, "m1", force=True) is True
    assert store.get(pid)["n_items"] == 3                       # grown-only, never shrunk
    n_hist = len(store.field_history(pid, "item#0"))
    assert p.seed_items_from(store, ws, same, pid, "m1", force=True) is True
    assert len(store.field_history(pid, "item#0")) == n_hist    # identical value -> no new row
    ws.close()


# ---------------------------------------------------------------------------
# Zero-hallucination: a machine seed is never a human confirmation
# ---------------------------------------------------------------------------

def test_machine_seeded_field_is_not_marked_confirmed(tmp_path):
    # canonical_spec used to push EVERY project_field through js.confirm(), stamping
    # SpecField(value, "user", True). So a machine-seeded item#0 rendered as src=user and counted in
    # readiness["confirmed"] — an INFERENCE auto-promoted to a human-confirmed FACT nobody signed.
    ws, store, pid = _seeded_project(tmp_path)
    spec, rd, _prov, _c = p.build_canonical(store, ws, {}, pid, None)
    fld = spec.items[0]["item"]
    assert fld.value == "troféus"
    assert (fld.source, fld.confirmed) == ("llm", False)
    assert "item#0" not in rd["confirmed"]
    assert "item#0" in rd["present"] and "item#0" in rd["unconfirmed"]
    assert rd["estimable"] is False
    ws.close()


def test_human_confirmation_still_confirms(tmp_path):
    # The other half of the property: an explicit human write DOES confirm the field.
    ws, store, pid = _seeded_project(tmp_path)
    store.set_field(pid, "item#0", "troféus em acrílico", channel="call", asserted_by="Bruno")
    spec, rd, _prov, _c = p.build_canonical(store, ws, {}, pid, None)
    fld = spec.items[0]["item"]
    assert (fld.value, fld.source, fld.confirmed) == ("troféus em acrílico", "user", True)
    assert "item#0" in rd["confirmed"]
    ws.close()


def test_is_machine_provenance_discriminator():
    # The single rule everything above depends on, stated directly.
    assert p.is_machine_provenance("mid:abc@x.pt", "", "") is True       # seed_items_from
    assert p.is_machine_provenance("mid:abc@x.pt", "email", "João") is False   # attributed
    assert p.is_machine_provenance("mid:abc@x.pt", "", "João") is False        # somebody stated it
    assert p.is_machine_provenance("", "", "") is False                  # workbench inline edit
    assert p.is_machine_provenance("user", "", "") is False              # explicit user sentinel
    assert p.is_machine_provenance("capture:c-1", "", "") is False       # capture confirmation
    assert p.is_machine_provenance(None, None, None) is False            # legacy NULL columns


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


# ── lifecycle: CANCELLED + close-out (ADR-017) ───────────────────────────────

def test_cancel_records_party_reason_and_closed_at():
    store = p.ProjectStore(_conn())
    pid = store.create("Troféus")
    store.set_stage(pid, "CANCELLED", close_party="client", close_reason="cliente desistiu do evento")
    row = store.get(pid)
    assert row["stage"] == "CANCELLED" and row["close_party"] == "client"
    assert row["close_reason"] == "cliente desistiu do evento" and row["closed_at"]
    assert "CANCELLED" in p.STAGES and "CANCELLED" in p.TERMINAL_STAGES


def test_reopening_a_cancelled_project_clears_the_closeout():
    store = p.ProjectStore(_conn())
    pid = store.create("X")
    store.set_stage(pid, "CANCELLED", close_party="our", close_reason="margem insuficiente")
    store.set_stage(pid, "GATHERING")                       # reopened
    row = store.get(pid)
    assert row["stage"] == "GATHERING"
    assert row["close_party"] is None and row["close_reason"] is None and row["closed_at"] is None


def test_lost_also_carries_a_closeout_party():
    store = p.ProjectStore(_conn())
    pid = store.create("Y")
    store.set_stage(pid, "LOST", close_party="supplier", close_reason="fornecedor não entrega a tempo")
    row = store.get(pid)
    assert row["stage"] == "LOST" and row["close_party"] == "supplier" and row["closed_at"]


# ── multi-owner on a project ─────────────────────────────────────────────────

def test_project_owners_set_and_clear():
    store = p.ProjectStore(_conn())
    pid = store.create("Z")
    assert store.owners_for(pid) == []
    store.set_owners(pid, ["Diogo", "Marta", "Diogo", ""])    # de-duped + blank trimmed
    assert store.owners_for(pid) == ["Diogo", "Marta"]
    store.set_owners(pid, ["Bruno"])                         # replace semantics
    assert store.owners_for(pid) == ["Bruno"]
    store.set_owners(pid, [])
    assert store.owners_for(pid) == []


def test_delete_project_also_clears_owners():
    conn = _conn()
    store = p.ProjectStore(conn)
    pid = store.create("Del")
    store.set_owners(pid, ["Diogo"])
    assert store.delete(pid) is True
    assert conn.execute("SELECT COUNT(*) FROM project_owners WHERE project_id=?", (pid,)).fetchone()[0] == 0


# ---------------------------------------------------------------------------
# ADR-026 — the timeline half of the widened re-extraction
# ---------------------------------------------------------------------------

def test_knowledge_events_returns_only_events_oldest_first_with_rowids():
    """The pass needs the rowid (to cite the note a value came from) and oldest-first order (so a
    later note wins), and it must NOT re-read field edits — those are extraction OUTPUT, not
    knowledge. ``timeline()`` gives neither, which is why this is its own query."""
    store = p.ProjectStore(_conn())
    pid = store.create("Corten")
    store.add_event(pid, "note", "cliente quer inox", acquired_at="2026-03-02")
    store.add_event(pid, "decision", "avançamos a 4mm", acquired_at="2026-03-01")
    store.set_field(pid, "deadline", "2026-04-01")          # a field edit, not knowledge

    evs = store.knowledge_events(pid)
    assert [e["text"] for e in evs] == ["avançamos a 4mm", "cliente quer inox"]   # oldest first
    assert [e["kind"] for e in evs] == ["decision", "note"]
    assert all(isinstance(e["rowid"], int) for e in evs)
    assert all("deadline" not in e["text"] for e in evs)


def test_apply_event_fields_writes_machine_provenance_so_it_stays_refreshable():
    """The load-bearing property: a value the model READ OUT OF a note is an unconfirmed extraction,
    not the human decision the note itself is. It must land as machine provenance — otherwise it
    would enter human_touched_fields and freeze a model guess as if a person had signed it off."""
    store = p.ProjectStore(_conn())
    pid = store.create("Corten")
    store.add_event(pid, "note", "prazo 15 de março", channel="call", asserted_by="Diogo")
    rowid = store.knowledge_events(pid)[0]["rowid"]

    written = p.apply_event_fields(store, pid, rowid, {"deadline": "2026-03-15"})
    assert written == ["deadline"]
    prov = store.field_provenance(pid)["deadline"]
    assert prov["source_mid"] == f"event:{rowid}"
    # the note's OWN attribution must not be copied onto the parsed field
    assert prov["channel"] == "" and prov["asserted_by"] == ""
    assert "deadline" in store.machine_fields(pid)
    assert "deadline" not in store.human_touched_fields(pid)


def test_apply_event_fields_never_overwrites_a_human_decision():
    """A note that merely MENTIONS a deadline must not undo the deadline a person typed."""
    store = p.ProjectStore(_conn())
    pid = store.create("Corten")
    store.set_field(pid, "deadline", "2026-04-01", channel="email", asserted_by="Diogo")
    assert "deadline" in store.human_touched_fields(pid)

    written = p.apply_event_fields(store, pid, 1, {"deadline": "2026-03-15", "material": "inox"})
    assert written == ["material"]                                  # the human value stood
    assert store.fields_for(pid)["deadline"][0] == "2026-04-01"


def test_apply_event_fields_is_idempotent_and_skips_blanks():
    """Re-running over unchanged notes must write no history rows (the idempotency convention), and
    a blank/whitespace value is never a fact."""
    store = p.ProjectStore(_conn())
    pid = store.create("Corten")
    assert p.apply_event_fields(store, pid, 1, {"material": "inox"}) == ["material"]
    before = len(store.field_history(pid))

    assert p.apply_event_fields(store, pid, 1, {"material": "inox"}) == []     # identical → no-op
    assert p.apply_event_fields(store, pid, 2, {"material": "   ", "deadline": ""}) == []
    assert len(store.field_history(pid)) == before


def test_event_ref_is_machine_provenance_but_a_capture_ref_is_not():
    """``event:<rowid>`` joins the frozen source_mid vocabulary (ADR-022 §7 → ADR-026) on the MACHINE
    side; ``capture:<cid>`` stays human — it is a person confirming a capture."""
    assert p.event_ref(12) == "event:12"
    assert p.is_machine_provenance("event:12", "", "") is True
    assert p.is_machine_provenance("capture:12", "", "") is False
    assert p.is_machine_provenance("event:12", "call", "Diogo") is False
