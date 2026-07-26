"""Authentication — credentials, sessions, and invites. The app's first auth layer (ADR-039).

Deliberately **not** a JWT design, and deliberately dependency-free.

The sibling materials-costing app signs a 24h HS256 JWT carrying `role` + `permissions`, re-reads the
user row on every request anyway (so the claims are dead weight), and documents a `sessions` table it
never creates — meaning logout only clears the cookie and a copied token stays valid for its whole
lifetime, with no kill switch short of rotating the global secret and logging everyone out. That
review is why this module does the opposite:

* **A session is a row, not a signed blob.** The cookie holds an opaque 256-bit random token; the DB
  stores only its SHA-256. Revocation is therefore a column write — "log out", "log out everywhere",
  and "kill a stolen session" all work on day one instead of being structurally impossible.
* **No server secret.** Nothing is signed, so there is no key to place in `.env`, leak, or rotate.
  Stealing the database yields hashes, not usable tokens.
* **No new dependencies.** `hashlib.scrypt` (memory-hard, stdlib), `secrets`, and
  `hmac.compare_digest` cover everything. This project ships two runtime dependencies; adding bcrypt
  and PyJWT to re-implement a weaker design would have been a poor trade.

Credentials live here in `out/auth.db`, never in `workspace.db`: the precious store holds human
decisions and must stay restorable from a backup without also restoring stale password material, and
a v-bump to the auth schema must never risk the DB that is "never auto-rebuilt". `people` (identity)
stays in workspace.db; only secrets are here. The two are joined by `person_id`, with no cross-file
foreign key — SQLite cannot enforce one, so `purge_person` exists to keep them from drifting.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

# scrypt parameters. n=2**14 keeps a verify near ~50 ms on this hardware — costly enough to make
# offline cracking of a stolen DB painful, cheap enough that a login feels instant. Stored per-hash
# (see ``hash_password``) so these can be raised later without invalidating existing passwords.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SALT_BYTES = 16

# How long a session lasts without re-login. Short enough that a forgotten browser on a shared
# workshop machine expires by itself; long enough not to nag a daily user.
SESSION_TTL_HOURS = 12
INVITE_TTL_HOURS = 72

# A reset link is mailed to an inbox, so its window is the one an attacker with later access to that
# inbox gets. 30 minutes is long enough to walk to another room and short enough that a mailbox
# compromised tomorrow yields nothing. Deliberately far shorter than INVITE_TTL_HOURS: an invite is
# handed to someone joining, a reset is issued when someone is already locked out and waiting.
RESET_TTL_MINUTES = 30

SCHEMA = """
-- One row per person who can sign in. ``person_id`` points at workspace.db `people`; there is no FK
-- because SQLite cannot reference across database files, so ``purge_person`` is what keeps the two
-- from drifting when a person is deleted.
CREATE TABLE IF NOT EXISTS credentials (
    person_id   TEXT PRIMARY KEY,
    password    TEXT NOT NULL,          -- "scrypt$n$r$p$salt_hex$hash_hex"; never a bare digest
    must_change INTEGER NOT NULL DEFAULT 0,
    created_ts  TEXT NOT NULL,
    updated_ts  TEXT NOT NULL
);

-- A session IS this row. The cookie carries the raw token; only its SHA-256 is stored, so a stolen
-- database yields nothing replayable. ``revoked_ts`` is the kill switch that materials-costing
-- documented but never built.
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    person_id   TEXT NOT NULL,
    created_ts  TEXT NOT NULL,
    expires_ts  TEXT NOT NULL,
    revoked_ts  TEXT,
    last_seen   TEXT,
    user_agent  TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_person ON sessions(person_id);

-- Single-use invite for setting a first password. Same hash-at-rest rule as sessions: the emailed
-- or hand-delivered link holds the raw token, the DB holds only its digest.
CREATE TABLE IF NOT EXISTS invites (
    token_hash  TEXT PRIMARY KEY,
    person_id   TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    created_ts  TEXT NOT NULL,
    expires_ts  TEXT NOT NULL,
    used_ts     TEXT
);

