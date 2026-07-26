#!/usr/bin/env bash
# Take a verified, restorable snapshot of the PRECIOUS stores before a schema migration.
#
# Why this exists (and why `cp` is not it): these DBs run in WAL mode, so the live rows sit in the
# `-wal` sidecar until a checkpoint. Copying only `workspace.db` yields a database that opens fine
# and is EMPTY or stale — a backup that looks successful and silently is not. `VACUUM INTO` asks
# SQLite itself to write a consistent, fully-checkpointed copy, which is the only cheap way to be
# sure. See docs/05-reference/data-stores.md.
#
# workspace.db is "never auto-rebuilt" (CLAUDE.md): if a migration corrupts it, the human decisions,
# projects and capture queue are gone. Nothing in bin/ could take a safe copy before this script.
#
# Usage:
#   bin/backup-workspace.sh                 # snapshot into backups/<utc-timestamp>/
#   bin/backup-workspace.sh --out DIR       # snapshot into a chosen directory
#   bin/backup-workspace.sh --verify DIR    # re-check an existing snapshot, take nothing
#
# Exit 0 only when every snapshot opened, passed integrity_check, and matched the source row counts.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR=""
VERIFY_ONLY=""

while [ $# -gt 0 ]; do
    case "$1" in
        --out)    OUT_DIR="${2:?--out needs a directory}"; shift 2 ;;
        --verify) VERIFY_ONLY="${2:?--verify needs a directory}"; shift 2 ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

# The precious ones. crm.db/sync.db are deliberately absent: both are regenerable (`email2data crm`,
# and sync.db re-bootstraps), so snapshotting them would imply a guarantee we do not need to make.
#
# auth.db is here (ADR-039) because losing it is not merely data loss: `has_any_credentials()` goes
# False, which re-opens the unauthenticated /setup route — and /setup mints an ADMIN. On a LAN bind
# that hands the whole app to whoever reaches it first. It also strands every `people` row in
# workspace.db, since the two stores are joined by person_id with no cross-file FK: restoring one
# without the other leaves identities with no credentials, or credentials pointing at nobody. They
# must be snapshotted together, in the same run, or the pair is inconsistent on restore.
STORES=("workspace.db" "auth.db")

PY="${ROOT}/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

snapshot_and_verify() {
    local src="$1" dest="$2"
    "$PY" - "$src" "$dest" <<'PYEOF'
import sqlite3, sys
from pathlib import Path

src, dest = Path(sys.argv[1]), Path(sys.argv[2])

# Read the source through a read-only URI so a running webapp is never disturbed, and so this script
# can never be the thing that writes to the precious DB.
conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
try:
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    before = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    user_version = conn.execute("PRAGMA user_version").fetchone()[0]
    dest.unlink(missing_ok=True)
    conn.execute("VACUUM INTO ?", (str(dest),))   # consistent + fully checkpointed, WAL included
finally:
    conn.close()

# Verify by OPENING the copy, not by trusting that the command returned 0.
check = sqlite3.connect(f"file:{dest}?mode=ro", uri=True)
try:
    integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        print(f"  FAIL integrity_check on the copy: {integrity}"); sys.exit(1)
    after = {t: check.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    copy_version = check.execute("PRAGMA user_version").fetchone()[0]
finally:
    check.close()

if after != before:
    drift = {t: (before[t], after[t]) for t in before if before[t] != after[t]}
    print(f"  FAIL row counts differ (source, copy): {drift}"); sys.exit(1)
if copy_version != user_version:
    print(f"  FAIL user_version {user_version} -> {copy_version}"); sys.exit(1)

total = sum(before.values())
print(f"  OK  schema v{user_version}, {len(tables)} tables, {total} rows, "
      f"{dest.stat().st_size / 1024:.0f} KiB -> {dest.name}")
PYEOF
}

if [ -n "$VERIFY_ONLY" ]; then
    echo "Verifying snapshot in ${VERIFY_ONLY}"
    rc=0
    for store in "${STORES[@]}"; do
        snap="${VERIFY_ONLY}/${store}"
        if [ ! -f "$snap" ]; then
            echo "  FAIL missing: ${snap}"; rc=1; continue
        fi
        "$PY" - "$snap" <<'PYEOF' || rc=1
import sqlite3, sys
from pathlib import Path
p = Path(sys.argv[1])
c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
try:
    if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        print(f"  FAIL integrity_check: {p.name}"); sys.exit(1)
    tables = [r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    rows = sum(c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables)
    print(f"  OK  {p.name}: schema v{c.execute('PRAGMA user_version').fetchone()[0]}, "
          f"{len(tables)} tables, {rows} rows")
finally:
    c.close()
PYEOF
    done
    exit $rc
fi

if [ -z "$OUT_DIR" ]; then
    OUT_DIR="${ROOT}/backups/$(date -u +%Y%m%dT%H%M%SZ)"
fi
mkdir -p "$OUT_DIR"

echo "Snapshotting precious stores -> ${OUT_DIR}"
rc=0
for store in "${STORES[@]}"; do
    src="${ROOT}/out/${store}"
    if [ ! -f "$src" ]; then
        echo "  SKIP ${store} (absent)"; continue
    fi
    snapshot_and_verify "$src" "${OUT_DIR}/${store}" || rc=1
done

if [ "$rc" -ne 0 ]; then
    echo "BACKUP FAILED — do not migrate." >&2
    exit 1
fi
echo "Backup verified. Restore with:"
for store in "${STORES[@]}"; do
    [ -f "${OUT_DIR}/${store}" ] && echo "  cp ${OUT_DIR}/${store} out/${store}"
done
# Restoring one without the other leaves people with no credentials (or credentials pointing at a
# person_id that no longer exists) — the cross-file join has no FK to catch it.
echo "(stop the containers first; remove any out/*.db-wal / -shm alongside them; restore BOTH stores"
echo " from the SAME snapshot — workspace.db and auth.db are joined by person_id.)"
