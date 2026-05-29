# email-2-data

Read-only email triage for Lindo Serviço inboxes: scan accounts over IMAP, classify each message by
**type + urgency** with Claude (driven by an editable playbook), and surface a prioritized decision
queue so the team sees what's critical fast.

## Status

v1 in progress — milestones **M0 (read-only fetch)** + **M1 (classify + eval)**. See the approach
doc before changing anything.

## Docs

- **Vision (the north star):** [VISION.md](VISION.md) — what this is and the governing principle.
- **Roadmap (where we are / next):** [ROADMAP.md](ROADMAP.md) — phased plan and status.
- **Approach (engineering detail):** [design/approach.md](design/approach.md) — what we build now,
  the non-negotiables, and what's deliberately postponed.
- Long-term architecture: [design/draft-architectural-report.md](design/draft-architectural-report.md)
- The classifier brain (editable): [config/triage_playbook.md](config/triage_playbook.md)

## Architecture modules (Phase 2/3 scaffolded — contracts, not yet implemented)

`signals.py` (Tier-0 direction/bulk) · `forwarding.py` (mine forwarded originals) ·
`store.py` (SQLite knowledge store) · `cascade.py` (Tier 0→1→2 cost-tiered router). See ROADMAP.

## Quick start (once implemented)

```bash
pip install -e ".[dev]"
cp config/settings.example.json config/settings.json   # edit hosts/accounts
export EMAIL2DATA_GERAL_PASSWORD=...    # IMAP app password (read-only account ideal)
export ANTHROPIC_API_KEY=...

email2data fetch       # M0: pull recent mail → corpus/*.eml (read-only)
email2data classify    # M1: playbook + Claude → out/results.jsonl + table
email2data eval        # score against labels/labels.csv
```

## Non-negotiables

Read-only IMAP (EXAMINE; never STORE/DELETE/APPEND) · never silently `IGNORE` a possible client ·
secrets via env vars only · raw bodies never logged. Details in
[design/approach.md](design/approach.md).
