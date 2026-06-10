# reference — index

Exact values, schemas, and contracts. The code is always the source of truth; these pages
mirror it for discoverability and must be updated in the same commit as a change that
invalidates them.

| Page | Covers |
| --- | --- |
| [triage-schema.md](triage-schema.md) | The verdict model — the four axes, vocabularies (counterparty/purpose/direction/priority), `derive_priority`, `TriageResult`, `Entities`, structured-output contracts, versioning, Phase-B spec schema. |
| [data-stores.md](data-stores.md) | `out/` files and the three SQLite stores, their recoverability tiers, migration discipline, dangling refs, and the Project lifecycle. |

See also the editable runtime config (not code, not docs): `config/triage_playbook.md`,
`config/gazetteer.csv`, `config/spec_playbook.md`, `config/reply_playbook.md`.
