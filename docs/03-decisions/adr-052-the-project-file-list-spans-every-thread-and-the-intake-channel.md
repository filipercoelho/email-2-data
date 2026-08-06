# ADR-052 — The project file list spans every thread and the intake channel

- **Status:** Accepted
- **Date:** 2026-08-03
- **Owner decisions:** 2026-07-31 (three, recorded verbatim in §2)
- **Extends:** [ADR-046](adr-046-the-attachment-funnel-bands-files-by-what-they-are-for.md) §4 ·
  [ADR-019](adr-019-conversational-intake-capture-adapter.md) / [ADR-020](adr-020-capture-egress-and-data-handling.md) ·
  [ADR-045](adr-045-per-person-visibility.md) · [ADR-044](adr-044-inicio-the-landing-page-is-a-decision-not-a-cockpit.md)
- **Supersedes:** nothing. ADR-046 §4 is **extended, not replaced**.

## 1. The request, and what was actually wrong

> «How can we enhance the project view in the webapp to contain all the attachments extracted from
> all communications throughout the project? As of right now, there is no way of consulting all
> attachments.»

The surprising part, verified in a real browser on 2026-07-31 before anything was designed: the
project-wide, cross-thread, content-deduped list **already rendered**. `loadSource()` runs eagerly on
every project open, folds each thread's `/api/thread` funnel through `attMerge()` and hands it to
`msgThreadHTML` — inside `#_origem`, a panel capped at `max-height:420px`. On `p-0006` that was
«📎 FICHEIROS DA CONVERSA — 4 ficheiros · 1 imagem no corpo», 5 tiles, dedup working
(`image001.png ×4`) — **381 px of funnel inside a 420 px box holding 2427 px of content (16 %)**, with
five message cards below it. ADR-046 §4's promise was kept. The gap was surfacing, not capability.

Five defects sat behind that, and only the first is the feature that was asked for:

1. **No surface.** Six tabs — *Especificação · Origem · Linha do tempo · Email ao cliente ·
   Descritivo · Registar*. None of them meant "files".
2. **The heading lied at project scope.** «Ficheiros da conversa» was a literal, printed over a list
   spanning N conversations.
3. **Provenance was computed and thrown away.** Every folded item already carried `from_email` and
   `first_seen`; the tile rendered neither, so nothing answered *"which email brought me this file?"*.
4. **One unreachable thread lost every other thread's files.** The `try` wrapped the whole root loop
   and `getJSON` throws on any non-2xx — and `/api/thread` 404s both for an ungranted root (ADR-045)
   and for a dangling one. The result was «falhou ao carregar contexto» and **total** loss on a
   **partial** failure.
5. **Intake captures were absent entirely.** The funnel only ever received `/api/thread` blocks, so a
   drawing photographed on the shop floor and sent through Telegram could never appear. The timeline
   showed only `media/0` as an 84 px thumbnail. All 3 captures on the live install carry media.

Separately, **Para Ti had never once rendered the funnel** — `loadDetail` stored `{messages, spec}`
and dropped `d.attachments`, while `detailHTML` went on passing `attachments: d.attachments`
(`undefined`), so `attFunnelHTML` returned `''` every time. Confirmed in Chrome: a thread showing
**8** `.tatt` chips with no `.attf` element at all. The existing sweep only ever asserted that no lens
*forks* the shared kit — never that a lens *renders* one, which is exactly why two lines of omission
survived a whole ADR.

## 2. Owner decisions (2026-07-31)

1. **Both places.** The funnel STAYS in «Origem» *and* gets a «Ficheiros» tab. ADR-046 §4 is
   extended, not superseded.
2. **Telegram capture files are included.** "All communications" means email **and** intake.
3. **Para Ti's dropped funnel is fixed in the same commit.**

## 3. Decision

### 3.1 A seventh tab, folded in the client

«Ficheiros» renders `attFunnelHTML(att, {title:'Ficheiros do projeto', showSource:true,
bigPreviews:true})` over the merge of every thread's funnel **plus** the project's capture media.
It makes **no fetch of its own**: `loadSource()` already walks the roots, and the tab reads what that
walk cached. The badge and the panel are written in the same pass from the same `items` array, so the
chip can never promise a count its destination does not hold — the «Rever classificação» lesson,
applied before it could recur.

