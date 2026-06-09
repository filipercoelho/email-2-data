"""C1a/C1b — account clustering (accounts.py + workspace identity_links)."""

from email2data.accounts import cluster
from email2data.workspace import Workspace


def _c(email, domain=None, msg_count=3, from_count=2, last_seen="2026-06-01", cp="CLIENT"):
    domain = domain or email.split("@")[1]
    return {"email": email, "domain": domain, "msg_count": msg_count,
            "from_count": from_count, "last_seen": last_seen, "last_counterparty": cp}


# ── domain clustering ─────────────────────────────────────────────────────────

def test_two_contacts_same_domain_become_one_cluster():
    cs = cluster([_c("a@acme.pt"), _c("b@acme.pt")])
    assert len(cs) == 1
    assert cs[0].key == "acme.pt"
    assert set(cs[0].emails) == {"a@acme.pt", "b@acme.pt"}
    assert cs[0].msg_count == 6  # summed


def test_different_domains_are_separate_clusters():
    cs = cluster([_c("a@acme.pt"), _c("b@rival.pt")])
    assert len(cs) == 2
    keys = {c.key for c in cs}
    assert keys == {"acme.pt", "rival.pt"}


def test_free_mail_each_gets_own_cluster():
    cs = cluster([_c("alice@gmail.com"), _c("bob@gmail.com")])
    assert len(cs) == 2
    assert all(c.kind == "free_mail" for c in cs)


def test_internal_domain_excluded():
    cs = cluster([_c("a@lindoservico.pt", domain="lindoservico.pt")])
    assert cs == []


# ── NIF merging ──────────────────────────────────────────────────────────────

def test_nif_merges_free_mail_into_domain_cluster():
    contacts = [_c("alice@gmail.com"), _c("a@acme.pt")]
    nif_refs = {"501234567": ["alice@gmail.com", "a@acme.pt"]}
    cs = cluster(contacts, nif_refs=nif_refs)
    assert len(cs) == 1
    assert cs[0].key == "acme.pt"
    assert "alice@gmail.com" in cs[0].emails
    assert "a@acme.pt" in cs[0].emails


def test_nif_creates_own_key_when_no_domain():
    contacts = [_c("alice@gmail.com"), _c("bob@hotmail.com")]
    nif_refs = {"501234567": ["alice@gmail.com", "bob@hotmail.com"]}
    cs = cluster(contacts, nif_refs=nif_refs)
    assert len(cs) == 1
    assert cs[0].key == "nif:501234567"


# ── identity_links overlay (C1b) ──────────────────────────────────────────────

def test_identity_link_merges_free_mail_into_domain():
    contacts = [_c("alice@gmail.com"), _c("a@acme.pt")]
    cs = cluster(contacts, identity_links={"alice@gmail.com": "acme.pt"})
    assert len(cs) == 1
    assert "alice@gmail.com" in cs[0].emails


def test_identity_link_takes_precedence_over_nif():
    # The link says gmail → acme.pt, even if NIF would create a different cluster.
    contacts = [_c("alice@gmail.com"), _c("a@acme.pt"), _c("b@rival.pt")]
    nif_refs = {"999": ["alice@gmail.com", "b@rival.pt"]}
    cs = cluster(contacts, nif_refs=nif_refs, identity_links={"alice@gmail.com": "acme.pt"})
    acme = next(c for c in cs if c.key == "acme.pt")
    assert "alice@gmail.com" in acme.emails  # link wins over NIF


# ── gazetteer hints ───────────────────────────────────────────────────────────

def test_gazetteer_hint_sets_display_name():
    cs = cluster([_c("a@acme.pt")], gazetteer_hints={"acme.pt": "Acme, Lda."})
    assert cs[0].display_name == "Acme, Lda."


def test_no_gazetteer_hint_falls_back_to_key():
    cs = cluster([_c("a@acme.pt")])
    assert cs[0].display_name == "acme.pt"


# ── aggregation ───────────────────────────────────────────────────────────────

def test_msg_count_is_summed_across_emails():
    cs = cluster([_c("a@acme.pt", msg_count=4), _c("b@acme.pt", msg_count=7)])
    assert cs[0].msg_count == 11


def test_sorted_by_msg_count_descending():
    cs = cluster([_c("a@small.pt", msg_count=1), _c("a@big.pt", msg_count=99)])
    assert cs[0].key == "big.pt"


def test_empty_input():
    assert cluster([]) == []


# ── workspace identity_links (C1b persistence) ───────────────────────────────

def test_identity_link_persists_and_survives_reconnect(tmp_path):
    db = tmp_path / "w.db"
    ws = Workspace(db).connect()
    ws.set_identity_link("alice@gmail.com", "acme.pt")
    ws.close()
    ws2 = Workspace(db).connect()
    links = ws2.identity_links()
    assert links["alice@gmail.com"] == "acme.pt"
    ws2.close()


def test_identity_link_idempotent_upsert(tmp_path):
    ws = Workspace(tmp_path / "w.db").connect()
    ws.set_identity_link("alice@gmail.com", "acme.pt")
    ws.set_identity_link("alice@gmail.com", "rival.pt")  # update
    assert ws.identity_links()["alice@gmail.com"] == "rival.pt"
    ws.close()


def test_identity_links_feeds_cluster(tmp_path):
    ws = Workspace(tmp_path / "w.db").connect()
    ws.set_identity_link("alice@gmail.com", "acme.pt")
    contacts = [_c("alice@gmail.com"), _c("a@acme.pt")]
    cs = cluster(contacts, identity_links=ws.identity_links())
    assert len(cs) == 1 and "alice@gmail.com" in cs[0].emails
    ws.close()
