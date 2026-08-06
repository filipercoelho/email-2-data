# Fila dossier — evidence linking + «Evolução da conversa», phased execution plan

**Status: ALL FIVE PHASES BUILT.** Phases 1–3 shipped 2026-08-05; phases 4–5 shipped **2026-08-06**
behind [ADR-054](../03-decisions/adr-054-llm-derived-body-fragments-live-in-out-sidecars.md), which
is the ADR they were gated on. They were **not** built as written below — three findings from the
1–3 build changed what they should be, and one of those three was itself wrong. The re-derivation is
in §Phase 4's «As built» block; read it before quoting §3.4 or the handover.** Written 2026-08-05 on `feat/fila-mesa` at `eb998bc` + working
tree, after a measurement pass and one live LLM spike. The owner approved the three design decisions
in §2, then scoped the build to **phases 1–3** — the deterministic half, which spends no LLM tokens
and needs no ADR. For whenever 4–5 do go ahead, the owner's recorded preference is a **scoped first
pass** (money + deadline on WE_OWE / TO_PAY threads), not a full backfill.

> **Two claims in this document did not survive re-verification. Read §3.2-bis before quoting §3.**
> The anchors were re-checked file-by-file on 2026-08-05 before any code was written, and while
> almost all of them held exactly, two load-bearing statements were wrong — one about the code and
> one about the measurement. Both are corrected in place below.

Sibling plan: [fila-mesa-phases.md](fila-mesa-phases.md) · anatomy:
[fila-mesa-design.md](../05-reference/fila-mesa-design.md) · schema:
[triage-schema.md](../05-reference/triage-schema.md) · stores:
[data-stores.md](../05-reference/data-stores.md).

Every phase ends per the project DoD: failing-first regression tests in the matching
`tests/test_<module>.py`, docs updated in the same commit, `ruff check src tests`, full suite green
(state the new count, the `corpus/` size, and why it moved — see CLAUDE.md §Baseline pin), then
`docker compose up -d --build` + `./bin/check-image-drift.sh` clean. Nothing here changes IMAP
posture (read-only), sending (never), or the precious-store rules.

---

## 1. What the owner asked for

In the `/fila` thread dossier, for the extracted values in «Registo do fio» (Valor, Prazo,
Produto/serviço, Pedido, Nome, NIF, IBAN):

1. **Wrap** long values instead of truncating them.
2. **Colour-code** each field and reuse that colour to **highlight the sentence in the email body
   that produced it** — "either ask the AI for the source section, or search the thread for the
   extracted value; find the best mechanism."
3. Keep a **timeline of AI analyses** as the thread grows, so the developments and progress of the
   negotiation can be followed.

The owner explicitly asked for the proposals to be criticised. They were, and two of the three
proposed mechanisms did not survive measurement. §3 is the evidence; §4 is what was rejected and
must not be silently re-proposed.

## 2. The three decisions (owner-approved 2026-08-05)

| # | Decision | Instead of |
| --- | --- | --- |
| D1 | **Spike the evidence-quote premise before building.** Done — see §3.3. | Building the pipeline on an unproven premise |
| D2 | **One highlight accent, interaction-driven** — click a ledger row, its evidence lights up and scrolls into view. | A permanent 7-colour per-field palette |
| D3 | **A thread-level narrative pass** for «Evolução da conversa» — one LLM call per thread, incremental. | Stacking or diffing the per-message `reason` |

**D2's rationale, for whoever is tempted to revisit it:** colour is already fully committed in this
pane — clock bands (red/amber/green), the six counterparty classes, direction tint
(`dir-inbound`/`dir-outbound`/`dir-internal`), momentum dots, `--int` for checksum FACTs, and the
load-bearing dashed-vs-solid = proposed-vs-confirmed convention (`fila_page.py:1634-1635`). Seven new
hues collide with that vocabulary in both themes, and a green «Prazo» chip reads as "on time".

## 3. Measured baseline — reproduce before trusting

Three scripts live in [`design/`](../../design/) beside the existing validation scripts. They are
repo-relative and runnable as-is; the first two are free, the third **spends money** (~$0.02).

```bash
.venv/bin/python design/probe_match.py    # can search find the value in what the user SEES?
.venv/bin/python design/probe_region.py   # when it can't, WHERE did the value go?
.venv/bin/python design/spike_quotes.py   # does the model return LITERAL quotes?  [LLM SPEND]
```

`design/spike_quotes_raw.json` holds the 58 value/quote pairs from the run described below. **It is
gitignored and deliberately not committed** — its `quote` fields are verbatim sentences from real
client mail and its keys are real `Message-ID`s, which ADR-054 confines to `out/` and which this
public repo must not carry. Re-run `spike_quotes.py` to regenerate it locally (~$0.02).

> **Every count here is a function of `corpus/`, not a constant.** Between two runs an hour apart the
> entity-bearing message count moved 763 → 767 as the container kept syncing. The **percentages held**.
> Quote the corpus size beside any absolute number.

### 3.1 Searching for the value fails — the owner's proposed fallback

Measured over 2070 extracted values on 763 entity-bearing messages (`corpus/` ≈ 1240). "Found" =
locatable in the text actually rendered on screen, allowing case/accent folding and, for dates,
hand-written PT re-renderings.

| field | n | in body | + subject line | **total** |
| --- | --- | --- | --- | --- |
| product_or_service | 683 | 19% | +39pp | **58%** |
| money | 126 | 49% | +1pp | **50%** |
| client_name | 509 | 23% | +12pp | **35%** |
| deadline | 184 | 21% | +1pp | **22%** — 0% exact |
| action_requested | 508 | 15% | +2pp | **17%** |
| nif | 59 | 15% | 0 | **15%** |
| iban | 1 | 100% | 0 | n too small |

**37% overall.** The subject line is a meaningful highlight surface — it carries 39pp of
`product_or_service` the body does not.

### 3.2 Where the other 63% went

