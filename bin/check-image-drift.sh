#!/usr/bin/env bash
# Is the running container actually running MY code?
#
# `docker compose ps` and /healthz answer "is a container alive", which is NOT the same question —
# both report healthy while the image serves code built days ago. That gap is how a stale UI gets
# reported as live (it happened on 2026-07-20). This compares the image's baked-in source against
# the working tree and is the only trustworthy answer.
#
# Scope = EVERY path the Dockerfile COPYs, which is what "baked into the image" means:
#   src/**/*.py, pyproject.toml, README.md, bin/healthcheck.py  (Dockerfile lines 13/14/26).
# Deliberately NOT config/, corpus/ or out/ — those are bind mounts, already live, and would produce
# permanent false drift.
#
# Keep the two `find` expressions below equal to that list. They were narrower than the Dockerfile
# until 2026-07-25 (README.md was COPY'd but not compared), so an edit to it left the image stale
# while this script reported OK — a green result on a partial comparison, i.e. exactly the proxy
# failure the script exists to prevent. IF YOU ADD A `COPY` LINE, ADD THE PATH TO BOTH `find`s.
#
# KNOWN BLIND SPOTS (a clean result does not prove these are current) — none of them are IN the image:
#   - Dockerfile      — the build recipe is not COPY'd into the image, so it cannot be compared here.
#   - docker-compose.yml, .env — mounted/compose-level, not baked; changing either needs
#                       `docker compose up -d`, and this check will still say OK.
# After touching any of those three, redeploy on the strength of the table in CLAUDE.md, not on a
# green run of this script.
#
# Exit 0 = container matches worktree. Exit 1 = STALE, rebuild. Exit 2 = could not determine.
set -uo pipefail

SERVICE="${1:-email2data}"
cd "$(dirname "$0")/.." || exit 2

# Hash "<sha>  <relative path>" pairs, sorted, then hash the whole listing. Paths are relative on
# both sides (repo root vs /app) so the two listings are directly comparable — verified by the fact
# that unchanged files hash identically across the boundary.
if ! container=$(docker compose exec -T "$SERVICE" sh -c \
    'cd /app && find src pyproject.toml README.md bin/healthcheck.py -type f \( -name "*.py" -o -name "pyproject.toml" -o -name "README.md" \) | sort | xargs sha256sum' 2>/dev/null | sha256sum | cut -d" " -f1); then
  echo "UNKNOWN  could not read source from service '$SERVICE' — is it running? (docker compose ps)" >&2
  exit 2
fi

if [ -z "$container" ] || [ "$container" = "$(printf '' | sha256sum | cut -d' ' -f1)" ]; then
  echo "UNKNOWN  service '$SERVICE' returned no source listing — refusing to report a clean result" >&2
  exit 2
fi

worktree=$(find src pyproject.toml README.md bin/healthcheck.py -type f \( -name '*.py' -o -name 'pyproject.toml' -o -name 'README.md' \) \
  | sort | xargs shasum -a 256 | awk '{print $1"  "$2}' | sha256sum | cut -d' ' -f1)

if [ "$container" = "$worktree" ]; then
  echo "OK  container '$SERVICE' matches the working tree."
  exit 0
fi

echo "STALE  container '$SERVICE' is NOT running your working tree."
echo
echo "  differing files:"
# Per-file detail so the report names what actually drifted rather than just failing.
tmp_container=$(mktemp) && tmp_worktree=$(mktemp)
trap 'rm -f "$tmp_container" "$tmp_worktree"' EXIT
docker compose exec -T "$SERVICE" sh -c \
  'cd /app && find src pyproject.toml README.md bin/healthcheck.py -type f \( -name "*.py" -o -name "pyproject.toml" -o -name "README.md" \) | sort | xargs sha256sum' 2>/dev/null \
  | awk '{print $2" "$1}' | sort > "$tmp_container"
find src pyproject.toml README.md bin/healthcheck.py -type f \( -name '*.py' -o -name 'pyproject.toml' -o -name 'README.md' \) \
  | sort | xargs shasum -a 256 | awk '{print $2" "$1}' | sort > "$tmp_worktree"
join -j1 -a1 -a2 -o 0,1.2,2.2 -e MISSING "$tmp_container" "$tmp_worktree" \
  | awk '$2 != $3 {print "    " $1}'
echo
echo "  fix:  .venv/bin/python -m pytest -q && docker compose up -d --build"
exit 1