CREATE INDEX IF NOT EXISTS idx_invites_person ON invites(person_id);

-- Single-use password-reset token (ADR-042). Structurally an invite with a much shorter fuse and a
-- different origin: an invite is minted BY an admin FOR someone joining, a reset is minted by the
-- locked-out person themselves and mailed to the address on their `people` row. Same hash-at-rest
-- rule -- the mailed link holds the raw token, the DB holds only its SHA-256 -- so a stolen auth.db
-- (or a stolen backup) yields nothing redeemable.
--
-- A SEPARATE table rather than a `purpose` column on `invites`: the two have different TTLs,
-- different issuers, and different audit questions, and folding them together would mean every
-- existing invite query grew a `WHERE purpose=...` that a future reader could forget -- turning a
-- missed filter into a reset token accepted as an invite.
CREATE TABLE IF NOT EXISTS password_resets (
    token_hash  TEXT PRIMARY KEY,
    person_id   TEXT NOT NULL,
    created_ts  TEXT NOT NULL,
    expires_ts  TEXT NOT NULL,
    used_ts     TEXT,
    user_agent  TEXT
);

CREATE INDEX IF NOT EXISTS idx_resets_person ON password_resets(person_id);
"""


# Every table in this store keyed by ``person_id``. Named ONCE because two lifecycle methods walk it
# (``known_person_ids`` reads, ``purge_person`` deletes) and they were previously two copies of the
# same literal tuple -- so adding a table meant remembering both, and forgetting one leaves a deleted
# person's secrets behind while the drift check reports clean. Add new person-keyed tables here.
_PERSON_TABLES = ("credentials", "sessions", "invites", "password_resets")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(when: datetime) -> str:
    return when.isoformat(timespec="seconds")


def _token_hash(raw: str) -> str:
    """SHA-256 of a bearer token. Not scrypt: these are already 256 bits of CSPRNG output, so there
    is no low-entropy guess to slow down, and a fast digest keeps per-request auth cheap."""
    return hashlib.sha256(raw.encode()).hexdigest()


# ── passwords ────────────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    """``scrypt$n$r$p$salt_hex$hash_hex``. Parameters travel with the hash so they can be raised
    later without invalidating anyone's existing password."""
    if not plain:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(plain.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R,
                            p=_SCRYPT_P, dklen=_SCRYPT_DKLEN)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(plain: str, stored: str) -> bool:
    """Constant-time verify. Returns False on any malformed stored value rather than raising — a
    corrupt row must fail the login, not 500 the login page."""
    try:
        scheme, n, r, p, salt_hex, hash_hex = (stored or "").split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            (plain or "").encode(), salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(hash_hex)))
    except (ValueError, TypeError, MemoryError):
        return False
    return hmac.compare_digest(digest.hex(), hash_hex)


