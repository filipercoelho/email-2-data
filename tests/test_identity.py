from email2data.identity import canonical_id, canonical_id_from_raw, safe_filename

RAW = (
    b"Message-ID: <ABC123@Host.LindoServico.PT>\r\n"
    b"Subject: teste\r\n\r\nbody\r\n"
)


def test_message_id_normalized_and_stable():
    a = canonical_id("<ABC123@Host.LindoServico.PT>", b"x")
    b = canonical_id("abc123@host.lindoservico.pt ", b"y")
    assert a == b == "mid:abc123@host.lindoservico.pt"


def test_fallback_to_content_hash_when_no_message_id():
    cid = canonical_id(None, b"hello")
    assert cid.startswith("sha256:")
    assert canonical_id(None, b"hello") == cid  # deterministic


def test_fetch_and_envelope_agree():
    # The id used for the filename (fetch) and the id stored in results (envelope) must match.
    from email2data.envelope import parse_eml

    assert canonical_id_from_raw(RAW) == parse_eml(RAW)["message_id"]


def test_safe_filename_is_flat():
    fn = safe_filename("mid:<a/b c>@host")
    assert "/" not in fn and " " not in fn and fn.endswith(".eml")