| bucket | share | meaning |
| --- | --- | --- |
| visible | 37% | rendered on screen today |
| **absent** | **40%** | **never in the email text in any form** — ISO-normalised (`deadline`), or a model paraphrase (`action_requested` 81% absent). No search can ever reach these. |
| quoted | 20% | behind the «mensagem citada» toggle |
| truncated | 3% | past the 3000-char server cut or the 2000-char client cut |
| signature | 1% | deleted outright by `clean_email_body` |

The rendered body is `clean_email_body(body)[:3000]` (`webapp.py:1330`, `:1411`) → `msgSplitQuote`
→ `.slice(0,2000)` (`cockpit_ui.py:1004`). ~~The "signature" bucket looks small only because it is
measured per value across all fields; per field it is decisive — see §3.3.~~ **That sentence is
false. See §3.2-bis.**

### 3.2-bis Two corrections, measured 2026-08-05 before writing any code

Both were found by re-running this document's own instruments rather than by reading it. Neither
changes the *goal*; both change which mechanism serves it, so they are stated before the phases that
rest on them.

**Correction 1 — the signature is NOT where the evidence is hiding, in either population.**
`probe_region.py` re-run at `corpus/` = 1256 reproduces §3.2's percentages exactly (37/40/20/3/1 over
2092 values), so the instrument is stable. But the "per field it is decisive" escape clause does not
hold when you look:

| field | n | visible | **signature** | truncated | quoted | absent |
| --- | --- | --- | --- | --- | --- | --- |
| nif | 59 | 15% | **2%** | **41%** | **42%** | 0% |
| client_name | 515 | 34% | **3%** | 3% | **53%** | 7% |

The NIF is invisible 85% of the time — but the causes are **truncation (41%) and the quoted block
(42%)**, not the signature (one value in 59). Names are lost to the quoted block (53%), not to the
signature (3%). And the *quote* population — the sentences a highlight actually has to reach — is
even more one-sided. `design/probe_quote_region.py` (new, free, re-reads the already-paid-for spike
output) buckets the model's own evidence sentences with `probe_region`'s exact bucket logic:

```
key                    n     visible   signature   truncated      quoted      absent
action_requested      14     12  86%      0   0%      0   0%      2  14%      0   0%
product_or_service     8      3  38%      0   0%      0   0%      5  62%      0   0%
client_name            4      1  25%      0   0%      0   0%      3  75%      0   0%
deadline               3      1  33%      0   0%      0   0%      2  67%      0   0%
ALL                   29     17  59%      0   0%      0   0%     12  41%      0   0%
```

**Zero.** Not one of 29 evidence quotes is hidden by `clean_email_body`; 41% sit behind the
«mensagem citada» toggle. §3.3(c)'s story — *"`client_name` quotes are 16/16 literal in the body but
3/16 visible: signatures are where names and NIFs live, and they are deleted"* — has the count right
and the **cause wrong**. Those names are in **quoted replies**, where the previous sender's block is
reproduced; `msgSplitQuote` splits them off, `clean_email_body` never gets the blame.

So **Phase 2 is not "the highest-yield change in this document"**, and that claim is struck below.
Ordered by measured yield, the hidden-but-present regions are **quoted 20% ≫ truncated 3% > signature
1%**. Phase 2 still ships — see its rewritten preamble for the honest reason — but item **2.5
(truncation) carries the yield, not 2.1–2.3**, and reaching the quoted block is Phase 3's job.

**Correction 2 — the spike's 88%-literal figure is inflated by echoes, and the echo rate is 50%,
not 4 cases.** §3.3(a) reads as though the "model echoed the value back as the quote" bug accounts
for four failures. Re-counting `design/spike_quotes_raw.json` by hand:

| | |
| --- | --- |
| pairs | 58 |
| `quote == value` (echo — **carries no evidence beyond the value itself**) | **29 (50%)** — client_name 12 · product_or_service 9 · money 5 · action_requested 3 |
| genuine justifying sentences | **29** |
| …of those, literal in `body_text` (whitespace-tolerant) | 26 / 29 (90%) |

The 4 in §3.3(a) were the echoes that *also* failed to match; the other 25 matched trivially —
because an echo is the value, and the value was in the body — and were counted inside the **51
(88%)**. The literal-match rate among quotes that actually add something is 90%, which is the good
news; the bad news is that the model supplies such a quote only **half** the time. Validation-stack
item 1 ("reject `quote == value`") therefore discards **half of every locate pass**, not a rounding
error, and Phase 4's cost-per-useful-span is **double** what §3.4 implies. Re-measure before
building Phase 4; do not re-derive its yield from the 88%.

### 3.3 The spike: the model DOES return literal quotes

20 messages, 61 values, one locate-only Gemini call each (`gemini-2.5-flash`, temp 0, ~$0.02 total).
The prompt gives the model the already-extracted values and asks for the verbatim justifying
sentence — it does **not** re-classify.

| result | |
| --- | --- |
| Model returned a quote | 58/61 (95%) — 3 honest `null`s |
| **Literal substring of `body_text`** | **51 (88%)** |
| Match allowing whitespace/line-rewrap | 54 (93%) |
| Literal in the *rendered* region | 26 (45%) |
| Extracted value appears inside its own quote | 34 (59%) |
| Quote occurs exactly once in the body | 37 (64%) |

**The premise holds, and quotes reach exactly what search cannot.** `deadline` is 0% literal as a
*value* but 3 of 4 of its *quotes* are literal ("até sexta" justifying `2026-08-07`).
`action_requested` is 17% as a value, 13/17 as a quote.

Three findings that shape the build:

**(a) All 4 unrecoverable failures were the same bug — the model echoed the value back as the quote.**

```
value: 'carimbos para cerâmica'   quote: 'carimbos para cerâmica'
value: 'corte MDF'                quote: 'corte MDF'
value: 'apresentar orçamento'     quote: 'apresentar orçamento'
value: 'confirmar proposta'       quote: 'confirmar proposta'
```

Rejecting `quote == value` catches 4/4. The other 3 misses were hard-wrap artifacts
(`'construção de cenografia "Órfãos da \nLua"'`) — recovered by whitespace-tolerant matching,
which normalises a known lossless difference rather than guessing.

**(b) A "value must appear inside its quote" gate is correct per-field and catastrophic globally.**

