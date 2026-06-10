# architecture — index

How email-2-data is structured, and why. Invariants live as ADRs in the
[decisions registry](../03-decisions/index.md); this shelf carries the structural picture.

| Page | Covers |
| --- | --- |
| [module-map.md](module-map.md) | The data flow (IMAP → triage → CRM → JobSpec → Projects → export) and every module's responsibility. Start here. |
| [approach.md](approach.md) | The right-sized v1 engineering detail — the reasoning behind the module boundaries. |
| [offline-extraction-layer.md](offline-extraction-layer.md) | The red-teamed Tier-0 offline extraction layer (high-precision, deterministic). |

UI/UX design specs are reference material on the [05-reference](../05-reference/cockpit-design.md) shelf.
