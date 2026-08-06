# reference — the attachment funnel

The exact contract of the `attachments` block on `GET /api/thread/{thread_root}`, the band rule,
and the constants. Decision + evidence: [ADR-046](../03-decisions/adr-046-the-attachment-funnel-bands-files-by-what-they-are-for.md).
Code: [`src/email2data/attachments.py`](../../src/email2data/attachments.py) — the source is the
truth; this page mirrors it.

## The block

```jsonc
"attachments": {
  "items":  [ /* see below, sorted: band order, then largest first, then name */ ],
  "counts": {"FICHEIROS": 4, "IMAGENS": 7, "ASSINATURAS": 15},
  "bands":  ["FICHEIROS", "IMAGENS", "ASSINATURAS"]
}
```

Built from **every** interaction of the thread, in the CRM's oldest-first order, and folded
**before** the message dedup — a file whose only carrier is a suppressed Trash copy still surfaces.

## One item

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | `str` | First 16 hex of the sha256 of the decoded bytes — the dedup key. A zero-byte part has no hash and gets `"n" + sha256("<message_id>:<index>")[:15]` instead, so it is unique and never merged. |
| `name` | `str` | Decoded filename, or `"(sem nome)"`. A later copy's real name replaces a missing one. |
| `type` | `str` | MIME content type, lowercased. |
| `kind` | `str` | Tile category: `pdf` · `image` · `sheet` · `doc` · `slides` · `archive` · `cad` · `mail` · `file`. |
| `size` | `int` | Decoded bytes. |
| `px` | `[w, h]` \| `null` | Pixel size, read from the file **header only** (`envelope._image_size`) — never decoded. `null` for non-images and unmeasurable formats. |
| `band` | `str` | `FICHEIROS` \| `IMAGENS` \| `ASSINATURAS`. Always an **INFERENCE**. |
| `band_evidence` | `str` | pt-PT — the rule that fired *and the measurement that triggered it*. Rendered on the tile. |
| `src` | `{message_id, index}` | The **first** carrier. `index` addresses `GET /api/attachment/{message_id}/{index}`. |
| `first_seen` | `str` | Date of that first carrier (post header-recovery). |
| `from_email` | `str` | Sender of that first carrier (post header-recovery). |
| `n_copies` | `int` | Byte-identical copies across the thread. |
| `preview` | `bool` | Server's decision: an `image` at or under `PREVIEW_MAX_BYTES`. The client must not re-derive this. |
| `pages` | `int` \| absent | PDF page count (pypdf, best-effort). |

Additive, on each message's own `attachments[]`: `sha` and `band`. That array stays index-aligned
with `/api/attachment/…/{i}` — see *Index stability* below.

## The band rule

Evaluated in order; first match wins.

| # | Test | Band |
| --- | --- | --- |
| 1 | `Content-Disposition: attachment` | `FICHEIROS` |
| 2 | no `cid:` reference in any `text/html` body | `FICHEIROS` |
| 3 | `cid:`-referenced but `content_type` is not `image/*` | `FICHEIROS` |
| 4 | long side > `BIG_SIDE`, or `w*h` > `BIG_PIXELS`, or bytes > `BIG_BYTES` | `IMAGENS` |
| 5 | short side ≥ `POSTCARD_MIN_SIDE` **and** aspect < `POSTCARD_MAX_ASPECT` | `IMAGENS` |
| 6 | otherwise | `ASSINATURAS` |

Rule 3 exists so the first `cid:`-referenced PDF is not filed as a logo (the corpus has none yet).
Rule 5 is the **calibrated** arm: measured over 553 messages, the widest real-content image is
**1.79:1** and the narrowest signature banner **2.14:1**.

## Constants

| Name | Value | Why |
| --- | --- | --- |
| `BIG_SIDE` | `600` px | long side |
| `BIG_PIXELS` | `250_000` | total px |
| `BIG_BYTES` | `200_000` | encoded size |
| `POSTCARD_MAX_ASPECT` | `2.0` | content ≤ 1.79:1, banners ≥ 2.14:1 |
| `POSTCARD_MIN_SIDE` | `250` px | at 200 px it leaks 7 signature cards to rescue 2 photos |
| `PREVIEW_MAX_BYTES` | `262_144` (256 KB) | ~70 % of previewable images; worst thread 6.31 MB → 0.45 MB |

## Index stability — the invariant

`message_parts()` walks parts under **exactly** the predicate `envelope._attachments` uses and
yields `index` from that same counter — the one `envelope.attachment_part` re-walks to serve bytes.
**Banding is additive metadata; nothing may filter before that counter increments.** A filter that
reindexes repoints every 📎 link at a different file, under the right name, and looks correct on
screen.

