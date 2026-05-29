from email2data.store import KnowledgeStore


def _store(tmp_path):
    csv = tmp_path / "gaz.csv"
    csv.write_text(
        "domain,counterparty,note\n"
        "# comment line\n"
        "corticoenetos.com,CLIENT,cork client\n"
        "spandex.com,SUPPLIER,laminate\n",
        encoding="utf-8",
    )
    s = KnowledgeStore(tmp_path / "k.db").connect()
    n = s.seed_gazetteer(csv)
    return s, n


def test_seed_skips_comments_and_loads(tmp_path):
    s, n = _store(tmp_path)
    assert n == 2
    assert s.lookup("corticoenetos.com") == "CLIENT"
    assert s.lookup("spandex.com") == "SUPPLIER"
    s.close()


def test_lookup_parent_domain_and_unknown(tmp_path):
    s, _ = _store(tmp_path)
    assert s.lookup("mail.spandex.com") == "SUPPLIER"   # parent-domain fallback
    assert s.lookup("unknown.pt") is None
    s.close()


def test_seed_is_idempotent_upsert(tmp_path):
    s, _ = _store(tmp_path)
    csv2 = tmp_path / "gaz2.csv"
    csv2.write_text("domain,counterparty,note\ncorticoenetos.com,CLIENT,updated\n", encoding="utf-8")
    s.seed_gazetteer(csv2)
    assert s.lookup("corticoenetos.com") == "CLIENT"
    s.close()
