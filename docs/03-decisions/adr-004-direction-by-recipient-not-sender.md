# ADR-004 — Direction is a separate axis; outbound is classified by recipient

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-06-10 (back-filled; commits 7df53b7, 93676ae) |

## Context

*Who sent a message* and *who the business relationship is with* are different axes. An
internal forward of a client PO is still *about a client*. Early logic conflated the two and
mis-classified outbound mail by looking at the sender (always Lindo) instead of the party
Lindo is writing to.

## Decision

**Direction (inbound/outbound) is its own axis, set deterministically from headers, not by
the model.** For **outbound** mail the counterparty is the **recipient**, so the gazetteer
lookup and classification anchor on the recipient (first `To`, then recipient domain), not
the sender. For inbound mail they anchor on the sender.

## Consequences

- The gazetteer lookup branches on direction
  (`src/email2data/cascade.py:57-64`): outbound → `lookup(first_to) or lookup(to_domain)`.
- Fixes the class of bug where Lindo's own outbound estimate to a client was binned as
  internal/noise.
- Trace: commits `7df53b7` (outbound direction rule), `93676ae` (outbound classification),
  `3585f4e` (gazetteer by recipient domain); `schema.py` `direction` field "set from
  signals, not the model".
