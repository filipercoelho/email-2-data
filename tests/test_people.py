"""People — the single assignable-identity namespace (workspace v10, ADR-039).

The properties that carry weight:

  * **Accountability is structural.** A person who cannot sign in MUST name a responsible user, so
    work assigned to them always lands in some signed-in human's view. Enforced by a DB CHECK *and*
    by a readable error, not by convention — this is "never silently bin a client" reaching ownership.
  * **One namespace, one name.** ``name`` is UNIQUE and case-insensitive, so "filipe" and "Filipe"
    can never become two people who each own half a queue.
  * **Rename cascades.** ``name`` is the join key in thread_owners / project_owners /
    captures.asserted_by / capture_users.roster_owner / roster, so a rename that updated only
    ``people`` would silently orphan every assignment.
  * **The v10 migration preserves the precious DB.** New tables only; not one existing row changes.
"""

import sqlite3

import pytest

from email2data.workspace import SCHEMA_VERSION, Workspace


@pytest.fixture()
def ws(tmp_path):
    w = Workspace(tmp_path / "workspace.db").connect()
    yield w
    w.close()


@pytest.fixture()
def seeded(ws):
    """The confirmed 2026-07-25 roster: 3 users + 1 non-login assignee."""
    ws.create_person("Filipe", can_login=True, is_admin=True)
    ws.create_person("Pedro", can_login=True)
    ws.create_person("Luís", can_login=True)
    ws.create_person("Rita", responsible="Filipe")
    return ws


# ── accountability ───────────────────────────────────────────────────────────

def test_non_login_person_requires_a_responsible_user(ws):
    ws.create_person("Filipe", can_login=True, is_admin=True)
    with pytest.raises(ValueError, match="responsible person"):
        ws.create_person("Rita")


def test_non_login_person_with_a_responsible_user_is_created(ws):
    admin = ws.create_person("Filipe", can_login=True, is_admin=True)
    rita = ws.create_person("Rita", responsible="Filipe")
    assert rita["can_login"] is False
    assert rita["responsible_id"] == admin["person_id"]


def test_responsible_person_must_exist(ws):
    with pytest.raises(ValueError, match="does not exist"):
        ws.create_person("Rita", responsible="Ninguém")


def test_responsible_person_must_be_able_to_sign_in(ws):
    """Chaining accountability through someone who never logs in defeats the point."""
    ws.create_person("Filipe", can_login=True, is_admin=True)
    ws.create_person("Rita", responsible="Filipe")
    with pytest.raises(ValueError, match="cannot sign in either"):
        ws.create_person("Zé", responsible="Rita")


def test_a_login_user_needs_no_responsible(ws):
    assert ws.create_person("Pedro", can_login=True)["responsible_id"] is None


def test_db_check_rejects_an_admin_who_cannot_sign_in(ws):
    """Defence in depth: even a direct INSERT cannot create an unusable admin grant."""
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        ws._conn.execute(
            "INSERT INTO people (person_id, name, name_key, can_login, is_admin, responsible_id, "
            "active, created_ts, updated_ts) VALUES ('PER-X','X','x',0,1,NULL,1,'t','t')")


def test_db_check_rejects_a_non_login_person_with_no_responsible(ws):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        ws._conn.execute(
            "INSERT INTO people (person_id, name, name_key, can_login, is_admin, responsible_id, "
            "active, created_ts, updated_ts) VALUES ('PER-X','X','x',0,0,NULL,1,'t','t')")


# ── one namespace, one name ──────────────────────────────────────────────────

def test_duplicate_name_is_refused_case_insensitively(seeded):
    with pytest.raises(ValueError, match="already exists"):
        seeded.create_person("filipe", can_login=True)


def test_blank_name_is_refused(ws):
    for bad in ("", "   ", "\t\n"):
        with pytest.raises(ValueError, match="needs a name"):
            ws.create_person(bad, can_login=True)


def test_whitespace_is_normalised_but_case_is_preserved(ws):
    person = ws.create_person("  Ana   Maria  ", can_login=True)
    assert person["name"] == "Ana Maria"
    assert ws.person("ana maria")["person_id"] == person["person_id"]


def test_lookup_by_name_is_case_insensitive(seeded):
    assert seeded.person("LUÍS")["name"] == "Luís"


def test_unknown_person_is_none_not_an_error(ws):
    assert ws.person("Ninguém") is None
    assert ws.person_by_id("PER-NOPE") is None


def test_people_are_listed_name_ordered(seeded):
    assert [p["name"] for p in seeded.people()] == ["Filipe", "Luís", "Pedro", "Rita"]