Verified by bytes, not inspection: every funnel item of every attachment-bearing thread was
refetched through the public endpoint and its sha256 compared — **1039/1039 match**.
Pinned by `tests/test_attachments.py::test_message_parts_indexes_exactly_like_attachment_part`.

## Âmbito: conversa vs projeto (ADR-052)

Everything above is **one thread**. A `/projetos/<pid>` «Ficheiros» panel is a *project* scope, and it
is assembled in the client — see
[ADR-052](../03-decisions/adr-052-the-project-file-list-spans-every-thread-and-the-intake-channel.md)
for why that is a security decision and not a convenience.

**Two sources, one list.**

| Source | Where it comes from | `src` shape | Byte route |
| --- | --- | --- | --- |
| email | `GET /api/thread/{root}` → `.attachments`, one call per attached root | `{message_id, index}` | `/api/attachment/{message_id}/{index}` |
| intake capture | `GET /api/projects/{pid}/captures` → the same block shape | `{capture_id, index}` | `/api/captures/{capture_id}/{index}`, i.e. `…/media/{index}` |

`GET /api/projects/{pid}/captures` answers `{items, counts, bands, n_captures}` — deliberately the
same shape as `attachments` above, so both fold through one `attMerge`. Its items add:

| Field | Type | Meaning |
| --- | --- | --- |
| `source` | `"capture"` | Present **only** on capture items. Nothing else in the funnel sets it. |
| `channel` | `str` | `telegram` \| `manual` \| … — the capture's own channel column. |
| `asserted_by` | `str` | Who registered it. Replaces `from_email`, which is `""` for a capture. |
| `missing` | `true` \| absent | The media file is not readable under `captures_dir`. Listed anyway, size 0 — ADR-020's store is the sole copy, so a gap is an incident to see. |

`id` follows the same content-hash rule as an email part, which is the whole point: **a capture joins
the dedup**, so the same drawing mailed and re-sent through Telegram is one item with `n_copies: 2`.
A capture whose bytes are unreadable falls back to `"k" + sha256("<cid>:<index>")[:15]` — the prefix
is outside the hex alphabet for the same reason `"n"` is.

**Client-side item fields** (added by `loadSource`, never sent by the server):

| Field | Meaning |
| --- | --- |
| `thread_root` | The root whose block carried this item. Drives «ver na fila →». Stamped per block *before* the merge. |

**`attMerge(blocks)`** flattens every block and sorts by `first_seen` **ascending before** the dedup
pass, so the surviving copy is the *chronologically first carrier* — not the first attached thread.
An item with no date sorts last. That ordering is a **precondition** of the provenance line: a tile
naming a sender it did not earn is a confident lie.

**`attFunnelHTML(att, o)` / `_attTile(it, o)` options:**

| Option | Effect |
| --- | --- |
| `title` | Heading text. Default `'Ficheiros da conversa'`; the project panel passes `'Ficheiros do projeto'`. |
| `showSource` | Render the `.atti-src` provenance line under each tile. The tile is then wrapped in `.attw` — the line carries its own link, and an `<a>` inside an `<a>` is invalid HTML. |
| `bigPreviews` | Preview an `image` regardless of `PREVIEW_MAX_BYTES` (still lazy). Only the Ficheiros panel sets it — see ADR-052 §3.8. |

Both bands call `.map(it=>_attTile(it,o))`. A bare function reference would pass `Array.map`'s
**index** as `o` — falsy for tile 0, truthy after — and is grepped for by
`tests/test_attachments.py::test_the_tile_helper_is_never_passed_the_array_index_as_options`.

**The list can be explicitly incomplete.** One `try` per root: a root that 404s (ungranted under
ADR-045, or dangling) is collected into `failed[]` and costs only itself, and the panel says
«⚠ N de M conversas não carregou — esta lista está incompleta. Pode faltar aqui um ficheiro.» One
merged sentence, because the client cannot distinguish the two 404s — and must not, or a 403 would
confirm a thread exists.

## Known limits

- The project list's **preview gate is opted out of, not fixed**: `bigPreviews` downloads a full-size
  image to draw a 132 px tile. Lazy, tab-gated, and named in ADR-052 §3.8; the real fix is
  server-side thumbnailing. Revisit at ~10 previewable items over 1 MB in one project.
- Cross-thread dedup is **unexercised in production** — all 13 live projects have exactly one thread.
  Only `tests/test_cockpit_urls_e2e.py` runs it.
- High-resolution signature art (4322×4320 social icons) lands in `IMAGENS`. Three demotion signals
  — density, filename, anchor-wrapping — were measured and **all three would bury real content**
  (ADR-046 §Rejected). Bounded by sort order instead; the duck drawing is pinned by a test.
- Two ~230 px product photos sit just under the postcard floor and stay collapsed — one click away.
- A `message/rfc822` sub-part decodes to zero bytes, so it has no content hash (same limitation as
  the pre-existing byte endpoint).
