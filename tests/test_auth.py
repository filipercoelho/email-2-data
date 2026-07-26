"""Authentication — credentials, sessions, invites (ADR-039).

These pin the properties the materials-costing review said were missing or wrong there, so a future
refactor cannot quietly reintroduce them:

  * **Revocation actually works.** A session is a row; logout, log-out-everywhere and
    password-change all kill live sessions. (materials-costing documents a `sessions` table it never
    creates, so a copied token stays valid for its full lifetime.)
  * **Nothing replayable is stored.** Session and invite tokens are held as SHA-256; a stolen DB
    yields no usable credential.
  * **Expiry is enforced on read**, not by a cleanup job that might not have run.
  * **Invites are single-use under races** — the atomic gate, kept from materials-costing, which is
    the one thing it got clearly right.
"""

import sqlite3
import time

import pytest

from email2data.auth import (
    AuthStore,
    _token_hash,
    hash_password,
    verify_password,
)


@pytest.fixture()
def auth(tmp_path):
    a = AuthStore(tmp_path / "auth.db").connect()
    yield a
    a.close()


PERSON = "PER-FILIPE1"
OTHER = "PER-PEDRO01"


# ── password hashing ─────────────────────────────────────────────────────────

def test_password_round_trips():
    stored = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", stored) is True
    assert verify_password("wrong", stored) is False


def test_hash_is_salted_so_equal_passwords_differ_at_rest():
    assert hash_password("same") != hash_password("same")


def test_hash_carries_its_parameters_for_future_upgrades():
    scheme, n, r, p, salt, digest = hash_password("x").split("$")
    assert scheme == "scrypt"
    assert (int(n), int(r), int(p)) == (2 ** 14, 8, 1)
    assert len(bytes.fromhex(salt)) == 16 and len(bytes.fromhex(digest)) == 32


def test_empty_password_is_refused():
    with pytest.raises(ValueError, match="must not be empty"):
        hash_password("")


@pytest.mark.parametrize("stored", ["", "garbage", "scrypt$bad", "bcrypt$1$2$3$4$5",
                                    "scrypt$x$8$1$aa$bb", "scrypt$16384$8$1$zz$bb"])
def test_malformed_stored_value_fails_the_login_rather_than_raising(stored):
    """A corrupt credential row must not 500 the login page."""
    assert verify_password("anything", stored) is False


def test_plaintext_never_appears_in_the_stored_value():
    assert "hunter2" not in hash_password("hunter2")


# ── credentials ──────────────────────────────────────────────────────────────

def test_virgin_install_has_no_credentials(auth):
    assert auth.has_any_credentials() is False
    auth.set_password(PERSON, "pw")
    assert auth.has_any_credentials() is True


def test_check_password(auth):
    auth.set_password(PERSON, "pw")
    assert auth.check_password(PERSON, "pw") is True
    assert auth.check_password(PERSON, "nope") is False


def test_unknown_person_fails_without_leaking_that_they_are_unknown(auth):
    """No user-enumeration oracle: the unknown-person path still runs a full scrypt."""
    auth.set_password(PERSON, "pw")
    t0 = time.perf_counter()
    auth.check_password("PER-NOBODY", "pw")
    unknown = time.perf_counter() - t0
    t0 = time.perf_counter()
    auth.check_password(PERSON, "wrong")
    wrong = time.perf_counter() - t0
    assert auth.check_password("PER-NOBODY", "pw") is False
    # Same order of magnitude — a bare dict lookup would be ~1000x faster than a scrypt verify.
    assert unknown > wrong / 10


def test_setting_a_password_replaces_rather_than_duplicates(auth):
    auth.set_password(PERSON, "old")
    auth.set_password(PERSON, "new")
    assert auth.check_password(PERSON, "old") is False
    assert auth.check_password(PERSON, "new") is True


def test_must_change_flag_round_trips(auth):
    auth.set_password(PERSON, "pw", must_change=True)
    assert auth.must_change_password(PERSON) is True
    auth.set_password(PERSON, "pw2")
    assert auth.must_change_password(PERSON) is False


def test_must_change_on_an_unknown_person_is_false_not_an_error(auth):
    assert auth.must_change_password("PER-NOBODY") is False


# ── sessions ─────────────────────────────────────────────────────────────────

