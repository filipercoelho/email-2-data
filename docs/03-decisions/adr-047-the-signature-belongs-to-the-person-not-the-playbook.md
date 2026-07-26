# ADR-047 — The signature belongs to the person, and the draft leaves for their mail client

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-07-26 |
| Relationship | Extends [ADR-013](adr-013-client-email-composer-deterministic.md) (deterministic composition), [ADR-033](adr-033-fila-as-the-decision-queue.md) §10 (contextual R), [ADR-041](adr-041-the-roster-becomes-people-and-a-person-owns-their-account.md) («A minha conta»). Bounded by [ADR-002](adr-002-read-only-imap-guarantee.md) / [ADR-042](adr-042-the-app-sends-exactly-one-kind-of-mail.md) (the app still sends exactly one kind of mail, and this is not it). |

## Context

The Fila's email detail panel drafts a reply and then stops. Two things ended there, and both were
holes rather than boundaries.

**The draft had nowhere to go.** The dossier offered a readonly textarea and a «Copiar» button. To
actually reply, a person copied the text, switched to their mail client, found the thread or started
a new message, pasted, then retyped the recipient and the subject **by hand** — from a screen they
had just left. Every one of those steps is a place to put the wrong address on a client email, and
the whole sequence is friction sitting directly on the action the queue exists to produce. The
Projetos composer had solved this in [ADR-031](adr-031-client-email-purpose-selector-and-verbatim-fact-guard.md)
with a `mailto:` hand-off (`#_openq`); the queue never got one.

**Every reply from every person ended identically.** The closing was a literal block inside
`config/reply_playbook.md`:

> Com os melhores cumprimentos,
> Lindo Serviço

That is a company footer, not a signature. A client who has been talking to one person for three
weeks cannot tell from the mail who wrote it, and the person who reviewed, edited and sent it is
invisible in their own message. This mattered more after ADR-039/-041 than before: the install went
from "the owner" to a roster of people who each sign in, and the one artefact a client ever reads
still said nothing about which of them was speaking. Meanwhile «A minha conta» — the surface ADR-041
built precisely so a person owns something outright — held a password form and nothing else.

## Decision

**1. `mailto:`, and it does not weaken "the app never sends".** «✉ Abrir no mail» opens the
operating system's default client with `to`, `Re: <subject>` and the draft body pre-filled, and stops
there. `mailto:` *opens a composer*; it cannot send. The human still reads, edits and presses send —
the same human-in-the-loop ADR-013 requires, with four manual steps removed from in front of them.
ADR-042's boundary is untouched: `mailer.py` remains the app's only outbound path, and it is still
the password-reset link and nothing else. «Copiar» stays, as the fallback on a machine with no mail
client registered.

`Re:` is added only when the subject does not already carry one, in either language
(`re|rv|res|fw|fwd|enc`), so a reply to a reply does not become «Re: Re: Re: Orçamento». The body is
`encodeURIComponent`-escaped, not `encodeURI` — a subject containing `&` or `#` would otherwise
truncate the body, and a truncated draft that *looks* complete in the mail client is the failure mode
worth one extra call to avoid.

**2. The closing is a property of the signed-in person, stored as a TEMPLATE.** Workspace v12 adds
`people.signature`, `people.job_title` and `people.phone`. `signature` holds the template — `{nome}`,
not "Filipe Coelho" — so a rename or a new phone number updates every future draft, instead of
leaving stale contact details in a block somebody pasted once. `''` means "close with the install
default", which is the state most people are in and is a real state, not an error.

**3. The placeholder vocabulary is closed, and every value comes from the person's own row.**
`{nome}` `{cargo}` `{telefone}` `{email}` — that is all of it. A signature is identity, so a value
invented from the thread, the counterparty or the model would be a lie told in someone else's name:
the zero-hallucination rule arriving where it is least forgivable. An unfillable token is refused at
**save time**, in the store rather than the route, so the CLI and any later caller inherit it — the
alternative is a client email with a literal `{telemovel}` printed in it.

**4. A line whose placeholders are ALL empty is dropped.** Naive substitution gives someone with no
phone number a line reading `Tel.:` followed by nothing, which a client reads as sloppiness. A line
mixing a filled and an empty placeholder keeps the filled one — documented in the editor as "one
field per line", because that line genuinely cannot disappear when half of it is real. This rule is
also why «A minha conta» renders a **preview**: with it in play you cannot tell what the block looks
like by reading the template.

**5. Strip-then-append, deterministically, after the model has finished.** `signature.sign()` cuts a
trailing sign-off before adding the real one. The reply playbook now tells the model not to write a
closing — but the playbook is bind-mounted and anyone may edit it back, and the deterministic
`payment` template ships with its own «Com os melhores cumprimentos / Lindo Serviço», so the strip is
what keeps a client from reading two sign-offs. The regex is deliberately **narrower than
`envelope._CLOSING`**, which also matches "Obrigado." — the last line of the ask/follow-up templates,
where it is body text. Reusing that regex would have silently deleted the thanks from every client
email.