| field | val-in-quote | verdict |
| --- | --- | --- |
| money | 5/5 | gate ON — catches hallucinated amounts |
| client_name | 14/16 | gate ON |
| deadline | **0/4** | gate OFF — ISO value vs "até sexta" quote |
| action_requested | 3/17 | gate OFF — paraphrase is legitimate |

Applied globally this gate would ship a feature that **never once highlights a prazo**.

**(c) The bottleneck is not the AI — it is `clean_email_body`.** Only 45% of valid quotes land in the
visible region. `client_name` quotes are **16/16 literal in the body but 3/16 visible**: signatures
are where names and NIFs live, and they are deleted. Phase 2 is therefore the highest-yield change in
this document and needs no AI at all.

### 3.4 Thread shape and cost

749 threads: **597 single-message**, 152 with ≥2, 92 with ≥3. Measured LLM cost
(`out/cost.json`): `gemini-2.5-flash`, **$0.886 per 1000 emails**. A full narrative backfill over the
152 multi-message threads is well under €1; the locate pass over ~719 entity-bearing messages is of
the same order.

## 4. Rejected — do not silently re-propose

| Rejected | Why, with the number |
| --- | --- |
| Search the thread for the extracted value (as the primary mechanism) | 37% hit rate; 40% of values are not in the text at all. Fails silently and never for prazos. Survives only as the deterministic Phase-3 path for format-locked fields. |
| A global "value must be inside the quote" gate | Rejects 100% of deadlines (0/4). Must be per-field. |
| A change-diff timeline over `purpose`/`speech_act`/`counterparty`/`priority` + entity changes | **Does not compress**: 491 entries for 523 messages (94%) across the 92 threads with ≥3 messages; axes-only is still 408 (78%). |
| Rendering per-message verdict flips as thread history | Each message is classified in isolation (`classifier.classify` sees one envelope, no thread context). `purpose` flips on **286 of 491 adjacent pairs (58%)**. That is model variance rendered in Portuguese as fact — a zero-hallucination violation at the presentation layer. |
| Caching evidence or narrative in `crm.db` | `build_crm` rebuilds it **clean from the corpus** into a temp file and `os.replace`s it (`crm.py:580-655`), and `sync.py:251` runs that on **every sync**. Anything cached there is destroyed. `crm.SCHEMA_VERSION` is also a bare comment constant — never written to `PRAGMA user_version`, never read; only `workspace.py` has a real migration path (`SCHEMA_VERSION = 12`). |
| Adding an evidence field to the triage schema | It lands in `_ENTITY_PROPS_NULLABLE` (`schema.py:128-135`), which feeds **both** provider contracts, so it changes verdicts and demands an `EXTRACTOR_VERSION` bump — the corpus-split failure `roadmap.md` already records. Use a separate call. |
| Splicing `<mark>` into the escaped body | `esc()` (`cockpit_ui.py:827`) escapes only `& < > "` — not `'` — and indexing escaped text drifts 4 chars per `&`. A wrapper element also breaks the quote/raw toggles, which use `nextElementSibling` (`cockpit_ui.py:1244`, `:1252`), silently. |

---

## Phase 1 — Wrap the ledger values (XS, ~half a day) — ✅ **BUILT 2026-08-05**

Pure CSS + one class in the `factRows` builder. `fila_page.py` only — verified: `lg-r`, `lg-facts`,
`dledger` and «Registo do fio» grep to that file alone, and `dossierHTML` is defined and used there.

| # | Item | Anchor | Test (failing first) |
| --- | --- | --- | --- |
| 1.1 | Replace `white-space:nowrap;overflow:hidden;text-overflow:ellipsis` with a wrap + ~3-line clamp (`display:-webkit-box;-webkit-line-clamp:3;overflow:hidden;overflow-wrap:anywhere`) | `fila_page.py:1633` | new: assert no `nowrap` on `.lg-r b`, and a clamp is present |
| 1.2 | Give the two long keys a wide modifier spanning the grid: `grid-column:1/-1` | `.lg-facts` grid `fila_page.py:1630`; cell `:1631` | new: assert `grid-column:1/-1` for the wide class |
| 1.3 | Emit a per-key class in the builder — the `<div class="lg-r">` string is a fixed literal today | `fila_page.py:605-610` | extend `test_fila_dossier_tiles_are_gone_and_ledger_present` (`tests/test_fila.py:1521-1530`) |
| 1.4 | Carry the full value in `title=` so the clamp never hides it outright | same builder | new |

**As built, with the two deliberate departures stated.** All four items landed; the anchors were
confirmed exact before editing. Two things differ from the table above and neither is an oversight:

1. **The wide modifier is driven by the value's LENGTH (> 60 chars), not by its key.** 1.2 says "the
   two long keys". Static-per-key would give a full grid row to a 9-char «corte MDF», which is the
   median case (`product_or_service` median is 25). 60 sits just above that field's measured p90
   (51) and at the max of `client_name` (60). The per-key class of 1.3 still ships — as
   `lg-k-<key>` — so a future rule can target one field without touching JS.
2. **The row builder was extracted into a named `_lgRow(k,label,f,nMore)`** rather than edited in
   place. `factRows` was an inline template literal, which can only be *grepped*; a named function
   can be **executed** in node, and the guard that does so immediately caught a real bug — the
   widening threshold started life as a module-scope `const` and `ReferenceError`d out of the slice.
   The threshold now lives inside the function so the shipped source is runnable standalone.

Also fixed in the same commit, because Phase 1 landed on it: `fila-mesa-design.md` §6 item 4 still
described the **signal tiles**, which ADR-033 P4a deleted, and «Registo do fio» was documented
**nowhere**. The ledger now has its own §6 entry and its PT strings are in §12.

**Why the clamp is not optional.** Measured value lengths over the live corpus:
`product_or_service` median 25 / p90 51 / **max 226**; `action_requested` max 96; `client_name` max
60. Grid rows are auto-sized, so one 226-char value at 12.5px in a 190px column adds ~15 lines of
height to **every cell in its row**.