def test_session_round_trips(auth):
    token = auth.start_session(PERSON)
    assert auth.session_person(token) == PERSON


def test_only_the_hash_is_stored(auth):
    """A stolen database must yield nothing replayable."""
    token = auth.start_session(PERSON)
    rows = auth._conn.execute("SELECT token_hash FROM sessions").fetchall()
    assert rows[0]["token_hash"] == _token_hash(token)
    assert token not in str(rows[0]["token_hash"])


def test_unknown_and_empty_tokens_are_rejected(auth):
    auth.start_session(PERSON)
    assert auth.session_person("not-a-real-token") is None
    assert auth.session_person("") is None


def test_logout_revokes_that_session_only(auth):
    keep = auth.start_session(PERSON)
    drop = auth.start_session(PERSON)
    assert auth.revoke_session(drop) is True
    assert auth.session_person(drop) is None
    assert auth.session_person(keep) == PERSON


def test_revoking_twice_reports_no_second_kill(auth):
    token = auth.start_session(PERSON)
    assert auth.revoke_session(token) is True
    assert auth.revoke_session(token) is False


def test_log_out_everywhere(auth):
    tokens = [auth.start_session(PERSON) for _ in range(3)]
    other = auth.start_session(OTHER)
    assert auth.revoke_all_sessions(PERSON) == 3
    assert all(auth.session_person(t) is None for t in tokens)
    assert auth.session_person(other) == OTHER, "another person's session must survive"


def test_changing_a_password_kills_live_sessions(auth):
    """The point of a password change is to lock out whoever prompted it."""
    auth.set_password(PERSON, "old")
    token = auth.start_session(PERSON)
    assert auth.session_person(token) == PERSON
    auth.set_password(PERSON, "new")
    assert auth.session_person(token) is None


def test_expiry_is_enforced_on_read_not_by_a_cleanup_job(auth):
    token = auth.start_session(PERSON, ttl_hours=-1)      # already past
    assert auth.session_person(token) is None
    assert auth._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1, \
        "the row is still there; it is the QUERY that refuses it"


def test_live_sessions_lists_only_usable_ones(auth):
    auth.start_session(PERSON, user_agent="Firefox")
    revoked = auth.start_session(PERSON)
    auth.start_session(PERSON, ttl_hours=-1)
    auth.revoke_session(revoked)
    live = auth.live_sessions(PERSON)
    assert len(live) == 1 and live[0]["user_agent"] == "Firefox"


def test_user_agent_is_truncated(auth):
    auth.start_session(PERSON, user_agent="x" * 500)
    assert len(auth.live_sessions(PERSON)[0]["user_agent"]) == 200


def test_purge_removes_only_long_dead_rows(auth):
    live = auth.start_session(PERSON)
    auth.start_session(PERSON, ttl_hours=-24 * 60)       # expired 60 days ago
    assert auth.purge_expired(older_than_days=30) == 1
    assert auth.session_person(live) == PERSON


def test_purge_is_housekeeping_only_and_cannot_resurrect(auth):
    token = auth.start_session(PERSON, ttl_hours=-1)
    auth.purge_expired(older_than_days=0)
    assert auth.session_person(token) is None


# ── invites ──────────────────────────────────────────────────────────────────

def test_invite_round_trips_and_sets_the_password(auth):
    token = auth.create_invite(PERSON, created_by="admin")
    assert auth.invite_person(token) == PERSON
    assert auth.redeem_invite(token, "chosen-pw") == PERSON
    assert auth.check_password(PERSON, "chosen-pw") is True


def test_an_invite_is_single_use(auth):
    token = auth.create_invite(PERSON, created_by="admin")
    assert auth.redeem_invite(token, "first") == PERSON
    assert auth.redeem_invite(token, "second") is None
    assert auth.check_password(PERSON, "first") is True, "the second redeem must not overwrite"


def test_reissuing_consumes_the_previous_invite(auth):
    """Re-sending a link must not leave two live ones."""
    first = auth.create_invite(PERSON, created_by="admin")
    second = auth.create_invite(PERSON, created_by="admin")
    assert auth.invite_person(first) is None
    assert auth.invite_person(second) == PERSON