def test_accented_names_fold_across_case(seeded):
    """Regression: SQLite COLLATE NOCASE folds ASCII only, so "LUÍS" missed "Luís" entirely."""
    assert seeded.person("LUÍS")["name"] == "Luís"
    assert seeded.person("luís")["name"] == "Luís"


def test_an_accented_name_cannot_be_duplicated_by_changing_case(seeded):
    """The dangerous half of the same bug: two rows, one person, half a queue each."""
    with pytest.raises(ValueError, match="already exists"):
        seeded.create_person("LUÍS", can_login=True)


def test_composed_and_decomposed_accents_are_the_same_person(seeded):
    """NFKC first, so "í" and "i + combining acute" do not become two people."""
    decomposed = "Lui\u0301s"          # i + U+0301 COMBINING ACUTE ACCENT
    assert decomposed != "Luís"
    assert seeded.person(decomposed)["name"] == "Luís"
    with pytest.raises(ValueError, match="already exists"):
        seeded.create_person(decomposed, can_login=True)


# ── inbox scopes (ADR-038 tokens) ────────────────────────────────────────────

def test_scopes_are_granted_and_read_back(seeded):
    pedro = seeded.person("Pedro")
    seeded.set_person_scopes(pedro["person_id"],
                             ["pedro.ferreira@lindoservico.pt", "orcamentos@lindoservico.pt"])
    assert seeded.person("Pedro")["scopes"] == [
        "orcamentos@lindoservico.pt", "pedro.ferreira@lindoservico.pt"]


def test_setting_scopes_replaces_rather_than_appends(seeded):
    """Idempotent: the result depends only on the argument, never on prior state."""
    pid = seeded.person("Pedro")["person_id"]
    seeded.set_person_scopes(pid, ["a@lindoservico.pt", "b@lindoservico.pt"])
    seeded.set_person_scopes(pid, ["b@lindoservico.pt"])
    assert seeded.person_scopes(pid) == ["b@lindoservico.pt"]


def test_empty_list_revokes_every_scope(seeded):
    pid = seeded.person("Pedro")["person_id"]
    seeded.set_person_scopes(pid, ["a@lindoservico.pt"])
    seeded.set_person_scopes(pid, [])
    assert seeded.person_scopes(pid) == []


def test_scope_tokens_are_normalised_and_deduped(seeded):
    pid = seeded.person("Pedro")["person_id"]
    seeded.set_person_scopes(pid, [" A@LindoServico.pt ", "a@lindoservico.pt", "", "  "])
    assert seeded.person_scopes(pid) == ["a@lindoservico.pt"]


def test_a_fresh_person_has_no_scopes(seeded):
    assert seeded.person("Rita")["scopes"] == []


# ── the lifecycle: promote, deactivate, remove (ADR-041) ─────────────────────
#
# Until now a person could only be CREATED. Everything after that — someone leaves, someone is
# promoted, someone was added by mistake — needed a sqlite3 prompt against the precious DB. The
# invariant that makes these safe to expose is the one below: the install can never reach zero
# administrators, because there is no unauthenticated way back in short of deleting auth.db.


def test_a_person_can_be_promoted_and_demoted(seeded):
    pedro = seeded.person("Pedro")
    seeded.set_person_admin(pedro["person_id"], True)
    assert seeded.person("Pedro")["is_admin"] is True
    seeded.set_person_admin(pedro["person_id"], False)
    assert seeded.person("Pedro")["is_admin"] is False


def test_promoting_someone_who_cannot_sign_in_is_refused(seeded):
    """An admin who cannot log in is a permission nobody holds — and the DB CHECK says so too."""
    rita = seeded.person("Rita")
    with pytest.raises(ValueError):
        seeded.set_person_admin(rita["person_id"], True)


def test_the_last_administrator_cannot_be_demoted(seeded):
    """The lockout that has no recovery: /setup 404s once any credential exists, so an install with
    zero admins cannot be repaired from the app at all."""
    filipe = seeded.person("Filipe")
    with pytest.raises(ValueError, match="administrador"):
        seeded.set_person_admin(filipe["person_id"], False)
    assert seeded.person("Filipe")["is_admin"] is True


def test_the_last_administrator_cannot_be_deactivated(seeded):
    """Same lockout by the other door — deactivation ends access just as thoroughly as demotion."""
    filipe = seeded.person("Filipe")
    with pytest.raises(ValueError, match="administrador"):
        seeded.set_person_active(filipe["person_id"], False)
    assert seeded.person("Filipe")["active"] is True