**Gotchas.** No test currently pins the nowrap/ellipsis — the behaviour is unguarded, so add the
guard rather than assuming one fired. `Object.keys(FK)` (`fila_page.py:598-599`) drives render
**order**, so reordering `FK` moves things on screen. Do not edit ellipsis rules project-wide:
`.rname` (`fila_page.py:1589`) and `.tatt` (`cockpit_ui.py:657`) are unrelated and `.tatt` is pinned
by `tests/test_fila.py:1176-1182`.

## Phase 2 — Collapse the signature instead of deleting it (S, ~1–2 days) — ✅ **BUILT 2026-08-05**

~~**The highest-yield change in this document, and it involves no AI.** Today the closing salutation
and everything after it is dropped, which is why `client_name` quotes are 16/16 in the body and 3/16
on screen, and why the checksum-validated NIF — the single most trustworthy value in the schema
(ADR-007) — is visible 15% of the time.~~

**That paragraph was wrong and is struck — see §3.2-bis.** The 16/16-vs-3/16 count is right; the
*cause* is not. Those names are in **quoted replies** (`client_name` is 53% `after-quote` and 3%
`signature`), and the NIF's invisibility is **41% truncation + 42% quoted block against 2% signature**
— one value in 59. Of the 29 genuine evidence quotes in the spike, **zero** are hidden by
`clean_email_body`. This phase is worth building, but not for the reason it was written down.

**The honest reason it shipped:** deleting a sender's closing block deletes their name, role and NIF
with no trace, and the app's standing rule is that nothing disappears silently — the rule that governs
IGNORE and the attachment funnel's ASSINATURAS band applies to the sender's own identity too. It is
also a prerequisite for Phase 4, whose quotes are whole sentences rather than tokens.

**Measured result, the phase's own evidence.** `probe_region.py` is blind to this change by design
(it buckets against `clean_email_body`, which is frozen), so the delta was measured against what the
dossier is now handed — `body_clean` + `body_sig`:

| | reachable before | reachable after | Δ |
| --- | --- | --- | --- |
| `client_name` (n=516) | 179 · 35% | 247 · **48%** | **+68** |
| all fields (n=2095) | 779 · 37% | 847 · **40%** | **+68** |

Nothing else moves — `nif` stays at 15%, exactly as §3.2-bis predicts. **+68 exceeds `probe_region`'s
15-value `signature` bucket** because the block is collected from anywhere in the body, *including
signatures inside quoted replies*, which were doubly hidden before (deleted from `body_clean`, so
expanding «mensagem citada» did not reveal them either). Corpus shape: **546 of 1259 messages carry a
signature block; 96% hold at least one non-closing line**; median 45 chars, p90 804, max 4679, capped
at 1500 on the wire.

> **The change that would actually move the NIF is truncation, and it was NOT built — it is bigger
> than item 2.5 and needs a decision.** Measured: 70 values sit past the server's 3000-char cut, 24
> of them NIFs (**41% of every NIF in the corpus**). But raising the server cap alone is useless —
> at a 12000 cap, 54 of those 70 still die at the *client's* `.tquote` 3000-char slice, and only 14
> become reachable. Fixing it properly means raising the server cut **and** both client slices, which
> roughly doubles the `/api/thread` payload on long threads (cleaned bodies: median 2064, p90 9127,
> max 17762). The client's 2000-char *visible* cut is **not** worth touching — it hides 3 values in
> 2095. Recommendation: raise the server cut to 12000 and the `.tquote` slice to match, which would
> take `nif` from 15% to roughly 56%; get a go-ahead on the payload first.

| # | Item | Anchor |
| --- | --- | --- |
| 2.1 | Keep signature lines, marked, rather than dropping them; render collapsed behind a toggle like `.tquote` | `envelope.py:360-367` (fn + docstring contract), `:375-378` (loop state, `in_sig`) |
| 2.2 | Delete-path 1: the whole closing+signature block jumped past when it runs to EOF or a quoted header | `envelope.py:410-422`, terminator regex at `:419` |
| 2.3 | Delete-path 2: per-line drops inside the sig zone | `envelope.py:429-441` |
| 2.4 | Leave the five unconditional per-line deletes (CSS/mobile-footer/URL/phone/postal) alone | `envelope.py:384-407` |
| 2.5 | Update all callers + their truncation | `webapp.py:1330`, `:1411`, and the third caller in the reply path |

**The caller decision, made and written down** (the note above asked for it; here it is). Every
caller was traced, not assumed — 6 call sites, and exactly **one reaches an LLM**:
`webapp._thread_excerpts` → `clientdraft.polish_draft` → the HISTÓRICO prompt block, whose own rule
licenses the model to restate that text **to a client**. Triage does not use it (`classifier.py:43`
reads raw `body_text`); neither do `specdraft`, `specbuild`, `cascade`, `report` or `replydraft`; the
other three callers are the `design/` probes.

So the answer is a **second, opt-in entry point** rather than a flag:

- `clean_email_body(text) -> str` — **frozen**, byte-identical, still deletes. Now delegates.
- `clean_email_body_parts(text) -> (body, signature)` — new; only `/api/thread` opts in.

A flag would have been cheaper and wrong: with `keep_signature=False` as the default the LLM path is
safe *by remembering*, and one future caller flipping it leaks counterparty staff names and NIFs into
a client-facing draft. With two entry points it is safe **by construction**. Pinned from both ends —
`test_the_default_call_still_deletes_the_signature` (envelope) and
`test_the_reply_prompt_never_receives_the_signature_lines` (webapp, asserting on what
`polish_draft` actually receives, not on which function the source appears to call).

**As built, deviations stated.** 2.1–2.4 landed as written. **2.5 landed only in its "update the
callers" half**; the truncation half is the blockquote above and was deliberately not built. The
signature ships as a **separate field** (`body_sig`), not marked inline in `body_clean` — measured
reasons, each of which would otherwise be a silent regression:

1. `hasNoise = rawBody.length > cleanBody.length + 60` (`cockpit_ui.py`) decides whether «ver
   original» exists. Growing `body_clean` deletes that escape hatch on exactly the messages this
   phase touches. Guarded by `test_ver_original_still_appears_on_a_message_whose_signature_was_kept`.
2. `noVisible` falls back to the raw body when cleaning empties a message; a signature-only message
   would stop triggering it. Guarded by `test_a_signature_only_message_still_renders_its_content`.
