# ADR-046 — The attachment funnel: band files by what they are *for*, dedup by what they *are*

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-07-26 |
| Scope | `attachments.py` (new), `webapp.py` (`/api/thread`, `/api/attachment`), `cockpit_ui.py` (shared kit), `fila_page.py`, `para_ti_page.py`, `projetos_page.py` |
| Amended by | [ADR-048](adr-048-recurring-branding-art-is-omitted-from-the-attachment-funnel.md) (2026-07-30) |

> **Read §1's "Nothing is ever dropped" with ADR-048 in hand.** That sentence held until 2026-07-30.
> Inline art we send ourselves that recurs across three or more unrelated threads — proven branding,
> measured, never guessed per-part — is now omitted from the payload entirely, with
> `email2data assets status` as the audit trail replacing the click-through. Everything else in this
> ADR stands unchanged, including the bands, the hash dedup, and the index-stability rule ADR-048
> depends on.

## Context

A thread's attachment parts are not one kind of thing, and the UI treated them as if they were.
Every part became a 📎 chip on its own message card, in MIME order, at equal weight. On the
corpus's worst thread that is **133 chips across 18 message cards** — and the four things a human
actually wants (a 5.7 MB photo, a `.zip` of originals, and two PDF invoices) sit somewhere inside
forty repetitions of the sender's Facebook icon.

Three genuinely different things arrive down the same pipe:

1. **A document someone attached** — the quote, the PO, the drawing file. What "anexo" means.
2. **An image pasted into the body** — a screenshot, a photo, a CAD render. Usually *is* the request.
3. **Signature and branding art** — logos, social icons, footer banners. Never the point.

Two further defects sat underneath, both found by *fetching the bytes back and comparing them*
rather than by reading the render:

- **`/api/attachment/{message_id}/{index}` 404'd for one attachment in five.** Outlook
  Message-IDs are base64-ish blobs that routinely contain `/`. The route was a plain two-parameter
  path, and the ASGI server percent-decodes before routing, so the client's `%2F` came back as a
  real separator and the extra segment matched nothing. **201 of 1039** corpus attachment links
  were dead, and it looked like missing data, not a routing bug.
- **Non-ASCII filenames produced an invalid header.** HTTP header values go out as latin-1, so
  `Comprovativo Pag. Lindo Serviço.pdf` emitted a byte no UTF-8 reader accepts. In a pt-PT shop the
  accented filename is the common case, not the edge.

## Decision

**1. A deterministic three-band rule, computed at render, labelled INFERENCE with its evidence.**

| Test | Band |
| --- | --- |
| `Content-Disposition: attachment` | `FICHEIROS` |
| no `cid:` reference in any HTML body | `FICHEIROS` |
| `cid:`-referenced but not an image | `FICHEIROS` |
| `cid:`-referenced **and** > 600 px side / > 250 kpx / > 200 KB | `IMAGENS` |
| `cid:`-referenced **and** postcard-shaped (aspect < 2.0, short side ≥ 250 px) | `IMAGENS` |
| otherwise | `ASSINATURAS` (collapsed, count visible) |

Nothing in MIME states "this is a logo", so every band is an **INFERENCE**, never a FACT. Each item
carries `band_evidence` — the rule that fired and the measurement that triggered it — rendered on
the tile. Per PROFILE.md, an inference that cannot show its evidence is a hallucination with better
manners.

**Nothing is ever dropped.** The third band is collapsed behind a *visible count*, one click away.
That keeps the funnel inside the "never silently bin a client" non-negotiable rather than beside it.

**2. Dedup on the sha256 of the decoded bytes, never the filename.** Measured: 784 byte-identical
duplicate parts per thread versus 603 same-name, and **220 pairs sharing a name while differing in
bytes** — one thread carries `composition.pdf` at both 154 KB and 152 KB. A hash key catches
strictly more duplicates and never merges two different documents. The same key replaced the
filename set that decided whether an empty-bodied Trash copy was worth keeping, where a name
collision silently deleted a whole message card.

**3. Aggregate server-side from every interaction, *before* the message dedup**, so a file whose
only carrier is a suppressed Trash copy still surfaces.

**4. The UI lives in the shared kit (`cockpit_ui.py`), not in `fila_page.py`** — Para Ti and the
Projetos origem panel inherit it. Projetos spans several threads, so `attMerge()` folds their
funnels on the same content hash.

