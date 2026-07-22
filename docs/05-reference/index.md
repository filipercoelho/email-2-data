# reference — index

Exact values, schemas, and contracts. The code is always the source of truth; these pages
mirror it for discoverability and must be updated in the same commit as a change that
invalidates them.

| Page | Covers |
| --- | --- |
| [triage-schema.md](triage-schema.md) | The verdict model — the four axes, vocabularies (counterparty/purpose/direction/priority), `derive_priority`, `TriageResult`, `Entities`, structured-output contracts, versioning, Phase-B spec schema. |
| [data-stores.md](data-stores.md) | `out/` files and the three SQLite stores, their recoverability tiers, migration discipline, dangling refs, and the Project lifecycle. |
| [cockpit-design.md](cockpit-design.md) | The cockpit / triage-delivery UI spec (Fila, Para Ti, Projetos, Contrapartes) — layout, trust grammar, motion, a11y. |
| [intake-bot-contract.md](intake-bot-contract.md) | The Telegram intake surface — commands, accepted message kinds, the reply sequence (incl. the mandatory voice transcription report), per-kind errors, and the pick-list button format. |
| [client-email-composer.md](client-email-composer.md) | The Projetos client-email composer — the 8-purpose registry + input kinds, editable templates/reasons, the `/draft[/polish]` route shapes, the `_FACT_RE` verbatim-number guard (with its FP/FN boundaries), the PT/EN/FR/ES output language, and the `POST /api/translate` reading aid. |

See also the editable runtime config (not code, not docs): `config/triage_playbook.md`,
`config/gazetteer.csv`, `config/spec_playbook.md`, `config/reply_playbook.md`,
`config/client_email_*_template.md`, `config/client_email_reject_reasons.md`,
`config/client_email_polish_playbook.md`, `config/translation_playbook.md`.
