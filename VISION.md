# Vision — email-2-data

## In one sentence

Turn the email flowing through Lindo Serviço's inboxes into a **trustworthy, prioritized decision
queue** — so the team instantly sees what is a paying client needing action, what is routine, and
what is noise — while spending the **least compute necessary** and getting **smarter with every
message**.

## Why this exists

Email is Lindo's de-facto coordination layer, but it has no notion of ownership, priority, or state.
A client's estimate request and a Festool promo arrive in the same undifferentiated stream. Manual
triage doesn't scale, and a missed client request is lost revenue. We treat email as an **event
source**, not UI content: the input is messages, the output is structured business signal.

## The governing principle

> **Spend compute (tokens, model power) in proportion to `uncertainty × business impact`.**

Certain and low-stakes → decide offline, for free. Uncertain and high-stakes (a possible client we
might ignore) → escalate to stronger models. This one rule drives cost, quality, and the learning
loop simultaneously.

## Tenets (several learned the hard way, from real mail)

1. **Read-only, always.** EXAMINE + `BODY.PEEK`; never mutate the live mailbox. The one
   unrecoverable mistake.
2. **Never silently bin a client.** A false `IGNORE` loses revenue; a false "needs review" costs
   seconds. The system is asymmetric by design.
3. **The body decides counterparty — not the domain.** Vision Box / Amadeus is a *client* despite
   its domain; Spandex is a *supplier*. Domain is at most a prior, never a verdict.
4. **Direction ≠ counterparty.** An internal forward of a client PO is still *about a client*. Who
   sent this message and who the business relationship is with are different axes.
5. **Knowledge compounds.** Confirmed facts — domain reputation, exemplars, human corrections — feed
   back and shrink future work. The system should need the expensive path less over time.
6. **Local-first & private.** Mail stays on our hardware. Logs carry no bodies/addresses; derived
   results are personal data and are treated as such.
7. **Rules before models; cheap models before expensive; models only where needed.**
8. **Every verdict explains itself** and records *who decided it* (tier/engine + version) — so it's
   debuggable, auditable, and replayable.

## What success looks like

- **~100% recall on client job requests / POs**, and **≈0 real-clients-binned**.
- Most mail resolved with **zero or one cheap LLM call**; the escalation rate **trends down** as
  knowledge accumulates.
- **Tokens per email decreasing** over time at constant-or-better accuracy.
- Triage time per inbox materially reduced — and nothing critical missed.

## Non-goals

Not an email client. Not sending mail (strictly read-only). Not autonomous decision-making (human
stays in the loop). Not locked to one provider or one mailbox.

---
See [roadmap.md](docs/01-requirements/roadmap.md) for the phased plan and
[docs/02-architecture/approach.md](docs/02-architecture/approach.md) for the right-sized v1
engineering detail.