### 3.2 The email half is folded CLIENT-side, and that is the security decision

`_may_open_project` is **ANY-thread** by design: a member granted one inbox passes the gate for a
whole multi-thread project, and `_project_view` returns `threads` unfiltered. A server-side
`/api/projects/{pid}/attachments` gated only by the middleware would therefore hand that member
filenames, sizes, page counts and sender addresses for mail they were never granted. The bytes route
would still 404 — but «Proposta_ClienteX_48k.pdf» is most of the leak.

Folding in the client means **every root passes the existing per-thread ADR-045 check**, one at a
time, and there is no new surface to leak from. Pinned by
`test_visibility.py::test_the_files_tab_shows_no_file_from_an_ungranted_thread`, which asserts on
content: the project opens, it hands back both roots, and one of them 404s.

### 3.3 The intake half IS a server endpoint, for a reason that does not apply to the first

`GET /api/projects/{pid}/captures` returns an ADR-046 funnel block. It is allowed to be a
server-built collection because **captures carry no per-thread scope at all** — they are project
knowledge, and the timeline endpoint beside it has served their thumbnails to exactly this audience
since ADR-019. It is gated by the same middleware path rule that gates every other
`/api/projects/{pid}/…` route (`_project_id_in_path`), so it is protected **by construction**, not by
remembering a decorator — ADR-040 §1's argument, unchanged.

Which captures belong to a project is read from existing provenance, not a new join: an applied
capture writes `source_mid = 'capture:<cid>'` into `project_field_history` (ADR-020/-022 §7 freeze
that value space), and `pstore.timeline(pid)` is where it is read back.

### 3.4 Capture media are hashed, so they join the dedup instead of sitting beside it

The original brief proposed giving capture media `src`-derived stable ids, on the grounds that they
have no sha256. **That was reversed by the owner and is the load-bearing part of this ADR.** A
`src`-derived id identifies a *slot*, not an artefact: the same drawing mailed by the client and then
re-sent through Telegram would have read as two different files, side by side, in a list whose entire
promise is one row per file.

`capture_media_items()` therefore **reads and hashes the bytes**, and a capture folds through the same
`attMerge` key as any MIME part. Verified end to end in Chrome: the fixture's drawing rides two
conversations and shows as one tile marked `×2`.

Costs, stated: one full read per media file per call. Measured on the live install that is 3 captures.
The endpoint does not touch the filesystem at all when a project has none — 10 of the 13 live projects.

### 3.5 They are not laundered into looking like email attachments

Two sources with different properties, and the FACT/INFERENCE rule applies unchanged. Every capture
item carries:

- `source: "capture"`, its `channel` and its `asserted_by` — the tile says *«captura · telegram ·
  Rita · 2026-07-14»*, never a `from_email` it does not have, and offers **no** «ver na fila →»
  because there is no conversation to jump to;
- its own `band_evidence` naming the channel;
- `src: {capture_id, index}`, so the byte link goes to `/api/captures/{cid}/media/{i}` rather than to
  a message that does not exist.

**Every index is enumerated, not just `0`.** A capture carrying a photo *and* a drawing hid the
drawing completely, on the timeline and everywhere else.

**They land in `FICHEIROS`, not in a fourth «Capturas» band.** A fourth band would have to hold a file
that is *both* mailed and captured — which is one file — and the three bands are a measured corpus
calibration this change has no evidence to extend. The channel is a property of the item, not a
partition of the list.

A media file **missing from the sole-copy store is listed, not skipped**: `missing: true`, size 0, a
plain-language evidence line, and a dashed tile. ADR-020 says `captures_dir` is the only copy, so a
gap in it is an incident to see. Silently shortening the list is how «never silently bin» dies.

### 3.6 Chronology is a precondition of the provenance line, not polish

`attMerge` was first-**block**-wins, i.e. `project_threads.added_ts` order — whichever thread happened
to be attached first. It inherited `fold_thread`'s wording ("the first occurrence wins: it supplies
`src`, `first_seen` and `from_email`") while feeding it an order that has nothing to do with time.