def test_an_admin_can_be_demoted_once_another_one_exists(seeded):
    seeded.set_person_admin(seeded.person("Pedro")["person_id"], True)
    seeded.set_person_admin(seeded.person("Filipe")["person_id"], False)
    assert seeded.person("Filipe")["is_admin"] is False
    assert seeded.person("Pedro")["is_admin"] is True


def test_deactivating_hides_a_person_from_the_roster_without_erasing_them(seeded):
    """Deactivation is the normal way someone leaves: their past assignments stay attributed, they
    just stop being offered. Erasing them would rewrite who decided what."""
    seeded.set_person_active(seeded.person("Rita")["person_id"], False)
    assert "Rita" not in [p["name"] for p in seeded.people()]
    assert "Rita" in [p["name"] for p in seeded.people(include_inactive=True)]
    assert seeded.person("Rita")["active"] is False


def test_a_deactivated_person_can_come_back(seeded):
    rita = seeded.person("Rita")["person_id"]
    seeded.set_person_active(rita, False)
    seeded.set_person_active(rita, True)
    assert seeded.person("Rita")["active"] is True


def test_a_person_with_no_history_can_be_removed(seeded):
    """The mistyped-name case, which is the only one where removal is honest."""
    seeded.create_person("Tpyo", responsible="Filipe")
    seeded.delete_person(seeded.person("Tpyo")["person_id"])
    assert seeded.person("Tpyo") is None


def test_removing_someone_who_owns_work_is_refused(seeded):
    """Deleting them would orphan the assignment: `name` is the join key in thread_owners and
    friends, so the row would point at nobody and the thread would silently lose its owner."""
    seeded.set_thread_owner("T1", "Pedro")
    with pytest.raises(ValueError, match="histórico"):
        seeded.delete_person(seeded.person("Pedro")["person_id"])
    assert seeded.person("Pedro") is not None


def test_removing_someone_who_is_responsible_for_another_person_is_refused(seeded):
    """Rita's accountability points at Filipe. Removing him would leave her work in nobody's view —
    the exact hole `create_person`'s responsible rule exists to close."""
    seeded.set_person_admin(seeded.person("Pedro")["person_id"], True)   # …so the last-admin guard
    with pytest.raises(ValueError, match="responsável"):                 #    is not what fires here
        seeded.delete_person(seeded.person("Filipe")["person_id"])


def test_removing_the_last_administrator_is_refused(seeded):
    seeded.set_person_active(seeded.person("Rita")["person_id"], False)   # clear the responsible tie
    seeded.delete_person(seeded.person("Rita")["person_id"])
    with pytest.raises(ValueError, match="administrador"):
        seeded.delete_person(seeded.person("Filipe")["person_id"])


def test_the_lifecycle_calls_are_idempotent(seeded):
    """Re-running a state change yields the same state — a double-clicked button is not a second
    decision."""
    pedro = seeded.person("Pedro")["person_id"]
    seeded.set_person_admin(pedro, True)
    seeded.set_person_admin(pedro, True)
    assert seeded.person("Pedro")["is_admin"] is True
    seeded.set_person_active(pedro, False)
    seeded.set_person_active(pedro, False)
    assert seeded.person("Pedro")["active"] is False


def test_lifecycle_calls_on_an_unknown_person_are_refused(seeded):
    for call in (lambda: seeded.set_person_admin("PER-NOBODY", True),
                 lambda: seeded.set_person_active("PER-NOBODY", False),
                 lambda: seeded.delete_person("PER-NOBODY")):
        with pytest.raises(ValueError):
            call()


# ── one roster, not three (ADR-041 / W8) ─────────────────────────────────────
#
# The owner picker read `settings.json team` ∪ the in-app `roster` table, while permissions read
# `people`. Two vocabularies for the same question: "Rita" could be an owner and not a person, so
# assigning her work was possible and granting her anything was not. This folds the first into the
# second — without losing a single name, which is the only way it is safe to do.


def test_the_backfill_turns_legacy_roster_names_into_people(ws):
    ws.create_person("Filipe", can_login=True, is_admin=True)
    ws.roster_add("Sofia")
    created = ws.backfill_people_from_roster(["Filipe", "Luís"])
    assert set(created) == {"Luís", "Sofia"}
    assert {p["name"] for p in ws.people()} == {"Filipe", "Luís", "Sofia"}


