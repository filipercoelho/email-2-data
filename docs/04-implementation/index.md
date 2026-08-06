# implementation — index

How to build, change, and verify the code.

| Page | Covers |
| --- | --- |
| [dev-workflow.md](dev-workflow.md) | Setup, the test/docs/QA change loop, where things live, conventions. |
| [capture-revision-chain-plan.md](capture-revision-chain-plan.md) | Execution plan for [ADR-022](../03-decisions/adr-022-capture-revision-chain.md): the six confirmed lineage defects, the WP-A→G order (and why it inverts the request), and the open questions still owned by the owner. |
| [fila-mesa-phases.md](fila-mesa-phases.md) | Execution plan for [ADR-033](../03-decisions/adr-033-fila-mesa-com-foco.md): P0 quick relief → P1 Mesa split-pane → P2 live+joins+vistas → P3 actions (Adiar v9, contextual R, Tratar agora) — each item with its failing-first test, plus sequencing risks. |
| [fila-evidence-and-narrative-phases.md](fila-evidence-and-narrative-phases.md) | **ALL FIVE PHASES BUILT** — P1–P3 2026-08-05, P4–P5 2026-08-06 behind [ADR-054](../03-decisions/adr-054-llm-derived-body-fragments-live-in-out-sidecars.md). Linking each extracted value in «Registo do fio» to the sentence that produced it, and «Evolução da conversa». P1 wrap ✅ → P2 collapse-not-delete the signature ✅ → P3 deterministic client-side spans ✅ → P4 locate pass ✅ → P5 thread narrative ✅. Carries the measurements that killed the search-for-the-value fallback (37% hit rate) and the change-diff timeline (94% no compression). **Read the two «As built» blocks before quoting §3 or §4** — three of this document's own load-bearing claims did not survive re-measurement, including the 50% echo rate, which is an artifact of pooling and reads **27%** on the rows Phase 4 actually serves. |
| [fila-evidence-p4-p5-handover.md](fila-evidence-p4-p5-handover.md) | **CLOSED** — the P4/P5 handover, kept only to score its own three findings: two were wrong (the echo rate inverted the conclusion; truncation costs 9 ledger rows of 440, not "the cheapest remaining win"), one was right and bigger than it sounded. Also records the owner scope decision that measurement superseded. |

Day-to-day running (CLI, Docker, auth) is on the [07-operations](../07-operations/running.md) shelf.