Harmless while nothing rendered a sender. **The instant a tile names who sent this file, it becomes a
confident lie** — the worst failure mode in this codebase. The merge now flattens every block and
sorts by `first_seen` ascending *before* the dedup pass; an item with no date sorts **last**, so a
dated carrier always outranks an undated one. The final band/size/name sort is unchanged.

### 3.7 `.map(_attTile)` — the trap that breaks the Fila, not Projetos

`Array.map` passes `(item, index, array)`. Two call sites read `lst.map(_attTile)`, so the moment the
tile helper grew a second parameter it received the **array index** as its options object: falsy for
tile 0, truthy for every tile after. The Fila's first tile looks perfect and every later one sprouts a
source line built from a number. It renders, it is wrong, and a Projetos-only check would have passed
it. Both call sites are now `.map(it=>_attTile(it,o))`, pinned by a test that both greps for the bare
shape **and executes the funnel** to confirm what the second argument actually is.

### 3.8 The preview gate inverts, and the tab opts out of it

Found by looking at the render, not by a test: `preview` is on only for images ≤ 256 KB
(`PREVIEW_MAX_BYTES`), so on `p-0006` three ~6 MB client photos drew a generic 🖼 glyph while the 6 KB
B|BRAUN **supplier logo** was the only tile with a real thumbnail. In a view called "the project's
files", the content was iconised and the branding was previewed.

**Decision: the Ficheiros panel passes `bigPreviews:true`; every other lens keeps the gate.** This is
a decision about the *surface*, not a guess about the *file* — the server still decides the default,
and the client still never infers previewability from a size. The panel is behind a tab click,
`loading="lazy"` means only what scrolls into frame is fetched, and it is the one surface whose entire
purpose is looking at the files. Server-side thumbnailing is the real fix and is a separate change
with a real dependency; **revisit when a project exceeds ~10 previewable items over 1 MB.**

### 3.9 Partial failure is said, never implied

The `try` moved **inside** the root loop. A failed root is collected into `failed[]`, and both
«Origem» and «Ficheiros» print «⚠ N de M conversas não carregou — esta lista está incompleta. Pode
faltar aqui um ficheiro.» One merged sentence, deliberately: the client **cannot** distinguish "not
granted to you" from "no CRM context" — both are a bare 404, so a 403 never confirms a thread exists.
`renderFiles()` runs on **every** exit path, including the no-threads early return, or the tab spins
forever.

## 4. What was NOT built

- **No new table, no migration.** `workspace.SCHEMA_VERSION` stays 12, `crm.SCHEMA_VERSION` stays 6 →
  single-service deploy, no `intake-bot` gate.
- **No index.** Measured 2026-07-30 on the live stores: **13 projects, every one with exactly 1
  thread**, 84 funnel items total. Nothing to index.
  **Revisit trigger:** a project attaching **>5 threads**, or **>50 previewable items** in one
  project. `SELECT project_id, COUNT(*) FROM project_threads GROUP BY 1 ORDER BY 2 DESC`.
- **ADR-046 index stability is untouched.** Nothing server-side changed in the MIME path, `src.index`
  is never recomputed, and byte links keep the `encodeURIComponent(message_id)+'/'+index` shape.

## 5. Known limits

- **Cross-thread dedup is unexercised in production** — all 13 live projects have exactly one thread.
  The browser e2e is the only thing that runs it before a user does, which is why the fixture rides
  the same bytes into two conversations rather than asserting a shape.
- **`loadSource` fetches serially** (`await` inside `for…of`), so a multi-thread project pays the sum.
  `Promise.allSettled` would fix it and reorders `all[]`; separate change.
- **Cold `_file_for` corpus index ~7 s**, reset after every sync. Not caused or fixed here.
- **ADR-048 will visibly shrink this list** once the branding register populates: `asset_spread` is
  not in the live `crm.db` yet, so `branding_shas` returns an empty set and nothing is dropped today.
  Someone who saw «11 ficheiros» will see fewer after the next `crm` rebuild. That is ADR-048 working,
  not this list losing files.
- **Two funnels on one page** (decision 1). No tile emits an `id=`, so nothing collides; the browser's
  HTTP cache dedups the preview bytes.
- Capture visibility is unchanged: ADR-045 recorded that **captures have no scope concept**, and this
  does not add one. A member who can open the project can see its captures, exactly as they could see
  the timeline thumbnails.
