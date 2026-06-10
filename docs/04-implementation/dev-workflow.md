# Implementation — dev workflow

| Field | Value |
| --- | --- |
| Type | Implementation |
| Status | Active |
| Last reviewed | 2026-06-10 |

How to set up, change, and verify the code. The quality bar is the
[CLAUDE.md](../../CLAUDE.md) definition of done + `standards/05-definition-of-done.md`.

## Setup

```bash
pip install -e ".[dev,web,vertex]"          # repo also ships a .venv
cp config/settings.example.json config/settings.json
cp .env.example .env                          # fill secrets (gitignored)
```

Python ≥ 3.11. Entry point: `email2data = email2data.cli:main` (`pyproject.toml`).

## The change loop (every L2+ change)

1. **Test** — add a regression test in the matching `tests/test_<module>.py` that fails before and
   passes after. No stubs, no `pytest.skip`.
   ```bash
   .venv/bin/python -m pytest -q
   ```
2. **Docs** — update every doc the change invalidates **in the same commit**: the relevant ADR
   ([03-decisions](../03-decisions/index.md)) if an invariant moves, [05-reference](../05-reference/index.md)
   for schema/values, [02-architecture](../02-architecture/index.md) for structure, or the
   `config/*_playbook.md` for classifier behavior. A playbook edit is a behavior change — bump
   `EXTRACTOR_VERSION` in `schema.py` if it changes verdicts.
3. **QA** — `ruff check src tests`; edge cases (empty/None/malformed MIME, non-ASCII charsets); the
   [non-negotiables](../03-decisions/index.md); idempotency. Report findings explicitly.

## Where things live

- The module map and data flow: [../02-architecture/module-map.md](../02-architecture/module-map.md).
- All LLM calls go through `llm.py` ([ADR-012](../03-decisions/adr-012-shared-llm-provider-dispatch.md)) —
  don't call an SDK directly from a stage.
- The verdict vocabularies + `derive_priority` are in `schema.py`
  ([reference](../05-reference/triage-schema.md)) — the enums are the single source of truth.

## Conventions

User-facing strings are Portuguese (pt-PT); code/comments/commits/docs are English. Commits are
small and self-contained and explain *why*; a bug fix references the test that would have caught it.
