# Reply signature — exact contract

The per-person closing of a reply draft, and the `mailto:` hand-off that carries the draft out of the
app. Decision and rationale: [ADR-047](../03-decisions/adr-047-the-signature-belongs-to-the-person-not-the-playbook.md).
Code is the source of truth — [`src/email2data/signature.py`](../../src/email2data/signature.py).

## Placeholder vocabulary — closed

Every value resolves from the **signed-in person's own `people` row**. Nothing here is ever derived
from the thread, the counterparty, or the model.

| Token | Column | Set by | Empty when |
| --- | --- | --- | --- |
| `{nome}` | `people.name` | admin (Administração → Pessoas) / CLI | never in practice — `name` is `NOT NULL` and blank names are refused |
| `{cargo}` | `people.job_title` | **the person**, «A minha conta» | not filled in (the default) |
| `{telefone}` | `people.phone` | **the person**, «A minha conta» | not filled in (the default) |
| `{email}` | `people.email` | **admin only** — it is the ADR-042 reset-link destination | no address on file (the default) |

A token outside this list is **refused at save time**, in `Workspace.set_person_profile`, with the
offending token named. Validation lives in the store, not the route, so the CLI inherits it.

## Rendering rules

1. **A line whose placeholders ALL resolve empty is dropped.** `Tel.: {telefone}` with no phone
   produces no line at all — not `Tel.:` followed by nothing.
2. **A line mixing a filled and an empty placeholder keeps the filled one.** `{nome} · {cargo}` with
   no `cargo` renders `Filipe Coelho ·`. This is why the editor says *one field per line*.
3. Runs of blank lines collapse to one; leading/trailing whitespace is trimmed.
4. An unknown token that somehow reaches `render` is left **verbatim** — a visible failure, not a
   silent one. Both write paths (the editor and `load_template`) prevent it from getting there.

## Which template is used

```text
person['signature'] (people.signature, a TEMPLATE)   ← if non-blank
  └── else config/signature_template.md              ← the install-wide default, read per call
        └── else signature.DEFAULT_TEMPLATE          ← file missing / empty / has an unknown token
```

`config/signature_template.md` follows the playbook convention: everything after the first `---`
fence is the template. It is bind-mounted, so an edit is **live without a rebuild**. A botched edit
(an unfillable token) degrades to `DEFAULT_TEMPLATE` rather than printing `{telemovel}` in a client's
email.

`people.signature = ''` is a **real state**, not an error: it means "close with the install default",
which is where every person starts and where most stay.

## Where the signature is applied

| Route | Surface | Signed |
| --- | --- | --- |
| `POST /api/reply` | Fila dossier (JobSpec ask draft) | ✅ after the memo, on both hit and miss |
| `POST /api/reply/stream` | legacy `/inbox` report | ✅ tail held back, signed on generator end |
| `POST /api/thread/reply-draft` | Fila dossier (follow-up · payment) | ✅ |
| `POST /api/projects/{pid}/draft[/polish]` | Projetos composer | ❌ **deliberately** — see below |

**Two ordering rules that are not optional.**

- `_reply_cache` is keyed on the **spec**, which says nothing about who is signed in. It therefore
  holds the **unsigned** body and `_sign_for` runs on the way out. Signing before caching hands the
  second reader of a thread a draft closed with the first reader's name and phone number.
- The **stream** cannot retract what it has sent, so the last `_CLOSING_TAIL_LINES + 2` lines are
  buffered and `sign()` runs on those at the end. Both reply paths emit byte-identical text.

**Projetos is excluded on purpose.** Its drafts pass through the ADR-027 AI polish, which may reword
prose; a contact block the model paraphrased is exactly the kind of confident wrongness this project
refuses. Extending signatures there requires the polish to treat the block as verbatim-protected (the
`keep_values` mechanism from ADR-031) and is its own decision.

## Strip-then-append

`signature.sign(body, person, config_dir)`:

1. Render the block. **If it is empty, return `body` untouched** — removing a closing and replacing it
   with nothing is worse than leaving it.
2. Cut a trailing sign-off: the **last** line matching `_CLOSING_RE`, provided it sits within the last
   `_CLOSING_TAIL_LINES` (6) non-blank lines **and is not line 0** (a body that *is* only a closing
   survives).
3. Append `\n\n` + the block.

