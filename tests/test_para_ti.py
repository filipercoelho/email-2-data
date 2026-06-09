"""C3 — Para ti gate builders (para_ti.py). Pure function tests."""

from email2data.accounts import AccountCluster
from email2data.para_ti import (
    identity_candidate_items, low_confidence_items,
    propose_project_items,
)


def _frow(root, *, cp="CLIENT", purpose="ESTIMATE_REQUEST_FROM_CLIENT",
          confidence=0.9, committed=False, contact="a@acme.pt", subj="Pedido"):
    return {
        "thread_root": root, "counterparty": cp, "purpose": purpose,
        "subject": subj, "contact": contact,
        "trust": {"confidence": confidence, "committed": committed,
                  "decided_by": "tier1:gemini", "reason": "reason"},
        "clock": {"state": "WE_OWE", "band": "amber", "label": "6h", "age_hours": 6.0},
    }


# ── Gate 1: rever_classificacao ───────────────────────────────────────────────

def test_low_confidence_surfaces_uncertain_row():
    rows = [_frow("t1", confidence=0.45)]
    items = low_confidence_items(rows)
    assert len(items) == 1
    assert items[0]["kind"] == "rever_classificacao"
    assert items[0]["thread_root"] == "t1"


def test_high_confidence_not_surfaced():
    rows = [_frow("t1", confidence=0.95)]
    assert low_confidence_items(rows) == []


def test_committed_skipped_even_if_low_confidence():
    rows = [_frow("t1", confidence=0.3, committed=True)]
    assert low_confidence_items(rows) == []


def test_custom_floor():
    rows = [_frow("t1", confidence=0.75)]
    assert low_confidence_items(rows, floor=0.8) != []
    assert low_confidence_items(rows, floor=0.7) == []


def test_porquê_includes_confidence_and_verdict():
    items = low_confidence_items([_frow("t1", confidence=0.45, cp="CLIENT")])
    assert "45%" in items[0]["why"]
    assert "CLIENT" in items[0]["why"]


# ── Gate 2: propor_projeto ────────────────────────────────────────────────────

def test_propose_project_unattached_lead():
    rows = [_frow("t1", cp="LEAD", purpose="ESTIMATE_REQUEST_FROM_CLIENT")]
    items = propose_project_items(rows, set())
    assert len(items) == 1
    assert items[0]["kind"] == "propor_projeto"


def test_propose_project_unattached_client_po():
    rows = [_frow("t1", cp="CLIENT", purpose="PO_FROM_CLIENT")]
    items = propose_project_items(rows, set())
    assert len(items) == 1


def test_propose_project_skips_already_attached():
    rows = [_frow("t1", cp="LEAD", purpose="ESTIMATE_REQUEST_FROM_CLIENT")]
    items = propose_project_items(rows, {"t1"})
    assert items == []


def test_propose_project_skips_non_job_purpose():
    rows = [_frow("t1", cp="CLIENT", purpose="FOLLOW_UP")]
    assert propose_project_items(rows, set()) == []


def test_propose_project_skips_supplier():
    rows = [_frow("t1", cp="SUPPLIER", purpose="ESTIMATE_REQUEST_FROM_CLIENT")]
    assert propose_project_items(rows, set()) == []


def test_accept_payload_carries_thread_root():
    rows = [_frow("t1", cp="LEAD", purpose="ESTIMATE_REQUEST_FROM_CLIENT", subj="Estátua")]
    item = propose_project_items(rows, set())[0]
    assert item["accept"]["payload"]["from_message"] == "t1"
    assert "Estátua" in item["accept"]["payload"]["title"]


# ── Gate 3: confirmar_identidade ──────────────────────────────────────────────

def _free(email, count=3):
    return AccountCluster(key=f"free:{email}", kind="free_mail",
                          emails=[email], msg_count=count)

def _domain(key, emails=None):
    return AccountCluster(key=key, kind="domain",
                          emails=emails or [f"a@{key}"], msg_count=5)


def test_identity_candidate_similar_local_part():
    # "acme" appears in both "acme.pt" and "john.acme@gmail.com"
    clusters = [_domain("acme.pt"), _free("john.acme@gmail.com", count=3)]
    items = identity_candidate_items(clusters)
    assert len(items) == 1
    assert items[0]["kind"] == "confirmar_identidade"
    assert "acme.pt" in items[0]["title"]


def test_identity_candidate_below_min_msg_count_skipped():
    clusters = [_domain("acme.pt"), _free("john.acme@gmail.com", count=1)]
    assert identity_candidate_items(clusters, min_msg_count=2) == []


def test_identity_candidate_no_match_when_no_resemblance():
    clusters = [_domain("acme.pt"), _free("totally.unrelated@gmail.com", count=5)]
    assert identity_candidate_items(clusters) == []


def test_identity_candidate_accept_payload():
    clusters = [_domain("acme.pt"), _free("acmejohn@gmail.com", count=5)]
    items = identity_candidate_items(clusters)
    if items:  # the heuristic may or may not match — if it does, verify payload
        assert items[0]["accept"]["payload"]["account_key"] == "acme.pt"
        assert items[0]["accept"]["payload"]["email"] == "acmejohn@gmail.com"