class AuthStore:
    """``out/auth.db`` — credentials, sessions, invites. Opened per process, like the other stores."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> "AuthStore":
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        return self

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # -- credentials ---------------------------------------------------------

    def has_any_credentials(self) -> bool:
        """False on a virgin install — what the first-run setup gate keys off."""
        assert self._conn is not None, "call connect() first"
        return self._conn.execute("SELECT 1 FROM credentials LIMIT 1").fetchone() is not None

    def has_credential(self, person_id: str) -> bool:
        """Whether this person has a password set — the honest answer for `auth list`."""
        assert self._conn is not None, "call connect() first"
        return self._conn.execute(
            "SELECT 1 FROM credentials WHERE person_id=?", (person_id,)).fetchone() is not None

    def set_password(self, person_id: str, plain: str, *, must_change: bool = False) -> None:
        """Create or replace a password. Every existing session for the person is revoked in the same
        transaction: a password change that left old sessions alive would not lock out whoever
        prompted the change."""
        assert self._conn is not None, "call connect() first"
        now = _iso(_now())
        stored = hash_password(plain)
        self._conn.execute(
            "INSERT INTO credentials (person_id, password, must_change, created_ts, updated_ts) "
            "VALUES (?,?,?,?,?) ON CONFLICT(person_id) DO UPDATE SET "
            "password=excluded.password, must_change=excluded.must_change, updated_ts=excluded.updated_ts",
            (person_id, stored, int(bool(must_change)), now, now))
        self._conn.execute(
            "UPDATE sessions SET revoked_ts=? WHERE person_id=? AND revoked_ts IS NULL", (now, person_id))
        self._conn.commit()

    def check_password(self, person_id: str, plain: str) -> bool:
        """Verify a password. Runs a dummy scrypt for an unknown person so a missing credential row
        and a wrong password take comparable time — the login page must not become a user oracle."""
        assert self._conn is not None, "call connect() first"
        row = self._conn.execute(
            "SELECT password FROM credentials WHERE person_id=?", (person_id,)).fetchone()
        if row is None:
            verify_password(plain, hash_password("dummy"))
            return False
        return verify_password(plain, row["password"])

    def must_change_password(self, person_id: str) -> bool:
        assert self._conn is not None, "call connect() first"
        row = self._conn.execute(
            "SELECT must_change FROM credentials WHERE person_id=?", (person_id,)).fetchone()
        return bool(row["must_change"]) if row else False

    # -- sessions ------------------------------------------------------------

    def start_session(self, person_id: str, *, user_agent: str = "",
                      ttl_hours: int = SESSION_TTL_HOURS) -> str:
        """Mint a session and return the RAW token — the only moment it exists in readable form.
        The caller puts it in a cookie and never logs it."""
        assert self._conn is not None, "call connect() first"
        raw = secrets.token_urlsafe(32)
        now = _now()
        self._conn.execute(
            "INSERT INTO sessions (token_hash, person_id, created_ts, expires_ts, revoked_ts, "
            "last_seen, user_agent) VALUES (?,?,?,?,NULL,?,?)",
            (_token_hash(raw), person_id, _iso(now), _iso(now + timedelta(hours=ttl_hours)),
             _iso(now), (user_agent or "")[:200]))
        self._conn.commit()
        return raw

    def session_person(self, raw_token: str) -> Optional[str]:
        """``person_id`` for a live session, else None (unknown, revoked, or expired).

        Expiry is enforced in the query rather than by a sweep, so a token cannot outlive its window
        just because no cleanup ran.
        """
        assert self._conn is not None, "call connect() first"
        if not raw_token:
            return None
        row = self._conn.execute(
            "SELECT person_id FROM sessions WHERE token_hash=? AND revoked_ts IS NULL "
            "AND expires_ts > ?", (_token_hash(raw_token), _iso(_now()))).fetchone()
        if row is None:
            return None
        self._conn.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?",
                           (_iso(_now()), _token_hash(raw_token)))
        self._conn.commit()
        return row["person_id"]

    def revoke_session(self, raw_token: str) -> bool:
        """Log out one session. True when a live session was actually ended."""
        assert self._conn is not None, "call connect() first"
        cur = self._conn.execute(
            "UPDATE sessions SET revoked_ts=? WHERE token_hash=? AND revoked_ts IS NULL",
            (_iso(_now()), _token_hash(raw_token)))
        self._conn.commit()
        return cur.rowcount > 0

    def revoke_all_sessions(self, person_id: str) -> int:
        """Log a person out everywhere. Returns how many live sessions were ended."""
        assert self._conn is not None, "call connect() first"
        cur = self._conn.execute(
            "UPDATE sessions SET revoked_ts=? WHERE person_id=? AND revoked_ts IS NULL",
            (_iso(_now()), person_id))
        self._conn.commit()
        return cur.rowcount

    def live_sessions(self, person_id: str) -> list[dict[str, Any]]:
        """A person's current sessions — real rows, so "where am I signed in?" is answerable."""
        assert self._conn is not None, "call connect() first"
        rows = self._conn.execute(
            "SELECT created_ts, expires_ts, last_seen, user_agent FROM sessions "
            "WHERE person_id=? AND revoked_ts IS NULL AND expires_ts > ? ORDER BY created_ts DESC",
            (person_id, _iso(_now()))).fetchall()
        return [dict(r) for r in rows]

    def purge_expired(self, *, older_than_days: int = 30) -> int:
        """Drop long-dead session rows. Housekeeping only — expiry is already enforced on read, so
        skipping this can never resurrect a session."""
        assert self._conn is not None, "call connect() first"
        cutoff = _iso(_now() - timedelta(days=older_than_days))
        cur = self._conn.execute(
            "DELETE FROM sessions WHERE expires_ts < ? AND (revoked_ts IS NULL OR revoked_ts < ?)",
            (cutoff, cutoff))
        self._conn.commit()
        return cur.rowcount

    # -- invites -------------------------------------------------------------

    def create_invite(self, person_id: str, *, created_by: str,
                      ttl_hours: int = INVITE_TTL_HOURS) -> str:
        """Mint a single-use invite and return the RAW token. Any earlier unused invite for the same
        person is consumed first, so re-issuing never leaves two live links."""
        assert self._conn is not None, "call connect() first"
        now = _now()
        self._conn.execute(
            "UPDATE invites SET used_ts=? WHERE person_id=? AND used_ts IS NULL", (_iso(now), person_id))
        raw = secrets.token_urlsafe(32)
        self._conn.execute(
            "INSERT INTO invites (token_hash, person_id, created_by, created_ts, expires_ts, used_ts) "
            "VALUES (?,?,?,?,?,NULL)",
            (_token_hash(raw), person_id, created_by, _iso(now),
             _iso(now + timedelta(hours=ttl_hours))))
        self._conn.commit()
        return raw

    def invite_person(self, raw_token: str) -> Optional[str]:
        """``person_id`` for a still-valid invite, else None. Does not consume it — the caller shows
        the set-password form first, then calls ``redeem_invite``."""
        assert self._conn is not None, "call connect() first"
        if not raw_token:
            return None
        row = self._conn.execute(
            "SELECT person_id FROM invites WHERE token_hash=? AND used_ts IS NULL AND expires_ts > ?",
            (_token_hash(raw_token), _iso(_now()))).fetchone()
        return row["person_id"] if row else None

    def redeem_invite(self, raw_token: str, new_password: str) -> Optional[str]:
        """Consume an invite and set the password. Returns the ``person_id``, or None if the invite
        was unknown, expired, or already used.

        The UPDATE is the atomic gate: two parallel redeems both pass the lookup, but only one gets
        ``rowcount == 1``, so a link cannot set two different passwords.
        """
        assert self._conn is not None, "call connect() first"
        person_id = self.invite_person(raw_token)
        if person_id is None:
            return None
        cur = self._conn.execute(
            "UPDATE invites SET used_ts=? WHERE token_hash=? AND used_ts IS NULL",
            (_iso(_now()), _token_hash(raw_token)))
        if cur.rowcount == 0:
            self._conn.rollback()
            return None
        self._conn.commit()
        self.set_password(person_id, new_password)
        return person_id

    # -- password resets (ADR-042) -------------------------------------------

    def create_reset(self, person_id: str, *, user_agent: str = "",
                     ttl_minutes: int = RESET_TTL_MINUTES) -> str:
        """Mint a single-use reset token and return the RAW value — the only readable moment.

        Any earlier unused reset for the same person is consumed first, so requesting twice never
        leaves two live links. That also makes the request endpoint self-limiting in the way that
        matters: spamming it mails more messages, but only the newest link ever works.
        """
        assert self._conn is not None, "call connect() first"
        now = _now()
        self._conn.execute(
            "UPDATE password_resets SET used_ts=? WHERE person_id=? AND used_ts IS NULL",
            (_iso(now), person_id))
        raw = secrets.token_urlsafe(32)
        self._conn.execute(
            "INSERT INTO password_resets (token_hash, person_id, created_ts, expires_ts, used_ts, "
            "user_agent) VALUES (?,?,?,?,NULL,?)",
            (_token_hash(raw), person_id, _iso(now),
             _iso(now + timedelta(minutes=ttl_minutes)), (user_agent or "")[:200]))
        self._conn.commit()
        return raw

    def reset_person(self, raw_token: str) -> Optional[str]:
        """``person_id`` for a still-valid reset token, else None. Does not consume it — the caller
        shows the set-password form first, then calls ``redeem_reset``."""
        assert self._conn is not None, "call connect() first"
        if not raw_token:
            return None
        row = self._conn.execute(
            "SELECT person_id FROM password_resets WHERE token_hash=? AND used_ts IS NULL "
            "AND expires_ts > ?", (_token_hash(raw_token), _iso(_now()))).fetchone()
        return row["person_id"] if row else None

    def redeem_reset(self, raw_token: str, new_password: str) -> Optional[str]:
        """Consume a reset token and set the password. Returns ``person_id``, or None if the token
        was unknown, expired, or already used.

        The UPDATE is the atomic gate, exactly as in ``redeem_invite``: two parallel redeems both
        pass the lookup, only one gets ``rowcount == 1``. ``set_password`` then revokes every live
        session for the person — which is the point, because "I lost my password" and "someone else
        is using my account" look identical from here, and the safe reading is the second.
        """
        assert self._conn is not None, "call connect() first"
        person_id = self.reset_person(raw_token)
        if person_id is None:
            return None
        cur = self._conn.execute(
            "UPDATE password_resets SET used_ts=? WHERE token_hash=? AND used_ts IS NULL",
            (_iso(_now()), _token_hash(raw_token)))
        if cur.rowcount == 0:
            self._conn.rollback()
            return None
        self._conn.commit()
        self.set_password(person_id, new_password)
        return person_id

    def recent_reset_count(self, person_id: str, *, within_minutes: int = 60) -> int:
        """How many resets this person was issued recently — the throttle input.

        Counts issued tokens, used or not, because the abuse being bounded is *mail sent*, not
        *links redeemed*. There is no lockout: refusing to mail is the whole mitigation, and it
        deliberately cannot lock anyone out of their account (see ADR-042 §Consequences).
        """
        assert self._conn is not None, "call connect() first"
        since = _iso(_now() - timedelta(minutes=within_minutes))
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM password_resets WHERE person_id=? AND created_ts > ?",
            (person_id, since)).fetchone()
        return int(row["n"]) if row else 0

    # -- lifecycle -----------------------------------------------------------

    def known_person_ids(self) -> set[str]:
        """Every ``person_id`` this store holds anything for, across all three tables.

        The join to workspace.db `people` is by ``person_id`` with no foreign key -- SQLite cannot
        reference across database files -- so nothing stops the two drifting. This is the read half
        of keeping them honest (``purge_person`` is the write half): compare against
        ``Workspace.people()`` and anything left over is a secret belonging to nobody.
        """
        assert self._conn is not None, "call connect() first"
        ids: set[str] = set()
        for table in _PERSON_TABLES:
            ids.update(r[0] for r in self._conn.execute(f"SELECT DISTINCT person_id FROM {table}"))
        return ids

    def purge_person(self, person_id: str) -> None:
        """Remove every secret belonging to a person. Called when a person is deleted in
        workspace.db — the cross-file join has no FK, so nothing else would clean this up."""
        assert self._conn is not None, "call connect() first"
        for table in _PERSON_TABLES:
            self._conn.execute(f"DELETE FROM {table} WHERE person_id=?", (person_id,))
        self._conn.commit()


def open_store(settings: dict[str, Any]) -> AuthStore:
    """Open the AuthStore at ``out/auth.db`` for the given settings."""
    from .config import paths

    p = paths(settings, settings["__settings_path__"])
    return AuthStore(p["out_dir"] / "auth.db").connect()