`_CLOSING_RE` is **narrower than `envelope._CLOSING`** and must stay so: that regex also matches
`Obrigado.`, which is the last line of the `ask`/`follow_up` templates and is body text. Matched
here: `com os melhores cumprimentos` · `melhores cumprimentos` · `os melhores cumprimentos` ·
`cumprimentos` · `atenciosamente` · `atentamente` · `best/kind/warm regards` · `regards` ·
`sincerely[ yours]` · `yours sincerely/faithfully/truly` · `cheers`, each with optional trailing
`,;:!.`.

## The `mailto:` hand-off (Fila dossier)

Button «✉ Abrir no mail», beside «Copiar», in the draft box footer.

```text
mailto:<encodeURIComponent(row.contact)>
  ?subject=<encodeURIComponent(_replySubject(row))>
  &body=<encodeURIComponent(row._draft)>
```

- `_replySubject` prefixes `Re: ` **only** when the subject does not already start with
  `re|rv|res|fw|fwd|enc` (case-insensitive, optional whitespace, then `:`).
- `row.contact` is the Fila row's counterparty address — the first inbound sender, with the
  outbound-only fallback from ADR-033 P4a. Blank is allowed: the client opens with an empty `To` and
  the button's tooltip says «sem endereço conhecido — escreve-o tu» rather than pretending.
- `encodeURIComponent`, not `encodeURI`: a subject containing `&` or `#` would otherwise truncate the
  body, and a truncated draft that looks complete in the mail client is the expensive failure.
- **This does not send.** `mailto:` opens the OS default client's composer. `mailer.py` remains the
  app's only outbound path (ADR-042), and it still sends only a password-reset link.
- «Copiar» stays as the fallback on a machine with no registered mail client, and for a draft long
  enough to hit a local URL-length limit.

## The pasted HTML signature

Nobody types a signature — they copy it out of Outlook or Gmail, where it is HTML.
`normalize_signature(text) -> (plain_text, was_html)` is the single entry point every write path uses.

**Detection** (`looks_like_html`) matches a **closed list of tag names**:
`!doctype html head body meta title table tbody thead tfoot tr td th div p br hr span img a b i u em
strong font style script ul ol li h1-h6 center small blockquote`. It is closed on purpose — a generic
`<[a-z]+` would treat `Filipe Coelho <filipe@lindoservico.pt>` as markup and strip the address out of
a perfectly good plain signature.

**Conversion** (`html_to_text`), deterministic and dependency-free:

| Input | Becomes |
| --- | --- |
| `<script>…</script>`, `<style>…</style>`, `<!-- … -->` | removed **whole** (a Gmail paste's `body{color:#000}` would otherwise read as an unknown placeholder and refuse the save) |
| `<img …>` | **dropped** — alt text on a signature is the logo and social icons (`AUTO`, `Facebook`, …), i.e. noise |
| `<br>`, `</p>`, `</div>`, `</tr>`, `</li>`, `</h1>`…`</h6>`, `</table>`, `<hr>` | a line break |
| `<a>`, `<span>`, `<b>` … (inline) | **no break** — breaking here splits `Tel.: +351 912 345 678` across three lines |
| any other tag | removed |
| `&eacute;` `&nbsp;` `&amp;` … | unescaped; NBSP and zero-width space become a normal space / nothing |
| blank lines | **dropped entirely**, not collapsed — HTML spacing is CSS padding, the breaks are markup nesting |

**After conversion:** placeholders survive, so the intended flow is *paste, then swap your name for
`{nome}`*. An HTML block that flattens to **empty** (image-only) raises `ValueError` rather than
storing `''` — storing it would mean "use the install default" and look like the save was ignored.

The route turns the `was_html` flag into `?ok=sightml` and an explicit banner. Silently rewriting a
paste is its own failure, so the person is always told the textarea now differs from what they pasted.

## Storage — workspace v12

Three columns on `people`, each added by a guarded `ALTER TABLE` in `Workspace._migrate`:

| Column | Type | Default |
| --- | --- | --- |
| `signature` | `TEXT NOT NULL` | `''` |
| `job_title` | `TEXT NOT NULL` | `''` |
| `phone` | `TEXT NOT NULL` | `''` |

No backfill exists or is wanted — `''` preserves the pre-v12 closing for everyone.
`set_person_profile(person_id, *, signature=None, job_title=None, phone=None)` treats `None` as
"leave alone" (so a partial form cannot blank a field it never showed) and `''` as an explicit clear.
Line endings are normalised to `\n` and surrounding whitespace stripped; a rejected signature writes
**nothing**, including the other fields.