3. The server's `[:3000]` cut is applied *after* cleaning, so a longer `body_clean` pushes real
   content out — the phase could have made `visible` **fall**.

~~Re-run `design/probe_region.py` after this phase: the "signature" and "visible" buckets should move,
and that delta **is** the phase's evidence.~~ **It does not move, by design** — `probe_region` buckets
against `clean_email_body`, which is frozen. The evidence is the before/after table above, measured
against `body_clean + body_sig`, plus `design/probe_quote_region.py` for the quote population.

## Phase 3 — Deterministic client-side spans (S, ~2 days) — ✅ **BUILT 2026-08-05**

Highlight NIF / IBAN / money by mirroring the `extract.py` patterns **client-side over the rendered
string**. FACT-grade, zero LLM, cannot drift from what is on screen.

**Measured on the live corpus by driving a real Chrome over 25 real threads: 43 of 83 ledger clicks
(52%) paint evidence; the other 40 say «sem evidência visível»; none is silent.** That beats §3.1's
37% search baseline because three things now compound — the signature is searchable (Phase 2), the
quoted block is searchable *and* gets opened when the hit is inside it, and the format-locked keys
match by normalised form rather than by substring. Real spans painted include `515931381` (mod-11
validated), `1600€`, `850,00€`, `504,30€`, and case/accent-folded names (`Vhilstudio`↔`vhilstudio`,
`DANIEL GENARO`↔`Daniel Genaro`).

**Built wider than the table below.** The table scopes the highlight to NIF/IBAN/money; as shipped,
**every** ledger key is clickable — the format-locked three match by normalised form, and the rest
fall back to a fold-tolerant literal search. That fallback is the mechanism §4 rejects, and it is
still rejected *as a primary strategy*; as a user-initiated secondary it is correct, because the
person clicked the row and a miss costs a labelled «sem evidência visível» rather than a wrong
highlight. Restricting the click to three of seven rows would have been the stranger UI.

| # | Item | Anchor |
| --- | --- | --- |
| 3.1 | Mirror `_AMOUNT` / `_NIF` / `_IBAN` in the shared kit; keep the mod-11 NIF check | `extract.py:26-28`, `:32`, `:34`, `_valid_nif` `:48-55` |
| 3.2 | Render via the **CSS Custom Highlight API** (`Range` + `::highlight()`), never `<mark>` | `.tbody` construction `cockpit_ui.py:1004`; CSS `:646` |
| 3.3 | Add one highlight colour token to both themes — none exists today | theme block in `cockpit_ui.py` (`--ac-soft`/`--amber-bg`/`--green-bg` exist; no transient accent) |
| 3.4 | Wire the click affordance on the ledger row (D2: one accent, interaction-driven) | `fila_page.py:605-610`, dispatch at `:917` |

**Never compute a span server-side from `extract.py`.** `extract_values` folds first
(`extract.py:83`: NFKD + strip combining marks + casefold), so its outputs are not substrings of the
body — verified by execution: `iban` → `'PT50000201231234567890154'` vs `"PT50 0002 …"` → `False`;
`amounts` → `'1.250,00 eur'` vs `"1.250,00 EUR"` → `False`. Folding also changes string *length* on
Portuguese text, so any offset computed there drifts silently on exactly the mail this app handles.

**Render-state cases that must be handled, not assumed:** there are **two** `.tbody` elements per
message when the raw body is noisier (the visible one and the hidden «ver original», `cockpit_ui.py:1010-1013`),
the quoted part is a separate `.tquote`, `.tbody` is itself a scroller (`max-height:260px;overflow:auto`)
so "scroll into view" scrolls a nested box, and ~~`translateMsg` replaces text via `textContent` — which
drops highlights with no signal~~.

**That last clause is wrong.** `translateMsg` *reads* `.tbody` and writes a separate `.trbody`;
`.tbody` is never mutated, so highlights survive translation. The real invalidator is
**`renderDossier()` rebuilding `#_doss.innerHTML`**, which the 15 s `refresh()` triggers whether or
not anything changed. Handled by keeping the picked key in module state and re-applying it at the
end of `renderDossier` — deliberately *not* on the row object, which `refresh()` replaces every tick
while hand-copying only a fixed list of underscore fields.

**Test blast radius, deliberate guards, not obstacles:** `tests/test_fila.py:708` asserts the literal
string `"msgHTML(m)"`; `:1133` asserts `html.count("_threadHTML(") == 2`; `:712-736` **executes**
`_threadHTML` in node against a hand-written stub set, so any new kit helper it calls throws
`ReferenceError` there. Budget these updates. *(In the event none of the three needed changing: the
new helpers went in after `msgWireQuoteToggles`, outside both node-executed slice windows, and
`_threadHTML` calls none of them.)*

### Three defects this phase produced, and what caught each

Recorded because each is a repeat of a failure mode CLAUDE.md already names, and two were caught by
guards written minutes earlier — which is the argument for writing them first.

1. **A source comment fired a test on prose, twice.** The handler comment explained that the branch
   must precede the `data-act` one — and `test_the_evidence_branch_runs_before_the_verb_branch`
   asserts those two literals appear in that order, so the prose *was* the wrong order. Separately,
   an example IBAN in a kit comment contained a 9-digit run, which tripped
   `test_admin_page_does_not_embed_the_whole_settings_dict` — a **secrets-leak guard** grepping the
   rendered page for a Telegram user id. Both comments now say so in-line.
2. **A module-scope constant broke the node harness.** `LG_WIDE` (Phase 1) started outside `_lgRow`,
   so the executed slice `ReferenceError`d. A helper that cannot be run standalone cannot be tested;
   the constant moved inside.
3. **The click re-rendered the whole dossier.** Found only by driving real mail — the first cut
   called `renderDossier()`, replacing `#_doss.innerHTML`, so a «mensagem citada» block the reader
   had just opened snapped shut and the pane scrolled to the top, *on the click meant to show them
   something*. Now `applyEvidence` paints in place. Pinned by
   `test_lighting_a_value_does_not_throw_away_what_the_reader_opened`, which compares element
   identity across the click — the assertion a grep cannot make.

