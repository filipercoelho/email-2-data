# ADR-048 — Recurring branding art is omitted from the attachment funnel

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-07-30 |
| Scope | `attachments.py`, `crm.py` (schema v6 + `build_crm`), `webapp.py` (`/api/thread`), `cli.py` (`assets status`) |
| Amends | [ADR-046](adr-046-the-attachment-funnel-bands-files-by-what-they-are-for.md) §1 — "nothing is ever dropped" is **narrowed**, deliberately, and this ADR is the record of that |

## Context

ADR-046 banded a thread's files into `FICHEIROS` / `IMAGENS` / `ASSINATURAS` and left signature art
one click away behind a visible count. In use, the visible band still fills with our own signature:
`image001.png`…`image004.png` from `@lindoservico.pt`, over and over, in front of the file someone
actually opened the thread to find. ADR-046 predicted this and called it a known limitation — the
"big" arm promotes a 4322x4320 social icon precisely *because* it is pixel-huge.

The reason it was left standing is that **every band rule reads one part in isolation**, and nothing
about 172 KB of 4322x4320 PNG says "Instagram". ADR-046 measured three ways to demote it and
rejected all three, each because it buried real content: density (a 2437x2441 CAD cut drawing of a
duck scores *below* the icons), filename (Outlook renames everything to `imageNNN`, including a
rescued 431x361 drawing), anchor-wrapping (a stone supplier's whole catalogue is link-wrapped).

**The obvious rule — "images from our own domain are signature art" — is wrong on this corpus, and
that is not a hypothetical.** Measured over the 825-message corpus, `@lindoservico.pt` sends inline,
from the same mailboxes, under the same `imageNNN.png` names:

| From own domain, inline | What it is |
| --- | --- |
| 1280x1280, 4322x4320, 840x779 (58 messages each) | Facebook / Instagram / LinkedIn icons |
| 494x223, 185x83 | the Lindo Serviço wordmark, two sizes |
| 22x22 (× many) | layout spacer gifs |
| **431x361, `image006/009/014.png`, from `orcamentos@`** | **an annotated CAD drawing with dimensions** |
| **1140x566** | **a client's press-kit slide** |
| **262x294** | **a cotton-bag product photo** |
| **2437x2441** | **the duck cut drawing** |

A blanket own-domain rule demotes 90 distinct images including all four of the bottom rows. The
counterparty who loses that drawing is the one paying for it.

## Decision

**1. A fourth signal, measured across the corpus instead of within one part: cross-thread spread.**
Branding rides *every* signature into unrelated conversations; a drawing lives in one conversation.
Counting **distinct `thread_root`s**, not messages:

```
41 threads  Facebook / Instagram / LinkedIn icons, Lindo wordmark
38 threads  small wordmark
 8-32       spacer gifs
 5 threads  animated footer banner
─────────── BRANDING_MIN_THREADS = 3
 2 threads  cotton-bag product photo
 1 thread   CAD drawing (5 messages), press-kit slide (5 messages), duck
```

Content tops out at **2** threads and branding starts at **5**; the threshold sits in that measured
gap. **Message count does not work and was tried first** — the CAD drawing and the press-kit slide
appear in 5 messages each, exactly like the animated footer. Five replies inside one conversation is
what a real drawing being discussed looks like.

This is allowed to demote where ADR-046's three rejected arms were not, because it is not a per-part
guess about pixels: it is arithmetic over observed recurrence, and the duck appears in one thread.

**2. Scope: inline images we sent.** An eligible part is `cid:`-referenced (i.e. not `FICHEIROS`),
`image/*`, and from `signals.OUR_DOMAIN` or a subdomain. A supplier's recurring logo stays in the
collapsed ADR-046 band — deciding about someone else's branding was not asked for and was not
measured. The register is **band-blind** past that: `IMAGENS` and `ASSINATURAS` alike, because
dropping half a proven logo's copies while collapsing the other half is incoherent.

**3. Matching is on the content hash, so an omission holds whoever forwarded it back.** A supplier
quoting our mail carries our logo in their reply; it is still our logo.

**4. Those items are omitted from the funnel payload entirely — no item, no count, no
click-through.** This narrows ADR-046 §1. It was proposed as "collapse into `ASSINATURAS`", which
would have preserved that ADR intact, and the owner chose full omission with the trade stated. The
audit trail that replaces the click is **`email2data assets status`**, which prints every omitted
hash with the measurement that hid it *and* the widest-spread images that stayed, so the threshold
is judgeable from both sides. VISION non-negotiable #2 is untouched: it governs binning a *message*,
and no message, thread or counterparty is ever hidden by this — only a repeated copy of our own logo.

**5. The register lives in `crm.db` (schema v6, `asset_spread`), built by `build_crm`.** It is a
measurement, never a curated list: rebuilt whole on every `crm`/`sync`, so art that stops being sent
leaves it. Cost is one extra MIME parse per message in a batch job (~7 s over 825 messages) and one
indexed query per thread render.

**6. Fail-open, in both directions that matter.** A missing or pre-v6 `crm.db` yields an empty set
and the funnel degrades to plain ADR-046 behaviour — shows too much, never hides silently. And
`assets status` **exits 1** on an absent or empty register rather than printing a clean-looking
empty list.

**7. The per-message 📎 chips keep every part.** The omission is scoped to the *aggregate* view,
which is what was asked for, and this is also the last route from the UI to a wrongly-omitted file.
It is load-bearing besides: those chips are positional against
`/api/attachment/{message_id}/{index}`, so the drop happens in `fold_thread`, never in
`message_parts`. A part removed before that counter increments repoints every later link at the
wrong file *under the right name* — which looks perfect on screen. Pinned by
`test_omitting_an_item_does_not_shift_the_indices_the_chips_address`.

## Consequences

- On the corpus, **1064 of 1673** inline-image placements we sent stop reaching the funnel, across
  **58 distinct hashes** — all 58 confirmed by eye to be logos, wordmarks, spacers, social icons or
  footer banners before the threshold was fixed.
- A logo seen in only one or two threads still lands in `IMAGENS`. ADR-046's known limitation
  survives for non-recurring art, and that is the accepted residue: a leaked logo costs a glance.
- **A new inline image is invisible to the register until the next `crm` rebuild**, and correctly so
  — spread is not measurable from one message. Fresh art shows until the evidence exists.
- Changing `BRANDING_MIN_THREADS` without re-measuring is the way to break this. The gap is 2-vs-5
  on today's corpus; too low buries a drawing, too high shows an extra logo, and only the first is
  expensive.
