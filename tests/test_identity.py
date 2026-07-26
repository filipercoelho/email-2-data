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


RAW_8BIT_MID = b"Message-ID: <ol\xc3\xa1@example.pt>\r\nSubject: teste\r\n\r\nbody\r\n"


def test_raw_8bit_message_id_does_not_crash_the_id_derivation():
    """The default parser returns a Header here, which has no .strip() — one out-of-spec header
    used to abort the whole fetch with AttributeError (ADR-043)."""
    from email2data.envelope import parse_eml

    cid = canonical_id_from_raw(RAW_8BIT_MID)
    assert cid == "mid:olá@example.pt"
    assert cid == parse_eml(RAW_8BIT_MID)["message_id"]   # fetch and envelope must still agree
    cid.encode("utf-8")                                   # storable: no surrogates left behind
