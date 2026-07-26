from email2data.store import KnowledgeStore


def _store(tmp_path):
    csv = tmp_path / "gaz.csv"
    csv.write_text(
        "domain,counterparty,note\n"
        "# comment line\n"
        "cork-example.com,CLIENT,cork client\n"
        "laminate-example.com,SUPPLIER,laminate\n"
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
    assert s.lookup("cork-example.com") == "CLIENT"
    assert s.lookup("laminate-example.com") == "SUPPLIER"
    s.close()


def test_email_level_key_beats_domain(tmp_path):
    s, _ = _store(tmp_path)
    assert s.lookup("joao@gmail.com") == "CLIENT"   # exact email matched (free-mail sender)
    assert s.lookup("outro@gmail.com") is None       # different person on the same free-mail domain
    s.close()


def test_parent_domain_and_multilabel_tld(tmp_path):
    s, _ = _store(tmp_path)
    assert s.lookup("mail.laminate-example.com") == "SUPPLIER"            # registrable parent
    assert s.lookup("loja.empresa.com.pt") == "SUPPLIER"         # .com.pt suffix not mistaken for the domain
    assert s.lookup("user@loja.empresa.com.pt") == "SUPPLIER"    # email -> subdomain -> registrable
    assert s.lookup("unknown.pt") is None
    s.close()


def test_normalization_www_case_and_trailing_dot(tmp_path):
    s, _ = _store(tmp_path)
    assert s.lookup("WWW.Laminate-Example.com.") == "SUPPLIER"
    s.close()


def test_seed_replaces_removed_keys_no_stale_rows(tmp_path):
    s, _ = _store(tmp_path)
    csv2 = tmp_path / "gaz2.csv"
    csv2.write_text("domain,counterparty,note\ncork-example.com,CLIENT,updated\n", encoding="utf-8")
    assert s.seed_gazetteer(csv2) == 1
    assert s.lookup("cork-example.com") == "CLIENT"
    assert s.lookup("laminate-example.com") is None   # removed from the CSV -> removed from the DB
    s.close()


# ── the source of truth must be recoverable, and its absence must be loud ─────
#
# `config/gazetteer.csv` is gitignored (it names real clients), so it is the one store input with no
# second copy. It went missing on the live host and nobody noticed for three days: `build_store`
# guarded the seed with a bare `if gaz.exists()`, so out/knowledge.db went on serving 15 rows frozen
# at their last seed — priors that still fired (including the ADR-005 anti-IGNORE veto) but that
# nobody could read or edit. These pin both halves of the fix: the table round-trips back to a CSV,
# and a missing CSV over a non-empty table is never silent again.


def _rows(store):
    return sorted(store._conn.execute("SELECT key, counterparty, note FROM gazetteer"))


def test_export_round_trips_the_table_losslessly(tmp_path):
    """Export -> re-seed into a FRESH store reproduces every row exactly. This is the recovery path:
    if it were lossy, restoring the CSV would silently drop priors instead of restoring them."""
    src, _ = _store(tmp_path)
    out_csv = tmp_path / "exported.csv"
    assert src.export_gazetteer(out_csv) == 4

    dst = KnowledgeStore(tmp_path / "k2.db").connect()
    assert dst.seed_gazetteer(out_csv) == 4
    assert _rows(dst) == _rows(src)
    assert dst.lookup("joao@gmail.com") == "CLIENT"      # email-level key survived
    assert dst.lookup("loja.empresa.com.pt") == "SUPPLIER"  # multi-label suffix survived
    src.close()
    dst.close()


def test_export_survives_notes_containing_commas_and_quotes(tmp_path):
    """Notes are free text a human typed. A note with a comma must not shift the columns and turn a
    counterparty into a fragment of prose — csv.writer quoting, pinned."""
    csv_in = tmp_path / "g.csv"
    csv_in.write_text(
        "domain,counterparty,note\n"
        'tricky.pt,CLIENT,"acrylic, MDF and ""special"" jobs"\n',
        encoding="utf-8",
    )
    s = KnowledgeStore(tmp_path / "k.db").connect()
    assert s.seed_gazetteer(csv_in) == 1
    out_csv = tmp_path / "out.csv"
    s.export_gazetteer(out_csv)

    dst = KnowledgeStore(tmp_path / "k2.db").connect()
    assert dst.seed_gazetteer(out_csv) == 1
    assert _rows(dst) == [("tricky.pt", "CLIENT", 'acrylic, MDF and "special" jobs')]
    s.close()
    dst.close()


def test_exported_csv_is_seedable_despite_its_comment_header(tmp_path):
    """The export writes an explanatory `#` preamble BEFORE the column header. seed_gazetteer filters
    comments out, so the header must still be the first row the reader sees."""
    s, _ = _store(tmp_path)
    out_csv = tmp_path / "out.csv"
    s.export_gazetteer(out_csv)
    text = out_csv.read_text(encoding="utf-8")
    assert text.startswith("#")
    assert "\ndomain,counterparty,note\n" in text
    dst = KnowledgeStore(tmp_path / "k2.db").connect()
    assert dst.seed_gazetteer(out_csv) == 4   # the preamble did not break the parse
    s.close()
    dst.close()


def test_seed_or_warn_shouts_when_the_csv_vanished_under_a_live_table(tmp_path, capsys):
    """THE regression. Priors still in the table + no CSV = a frozen snapshot masquerading as a
    curated list. It used to produce no output at all."""
    s, _ = _store(tmp_path)
    assert s.seed_or_warn(tmp_path / "gone.csv") is None
    err = capsys.readouterr().err
    assert "MISSING" in err and "frozen" in err
    assert "gazetteer export" in err              # the message names the way out
    assert s.count() == 4                         # and the priors keep working meanwhile
    assert s.lookup("cork-example.com") == "CLIENT"
    s.close()


def test_seed_or_warn_is_quiet_on_a_fresh_install(tmp_path, capsys):
    """No CSV over an EMPTY table is just a new install with nothing curated yet — warning about it
    every run would train people to ignore the warning that matters."""
    s = KnowledgeStore(tmp_path / "k.db").connect()
    assert s.seed_or_warn(tmp_path / "gone.csv") is None
    assert capsys.readouterr().err == ""
    s.close()


def test_seed_or_warn_seeds_normally_and_says_nothing_when_the_csv_is_there(tmp_path, capsys):
    csv_in = tmp_path / "g.csv"
    csv_in.write_text("domain,counterparty,note\nacme.pt,CLIENT,ok\n", encoding="utf-8")
    s = KnowledgeStore(tmp_path / "k.db").connect()
    assert s.seed_or_warn(csv_in) == 1
    assert capsys.readouterr().err == ""
    assert s.lookup("acme.pt") == "CLIENT"
    s.close()


def test_counts_by_counterparty_summarises_without_revealing_keys(tmp_path):
    s, _ = _store(tmp_path)
    assert s.counts_by_counterparty() == {"CLIENT": 2, "SUPPLIER": 2}
    s.close()
