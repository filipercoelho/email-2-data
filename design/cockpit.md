# Cockpit — the triage-delivery surface

Companion to [VISION.md](../VISION.md) and [ROADMAP.md](../ROADMAP.md). This is the design detail for
the **delivery layer**: the surface the team actually works in. It does not introduce new scope — it is
the **convergence point** of work already on the roadmap, plus the two ideas the customer-graph proposal
([design/draft-architectural-report.md](draft-architectural-report.md)) got right that we don't yet have.

Status legend: ✅ done · 🔄 in progress · ⬜ planned.

---

## In one sentence

Turn the prioritized verdicts we already produce into a **quiet cockpit** where the single
highest-stakes thread is always in front of you, clears in one keystroke, and **nothing falls through** —
extending our guarantee from *"we classified it right"* to *"someone actually answered it in time."*

## Why this exists

Today the pipeline produces trustworthy per-email verdicts, an estimation funnel, and a CRM substrate —
but the **team still cannot *act* from one place.** A correct verdict that nobody sees is the same lost
revenue as a missed email. The cockpit closes that gap.

The product thesis is one line: **classification recall → response recall.** Our VISION metric is
"≈0 real-clients-binned" — proving we *saw* the client. The cockpit extends the promise to *follow-through*
(it got answered, by someone, before the clock ran out) and adds *consolidation* (one counterparty, one
story), **without** re-rooting the app on the customer — the durable spine stays the **job**, exactly as
built. We harvest the proposal's best ideas as derived layers; we decline its re-platform.

### What it unifies (not new scope)

| Roadmap thread | Becomes, in the cockpit |
|---|---|
| **P6 Delivery** — "triage from the queue, not the mailbox" | the **Fila** lens |
| **P4** — dynamic thread-aware priority, "who owes the next reply, how long it's been" | the **response clock** |
| **P4** — CRM substrate (`crm.py`) + (deferred) identity resolution | the **Contrapartes** lens |
| **P7** — estimation funnel: JobSpec readiness + clarifying reply | the **Projetos** lens |
| **Proposal graft** — `owner_user_id` on every entity (we have it nowhere) | **ownership** |
| **Proposal graft** — a review queue for irreversible decisions | the **Para ti** decision inbox |

## The experience: a quiet cockpit

The reference class is Superhuman / Linear / Things — a dense, fast, keyboard-first pro tool — **not** a
gamified consumer app. "Addictive" is engineered from three *honest* loops, and we refuse the manipulative
ones:

- **Relief** — every session can reach a real, earned zero (*"Tudo tratado · nada em risco"*). The hook is
  anxiety reduction, and the relief is true: it reflects actual coverage.
- **Momentum** — the pile visibly shrinks; open loops close.
- **Mastery + compounding trust** — keyboard fluency makes you faster, and your corrections visibly make
  the assistant smarter (VISION tenet 5).

**We refuse:** fake urgency, streak-guilt, notification spam, infinite scroll. The urgency here is *real
revenue risk*, which is what makes the engagement honest. **Success is the team spending *less* time in
the app at *zero* misses** — the inverse of dark-pattern design.

### Design principles (every screen obeys these)

1. **The next move is never a question.** The system always has the highest-stakes item focused.
2. **One keystroke, instant feedback, always undoable.** Optimistic UI; nothing waits on the network; `Z` reverses anything.
3. **Pivot, don't navigate.** The same object flows between lenses without losing your place.
4. **AI proposes in dashed ink; the human commits in solid.** A visible grammar of trust.
5. **Never a fake number.** Missing certainty is shown as gaps and questions, never invented confidence.
6. **Calm, not loud.** Rewards are a colour shift and a slide, never confetti.

## The shape: one spine, three lenses + Para ti

One **spine** — `interaction · verdict · project · owner · clock` — already ~80% present (`crm.py`
interactions, `schema.py` verdicts, `project.py`). Every lens is a **pure projection** of that spine; the
same object is never two records, so a fact confirmed in one lens is true in all three instantly.

