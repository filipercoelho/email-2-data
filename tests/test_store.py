from email2data.store import KnowledgeStore


def _store(tmp_path):
    csv = tmp_path / "gaz.csv"
    csv.write_text(
        "domain,counterparty,note\n"
        "# comment line\n"
        "corticoenetos.com,CLIENT,cork client\n"
        "spandex.com,SUPPLIER,laminate\n"
        "joao@gmail.com,CLIENT,free-mail client (email-level key)\n"
        "empresa.com.pt,SUPPLIER,multi-label tld\n"
        "BADROW,NOTACP,invalid counterparty -> skipped\n",
        encoding="utf-8",
    )
    s = KnowledgeStore(tmp_path / "k.db").connect()
    n = s.seed_gazetteer(csv)
    return s, n


def test_seed_loads_valid_and_skips_invalid_counterparty(tmp_path):
    s, n = _store(tmp_path)
    assert n == 4  # the BADROW with an invalid counterparty is skipped
    assert s.lookup("corticoenetos.com") == "CLIENT"
    assert s.lookup("spandex.com") == "SUPPLIER"
    s.close()


def test_email_level_key_beats_domain(tmp_path):
    s, _ = _store(tmp_path)
    assert s.lookup("joao@gmail.com") == "CLIENT"   # exact email matched (free-mail sender)
    assert s.lookup("outro@gmail.com") is None       # different person on the same free-mail domain
    s.close()


def test_parent_domain_and_multilabel_tld(tmp_path):
    s, _ = _store(tmp_path)
    assert s.lookup("mail.spandex.com") == "SUPPLIER"            # registrable parent
    assert s.lookup("loja.empresa.com.pt") == "SUPPLIER"         # .com.pt suffix not mistaken for the domain
    assert s.lookup("user@loja.empresa.com.pt") == "SUPPLIER"    # email -> subdomain -> registrable
    assert s.lookup("unknown.pt") is None
    s.close()


def test_normalization_www_case_and_trailing_dot(tmp_path):
    s, _ = _store(tmp_path)
    assert s.lookup("WWW.Spandex.com.") == "SUPPLIER"
    s.close()


def test_seed_replaces_removed_keys_no_stale_rows(tmp_path):
    s, _ = _store(tmp_path)
    csv2 = tmp_path / "gaz2.csv"
    csv2.write_text("domain,counterparty,note\ncorticoenetos.com,CLIENT,updated\n", encoding="utf-8")
    assert s.seed_gazetteer(csv2) == 1
    assert s.lookup("corticoenetos.com") == "CLIENT"
    assert s.lookup("spandex.com") is None   # removed from the CSV -> removed from the DB
    s.close()