**5. Previews are size-gated and lazy.** The server decides (`item.preview`): an image at or under
256 KB gets a real `<img loading="lazy">` off the existing byte endpoint; everything else renders a
kind glyph, and no byte is fetched to draw an icon. PDFs carry a page count from pypdf. The worst
thread drops from **6.31 MB eager to 0.45 MB**.

**6. `/api/attachment/{ref:path}`, split on the last slash**, so a slash-bearing Message-ID
resolves. Filenames go out as RFC 6266 — a transliterated ASCII `filename=` plus the
percent-encoded `filename*=UTF-8''`.

## Calibration — the rule was wrong until it was looked at

The first cut banded by **size alone**. Sweeping all 553 messages and then *opening the images*
showed it burying **11 distinct real-content images** in the collapsed band: a 431×361 CAD drawing
with dimension annotations (in 10 messages), two order summaries showing totals (€59,34 and
€6.578,38), a 353×336 RAL 5013 colour swatch, a payslip breakdown, a 513×458 bank-portfolio
screenshot, and five product photos. That is exactly the failure that costs a quote.

No size threshold fixes it — a 377×377 BANEMA logo and a 353×336 colour swatch are the same shape
and the same weight. What separates them, measured, is **aspect ratio**: the widest real-content
image in the corpus is **1.79:1**, the narrowest signature banner **2.14:1**. A clean gap, hence
the postcard arm. Cost: 3 squarish logos now show inline. The trade is deliberately asymmetric —
**a leaked logo costs a glance, a buried drawing costs a quote.**

The 250 px floor is an operating point, not a round number: at 200 px it rescues two ~230 px
product photos but leaks seven Lindo Serviço signature cards (205×278, 181×228). Those two photos
stay one click away, not gone.

### Rejected: every way of demoting high-resolution logos

Looking at the rendered funnel showed Instagram/Facebook/LinkedIn icons (4322×4320, 1920×1919,
1280×1280) in the visible band — pixel-huge, so the "big" arm promotes them. Three demotion signals
were measured and **all three would have buried real content**:

- **Density** (bytes/px — what `envelope._is_signature_image` uses for the LLM path): a 2437×2441
  CAD cut drawing of a duck, with red annotation marks, scores **0.0117 B/px**, *below* the
  Instagram icon's 0.0095–0.047. Flat line art and flat logo art compress identically. That
  heuristic is only safe in the LLM path because it is gated behind the `imageNNN.ext` name pattern
  and a 200 KB floor.
- **Filename**: Outlook renames every inline image, and the rescued 431×361 drawing is
  `image031.jpg`.
- **Anchor-wrapping** (is the `<img>` inside an `<a href>`? social icons are): a stone supplier's
  entire product catalogue is link-wrapped — `1_gtr_macondo_..._295x195x2.jpg` and 16 siblings at
  700×~500.

So **no demotion arm ships.** The leak is bounded by order instead: items sort largest-first inside
a band and flat logo art is small, so it lands at the bottom. `test_flat_line_art_is_not_demoted_out_of_the_visible_band`
pins the duck against a future "fix".

## The thing not to get wrong

`src.index` indexes the `_attachments` walk order, which `attachment_part` re-walks identically to
serve bytes. **Banding is additive metadata only.** Any filter applied before that counter
increments would silently repoint every 📎 link at a different file — under the right name, looking
perfectly correct on screen. `message_parts` therefore copies the walk predicate exactly and filters
nothing, and the invariant is verified by **bytes, not by inspection**: every funnel item of every
attachment-bearing thread was fetched back through the public endpoint and its sha256 compared —
**1039/1039 match, 0 mismatches, 0 errors** (was 835 match / 201 dead before the route fix).

## Consequences

- The corpus's worst thread renders **26 funnel items instead of 133 chips**; the four real
  documents are the first four tiles.
- Per-message 📎 chips stay exactly as they were — they answer a different question ("what came with
  *this* message"). The funnel answers "what did they send us?".
- `msg.attachments[]` gains `sha` and `band` (additive; the array is still index-aligned with
  `/api/attachment/…/{i}`).
- Two pre-existing endpoint defects fixed as a side effect, both in the funnel's dependency path.

### Known limits

- High-resolution signature art shows inline (above). Bounded by sort order, deliberately not by a
  heuristic.
- Two ~230 px product photos sit just under the postcard floor and stay collapsed — one click away.
- A `message/rfc822` sub-part decodes to zero bytes, so it has no content hash and is never merged;
  each gets a `src`-derived id. Same limitation as the pre-existing byte endpoint.
- The band is recomputed per request. The endpoint is lazy (only a thread expansion hits it), so
  this is not on the polled queue path.