def test_expired_invite_is_refused(auth):
    token = auth.create_invite(PERSON, created_by="admin", ttl_hours=-1)
    assert auth.invite_person(token) is None
    assert auth.redeem_invite(token, "pw") is None


def test_unknown_invite_is_refused(auth):
    assert auth.invite_person("nope") is None
    assert auth.redeem_invite("nope", "pw") is None


def test_only_the_invite_hash_is_stored(auth):
    token = auth.create_invite(PERSON, created_by="admin")
    stored = auth._conn.execute("SELECT token_hash FROM invites").fetchone()["token_hash"]
    assert stored == _token_hash(token) and stored != token


def test_concurrent_redeem_has_exactly_one_winner(auth):
    """The atomic UPDATE gate: both callers pass the lookup, only one gets rowcount 1."""
    token = auth.create_invite(PERSON, created_by="admin")
    results = [auth.redeem_invite(token, f"pw-{i}") for i in range(2)]
    assert results.count(PERSON) == 1 and results.count(None) == 1


# ── lifecycle ────────────────────────────────────────────────────────────────

def test_purge_person_removes_every_secret(auth):
    auth.set_password(PERSON, "pw")
    token = auth.start_session(PERSON)
    auth.create_invite(PERSON, created_by="admin")
    auth.set_password(OTHER, "pw")

    auth.purge_person(PERSON)

    assert auth.check_password(PERSON, "pw") is False
    assert auth.session_person(token) is None
    for table in ("credentials", "sessions", "invites"):
        assert auth._conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE person_id=?", (PERSON,)).fetchone()[0] == 0
    assert auth.check_password(OTHER, "pw") is True, "another person must be untouched"


def test_schema_is_created_idempotently(tmp_path):
    path = tmp_path / "auth.db"
    AuthStore(path).connect().close()
    a = AuthStore(path).connect()
    a.set_password(PERSON, "pw")
    a.close()
    AuthStore(path).connect().close()
    a = AuthStore(path).connect()
    assert a.check_password(PERSON, "pw") is True
    a.close()