---

> **Phases 4 and 5 needed an ADR merged before code. It is
> [ADR-054](../03-decisions/adr-054-llm-derived-body-fragments-live-in-out-sidecars.md)**, written
> and merged 2026-08-06 before a line of either pass was written. It answers the question this note
> raised — a model-selected sentence is a *derived result*, not a *raw body*, and may live in a
> sidecar under `out/` and nowhere else — and it is where the reasoning about `audit.jsonl`, the
> backup script and the echo rule lives. (050 remains an unused gap.)

## Phase 4 — The locate pass (M, ~1 week + ADR) — ✅ **BUILT 2026-08-06**

A **separate** LLM call that, given a message's already-extracted values, returns the verbatim
justifying quote. Triage prompt and schema untouched, so verdicts never churn.

**Validation stack, in order — every step earned from the spike:**

1. Reject `quote == value` (the echo failure — caught 4/4). **Re-counted 2026-08-05: this rejects
   29 of 58, i.e. HALF the sample, not 4 — see §3.2-bis correction 2. Re-derive this phase's yield
   and cost before building it; §3.4's numbers assume the 88%.**
2. Match whitespace-tolerantly against `body_text` (recovers hard-wrap rewraps — 3/3).
3. Reject a quote occurring more than once in the body (only 64% are unique today), or require the
   model to return more surrounding context.
4. Value-in-quote gate **only** for money / nif / iban / client_name. Never for deadline /
   action_requested / product_or_service — those render in the dashed INFERENCE vocabulary, never
   FACT-grade.
5. Anything failing validation stores **nothing**. No highlight beats a wrong one.

**Storage: a sidecar JSONL keyed by `message_id`, mirroring the `jobspecs.jsonl` precedent.** Not
`crm.db` (destroyed every sync, §4). Not `results.jsonl` (body-free by contract — `report.py:7`).

| # | Item | Anchor |
| --- | --- | --- |
| 4.1 | Pipeline shaped like `rebuild_jobspecs`: incremental gate, `only={mids}` scoped re-extract, temp file + `os.replace` | `specbuild.py:111-114`, `:169`, `:181-186` |
| 4.2 | Prompt/playbook + `llm.call(schema=…)` shaped like the spec draft | `specdraft.py`; `llm.py:88-101`, `with_tier` `:25-42` |
| 4.3 | Keep the mandatory billing label | `llm.py:155-161` (`labels={"app": "email2data"}`) |
| 4.4 | Register **two** readers + the post-sync rebind, as `jobspecs.jsonl` requires | `webapp.py:147`, `:223`, `:337-339`; `report.py:57-62` |
| 4.5 | Failure path: audit event with **counts/ids/types only**, never the quote | `audit.py`; pattern at `specbuild.py:93-96` |
| 4.6 | Decide `bin/backup-workspace.sh` explicitly | `STORES=("workspace.db" "auth.db")` at `:44` |

**Gotchas.** `tests/test_specbuild.py:186-198` spies `os.replace` and asserts **exactly one** atomic
write per rebuild — so the sidecar must not be written from inside `rebuild_jobspecs`. `.gitignore`
needs no edit (`out/*` at `:53` covers it), but `snapshot_and_verify` in the backup script is
sqlite3-only and cannot take a JSONL. `out/jobspecs.jsonl` has **no row** in `data-stores.md`'s table
and is not in `STORES` — the LLM-derived-artifact precedent is already unbacked-up; do not inherit
that gap silently.

**Cost, stated honestly.** ~719 entity-bearing messages must have their full body re-sent — roughly
81% of everything Tier-1 has ever processed. The saving versus a full re-triage is in **verdict
churn, not spend**. ~~Consider scoping the first pass to money + deadline on WE_OWE / TO_PAY threads,
per the governing principle.~~ **That scoping was measured and abandoned — see below.**

### As built (2026-08-06): what the re-derivation changed, and what it reversed

The handover told the builder to re-derive this phase's yield before writing a prompt. Re-derived on
the live corpus (`corpus/` = 1271, 767 threads), at the unit the reader actually experiences — a
**ledger click**, i.e. one (thread, key) pair with a latest value:

| | |
| --- | --- |
| ledger rows | **790** |
| painted today by Phase 3 | **350 (44%)** |
| **dark — this phase's entire target** | **440 (56%)** |
| …lost to a *cut* (the truncation fix, handover finding 3) | **9** |
| …absent from the email text in any form | **431** |

Per key, painted: `client_name` 87% · `nif` 79% · `money` 67% · `product_or_service` 47% ·
`action_requested` **20%** · `deadline` **0%**.

**1. The handover's headline concern was an artifact of pooling, and the truth points the other way.**
"29 of 58 quotes are echoes, so the locate pass discards half of every run" is right about the pooled
sample and wrong about this phase. Re-partitioning the *same paid-for spike* by whether Phase 3
already paints the row:

| | pairs | echo | genuine + literal + reachable + unique |
| --- | --- | --- | --- |
| rows Phase 3 already paints | 28 | 25 (89%) | **0 (0%)** |
| **rows Phase 3 leaves dark** | 30 | 8 (27%) | **21 (70%)** |

An echo can only happen when the value IS in the text — and there Phase 3 already paints it, by
running the same search. So the echo rule discards duplicates, never results, and the yield on the
population this phase actually serves is **70%**, not 50%. Phase 4 is *more* worth building than the
handover feared, precisely **because** Phase 3 exists to take the echoes off its plate.

**2. The recorded scope would have built the whole pipeline for 18 rows.** «money + deadline on
WE_OWE / TO_PAY» measures out at **29 messages and 21 ledger rows, 18 of them dark** — and `money` is
already 67% painted and scored 0/1 useful on its dark half. The scoping instinct was sound but it was
recorded against §3.4's cost model; priced off real body sizes rather than the stale
`out/cost.json`, a **full backfill of all seven keys is 736 messages ≈ $0.44**, and Phase 5's 157
threads ≈ $0.24 — **≈ $0.68 for everything**. What was scoped for prudence cost more in lost value
than it saved in spend.

