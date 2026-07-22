# ADR-025 — Attachment guards, surfaced spec failures, and scoped re-extraction with model tiers

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-07-19 |

## Context

Project `p-0002` ("FW: Orçamento Placas em Aço Corteno") presented as a near-empty lead — one item
field, coverage `0.22` — although its email chain carried material, thickness, dimensions and finish
in plain text. The obvious reading was "the model was too weak, give us a heavier one". That reading
was wrong, and the difference matters because it would have bought a more expensive model to fix a
bug that had nothing to do with model strength.

Reproduced live against Vertex, same email, same `gemini-2.5-flash`, the only variable being the
attachment:

```
WITH IMAGE:    LLMError → 400 INVALID_ARGUMENT "Provided image is not valid."
WITHOUT IMAGE: {"line_items":[{"item":"estrutura","material":"Aço Corten",
                "dimensions":"Altura total: 200 cm","thickness":"entre 5 e 8 mm",
                "colour_finish":"Natural (sem pintura)"}], ...}
```

The attachment was `portico.png` — **1,036,389 bytes at 17975×15776 px (283.6 megapixels)**, a vector
export that compresses to about 0.06 bytes per pixel. `envelope.attachment_media` filtered images by
**byte size only** (20 KB–6 MB), so it sailed through. Vertex rejects it deterministically, and
`llm.call` burned all five retries on the identical 400.

Three defects then conspired to make a hard failure look like a thin email:

1. **`specbuild.build_entry` swallowed the exception** — one line to stdout, `drafted_obj = None`.
   Nothing reached the DB, the UI, `results.jsonl` or `audit.jsonl`.
2. **It degraded silently** to the single-item entities fallback, which is indistinguishable from a
   genuinely sparse lead.
3. **The incremental gate froze it forever** — the broken entry is in `jobspecs.jsonl`, so every
   subsequent sync kept it. The only escape was `jobspec --draft`, which re-bills the whole corpus and
   would have failed identically on this message.

Separately, `portico.svg` (580 B — the actual machine-readable geometry) was *dropped for being too
small*, so even a working image path never saw the most precise drawing in the email.

This is the same class of silent loss that [ADR-016](adr-016-post-audit-resilience-hardening.md)
hardened for triage (Tier-1 failure → `NEEDS_REVIEW`). The spec pass never received that treatment.

## Decision

1. **Guard attachments by pixels, not just bytes.** `envelope._image_size` reads dimensions from the
   file *header* (PNG/JPEG/GIF/WEBP) without decoding — decoding a 283 MP image to discover it is too
   big would defeat the purpose. Images above `max_image_pixels` (33.2 MP) or `max_image_side`
   (8192 px) are dropped. **Unmeasurable formats are let through**, not blocked: dropping a readable
   drawing on a guess is the worse error. A partial spec beats no spec.

2. **SVG is text, not an image.** `image/svg+xml` is routed into `attachment_texts` as XML source.
   It is machine-readable geometry and is usually far below the image byte floor, so the previous
   path guaranteed it was discarded.

3. **A failed spec pass is visible.** `build_entry` sets `entry["spec_error"]` and writes a
   `spec_draft_failed` audit event (`message_id`, error **type**, tier — counts/ids only, per the
   `audit.py` privacy rule). The UI surfaces it on the project page. An extraction that failed must
   never again be indistinguishable from an email with little to say.

4. **Re-extraction is scoped, not global.** `rebuild_jobspecs(only={message_id, …})` rebuilds exactly
   those ids *even in incremental mode*; everything outside `only` keeps its existing entry
   byte-for-byte. This is deliberately narrower than `incremental=False`: re-extracting one project
   must not re-bill a Tier-1 pass for every job email in the corpus. Normal-path idempotency
   (CLAUDE.md) is unchanged — the bypass requires an explicit, human-initiated `only` set.

5. **Model tiers are per call, never per process.** `llm.with_tier(cfg, tier)` returns a **copy** of
   the LLM config with `llm.tiers.<tier>` applied. Returning a copy is load-bearing: `settings` is
   shared process state, and mutating it would silently repoint every later call in the same run.
   An absent or unknown tier is a no-op rather than a surprise model switch.

   | Tier | Model | Use |
   | --- | --- | --- |
   | `light` | `gemini-2.5-flash-lite` | cheap re-pass |
   | `standard` | `gemini-2.5-flash` | the configured default |
   | `heavy` | `gemini-2.5-pro` (`max_tokens` 8192, `thinking_budget` 4096) | a stubborn lead |

   `thinking_budget` was hardcoded to `0` for every Gemini call; it now comes from config, because a
   reasoning model given a zero budget can spend its whole output allowance on thinking and return
   empty text — which would surface as exactly the silent failure this ADR exists to remove.

   The context-cache key is `(model, sha256(system))`, so tiers cannot collide: each model keeps its
   own cached prefix.

6. **`jobspecs.jsonl` is written atomically** (temp + `os.replace`). A crash mid-write used to
   truncate it to a prefix, and the lost entries are recoverable only by re-spending the LLM pass
   that produced them.

## Consequences

- The Corten lead re-extracts to coverage **0.56** with five populated fields, on the **same
  `standard` model** — confirming the root cause was the attachment, not model strength.
- Tier selection is available where it genuinely helps (a sparse but valid extraction), and is
  correctly understood as *not* the fix for a crashing request.
- A re-extract costs one LLM call per message in the target project, not one per job email in the
  corpus.
- `spec_error` is a new optional key in `jobspecs.jsonl` entries. Readers must tolerate its absence.
- **Not addressed here:** `p-0002` reaches only one message, while its conversation spans five
  `thread_root`s in `crm.db`. Attaching the rest is a thread-graph question, and pulling SUPPLIER or
  INTERNAL mail into a client jobspec would cross [ADR-003](adr-003-counterparty-from-body.md) /
  [ADR-004](adr-004-direction-is-not-counterparty.md) — it needs its own ADR, not a wider `only` set.
- **Also not addressed:** `process` is in the field registry but absent from `SPEC_ITEM_KEYS`, so no
  LLM channel can ever populate it. That is a registry/schema gap, tracked separately.
