# email-2-data

Read-only email triage for Lindo Serviço inboxes: scan accounts over IMAP, classify each message by
**counterparty · purpose · direction · priority** (Gemini on Vertex, driven by an editable playbook,
with a free deterministic pre-filter), and surface a prioritized decision queue so the team sees
what's critical fast.

## Status

Phases **0 (foundation)**, **1 (taxonomy migration + baseline)**, and **2 (Tier-0 signals + gazetteer)**
done. The pipeline emits the counterparty/purpose/direction model and runs a two-tier cascade:
deterministic header signals decide obvious bulk offline (zero tokens); everything else goes to the
cheap LLM with those signals + a gazetteer hint attached. See [ROADMAP.md](ROADMAP.md).

## Docs

- **Vision (the north star):** [VISION.md](VISION.md) — what this is and the governing principle.
- **Roadmap (where we are / next):** [ROADMAP.md](ROADMAP.md) — phased plan and status.
- **Offline-layer design (red-teamed):** [design/offline-extraction-plan.md](design/offline-extraction-plan.md).
- **Approach (engineering detail):** [design/approach.md](design/approach.md).
- The classifier brain (editable): [config/triage_playbook.md](config/triage_playbook.md) ·
  the gazetteer (editable): [config/gazetteer.csv](config/gazetteer.csv).

## Pipeline / modules

```text
fetch.py    read-only IMAP → corpus/*.eml        (M0)
envelope.py raw .eml → normalized fields          (robust MIME/charset)
signals.py  Tier-0 header facts: direction, bulk/automated, looks-forwarded
extract.py  Tier-0 deterministic values: nif/iban (authoritative) + amount/date/doc candidates
store.py    gazetteer: email-or-domain → counterparty hint (SQLite, hand-curated, a PRIOR not a verdict)
cascade.py  Tier-0 bulk-IGNORE offline  →  Tier-1 classifier.py (Gemini) with facts+values+hint attached
crm.py      CRM PoC: interactions (event log) + contacts (person rollup) from headers+verdicts (no LLM)
schema.py   TriageResult + structured-output contracts + priority derivation
cli.py      fetch | triage | eval | crm
```

## Quick start

```bash
pip install -e ".[dev]"            # (repo ships a .venv; or make your own)
cp config/settings.example.json config/settings.json   # set IMAP host/accounts + Vertex project
export EMAIL2DATA_<ACCOUNT>_PASSWORD=...   # read-only IMAP password (provided per session, never stored)
gcloud auth application-default login      # Vertex/Gemini uses ADC — no API key

email2data fetch       # read-only pull → corpus/*.eml
email2data triage      # Tier-0 signals → Tier-1 Gemini → out/results.jsonl + priority table
email2data eval        # score counterparty/priority vs labels/worksheet.csv
```

LLM provider is configurable in `settings.json` (`llm.provider`: `vertex_gemini` via gcloud ADC, or
`anthropic` via `ANTHROPIC_API_KEY`). This project uses Vertex Gemini (the GCP project has Vertex
enabled but not the Anthropic models).

## Non-negotiables

Read-only IMAP (EXAMINE + `BODY.PEEK`; never STORE/DELETE/APPEND) · counterparty is from **Lindo's
POV**, decided by the body not the domain · **only header signals may bin offline**; never silently
IGNORE a possible client · secrets via env/ADC only · raw bodies never logged. See
[VISION.md](VISION.md) and [design/approach.md](design/approach.md).
