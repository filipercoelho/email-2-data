# ADR-043 — Header decoding owns the raw 8-bit case (the parser destroys it first)

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-07-26 |
| Scope | `headers.py` (new), `envelope.py`, `identity.py` |

## Context

A real client email — a scenography quote request, the most valuable kind of mail this app
handles — rendered in the Fila as:

```
Pedido de or��amento ��� constru����o de cenografia "Imita����o dos P��ssaros"
```

One U+FFFD per **byte**: `ç` is 2 bytes in UTF-8, `—` is 3, `çã` is 4. That counting is the whole
diagnosis — the bytes were decoded by something that had already given up on them individually.

The sender wrote the header as **raw 8-bit UTF-8**, without RFC 2047 encoded-words. That is out of
spec (RFC 5322 restricts headers to ASCII) but common enough to matter: **2 of the 553** messages
in `corpus/` do it, and both are client mail.

`envelope._decode_header` handled RFC 2047 correctly and was never the problem — **it never saw the
bytes.** `email.message_from_bytes` reads the message as ASCII + `surrogateescape`, then compat32's
`header_fetch_parse` sanitizes any header carrying non-ASCII into `Header(charset="unknown-8bit")`,
whose `str()` replaces every byte with U+FFFD. The loss happens **inside `msg.get()`**, before any
decoding code can run, and it is not reversible afterwards.

The same root cause carried two latent defects nobody had hit yet:

- `identity.canonical_id` did `message_id.strip()` on what is a `Header` object for an 8-bit
  Message-ID → **`AttributeError`, aborting a whole fetch** over one out-of-spec header.
- `decode_header` **raises `HeaderParseError`** on a malformed encoded-word (`=?utf-8?b?a?=`), and
  the old `_decode_header` did not catch it → the same whole-run abort.

## Decision

**1. A new module, `headers.py`, owns "header value → text".** Both derivations of a message's
identity (`envelope.parse_eml` and `identity.canonical_id_from_raw`) now go through it, which is
what keeps them from diverging — the property `identity.py` exists to guarantee.

**2. Parse with `RAW_HEADERS`, a `Compat32` subclass whose `header_fetch_parse` returns the value
unchanged.** Everything else about the parser is identical (same `Message` class, same `walk()`,
`get_payload(decode=True)`, `get_content_charset()`); only header *retrieval* changes, handing back
the raw surrogate-escaped value instead of a lossy `Header`. The bytes stay recoverable.

**3. Decode with a ladder, and trust the declared charset first.** For encoded-words the declared
charset wins; for raw 8-bit, RFC 5322 supplies no charset, so: UTF-8 → cp1252 (Outlook's smart
quotes and dashes) → latin-1, which cannot fail and closes the ladder. **Measured before choosing:**
across all `Subject`/`From`/`To`/`Cc`/`Reply-To` headers in the corpus (573 utf-8, 77 iso-8859-1, 12
windows-1252, 367 unlabelled), **zero** declared charsets fail to decode and **zero** are
mislabelled. So no "declared charset is probably wrong" heuristic was added — there is nothing here
for it to fix, and guessing against an explicit label is how correct mail starts rendering wrong.

**4. A header that mixes an encoded-word with raw 8-bit text is decoded per chunk.** `decode_header`
hands the *plain* runs back as `raw-unicode-escape` bytes; decoding with that same codec is the
exact inverse, which keeps the 8-bit bytes as surrogates for repair. Decoding them as UTF-8 (the old
behaviour) turned them into literal `\udcc3` backslash text.

**5. No surrogate may leave `headers.py`.** This is the invariant, not a nicety: sqlite3 and
`json.dump` both raise `UnicodeEncodeError` on a lone surrogate, so a surrogate that escapes turns a
*rendering* bug into a *write* failure. `header_text()` repairs at the point of retrieval, so
address headers, `Message-ID`, `References` and `Date` are all text before anything else touches
them.

**6. Never raise on one bad header.** A malformed encoded-word degrades to the repaired raw text.
Losing the subject of a real client email is bad; aborting the run that classifies 553 of them is
worse.

## Consequences

Verified by re-parsing the whole corpus with the old and new code and diffing every field of all 553
envelopes: **2 messages changed, in exactly 3 fields** (`subject`, `to`, `attachments[].filename` —
the attachment's download name was mojibake too), and **`message_id` changed in zero**, so no id
churn and no orphaned rows. Cached artifacts were repaired in place: the 2 stale subjects in
`out/results.jsonl` (backed up first; verdict fields untouched, no LLM re-spend) and `crm.db`, which
re-derives subjects from the live parser on `email2data crm`.

Both affected messages were classified by Gemini while their subject was mojibake. The bodies were
never affected (body decoding uses the payload bytes and its declared charset), so the verdicts are
built on intact evidence, but a `triage --full` on those two is the only way to remove the doubt —
**not** done here, since re-triage changes verdicts and that is the owner's call.

Non-negotiable #1 is untouched: this is all parse-side, no IMAP behaviour changes. The
zero-hallucination doctrine is served rather than bent — a decoded character is recovered from the
sender's own bytes, never invented, and the fallback ladder is deterministic, so re-running yields
the same string.

**Known limit.** `scopes.py`, `cascade.py` and `crm.py` still call `email.message_from_bytes`
directly for *header signals and addresses only* (direction, delivery headers) — ASCII-structured
fields where the sanitization is harmless. They were deliberately left alone to keep the blast
radius at the display/identity path; if any of them ever needs a human-readable header, it should
move to `headers.parse_message`.