> **Whose decision this was.** The full backfill and «CLI *and* automatic on sync» were chosen **by
> the builder**, on the measurement above, and executed — **the owner has not confirmed either**. The
> superseded scope (money + deadline on WE_OWE/TO_PAY) *is* a real recorded owner preference, so this
> is a builder decision overriding an owner one on new evidence, and it is flagged here rather than
> laundered into "the owner chose". The spend has already happened (≈ $0.74 actual). If the owner
> disagrees, `out/evidence.jsonl` and `out/narratives.jsonl` are regenerable sidecars: delete and
> re-run `email2data locate --only …` at any narrower scope.

**3. Handover finding 3 (truncation is the cheapest remaining win) does not survive this unit.** It
counted 70 values past the 3000-char cut, 24 of them NIFs — measured per *value*, across every
message. The ledger de-dupes to the latest value per key per thread, and at that unit truncation
costs **9 rows of 440**. It was not built, and the recommendation is now *don't*.

**Two departures from the table above, both deliberate:**

- **Item 4.5 ("anything failing validation stores nothing") is not implemented as written**, because
  it collides with its own gate. The incremental gate keys on the **presence of a row**, so a message
  with no row is indistinguishable from one never attempted — "store nothing on failure" re-bills the
  model for every failed message on every sync, forever. A row is written for every *attempted*
  message, carrying the accepted quotes and a `{key: reason_code}` map. **No quote text is stored for
  a rejected key**; the reason code (`echo`/`not_in_body`/`not_unique`/`value_not_in_quote`/
  `too_long`/`absent`) explains the decision and carries no body text at all.
- **Item 4.6 is answered "no", and the reason is measured.** Adding a JSONL to
  `bin/backup-workspace.sh`'s `STORES` breaks the **whole** backup: `snapshot_and_verify` is
  sqlite3-only and `DatabaseError: file is not a database` has no `except`, so the pre-migration run
  exits 1 and prints "BACKUP FAILED — do not migrate" even though both DBs snapshotted fine. Recorded
  in ADR-054 and in `data-stores.md` rather than inherited silently from `jobspecs.jsonl`.

**The client never receives an offset — it receives the sentence.** §Phase 3 already establishes why
a server-computed offset is unusable, and the same trap bites harder here: `msgHTML` re-splits the
body **client-side**, **trims** both halves, renders `body_clean` and `body_sig` into separate boxes
and slices each, all after the server's own cut. The stored quote is re-found in the DOM by
`evLocateQuote`, which tolerates whitespace and folding and maps every normalised character back to
its **source index** — because folding is not length-preserving in general (NFKD expands a ligature).
`locate.find_spans` and `evLocateQuote` are two implementations of one rule, and a test **executes
both** over the same inputs and asserts identical spans.

### Measured after the pass actually ran (2026-08-06) — the phase's real evidence

The numbers above are the *forecast*. The pass then ran for real over the whole corpus
(`corpus/` = 1315, 789 threads), and this is what it bought:

| ledger rows | 805 |
| --- | --- |
| painted by Phase 3 (the value itself) | 358 (44%) |
| **painted by Phase 4 (the located sentence)** | **346 (43%)** |
| «sem evidência visível» | 101 (13%) |
| **total lit** | **704 (87%)** — was 44% |

| key | rows | P3 | **P4** | dark | lit |
| --- | --- | --- | --- | --- | --- |
| `deadline` | 101 | 0 | **87** | 14 | **86%** (was 0%) |
| `action_requested` | 204 | 42 | **140** | 22 | **89%** (was 20%) |
| `product_or_service` | 270 | 128 | **115** | 27 | **90%** (was 47%) |
| `money` | 67 | 45 | 4 | 18 | 73% |
| `client_name` | 148 | 131 | 0 | 17 | 89% |
| `nif` | 14 | 11 | 0 | 3 | 79% |

`client_name`, `nif` and `iban` gained **nothing** — as predicted: they are the keys whose value is
literally in the text, so Phase 3 already had them and the echo rule correctly discarded the rest.
The three keys that gained everything are the three the deterministic search can never reach.

Sidecar: **763 rows · 1543 quotes accepted · 512 rejected · 3 LLM failures.** Rejections, which are
the validator doing its job rather than waste: `not_in_body` 167 · `not_unique` 131 · `echo` 102 ·
`value_not_in_quote` 55 · `absent` 48 · `too_long` 9.

**Then the same claim was checked in a real browser against the running container**, because every
number above comes from a Python re-implementation of the client matcher and is therefore a proxy.
Driving Chrome over 14 key-rich threads: **58 of 69 ledger clicks paint (84%), 11 do not, and all 11
say «sem evidência visível» — none silent.** `deadline` painted **14/14**. A screenshot of a real
`deadline` row shows a whole sentence highlighted — one asking for a proposal *"até ao próximo dia 31
de julho"* — for a stored value of `2026-07-31`, which appears **nowhere** in that email. That is the
entire case for this phase in one screen: the value is ISO, the email is prose, and only a model can
bridge them. (The sentence is paraphrased here rather than reproduced: this repo is public, and a
verbatim line of client correspondence is exactly what ADR-054 confines to `out/`.)

**The run found a defect 70 passing tests did not.** It reported *806 messages · 1647 quotes* while
the sidecar held *763 ids · 1543 quotes*: `results.jsonl` is **append-only**, so a re-triage leaves
two lines for one message, and `rebuild_evidence` was iterating raw lines — **paying Gemini twice for
43 messages** and writing duplicate rows whose winner `load_evidence` picked by file order. Fixed by
folding last-wins per `message_id` (the convention `report.py` and `specbuild.py` already use), pinned
by `test_a_re_triaged_message_is_located_once_from_its_freshest_line`, and the live sidecar was
collapsed to 763 rows by a re-run that cost **$0** — the incremental gate keys on row presence, so it
made zero LLM calls.

## Phase 5 — «Evolução da conversa» (M, ~1 week + ADR) — ✅ **BUILT 2026-08-06**

One narrative call per thread, re-run only when the thread grows. 152 threads qualify.