def test_a_backfilled_name_is_assignable_only_and_accountable_to_an_admin(ws):
    """They were free text a moment ago — nobody decided they could sign in. Assignable-only is the
    honest reading, and the accountability rule then demands a responsible user."""
    filipe = ws.create_person("Filipe", can_login=True, is_admin=True)
    ws.backfill_people_from_roster(["Sofia"])
    sofia = ws.person("Sofia")
    assert sofia["can_login"] is False and sofia["is_admin"] is False
    assert sofia["responsible_id"] == filipe["person_id"]


def test_the_backfill_is_idempotent_and_never_touches_an_existing_person(ws):
    ws.create_person("Filipe", can_login=True, is_admin=True)
    ws.backfill_people_from_roster(["Filipe", "Sofia"])
    ws.set_person_admin(ws.person("Sofia")["person_id"], False)      # no-op, but stamps updated_ts
    before = ws.person("Sofia")
    assert ws.backfill_people_from_roster(["Filipe", "Sofia"]) == []
    assert ws.person("Sofia")["person_id"] == before["person_id"]
    assert len(ws.people()) == 2


def test_the_backfill_matches_a_name_exactly_the_way_a_lookup_does(ws):
    """`settings.team` is hand-typed config, so "luís" there and "Luís" in people must be ONE person
    or the picker grows a duplicate that owns half a queue. It folds case and normalises composed vs
    decomposed accents — and it stops there, exactly like `person()`: an accent is a letter, so
    "Luis" and "Luís" stay two people. A backfill matching more loosely than every other lookup would
    be a second vocabulary, which is the thing this whole change is removing."""
    ws.create_person("Filipe", can_login=True, is_admin=True)
    ws.create_person("Luís", can_login=True)
    decomposed = "Lui\u0301s"                 # "i" + combining acute, as a Mac paste produces
    assert ws.backfill_people_from_roster(["LU\u00cdS", decomposed]) == []
    assert len(ws.people()) == 2
    assert ws.backfill_people_from_roster(["Luis"]) == ["Luis"]      # unaccented ≠ accented


def test_the_backfill_waits_for_an_admin_rather_than_guessing(ws):
    """On a virgin install there is nobody to be accountable yet. Creating the names with a made-up
    responsible would be an invention; skipping and retrying next boot is not."""
    ws.roster_add("Sofia")
    assert ws.backfill_people_from_roster(["Sofia"]) == []
    assert ws.people() == []
    ws.create_person("Filipe", can_login=True, is_admin=True)
    assert ws.backfill_people_from_roster(["Sofia"]) == ["Sofia"]


def test_a_deactivated_person_is_not_resurrected_by_the_backfill(ws):
    """Someone who left is still in settings.team — config nobody edits. Re-creating them on every
    boot would make deactivation impossible to keep."""
    ws.create_person("Filipe", can_login=True, is_admin=True)
    ws.create_person("Sofia", responsible="Filipe")
    ws.set_person_active(ws.person("Sofia")["person_id"], False)
    assert ws.backfill_people_from_roster(["Sofia"]) == []
    assert ws.person("Sofia")["active"] is False


# ── rename must cascade ──────────────────────────────────────────────────────

def test_rename_cascades_into_every_table_that_stores_the_name(seeded):
    """A rename that touched only `people` would orphan assignments silently."""
    conn = seeded._conn
    conn.execute("INSERT INTO thread_owners(thread_root, owner, ts) VALUES ('t1','Rita','x')")
    conn.execute("INSERT INTO project_owners(project_id, owner, ts) VALUES ('p1','Rita','x')")
    conn.execute("INSERT INTO roster(name, added_ts) VALUES ('Rita','x')")
    conn.commit()

    touched = seeded.rename_person("Rita", "Rita Silva")
    assert touched >= 3

    assert seeded.person("Rita") is None
    assert seeded.person("Rita Silva") is not None
    assert conn.execute("SELECT owner FROM thread_owners").fetchone()[0] == "Rita Silva"
    assert conn.execute("SELECT owner FROM project_owners").fetchone()[0] == "Rita Silva"
    assert conn.execute("SELECT name FROM roster").fetchone()[0] == "Rita Silva"


def test_rename_preserves_the_person_id_so_scopes_survive(seeded):
    pid = seeded.person("Rita")["person_id"]
    seeded.set_person_scopes(pid, ["a@lindoservico.pt"])
    seeded.rename_person("Rita", "Rita Silva")
    assert seeded.person("Rita Silva")["person_id"] == pid
    assert seeded.person("Rita Silva")["scopes"] == ["a@lindoservico.pt"]


def test_rename_onto_an_existing_name_is_refused(seeded):
    with pytest.raises(ValueError, match="already exists"):
        seeded.rename_person("Rita", "Pedro")