def test_no_password_material_reaches_the_workspace_db(tmp_path):
    """Credentials live in auth.db so a workspace restore never brings back stale secrets."""
    from email2data.workspace import Workspace

    ws = Workspace(tmp_path / "workspace.db").connect()
    ws.create_person("Filipe", can_login=True, is_admin=True)
    tables = [r[0] for r in ws._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    ws.close()
    assert "credentials" not in tables and "sessions" not in tables

    conn = sqlite3.connect(tmp_path / "workspace.db")
    dump = "\n".join(conn.iterdump())
    conn.close()
    # SQL line comments are stripped before scanning. The v11 `people.email` DDL comment explains
    # that the column receives a *password*-reset link (ADR-042), and matching that would fail this
    # test on a piece of documentation rather than on a secret. What must stay caught is the real
    # thing: a stored hash, a token digest, or a column actually holding credential material — none
    # of which live in a comment. Scanning the comment-free SQL keeps the signal and drops the noise.
    live_sql = "\n".join(line.split("--", 1)[0] for line in dump.splitlines())
    for leak in ("password", "scrypt", "token_hash"):
        assert leak not in live_sql.lower(), f"{leak!r} reached workspace.db outside a comment"


# ── cross-store consistency (W10) ────────────────────────────────────────────
#
# workspace.db holds identity, auth.db holds secrets, and they are joined by person_id with NO
# foreign key — SQLite cannot reference across database files. Nothing detects drift, so the pair
# has to be able to report on itself.

def test_known_person_ids_spans_all_three_tables(auth):
    auth.set_password(PERSON, "pw-for-person")
    auth.start_session(OTHER)
    auth.create_invite("PER-RITA001", created_by="cli")
    assert auth.known_person_ids() == {PERSON, OTHER, "PER-RITA001"}


def test_known_person_ids_is_empty_on_a_virgin_store(auth):
    assert auth.known_person_ids() == set()


def test_purge_person_clears_them_from_known_ids(auth):
    """The write half and the read half must agree, or `auth list` reports phantom orphans."""
    auth.set_password(PERSON, "pw-for-person")
    auth.start_session(PERSON)
    auth.create_invite(PERSON, created_by="cli")
    auth.purge_person(PERSON)
    assert auth.known_person_ids() == set()


def test_a_restore_of_one_store_without_the_other_is_detectable(tmp_path):
    """The failure this exists for: auth.db restored from a snapshot that predates a person, or
    workspace.db restored from one that postdates them. Either way the leftover is a secret
    belonging to nobody, and only a set difference finds it."""
    from email2data.workspace import Workspace

    ws = Workspace(tmp_path / "workspace.db").connect()
    filipe = ws.create_person("Filipe", can_login=True, is_admin=True)
    auth = AuthStore(tmp_path / "auth.db").connect()
    auth.set_password(filipe["person_id"], "a-real-password")
    auth.set_password("PER-GHOST01", "orphaned-by-a-partial-restore")

    live = {p["person_id"] for p in ws.people(include_inactive=True)}
    assert auth.known_person_ids() - live == {"PER-GHOST01"}
    auth.close()
    ws.close()


# ── password resets (ADR-042) ────────────────────────────────────────────────
#
# Structurally an invite with a much shorter fuse, so these mirror the invite tests. The two are
# deliberately separate tables: a `purpose` column would mean every existing invite query needed a
# filter that a future reader could forget, turning a missed WHERE into a reset token accepted as an
# onboarding invite.

def test_a_reset_token_is_single_use(tmp_path):
    store = AuthStore(tmp_path / "a.db").connect()
    store.set_password("PER-1", "original-pw-1234")
    token = store.create_reset("PER-1")
    assert store.redeem_reset(token, "brand-new-pw-99") == "PER-1"
    assert store.redeem_reset(token, "third-pw-0000") is None
    assert store.check_password("PER-1", "brand-new-pw-99"), (
        "the refused second redeem changed the password anyway")


def test_minting_a_reset_consumes_any_earlier_one(tmp_path):
    """Two live links for one person is two windows an attacker could be inside."""
    store = AuthStore(tmp_path / "a.db").connect()
    first = store.create_reset("PER-1")
    second = store.create_reset("PER-1")
    assert store.reset_person(first) is None
    assert store.reset_person(second) == "PER-1"


def test_an_expired_reset_is_refused_without_a_sweep(tmp_path):
    """Expiry is enforced in the query, so a token cannot outlive its window just because no
    cleanup job ran."""
    store = AuthStore(tmp_path / "a.db").connect()
    token = store.create_reset("PER-1", ttl_minutes=-1)
    assert store.reset_person(token) is None
    assert store.redeem_reset(token, "brand-new-pw-99") is None


def test_redeeming_a_reset_revokes_every_live_session(tmp_path):
    store = AuthStore(tmp_path / "a.db").connect()
    store.set_password("PER-1", "original-pw-1234")
    session = store.start_session("PER-1")
    store.redeem_reset(store.create_reset("PER-1"), "brand-new-pw-99")
    assert store.session_person(session) is None


def test_the_reset_token_is_stored_only_as_a_digest(tmp_path):
    store = AuthStore(tmp_path / "a.db").connect()
    token = store.create_reset("PER-1")
    rows = list(store._conn.execute("SELECT * FROM password_resets"))
    assert rows and all(token not in str(tuple(row)) for row in rows)


def test_recent_reset_count_bounds_mail_not_logins(tmp_path):
    store = AuthStore(tmp_path / "a.db").connect()
    store.set_password("PER-1", "original-pw-1234")
    for _ in range(3):
        store.create_reset("PER-1")
    assert store.recent_reset_count("PER-1") == 3
    assert store.recent_reset_count("PER-2") == 0
    # The throttle input must never affect authentication itself.
    assert store.check_password("PER-1", "original-pw-1234")


def test_purging_a_person_removes_their_reset_tokens(tmp_path):
    """`purge_person` and `known_person_ids` walk one shared table tuple. A new person-keyed table
    that is not in it leaves secrets behind while `auth list` reports no drift."""
    store = AuthStore(tmp_path / "a.db").connect()
    store.create_reset("PER-1")
    assert "PER-1" in store.known_person_ids()
    store.purge_person("PER-1")
    assert "PER-1" not in store.known_person_ids()
    assert store._conn.execute(
        "SELECT COUNT(*) FROM password_resets WHERE person_id='PER-1'").fetchone()[0] == 0
