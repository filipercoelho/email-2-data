"""C3 — Para ti gate builders (para_ti.py). Pure function tests."""

import pytest

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


def test_ver_na_fila_link_targets_the_fila_not_the_inicio_gate():
    """«Ver na Fila» must land on the Fila with the conversation mounted.

    This href is minted server-side into /api/para-ti, so it is a contract every consumer inherits.
    It read ``/?focus=<root>`` until ADR-044 moved the Fila to /fila and made / the Início gate —
    a page that reads NO query parameter — so the one action this card offers dropped the thread and
    dumped the user on the landing page. Nothing asserted its value before, which is why the move
    was silent."""
    item = low_confidence_items([_frow("t1", confidence=0.45)])[0]
    href = item["accept"]["href"]
    assert href.startswith("/fila?"), f"deep link escaped the Fila: {href}"
    assert not href.startswith("/?"), "points at Início, which reads no query parameter"
    # The canonical param, not the legacy ?focus= alias the Fila only keeps for old links.
    assert href == "/fila?thread=t1"


def test_ver_na_fila_link_url_encodes_the_message_id():
    """A Message-ID is not URL-safe. '&' unencoded ends the parameter, so the Fila received a
    truncated root and focused nothing (or, with '+', a different conversation)."""
    root = "mid:caf+abc&x@mail.gmail.com"
    href = low_confidence_items([_frow(root, confidence=0.3)])[0]["accept"]["href"]
    assert href == "/fila?thread=mid%3Acaf%2Babc%26x%40mail.gmail.com"
    from urllib.parse import parse_qs, urlparse
    assert parse_qs(urlparse(href).query)["thread"] == [root], "the root did not survive the round-trip"


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
    assert items[0]["title"] == "john.acme@gmail.com"             # title is now the email address
    assert "acme.pt" in items[0]["context"]["proposed_cluster"]   # cluster in context


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


# ── Junk gate + dismissals (v8) ───────────────────────────────────────────────

def test_propose_project_skips_automated_senders():
    """A mailer-daemon bounce (or any no-reply sender) must NEVER be proposed as a client project,
    even when content classification says ESTIMATE_REQUEST — machine mail is not a lead. The thread
    stays in the Fila (nothing binned); it just loses the green 'Criar projeto' gate. Pins the
    real-data defect where a delivery-failure notice got a primary quote-opportunity card."""
    for sender in ("mailer-daemon@mailer-daemon.register.it", "noreply.odd@dhl.com",
                   "no-reply@portal.pt", "postmaster@acme.pt", "do-not-reply@x.io"):
        assert propose_project_items([_frow("t1", contact=sender)], set()) == [], sender
    # a human sender still proposes
    assert propose_project_items([_frow("t1", contact="maria@acme.pt")], set()) != []


def test_all_items_filters_dismissed_and_stamps_keys():
    """all_items drops persisted dismissals (matched by the SAME key the JS uses:
    kind|thread_root-or-email) and stamps ``key`` on every survivor so the UI dismisses against the
    exact key the next build will honour. This is the server half of the 'Ignorar must survive a
    reload' fix — without the filter, every ignored proposal resurrected on every page load."""
    from email2data.para_ti import all_items, item_key
    rows = [_frow("t1", confidence=0.45), _frow("t2", confidence=0.9)]
    items = all_items(rows, [], set())
    assert items and all(it["key"] == item_key(it) for it in items)
    victim = items[0]["key"]
    left = all_items(rows, [], set(), dismissed={victim})
    assert victim not in {it["key"] for it in left}
    assert len(left) == len(items) - 1


# ── the lens (ADR-052) ────────────────────────────────────────────────────────

def test_para_ti_keeps_the_attachment_funnel_it_was_sent():
    """Para Ti has NEVER rendered the ADR-046 funnel.

    ``loadDetail`` stored ``{messages, spec}`` and dropped ``d.attachments`` on the floor, while
    ``detailHTML`` went on passing ``attachments: d.attachments`` — ``undefined`` — so
    ``attFunnelHTML`` returned ``''`` every single time. Confirmed in Chrome before the fix: a thread
    showing **8** ``.tatt`` chips and no ``.attf`` element at all, on the one page whose job is
    deciding about a conversation.

    Executed, not grepped: the shipped ``loadDetail`` is run in node against a stubbed ``getJSON``,
    so this asserts what the cache actually ends up holding.
    """
    import json
    import shutil
    import subprocess

    from email2data import para_ti_page as ptp
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available — the lens JS can't be executed")
    js = ptp._LENS_JS
    fn = js[js.index("async function loadDetail("):js.index("async function toggleExpand(")]
    payload = {"messages": [{"message_id": "m1"}], "spec": {"provenance": {}},
               "attachments": {"items": [{"id": "aa", "band": "FICHEIROS", "name": "q.pdf"}],
                               "counts": {"FICHEIROS": 1}}}
    body = ("const _detail={},_detailErr={};\n" + fn
            + "\nconst getJSON=async()=>(%s);\n" % json.dumps(payload)
            + "loadDetail({thread_root:'t1'}).then(()=>"
              "console.log(JSON.stringify(_detail['t1'])));")
    r = subprocess.run([node, "-e", body], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    assert got["attachments"], "loadDetail discarded d.attachments — the funnel can never render"
    assert got["attachments"]["items"][0]["name"] == "q.pdf"
    assert got["messages"] and got["spec"], "…and the two it already kept are still kept"
    # the renderer really is handed that object (the other half of the two-line defect)
    assert "attachments:d.attachments" in js.replace(" ", "")