def test_rename_of_an_unknown_person_is_refused(seeded):
    with pytest.raises(ValueError, match="does not exist"):
        seeded.rename_person("Ninguém", "Alguém")


def test_rename_to_blank_is_refused(seeded):
    with pytest.raises(ValueError, match="needs a name"):
        seeded.rename_person("Rita", "   ")


def test_renaming_to_the_same_name_in_a_different_case_is_allowed(seeded):
    """Fixing capitalisation is a legitimate rename, not a duplicate."""
    seeded.rename_person("Rita", "RITA")
    assert seeded.person("rita")["name"] == "RITA"


# ── the v10 migration ────────────────────────────────────────────────────────

def test_v10_adds_only_tables_and_loses_no_rows(tmp_path):
    """The precious DB: a migration may add, never disturb."""
    path = tmp_path / "workspace.db"
    old = Workspace(path).connect()
    old._conn.execute("INSERT INTO thread_state(thread_root, handled, updated_ts) VALUES ('t1',1,'x')")
    old._conn.execute("PRAGMA user_version = 9")
    old._conn.execute("DROP TABLE people")
    old._conn.execute("DROP TABLE person_scopes")
    old._conn.commit()
    old.close()

    def snapshot():
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        tables = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        counts = {t: c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
        version = c.execute("PRAGMA user_version").fetchone()[0]
        c.close()
        return version, counts

    before_version, before = snapshot()
    assert before_version == 9 and "people" not in before

    Workspace(path).connect().close()          # migrate
    after_version, after = snapshot()

    # Not pinned to a literal: this test is about the PROPERTY (a migration may add, never disturb),
    # and hard-coding the version turned every later schema bump into an unrelated failure here.
    # v11 (ADR-042) adds people.email, which lands inside the `people` table this run already
    # creates — so the add-only assertion below is unchanged and still meaningful.
    assert after_version == SCHEMA_VERSION
    assert sorted(set(after) - set(before)) == ["people", "person_scopes"]
    assert {t: after[t] for t in before} == before, "a pre-existing row changed"


def test_the_worker_still_refuses_to_migrate_the_precious_db(tmp_path):
    """v10 must not weaken the single-migrator gate the intake worker depends on."""
    from email2data.workspace import WorkspaceVersionError

    path = tmp_path / "workspace.db"
    w = Workspace(path).connect()
    w._conn.execute("PRAGMA user_version = 9")
    w._conn.commit()
    w.close()

    with pytest.raises(WorkspaceVersionError, match="will not migrate"):
        Workspace(path).connect(migrate=False)


# ── people.email (v11, ADR-042) ──────────────────────────────────────────────
#
# The column exists to receive a password-reset link. Its most important property is what it does
# NOT do: it is never derived. person_scopes holds inboxes a person may READ and
# imap.accounts[].username holds mailboxes the app FETCHES — neither is evidence of whose address it
# is, and a guess here mails a credential-bearing link to the wrong human.

def test_a_new_person_has_no_address_on_file(tmp_path):
    ws = Workspace(tmp_path / "w.db").connect()
    person = ws.create_person("Filipe", can_login=True, is_admin=True)
    assert person["email"] == ""


def test_an_address_is_normalised_on_the_way_in(tmp_path):
    ws = Workspace(tmp_path / "w.db").connect()
    person = ws.create_person("Filipe", can_login=True, is_admin=True)
    updated = ws.set_person_email(person["person_id"], "  Filipe.Coelho@LindoServico.PT  ")
    assert updated["email"] == "filipe.coelho@lindoservico.pt"


@pytest.mark.parametrize("bad", ["nope", "a@b", "a b@c.pt", "@x.pt", "a@", "a@.pt", "a@b..pt"])
def test_a_malformed_address_is_refused(tmp_path, bad):
    """A shape check, not RFC 5322. Rejecting an exotic-but-legal address costs a retype; accepting
    a typo costs someone their only way back in, discovered when they are already locked out."""
    ws = Workspace(tmp_path / "w.db").connect()
    person = ws.create_person("Filipe", can_login=True, is_admin=True)
    with pytest.raises(ValueError):
        ws.set_person_email(person["person_id"], bad)


def test_clearing_an_address_is_a_real_state_not_a_no_op(tmp_path):
    ws = Workspace(tmp_path / "w.db").connect()
    person = ws.create_person("Filipe", can_login=True, is_admin=True)
    ws.set_person_email(person["person_id"], "filipe@lindoservico.pt")
    assert ws.set_person_email(person["person_id"], "")["email"] == ""


def test_one_address_cannot_belong_to_two_people(tmp_path):
    """A shared address is a reset link with two possible destinations."""
    ws = Workspace(tmp_path / "w.db").connect()
    first = ws.create_person("Filipe", can_login=True, is_admin=True)
    second = ws.create_person("Pedro", can_login=True)
    ws.set_person_email(first["person_id"], "partilhado@lindoservico.pt")
    with pytest.raises(ValueError, match="Filipe"):
        ws.set_person_email(second["person_id"], "partilhado@lindoservico.pt")


def test_setting_the_same_address_on_the_same_person_is_idempotent(tmp_path):
    ws = Workspace(tmp_path / "w.db").connect()
    person = ws.create_person("Filipe", can_login=True, is_admin=True)
    ws.set_person_email(person["person_id"], "filipe@lindoservico.pt")
    assert ws.set_person_email(person["person_id"], "filipe@lindoservico.pt")["email"] == \
        "filipe@lindoservico.pt"


def test_person_by_email_finds_only_active_login_capable_people(tmp_path):
    """The reset flow's only lookup. A deactivated person must not be reachable through a stale
    address, and someone with no login has no password to reset."""
    ws = Workspace(tmp_path / "w.db").connect()
    admin = ws.create_person("Filipe", can_login=True, is_admin=True)
    ws.set_person_email(admin["person_id"], "filipe@lindoservico.pt")
    assert (ws.person_by_email("filipe@lindoservico.pt") or {}).get("name") == "Filipe"

    gone = ws.create_person("Antigo", can_login=True)
    ws.set_person_email(gone["person_id"], "antigo@lindoservico.pt")
    ws.set_person_active(gone["person_id"], False)
    assert ws.person_by_email("antigo@lindoservico.pt") is None

    rita = ws.create_person("Rita", can_login=False, responsible="Filipe")
    ws.set_person_email(rita["person_id"], "rita@lindoservico.pt")
    assert ws.person_by_email("rita@lindoservico.pt") is None


def test_person_by_email_never_matches_a_blank_probe(tmp_path):
    """Most rows carry '' by default, so a blank probe that matched would return an arbitrary
    person and mail them a reset link."""
    ws = Workspace(tmp_path / "w.db").connect()
    ws.create_person("Filipe", can_login=True, is_admin=True)
    assert ws.person_by_email("") is None
    assert ws.person_by_email("   ") is None
    assert ws.person_by_email("garbage-not-an-address") is None


def test_a_v10_database_migrates_to_v11_without_losing_anyone(tmp_path):
    """The precious DB is never rebuilt, so the ALTER has to land in a numbered migration block —
    a forgotten one ships a column-less DB that throws "no such column" on first write."""
    path = tmp_path / "w.db"
    ws = Workspace(path).connect()
    ws.create_person("Filipe", can_login=True, is_admin=True)
    ws.create_person("Rita", can_login=False, responsible="Filipe")
    ws.close()

    # Rewind to v10 and drop the column, i.e. a real pre-ADR-042 database.
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE people DROP COLUMN email")
    conn.execute("PRAGMA user_version = 10")
    conn.commit()
    conn.close()

    reopened = Workspace(path).connect()
    assert reopened._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    names = {p["name"] for p in reopened.people(include_inactive=True)}
    assert names == {"Filipe", "Rita"}
    assert all(p["email"] == "" for p in reopened.people(include_inactive=True)), (
        "the migration invented an address instead of leaving it blank")


def test_the_migration_is_idempotent(tmp_path):
    path = tmp_path / "w.db"
    Workspace(path).connect().close()
    again = Workspace(path).connect()
    assert again._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_renaming_a_person_keeps_their_address(tmp_path):
    """rename_person cascades across six tables; the address must ride along rather than being
    silently cleared by a re-INSERT that forgot the new column."""
    ws = Workspace(tmp_path / "w.db").connect()
    person = ws.create_person("Luis", can_login=True, is_admin=True)
    ws.set_person_email(person["person_id"], "luis@lindoservico.pt")
    ws.rename_person("Luis", "Luís")
    assert ws.person("Luís")["email"] == "luis@lindoservico.pt"


# ── v12: the person's own signature + the profile fields that fill it (ADR-047) ───────────────────

def test_a_new_person_has_no_signature_and_that_is_a_real_state(tmp_path):
    """'' means "close with the install default", not "broken". Most people never open the editor,
    and their drafts must keep closing exactly as they did before v12."""
    ws = Workspace(tmp_path / "w.db").connect()
    person = ws.create_person("Filipe", can_login=True, is_admin=True)
    assert person["signature"] == "" and person["job_title"] == "" and person["phone"] == ""


def test_setting_the_profile_stores_the_template_verbatim(tmp_path):
    ws = Workspace(tmp_path / "w.db").connect()
    person = ws.create_person("Filipe", can_login=True, is_admin=True)
    out, converted = ws.set_person_profile(person["person_id"], signature="Abraço,\n{nome}\n{cargo}",
                                           job_title="Produção", phone="+351 912 345 678")
    assert converted is False                              # plain text in, nothing to convert
    assert out["signature"] == "Abraço,\n{nome}\n{cargo}"   # the TEMPLATE, not a rendered block
    assert out["job_title"] == "Produção" and out["phone"] == "+351 912 345 678"
    assert ws.person("Filipe")["signature"] == "Abraço,\n{nome}\n{cargo}"


def test_omitting_a_field_leaves_it_alone_rather_than_blanking_it(tmp_path):
    """A form that posts only the signature must not silently erase a phone number it never showed."""
    ws = Workspace(tmp_path / "w.db").connect()
    person = ws.create_person("Filipe", can_login=True, is_admin=True)
    ws.set_person_profile(person["person_id"], job_title="Produção", phone="912345678")
    ws.set_person_profile(person["person_id"], signature="Abraço,\n{nome}")
    row = ws.person("Filipe")
    assert row["job_title"] == "Produção" and row["phone"] == "912345678"


def test_clearing_the_signature_returns_to_the_install_default(tmp_path):
    """Reachable after someone has tried a custom one — otherwise "undo" means asking an admin."""
    ws = Workspace(tmp_path / "w.db").connect()
    person = ws.create_person("Filipe", can_login=True, is_admin=True)
    ws.set_person_profile(person["person_id"], signature="Abraço,\n{nome}")
    assert ws.set_person_profile(person["person_id"], signature="")[0]["signature"] == ""


def test_an_unfillable_placeholder_is_refused_at_the_store(tmp_path):
    """The alternative is a client email with a literal {telemovel} in it. Validation lives in the
    store, not the route, so the CLI and any later caller inherit it."""
    ws = Workspace(tmp_path / "w.db").connect()
    person = ws.create_person("Filipe", can_login=True, is_admin=True)
    with pytest.raises(ValueError, match=r"\{telemovel\}"):
        ws.set_person_profile(person["person_id"], signature="Abraço,\n{telemovel}")
    assert ws.person("Filipe")["signature"] == ""          # nothing was written


def test_a_rejected_signature_does_not_write_the_other_fields_either(tmp_path):
    """One UPDATE, validated first — a half-applied save is how a person ends up with a new job title
    and an error message telling them nothing was saved."""
    ws = Workspace(tmp_path / "w.db").connect()
    person = ws.create_person("Filipe", can_login=True, is_admin=True)
    with pytest.raises(ValueError):
        ws.set_person_profile(person["person_id"], signature="{nao_existe}", job_title="Produção")
    assert ws.person("Filipe")["job_title"] == ""


def test_line_endings_and_stray_whitespace_are_normalised(tmp_path):
    """A textarea posted from a browser sends CRLF; storing it verbatim makes every rendered
    signature carry \\r characters into a client's mail client."""
    ws = Workspace(tmp_path / "w.db").connect()
    person = ws.create_person("Filipe", can_login=True, is_admin=True)
    out, _ = ws.set_person_profile(person["person_id"], signature="\r\nAbraço,\r\n{nome}\r\n  ",
                                   job_title="  Produção  Geral ")
    assert out["signature"] == "Abraço,\n{nome}"
    assert out["job_title"] == "Produção Geral"


def test_setting_a_profile_on_an_unknown_person_raises(tmp_path):
    ws = Workspace(tmp_path / "w.db").connect()
    with pytest.raises(ValueError, match="does not exist"):
        ws.set_person_profile("PER-NOPE", signature="Abraço,\n{nome}")


def test_a_v11_database_migrates_to_v12_without_losing_anyone(tmp_path):
    """Three NEW COLUMNS on the pre-existing people table — SCHEMA's CREATE-IF-NOT-EXISTS cannot
    deliver them, so a forgotten ALTER ships a DB that throws "no such column" on the first save."""
    path = tmp_path / "w.db"
    ws = Workspace(path).connect()
    filipe = ws.create_person("Filipe", can_login=True, is_admin=True)
    ws.set_person_email(filipe["person_id"], "filipe@lindoservico.pt")
    ws.create_person("Rita", can_login=False, responsible="Filipe")
    ws.close()

    # Rewind to v11 and drop the three columns, i.e. a real pre-ADR-047 database.
    conn = sqlite3.connect(path)
    for col in ("signature", "job_title", "phone"):
        conn.execute(f"ALTER TABLE people DROP COLUMN {col}")
    conn.execute("PRAGMA user_version = 11")
    conn.commit()
    conn.close()

    reopened = Workspace(path).connect()
    assert reopened._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    people = reopened.people(include_inactive=True)
    assert {p["name"] for p in people} == {"Filipe", "Rita"}
    assert reopened.person("Filipe")["email"] == "filipe@lindoservico.pt"   # v11 data untouched
    assert all(p["signature"] == "" and p["job_title"] == "" and p["phone"] == "" for p in people), (
        "the migration invented a signature instead of leaving it blank")
    # And the column is usable immediately, which is what "no such column on first write" would fail.
    reopened.set_person_profile(reopened.person("Filipe")["person_id"], signature="Abraço,\n{nome}")


def test_renaming_a_person_keeps_their_signature(tmp_path):
    """rename_person cascades across six tables; the signature must ride along rather than being
    cleared by an UPDATE that forgot the new columns."""
    ws = Workspace(tmp_path / "w.db").connect()
    person = ws.create_person("Luis", can_login=True, is_admin=True)
    ws.set_person_profile(person["person_id"], signature="Abraço,\n{nome}", job_title="Produção")
    ws.rename_person("Luis", "Luís")
    assert ws.person("Luís")["signature"] == "Abraço,\n{nome}"
    assert ws.person("Luís")["job_title"] == "Produção"
    # {nome} is a TEMPLATE token, so the rename reaches the rendered block for free — which is the
    # reason a template is stored rather than the text someone pasted once.
    from email2data.signature import for_person
    assert for_person(ws.person("Luís"), None) == "Abraço,\nLuís"


def test_a_pasted_html_signature_is_flattened_and_the_caller_is_told(tmp_path):
    """Found by looking at the rendered page: a real signature is COPIED out of Outlook, so it
    arrives as HTML. Stored verbatim it put `<td style="padding:12px">` in a client's inbox.

    The boolean is not decoration — the textarea now holds something different from what the person
    pasted, and a save that does not say so reads as the app having mangled their signature."""
    ws = Workspace(tmp_path / "w.db").connect()
    person = ws.create_person("Filipe", can_login=True, is_admin=True)
    row, converted = ws.set_person_profile(person["person_id"], signature=(
        '<div style="font-weight:700">FILIPE COELHO</div>'
        '<div>Departamento T&eacute;cnico</div>'
        '<div><a href="#">+351&nbsp;934&nbsp;367&nbsp;794</a></div>'
        '<div><img src="logo.png" alt="LINDO SERVI&Ccedil;O"></div>'))
    assert converted is True
    assert row["signature"] == "FILIPE COELHO\nDepartamento Técnico\n+351 934 367 794"
    assert "<" not in row["signature"] and "LINDO SERVIÇO" not in row["signature"]


def test_an_image_only_html_signature_is_refused_rather_than_silently_cleared(tmp_path):
    """It flattens to nothing. Storing '' would mean "use the install default" — a save that looks
    like it worked and quietly reverted them, which is worse than a refusal that says why."""
    ws = Workspace(tmp_path / "w.db").connect()
    person = ws.create_person("Filipe", can_login=True, is_admin=True)
    ws.set_person_profile(person["person_id"], signature="Abraço,\n{nome}")
    with pytest.raises(ValueError, match="só imagens"):
        ws.set_person_profile(person["person_id"],
                              signature='<table><tr><td><img src="sig.png" alt="Assinatura">'
                                        '</td></tr></table>')
    assert ws.person("Filipe")["signature"] == "Abraço,\n{nome}", "the refusal wrote anyway"


def test_a_gmail_style_paste_with_a_css_block_does_not_trip_the_token_guard(tmp_path):
    """`<style>body{color:#000}</style>` would otherwise arrive as text, and `{color}` reads as an
    unknown placeholder — a valid paste refused for a reason nobody could act on."""
    ws = Workspace(tmp_path / "w.db").connect()
    person = ws.create_person("Filipe", can_login=True, is_admin=True)
    row, converted = ws.set_person_profile(
        person["person_id"],
        signature="<style>body{color:#000}.s{margin:0}</style><div>Filipe Coelho</div>")
    assert converted is True and row["signature"] == "Filipe Coelho"