```
                  ┌───────────  ⌘K palette  ───────────┐
   ┌─────────┐ ┌──┴──────┐ ┌──────────────┐ ┌──────────┴──┐
   │ PARA TI │ │  FILA   │ │ CONTRAPARTES │ │  PROJETOS   │
   │ decisões│ │ por risco│ │  por relação │ │ por trabalho│
   └────┬────┘ └────┬────┘ └──────┬───────┘ └──────┬──────┘
        └───────────┴──── ONE SPINE ───────────────┘
```

Enter by mood (*what's urgent / who is this / which job*); pivot between lenses on the same object.

### The component kit (build once, reuse everywhere)

`Counterparty badge` (semantic colour, identical everywhere) · `Response clock` (green→amber→red + age +
text) · `Owner chip` (`sem dono` = subtle warning) · `Confidence tag` (`regra·NIF` / `Gemini·0.91` →
"Porquê?") · `Source dot` (● offline / ◐ llm / ● user-green) · `Readiness ring` · `Action bar`
(one action, three entry points: key + button + ⌘K). Building this kit *is* the unit of work.

---

## Required deliverables

Each is a shippable unit. Sizes: **S** ≈ ½–1 day · **M** ≈ 2–3 days · **L** ≈ 4–6 days.

| # | Deliverable | What it is | Graft / phase | Depends on | Size |
|---|---|---|---|---|---|
| **D1** | **Response clock + thread state** | per-thread *who-owes-whom* + age + risk score + sort key | I1 · P4 | `crm.interactions` (thread_root, direction, date), `AWAITED_OUTBOUND` | **M** |
| **D2** | **Ownership** | one owner per thread/project; `sem dono` flag; assign action; team roster | I2 | new `thread_state` in `Workspace` | **S** |
| **D3** | **Fila lens (hero UI)** | thread-collapsed queue, risk sort, thread card + clock + owner chip + action bar, keyboard + optimistic + undo, zero state | P6 | D1, D2, the component kit | **L** |
| **D4** | **Contrapartes lens** | counterparty timeline (interactions + project milestones), "devemos resposta há", open projects | I3 | `crm.by_contact/by_entity`, `project.py` | **M** |
| **D5** | **Para ti decision inbox** | unified human-in-loop gates: low-confidence triage, propose-project, propose identity match, approve draft | I4 | `NEEDS_REVIEW`, D1, D6 | **M** |
| **D6** | **Identity clustering** | deterministic rungs (exact email / known domain / NIF) auto-cluster; weak rungs → Para ti | I5 · P4 (deferred) | `crm.entity_refs`, gazetteer | **M** |
| **D7** | **Projetos lens polish** | readiness ring, gaps-as-PT-questions inline, honest-conditional draft one click, stage pipeline, export-at-estimable | P7 | `jobspec.py`, `replydraft.py`, `project.py` (mostly compose) | **S–M** |

**Cross-cutting (applies to every deliverable):** command bus (one action / three entry points) +
optimistic-state with a global undo stack · trust/explainability (confidence + `decided_by` + "Porquê?" +
source dots; dashed=proposed / solid=committed grammar) · a11y (keyboard-complete, colour-never-the-sole-
signal, `prefers-reduced-motion`) · i18n PT-PT · purposeful motion (120–200ms, feedback only) ·
**read-only / never-sends preserved.**

---

## The critical MVP (one week): the *Fila* response cockpit

Ship the smallest slice that delivers the thesis — **D1 + basic D2 + the core of D3.** That is the entire
Fila upgrade, ~70% of the felt value, and it stands alone.

### In scope

- **Thread-collapse** — the queue unit becomes the *thread* (latest state), folded from `crm.db`.
- **Response clock** — per-thread who-owes-whom + age, computed deterministically from direction + dates;
  **response-risk replaces priority×urgency as the default sort** (priority still feeds it).
- **The loop** — `E` *marcar tratado* → thread leaves the active queue → counter decrements → next thread
  auto-focuses; **optimistic, `Z` to undo**; persisted to the precious `Workspace` (survives `sync`).
- **Basic ownership** — assign from a static `settings.team` roster; `sem dono` flag; `A` to assign.
  Thread-level only.
- **Zero state** — *"Tudo tratado · 0 em risco"* + the day's honest tally.
- **Keyboard** — `J/K`, `E`, `A`, `Z`, `Enter`, `/`, reusing the existing live webapp.

### Explicitly out (follow-on deliverables)

Contrapartes lens (D4) · Para ti (D5) · identity clustering (D6) · readiness ring & funnel polish (D7) ·
⌘K command palette · propose-project automation · motion polish · **Sent-folder auto-detection of replies**
· multi-inbox.

### Build order (≈5–6 days)

1. **Day 1 — `cockpit.py`** (new): thread-fold + response-clock logic; unit tests on `crm` fixtures;
   **validate who-owes-whom on a hand-checked 20–30 thread sample from the real 265-corpus.**
2. **Day 2 — persistence + API:** `thread_state` table in `Workspace` (owner, handled, ts); endpoints
   (`POST /api/thread/{root}/handled`, `/owner`, undo) following the `confirm`/`reply` pattern in
   [webapp.py](../src/email2data/webapp.py); confirm state survives a `sync` re-run.
3. **Days 3–4 — Fila UI** in the report template: thread cards, clock, owner chip, action bar; risk sort;
   keyboard + optimistic + undo; zero state. *Coordinate with the in-flight `report.py` WIP.*
4. **Day 5 — end-to-end validation** on the 265-corpus; fix clock edge cases; extend `test_webapp`.
5. **Day 5–6 — buffer + a11y pass** (keyboard-complete, focus ring, colour-not-sole-signal) + PT strings.

### Exit criteria (measurable, end-to-end — no proxies)

- On the **real corpus**, the Fila folds N emails → M threads, each with a correct who-owes-whom + age,
  sorted by response risk; **hand-checked "we owe a reply" accuracy ≥ an agreed bar on the 20–30 thread
  sample** (red-team the clock against reality, not a cached run).
- A human clears the active queue to **zero** with the keyboard, assigns owners, and undoes — and that
  state **survives `email2data sync`** (precious overlay, like `decisions`/`projects`).
- **Zero regression:** still read-only, still never-sends; existing confirm/reply/jobspec panels still
  work; test suite green.

### Non-negotiables

Read-only · never sends · precious human state survives re-runs · no fake numbers · every AI value still
shows confidence + `decided_by`.

### Red-team / open questions

1. **The "we answered" signal under read-only.** Replies are sent from the user's own mail client, so the
   app can't *see* the outgoing reply unless we observe the Sent mailbox. **MVP decision:** *tratado* is a
   **manual** one-key mark (and a future "Copiar resposta" auto-marks *aguarda envio*); automatic
   reply-detection (Sent-folder fetch / detecting our outbound in the thread) is a **Phase-2** deliverable.
   The clock must be honest about what it knows. *This is the crux of making the clock real.*
2. **Owner roster source.** Static `settings.team` for the MVP (single-user localhost → owner is an
   attribution label, not auth). Real accounts/SSO later.
3. **Thread identity edge cases.** Subject-change breaks `References`; internal forwards of a client PO; a
   thread spanning client + supplier. Reuse `crm._thread_root` and its known limits — **surface, don't
   hide, mis-threads.**
4. **Snooze vs handled.** Defer *adiar/snooze* past week 1 — handled + undo covers the loop.
5. **Coordination with the `report.py` WIP** (300-line uncommitted diff on `feat/generic-filters`). The MVP
   heavily touches the report template; land/rebase that work first, or carve the Fila as a distinct render
   path, to avoid churn.

---

*Next: wire this as the Phase-6 delivery detail in [ROADMAP.md](../ROADMAP.md), then build D1.*