**6. If the rendered signature is empty, nothing is stripped and the draft is returned untouched.**
Removing a closing and replacing it with nothing is worse than leaving the closing alone. This is
also what makes `person=None` (an unguarded render path) safe rather than merely non-crashing: it
renders the install default with every value empty, rule 4 drops each personal line, and what is left
is the company closing — byte-identical to the pre-ADR-047 output. The honest answer when we do not
know who is asking.

**7. The signature is applied AFTER the reply memo, never inside it.** `_reply_cache` is keyed on the
**spec**, which says nothing about who is signed in. Caching a signed draft would mean the second
person to open a thread reads one closed with the first person's name, function and phone number — a
client email in the wrong name, served from what looks like a pure optimisation. The memo therefore
holds the unsigned body and `_sign_for` runs on the way out, on both the hit and the miss paths.

**8. Signing a STREAM holds back the tail.** `/api/reply/stream` cannot retract what it has already
sent, so a model-written sign-off would be on screen before there was anything to strip it with. The
last few lines are buffered until the generator ends and `sign()` runs on those. Both reply paths
then produce byte-identical text, rather than the streaming one quietly closing twice.

**9. `email` stays admin-set.** `{email}` renders `people.email`, but that column is where a
password-reset link is sent (ADR-042). A stolen session that could rewrite it turns a walk-up into a
permanent takeover, so the signature form does not accept it. Changing your own job title is a
Tuesday; redirecting your own password recovery is not.

**10. A pasted signature is recognised as HTML and flattened to text — and we say that we did.**
Found by looking at the rendered page rather than by a test: nobody *types* a signature, they **copy**
it out of Outlook or Gmail, where it is an HTML table of logos, social icons and inline styles. The
first version stored that verbatim, so `<td style="padding:12px 0px 8px 12px;">` went straight into
the draft a client would read. Every surface this feature feeds is plain text end to end — the
readonly textarea, the clipboard, and the `mailto:` body, which has no HTML at all — so HTML is never
something we can honour as-is. `signature.normalize_signature` detects it against a **closed list of
tag names** (a generic `<[a-z]+` would convert an ordinary `Filipe <filipe@lindoservico.pt>` line),
flattens it, and returns a flag the route turns into an explicit banner. Rewriting what somebody
pasted without telling them is the other half of the same failure.

Three conversion choices are decisions, not defaults:

- **Images are dropped, not replaced by their `alt`.** A mail-client signature's images are the logo
  and the social icons, so alt text contributes `AUTO`, `Facebook`, `Instagram`, `LinkedIn` — noise
  that reads like a bug in a client's inbox. A link that wrapped only an image has no text left and
  disappears with it; a link with visible text keeps it.
- **Every blank line is dropped, not merely collapsed.** A signature's spacing in HTML comes from CSS
  padding, which plain text cannot carry, while the markup nesting (`</div></td></tr>`) emits a break
  per level. Preserving blank lines preserves the artefact and none of the design.
- **`<style>`/`<script>` blocks are removed whole**, which is also what stops a Gmail paste's
  `body{color:#000}` from being read as an unknown placeholder and refusing an otherwise valid save.

An HTML block that flattens to **nothing** (an image-only signature) is a **refusal**, not a silent
clear: storing `''` means "use the install default", so it would look like the save worked and quietly
reverted them. Placeholders survive conversion, so the intended workflow is *paste your Outlook block,
then swap your name for `{nome}`* — which is exactly why a template is stored rather than rendered
text.

## Scope, stated rather than implied

This covers the **email detail panel's** reply drafts: `/api/reply` (+ its stream) and
`/api/thread/reply-draft`. The **Projetos composer is deliberately unchanged** — its drafts pass
through the ADR-027 AI polish, which is allowed to reword the prose, and a contact block the model
paraphrased is exactly the confident wrongness this project refuses. Extending signatures there needs
the polish to treat the block as verbatim-protected (the `keep_values` mechanism ADR-031 already
built for prices), which is its own decision and its own ADR. Because the queue path strips a
trailing closing rather than requiring the templates to lose theirs, no shared config file changed
and the Projetos composer keeps closing exactly as it did.

## Consequences

- Workspace **v12** — three guarded `ALTER TABLE people ADD COLUMN` in `_migrate`. No backfill is
  possible or wanted: `''` is the correct value for everyone, and it preserves current behaviour.
- `config/reply_playbook.md` loses its signature block and gains an instruction not to write one.
  Tokens saved per call; the strip covers an install that edits it back.
- New editable config: `config/signature_template.md`, the install-wide default. Bind-mounted, so it
  is live without a rebuild, like every other playbook.
- New module `signature.py` and **58 tests** (32 in `tests/test_signature.py`, the rest across
  `test_people`, `test_fila`, `test_auth_gate`, `test_cockpit_ui`, `test_webapp`).
- **What this does not do:** it does not add HTML signatures (the drafts are plain text end to end),
  it does not verify that the address in `{email}` is one the person can actually send from, and
  `mailto:` inherits whatever URL-length limit the local client has — a long draft could in principle
  be truncated by the OS, which is why «Copiar» remains.
