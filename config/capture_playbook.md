# Capture Resolution Playbook (Conversational intake — ADR-019 §4)

Edits here change behaviour with **no code change** — treat a change like a code change (test + commit).
This file drives the **deterministic** capture→project resolver (`capture_resolve.py`, Increment 1) and
seeds the **LLM** inference/extraction prompts (Increment 2). Seeded from the existing pt-PT email
assets: the `jobspec` field questions, `labels.py`, the [gazetteer](gazetteer.csv), and the
`/api/reclassifications` human-corrected pairs. **No capture is ever auto-applied** — the resolver only
suggests; the human confirms in the Caixa de Capturas (ADR-019 §5 / R9).

## What a capture is

The staffer's own off-desk knowledge, sent from the field via Telegram: a typed note, a voice memo
(transcribed to pt-PT), or a photo/quote artifact. It is **the staffer's assertion or an artifact**, not
a covert client-call recording (ADR-019 §3). The goal is to file it against the right **project** and,
when it states a job fact, to surface the field VALUE for the human to confirm.

## Deterministic resolve — how a capture is matched to a project

Rank the **active** projects (terminal stages excluded) by how strongly the capture text names them:

- **Client identity wins.** A capture mentioning the client's name, company, or email local-part is the
  strongest signal (e.g. "o Sousa quer mais duas" → the "Estante Sousa" project).
- **Product / material words help.** Material and product terms (acrílico, MDF, alumínio, cortiça,
  vinil, placas, expositor, sinalética, troféus) match a project whose title/spec uses them.
- **Gazetteer.** When a project's client sits at a [gazetteer](gazetteer.csv) domain, that domain's note
  contributes extra match terms (e.g. corticoenetos.com → "cork").
- **Confident vs ambiguous.** The resolver pre-selects a project only when one match **strictly beats**
  the rest. A tie or a no-signal capture stays unresolved and waits for the human (or, Increment 2, the
  model) — never a guess (ADR-001: compute ∝ uncertainty).

## Aliases

Short forms the staffer says → the canonical project-facing term. Both sides are reduced to word tokens,
so an alias expands the capture text before matching. One per line: `- <alias> -> <canonical>`.

- VDH -> Violaine d'Harcourt
- Vhils -> Vhils studio
- VisionBox -> Amadeus Vision Box
- acrilico -> acrílico
- inox -> aço inox
- sinaletica -> sinalética
- expositor -> expositor display

## Job-fact vocabulary (seeds Increment 2 extraction)

When a capture states a job fact, it maps to one of the 14 `jobspec` fields. The clarifying questions
(from `jobspec.FIELDS`, pt-PT) name what each field means:

- **item** — O que pretendem produzir?
- **design_ready** — Têm o ficheiro/desenho final? Em que formato?
- **dimensions** — Quais as dimensões de cada peça?
- **material** — Em que material?
- **thickness** — Que espessura?
- **material_supplied_by** — Fornecem o material ou tratamos da compra?
- **quantity** — Que quantidade?
- **deadline** — Para quando precisam?
- **colour_finish** — Que cor ou acabamento?
- **quality_acceptance** — Precisam de amostra/prova antes da produção?
- **delivery** — Entrega, morada e instalação?
- **budget** — Têm um budget de referência?

## The one rule (shared with the spec playbook)

**Never invent.** A capture that does not state a field leaves it empty — the resolver/extractor returns
no value rather than a plausible guess. Every value is FACT (the staffer said it) and still requires the
human to confirm it before it can affect the estimable gate.
