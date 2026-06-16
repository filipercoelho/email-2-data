# ADR-021 — Intake webapp network posture: loopback → LAN-only behind minimal single-user auth

| Field | Value |
| --- | --- |
| Status | Proposed |
| Date | 2026-06-16 |

## Context

Capture validation happens in the webapp (the "Caixa de Capturas" pending-edits queue), and **no
navigable link is sent through Telegram** (decision R6) — the user opens the app themselves. For the
user to validate from their workstation, the webapp must be reachable on the LAN, which conflicts with
the standing invariant: **port 8042, `127.0.0.1` loopback only** (CLAUDE.md). The app has **no
authentication** today; the sibling materials-costing app is LAN-reachable but sits behind full auth.

## Decision

1. **Relax loopback-only → LAN-only (R6).** The webapp binds to the LAN interface so it is reachable
   from workstations on the workshop network. This narrows the "`127.0.0.1` loopback only" clause; the
   **port (8042) is unchanged** and the [serve port rules](adr-016-post-audit-resilience-hardening.md)
   (fail-loud in container mode) still hold.
2. **"Never public" stays firm.** No inbound webhook, no public exposure, no port-forward. The intake
   bot adds **no inbound port** — it long-polls **outbound** only
   ([ADR-019](adr-019-conversational-intake-capture-adapter.md); design §6).
3. **Minimal single-user auth gate (R11) — the app's first authentication.** A minimal passphrase/login
   gate fronts the webapp; the secret lives in `.env` (CLAUDE.md secrets rule). It guards against
   casual/guest-network access now that a write-capable surface is LAN-reachable. (The owner chose this
   over trusting the LAN unauthenticated, matching the materials-costing posture.)
4. **The in-process worker bypasses the gate by design.** The intake worker writes through the store
   seam (`ProjectStore`), **not** the HTTP API, so it neither needs the auth gate nor opens a port —
   the gate guards **only** the human webapp surface. This is why the bot never needs an authenticated
   public API (no new attack surface on 8042).

## Consequences

- **Status path:** Proposed; becomes **Accepted when the auth gate + LAN bind ship** (Phase 1).
- **Net-new:** the app gains its first auth layer; until now its security model *was* the loopback bind.
  The threat model shifts from "physical access to the host" to "trusted-LAN + a single shared secret."
- **Trace (pending implementation):** the bind/host change in `cli.py`, the auth middleware + `.env`
  secret; tests that an **unauthenticated webapp request is rejected** and that the **worker write path
  is unaffected** by the gate. Design: [solution-design-v1](../10-external-proposals/intake-bot-solution-design-v1.md)
  §2, §6, §11.
