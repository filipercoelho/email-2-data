# ADR-028 — A decision persists, is undoable, and stays reviewable

**Status:** Accepted (2026-07-20)
**Context:** an unbiased UX review of the live cockpit (65-thread queue, real data) found the five
structural defects fixed here. The worst was a broken promise: **"Ignorar" on a Para-ti card showed
a toast and kept nothing** — the dismissal lived in a JS `Set`, so every ignored proposal
resurrected on the next page load. `Z` (undo) was advertised on every page but the Para-ti actions
never filled the undo stack, and once a thread was *tratado* it vanished from every surface with no
way to see or reverse the decision. The cockpit design principle ("one keystroke, instant feedback,
**always undoable**") was stated but not kept.

## Decision

1. **Every disposition persists.** Para-ti dismissals are precious human decisions and live in
   `workspace.db` (v8 table `para_ti_dismissals`, keyed by the **content key** `kind|thread_root‑or‑email`
   — the same key the JS uses; `para_ti.item_key` mirrors it and `all_items(dismissed=…)` filters,
   so a dismissal survives reload, restart, and re-sync). Endpoints:
   `POST /api/para-ti/dismiss` / `POST /api/para-ti/undismiss`. The **thread itself is untouched** —
   nothing is binned (non-negotiable #2); only the *proposal* stops resurfacing.
2. **`Z` is universal.** Ignorar, Tratado (both lenses), and the ledger's *reabrir* all push a
   revert closure onto the shared undo stack. A failure toast now tells the truth:
   `falhou — revertido` **only** when an optimistic change was actually rolled back; plain `falhou`
   when the action simply didn't happen (`S.falhou` vs `S.revertido` in `cockpit_ui`).
3. **Decided ≠ deleted: the Tratados ledger.** `GET /api/fila?include=resolved` exposes the
   HANDLED rows; the Fila's *tratados* toggle renders them, `E`/`↺` reopens (with undo), and a
   `?thread=` deep-link that misses the active queue **falls back to the ledger** — so a link from a
   contraparte timeline to an already-decided conversation lands somewhere real, never on nothing.
4. **Machine identity is never a display name** (v8 table `counterparty_names`). A cluster key
   (`nif:…`, `free:…`) stays the identity; what a person sees is: human override → contact
   display-name fallback (`accounts.cluster`) → key. Projects get `POST /api/projects/{pid}/rename`
   — the raw email subject a project is born with is identity, not a name.
5. **Automated senders never reach a decision gate.** `para_ti.propose_project_items` skips
   contacts matching `signals.NO_REPLY_RE` (the Tier-0 pattern — one regex, no drift): a
   mailer-daemon bounce must never carry a green "Criar projeto" button, even if content
   classification read it as an estimate request. The thread stays in the Fila.

## Consequences

- `workspace.SCHEMA_VERSION` = **8** (two new tables, delivered additively by `SCHEMA`; no ALTERs).
  Migration pinned by `tests/test_workspace_migration.py::test_v7_to_v8_…`.
- The undo stack stays **in-memory by design**: `Z` undoes *this session's* actions; the ledger (and
  `/api/para-ti/undismiss`) is the durable reversal path.
- Deep-links from Contrapartes now stay inside the cockpit (Fila `?thread=`/`?search=`, Para-ti
  `?item=`) — the legacy `/inbox` app is no longer a navigation target (ADR-014 extended).
- Companion queue-legibility fixes shipped with this ADR (no schema impact): the `em risco` chip is
  a filter; ordering is sticky per user (`localStorage`, URL wins); only the **critical** red tier
  (WE_OWE ≥ 72 h) pulses; a visible owner filter; `can_draft` rows offer the ADR-013/016 reply
  draft from the queue itself.
