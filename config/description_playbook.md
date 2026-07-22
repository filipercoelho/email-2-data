# Description Playbook (DESCRIÇÃO da proposta / fatura)

Deterministic skeleton for the **descritivo do produto/serviço** — the text that goes in the
`DESCRIÇÃO` column of an ARTSOFT proposta (C100) or fatura (V001). It is **not** an LLM prompt:
`descdraft.build_description` assembles the block by splicing **confirmed JobSpec fields** into
the template below, with no model in the loop. **A human reviews, edits, and puts it in ARTSOFT;
the system never issues a document.**

Edit the prose freely (pt-PT). Two rules:

1. Keep the `{tokens}` intact — they are replaced with facts from the JobSpec.
2. A line/clause whose token has no confirmed value renders a **gap marker** (`[[MATERIAL?]]`) so
   the drafter cannot miss it. The one exception is the `Obs.:` line, which is dropped entirely
   when empty — it is a commercial caveat, not a spec field.

**A playbook change is a behaviour change** — treat it like a code change (test + doc).

## House style — the AVERAGE of all documents analysed

This template **represents the average style of the whole analysed corpus** (59 sent PDFs;
`out/wording-analysis/`), taken as the modal (most-frequent) form on each independent dimension —
not one hand-picked style. The measured averages that drive it:

| Dimension | Average across the 59 docs | Used here |
| --- | --- | --- |
| Macro-structure | **prose** 39/59 (66%) vs labelled 20/59 | one flowing sentence, **not** bullet lines |
| Shape | **header + 1 body segment** is the mode (32/59) | title line + one sentence |
| Opener verb | **`Produção de`** dominant (15) | default opener |
| Material preposition | **`em`** 45 vs `de` 10 (82%) | `em {material}` |
| Header casing | **title/sentence** 33 vs UPPER 12 | **passed through as typed** (not upper-cased) |
| Dimension lead-in | `c/` / parenthetical common | `c/ {dimensoes}` |
| Axis order | `L x A x P` | preserved as given |

The other two observed styles (labelled `Suporte:`/`Medidas:` blocks; `Material:`/`Técnica:`
blocks) are the minority and are **not** emitted — the point of the module is to converge future
documents on the representative average, not to reproduce the historical spread.

The deterministic skeleton keeps the joins simple (comma-separated clauses). The optional AI
polish (`descdraft.polish_description`) turns it into natural prose **while keeping every confirmed
fact verbatim** — that is where connective smoothing belongs, because it is fact-checked.

---

{titulo}

{processo} {item} em {material}, c/ {dimensoes}, {acabamento}.

Obs.: {observacoes}

---

## Style defaults (not facts)

`{processo}` falls back to **`Produção de`** when the job's opener is unconfirmed. This is a
*style* default, not a claim about the job: "produção" is true of everything the shop makes,
whereas `Corte Laser de` or `Impressão Direta` are factual claims and are only emitted when the
caller supplies them. Never widen this fallback to a specific process. The registry `process`
field is **internal** ("(interno) Definir o processo de fabrico") and never reaches this text.

## Controlled vocabulary — observed surface forms

Reference pick-lists extracted from the 59-document corpus, with counts. Use these spellings.
They are for the drafter and for the polish prompt; the module never picks a term on its own —
every value comes from the JobSpec.

### Opener (processo)

`Produção de` (15) · `Produção e Fornecimento de` (2) · `Corte de` (4) · `Corte Laser de` ·
`Corte tipográfico` (5) · `Impressão Direta HQ` · `Impressão 3D` (2) · `Aplicação de` (3) ·
`Laminação e aplicação de` · `Recorte em plotter de vinil` · `Construção de` · `Recuperação de` ·
`Reparação de` · `Substituição` · `Instalação de`

### Materiais (após `em`)

`acrílico` (8) — cristal / foscado / opalino / transparente / cor · `MDF` (8) — sempre em
maiúsculas, nunca expandido · `EPS` (7) — `EPS 100`; sinónimo corrente `esferovite` ·
`Compósito de Alumínio` (4) · `inox` (3) — `inox 316`, `aço inox micro escovado` · `cortiça` (2) ·
`Hypalon Neoprene` (2) · `vinil` (2) — `vinil oracal 638 wall art` · `Biadesivo Ref Nitto 5115` (2) ·
`Valchromat` · `policarbonato cristal` · `fenólico` · `contraplacado de choupo` · `Pinho` ·
`Veludo` · `PU` · `resina/cimento` · `Fibra de Vidro` · `filamento de Asa` · `Ferro` · `Aço` ·
`espuma de poliuretano`

### Acabamentos

`Pintura` — `pintada a branco` (3), `pintura simples a azul` (2), `pintado a 1 cor`,
`Pintura geral a Cor de Rosa`, `Pintura a simular alumínio` · `sem pintura` (2) ·
`Lacagem a azul` · `Gravação` — `Gravação a baixo relevo`, `gravação personalizada em baixo
relevo`, `Gravação Laser`, `Corte e gravação laser no formato` · `esmaltagem` — `esmaltagem a 2
cores (verde e branco)`, `esmaltagem de texto` · `Impressão direta a cores` ·
`impressão direta CMYK - 1 Face` · `impressão UV de texto` · `Corte especial` — `(Cantos
redondos)`, `no formato`, `laser no formato` · `limpeza de arestas` · `descasque do vinil` ·
`aplicação de película para transfer` · `Aplicação de Biadesivo` (2) · `sem acabamentos`

### A cor leva a preposição `a`

`pintada a branco`, `a 2 cores`, `a 1 cor` — 10/10 no corpus. Nunca `em branco` / `de branco`.
Quando a cor ainda não está fechada, o corpus escreve `(RAL a definir)`.

### Proveniência (frequente no fecho da frase)

`conforme referências recebidas` · `conforme ficheiros enviados` · `conforme arte-final
fornecida` · `conforme desenhos recebidos` · `(fornecido pelo cliente)`.