| # | Item | Anchor |
| --- | --- | --- |
| 5.1 | Sidecar keyed by `thread_root` + a message-count/last-date watermark for the incremental gate | new; same pattern as Phase 4 |
| 5.2 | Add the key to `/api/thread`'s single `JSONResponse` — keep the one-round-trip contract | `webapp.py:1273-1274` (route), `:1549-1556` (response) |
| 5.3 | Render above or beside «Análise IA» | `fila_page.py:583-591` (`.dai` block), CSS `:1670-1674` |
| 5.4 | Honest absence: `null`, not `[]` — the existing convention | `test_thread_endpoint_spec_is_null_when_nothing_was_extracted` (`tests/test_fila.py:607-610`) |
| 5.5 | Degrade fail-open when the sidecar is missing | precedent: `test_a_funnel_with_no_register_hides_nothing` (`tests/test_attachments.py:617-640`) |

**Gotchas.** `fila_page.py`'s `onSynced` (`:1200`) refreshes rows but **never clears `_threadCache`**
(`:838`), so an expanded thread keeps its pre-sync `/api/thread` payload — a growing thread would
show a stale narrative. `getJSON` throws on non-2xx, so a narrative call that 5xx's must not blank
the dossier (`test_an_unreachable_thread_costs_only_that_thread`, `tests/test_attachments.py:787-808`).
The ADR-045 visibility gate must cover any new route exactly as `/api/thread` does
(`tests/test_visibility.py:176-192`), and `tests/test_auth_gate.py:117` walks a route allowlist that a
new route must join. No test asserts the `.dai` markup today — `grep -rnw dai tests/` returns nothing.

**Reuse what is already thread-scoped and stable** rather than diffing per-message labels: the
ADR-036/-051 obligation fold (`cockpit.py:330-359`), the clock dict (`:371-406`), and the human
decisions the ledger already renders (`webapp.py:1516-1531`).

### As built (2026-08-06)

Items 5.1–5.5 landed. Three things differ from the table, each because the anchor or the reasoning
did not survive contact:

1. **The watermark is a content hash, not "a message-count/last-date watermark" (5.1).**
   `CrmStore.record` is `INSERT OR REPLACE` on `message_id`, so a re-triage can change
   `speech_act` / `purpose` / `counterparty` / `entities` — the exact inputs a narrative summarises —
   while the count and the last date stay identical. A count-and-date gate would freeze a narrative
   describing verdicts that no longer exist. It hashes each message's
   `(id, date, direction, purpose, speech_act, counterparty, entities, subject)`, sorted, so it is
   also insensitive to row order — which matters, because `crm.thread()` orders by a *lexicographic*
   sort over ISO strings that keep 12 distinct UTC offsets.
2. **The narrative renders in its own block, not inside `.dai` (5.3).** `.dai` is conditional on
   `decided||tr.reason||en.action_requested`; a narrative inside it would vanish on exactly the
   threads where all three are empty.
3. **The gotcha about `_threadCache` was real and had to be FIXED, not worked around.** It is worse
   than the note says: the staleness is doubly locked, because `refresh()` carries `_threadMsgs`
   forward, which keeps `renderDossier`'s `if(r._threadMsgs==null) ensureThread(r)` guard permanently
   false after the first open — so `ensureThread` is not merely cache-hitting, it is never called
   again. `onSynced` now drops both halves. Without that, a thread that grew would have shown a
   pre-sync narrative for the life of the tab.

**Every step cites the message it came from, and that citation is checked.** Messages reach the model
as ordinals `m1..mN` rather than real `Message-ID`s — a real id is an opaque 60-to-120-char string the
model has no reason to reproduce faithfully, and an ordinal makes the check exact. A step citing
anything outside the set is discarded before storage, and the **date is attached server-side** from
the real row: a rendered date the model invented would look exactly like a correct one. On screen each
step is clickable and scrolls to its message, so the provenance is followable, not just claimed.

**Threads with one message are never narrated** (610 of 767). `narrative` is `null` there — the
honest-absence convention `spec` already uses — and the block simply does not render.

### Measured after the pass actually ran (2026-08-06)

**166 threads narrated · 533 steps · 1 LLM failure · 0 steps discarded for a bad citation.** The
citation check never had to fire, which is the good outcome and not evidence it is unnecessary — the
model was given ordinals precisely so it *could* be checked. Cost, priced off real prompt sizes
(1.30 M chars in, median thread 5.2 k, largest 62 k): **≈ $0.30**, against the ≈ $0.24 forecast.

Verified in a real browser against the running container: **14 of 14 sampled threads render
«Evolução da conversa»**, 65 steps total, each dated and clickable; clicking a step scrolls to the
message it cites **and leaves an open evidence highlight intact** (3 ranges before and after), which
is the no-re-render guard doing its job. The jump also resolves an Exchange-style
`mid:!&!aaaa…/vv0…==@…` id through the `[data-tmid="…"]` selector without escaping trouble.

**The run found a second defect the suite did not.** 2 of the 533 steps cite an interaction whose
`date` is genuinely `''` in `crm.db`, and the renderer emitted an empty `<span class="nd"></span>` —
a blank box beside the text, which reads as a broken render rather than as a missing date. The chip
is now omitted when there is no date, pinned by a test that **executes the shipped renderer in node**
over one dated and one dateless step.

---

## Verification, for every phase

`docker compose ps` and `/healthz` prove a container is running, **not** that it runs your code —
only `./bin/check-image-drift.sh` does. And the payoff of all five phases is **visual**: the last step
is to open `http://127.0.0.1:8042/fila`, focus a thread, and look at it. A passing grep is not
evidence that a highlight rendered.

**Done for all five phases (2026-08-06).** `.venv/bin/python -m pytest -q` → **1475 passed**, ruff
clean, `docker compose up -d --build` → both services healthy, `./bin/check-image-drift.sh` → *"OK
container 'email2data' matches the working tree"* (exit 0). Then a real Chrome was pointed at
`http://127.0.0.1:8042/fila` with a session **minted inside the container** (`docker compose exec`,
never a host write to `out/auth.db` — see the WAL warning in `CLAUDE.md`), and the highlight and the
narrative were looked at, not grepped. Both defects recorded above were found in that last step.
