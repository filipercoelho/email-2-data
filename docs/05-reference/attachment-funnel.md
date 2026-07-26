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

## Known limits

- High-resolution signature art (4322×4320 social icons) lands in `IMAGENS`. Three demotion signals
  — density, filename, anchor-wrapping — were measured and **all three would bury real content**
  (ADR-046 §Rejected). Bounded by sort order instead; the duck drawing is pinned by a test.
- Two ~230 px product photos sit just under the postcard floor and stay collapsed — one click away.
- A `message/rfc822` sub-part decodes to zero bytes, so it has no content hash (same limitation as
  the pre-existing byte endpoint).
