# Conversational Intake — Execution Plan for the Remaining Items

| Field | Value |
| --- | --- |
| Status | **Execution plan.** Sequenced build plan for the work left after M1–M3(API). |
| Date | 2026-06-21 |
| Builds on | [solution-design-v1](intake-bot-solution-design-v1.md), [mvp-plan-v1](intake-bot-mvp-plan-v1.md), ADR-019/-020/-021 |
| Committed so far | M1 `75265bb` · M2 `0906d4c` · M3-API `ddae67e` — **372 tests green, ruff clean** |

## How this plan works

Each work-package (WP) is an independently shippable commit and follows the same gate that caught real
defects in M1/M2: **build → test (a fail-before/pass-after regression) → adversarial review → fix →
commit.** The order respects data-safety and the precious-DB migration discipline. Every LLM step
(WP3/WP4) reuses the existing Vertex/Gemini dispatch (R3): `classifier.make_client(settings)` once, then
`llm.call(client, settings["llm"], system, user, schema=…, text=…, images=[{mime,data}])` — no new SDK
client.

**Sequence & dependencies**

```text
WP0 loose ends (gitignore)         ── independent, do first (cheap, unblocks a clean tree)
WP1 M3 UI (Caixa de Capturas page) ── needs M3-API (done)        ─┐
WP2 M3 adversarial review + fixes  ── needs WP1                   ─┴─► loop is clickable → OWNER validation session (M4)
WP3 Increment 1 — audio + resolve  ── needs LLM client wiring + workspace.db v6
WP4 Increment 2 — inference+extract ── needs WP3 wiring + workspace.db v7
WP5 ADR graduation (Proposed→Accepted) ── after the milestones it documents have shipped
```

> **Gate before WP3/WP4:** the OWNER's live validation session (M4) is the cheap test of H1–H3. The
> increments are worth building once the loop is proven (and the owner has already chosen R3 = Vertex,
> so they proceed). WP3/WP4 may run in parallel with the session since they touch different code.

**workspace.db schema roadmap** (one guarded `ALTER` per increment, the house pattern — add a column
only when its feature needs it, never speculatively):

| Version | Adds | Owner WP |
| --- | --- | --- |
| v5 (shipped) | `captures`, `capture_users` | M1 |
| **v6** | `captures.transcript` | WP3 |
| **v7** | `captures.extracted_fields_json`, `captures.confidence` | WP4 |

---

## WP0 — Loose ends: ignore generated/sole-copy artifacts

**Goal.** Keep the precious sole-copy media and the regenerable cursors out of git.

**Tasks.**
- `.gitignore`: add `captures/` (precious media — never committed, lives in the backup set instead),
  `out/intake_offset.json`, and the WAL sidecars `*.db-wal` / `*.db-shm` (created by M1's WAL).
- Verify `config/settings.json` (with the real token-env name + allowlist) is already gitignored;
  `settings.example.json` (committed) documents the shape.

**DoD.** `git status` is clean after a worker run writes `captures/` + `intake_offset.json` + WAL
sidecars. No precious media or cursor is ever staged.

**Risk.** Trivial. (Mind: `captures/` being gitignored makes the backup set the ONLY safety net — that
discipline is already pinned in [data-stores.md](../05-reference/data-stores.md).)

---

## WP1 — M3 UI: the Caixa de Capturas page

**Goal.** Make the validation loop clickable: a queue page where the user applies/discards each pending
capture. The API (WP done) is the contract; this is the house-style glue.

**Tasks.**
- **`src/email2data/captures_page.py`** (new, mirrors `fila_page.py`): a `build_html(captures, projects,
  nav_counts)` calling `cockpit_ui.page("Capturas", "capturas", _BODY, embeds={"captures": …,
  "projects": …, "labels": …}, lens_js=_LENS_JS, nav_counts=…)`. The lens JS MUST define
  `render()`, `paletteItems(q)`, `onKey(e)` (the `cockpit_ui` contract). Each row renders: the text
  (or "📷 foto"), a thumbnail `<img src="/api/captures/<cid>/media/0">` when `media_paths`, a project
  `<select>` (active projects, pre-selected to `inferred_project_id`), a kind selector
  (note/decision/opinion/todo), and **Aplicar** / **Descartar** buttons. Use the shared `post()` +
  `toast()` helpers; **`esc()` every capture text/title** (untrusted — see WP2 XSS).
- **`cockpit_ui.py`**: add a `"capturas"` entry to `_NAV` (label "Capturas") so the nav strip + count
  badge render.
- **`webapp.py`**: `GET /capturas` → `HTMLResponse(captures_page.build_html(cstore.list_pending(),
  <active projects>, nav_counts=_nav_counts()))`; add the pending-capture count into `_nav_counts()`;
  import `captures_page`.
- **`projetos_page.py`**: extend `timelineHTML(rows)` — when `r.op==='event'` and `r.source_mid`
  starts with `capture:`, append a thumbnail `<img src="/api/captures/<cid>/media/0">`
  (`cid = source_mid.slice(8)`); add one small thumbnail CSS class. This is "the photo in the project
  timeline" deliverable.

**DoD.** `GET /capturas` returns 200 and the rendered HTML contains the embeds + the three contract JS
functions + the pt-PT labels (a render smoke test, mirroring `test_faceted_filter_panel_wired`); the nav
badge shows the pending count. ruff clean.

**Risk.** UI behaviour is validated manually in the M4 session; the smoke test only catches template
breaks. The substantive risk is XSS (WP2).

---

## WP2 — M3 adversarial review + fixes

**Goal.** Same multi-lens review that paid off in M1/M2, over the full M3 (API + UI). Reproduce every
medium+ finding; fix what's confirmed.

**Review lenses.**
- **Security/XSS:** capture `raw_text` and project titles are untrusted, now rendered into page HTML —
  every interpolation must be `esc()`'d. Re-confirm the media path-traversal guard
  (`root not in full.parents` → 404) against `../`, absolute paths, and symlinks.
- **Correctness:** the apply ordering (`add_event` → `set_project` → `mark_applied`); the `capture:<cid>`
  source_mid convention (text-only vs photo); double-apply / apply-after-discard idempotency (the
  lifecycle guards from M1).
- **Contract/UX:** matches the plan; no silent cap on the queue length; pt-PT strings; the nav count
  stays in sync after apply/discard.

**DoD.** Confirmed findings fixed with regression tests; full suite green; ruff clean. **Commit M3
(UI + review fixes).**

---

## WP3 — Increment 1: audio transcription (Vertex) + deterministic resolve

**Goal.** Accept the staffer's voice memos and turn them into text via the existing Vertex path, and
pre-filter the project pick-list deterministically (R2 seed).

**Tasks.**
- **Schema v5→v6:** add `captures.transcript TEXT` (a guarded `_add_column` in an `if version < 6:`
  block in `workspace.py._migrate`; bump `SCHEMA_VERSION`; the v6 comment). Pin with a populated-DB
  round-trip test (the `_V5_PARTIAL` pattern). `CaptureStore`: a `set_transcript(cid, text)` writer +
  `transcript` in `_row`.
- **Worker (`intake.py`):** handle `message["voice"]`/`["audio"]`. Order (persist-then-scrub, never
  break it): download audio → **persist the capture** (audio file in `captures_dir`, `transcript=NULL`,
  `content_class="conversation"`) → **scrub Telegram** → **transcribe best-effort** →
  `set_transcript`. A transcription failure leaves the capture intact (audio preserved, transcript
  empty) and surfaced for manual handling — the capture is PRECIOUS; inference is best-effort.
- **Transcription call:** `llm.call(client, cfg, system="Transcreve o áudio em pt-PT; devolve só o
  texto.", user="", text=True, images=[{"mime": "audio/ogg", "data": ogg_bytes}])`, wrapped in
  `try/except llm.LLMError` (and bare `Exception`). The worker gets the client from
  `classifier.make_client(settings)` (lazy; only if LLM configured) — wire it through `cmd_intake_bot`
  into `IntakeBot`. The bot degrades to "stored, not transcribed" when no client / on failure.
- **Deterministic resolve (`capture_resolve.py` or in `intake.py`):** rank active projects against the
  text/transcript by client name/email + gazetteer alias + a new editable **`config/capture_playbook.md`**
  (seeded from `jobspec` pt-PT field questions, `labels.py`, the gazetteer, and the
  `/api/reclassifications` corrected pairs — R2). Use it to pre-select / reorder `_offer_projects` and
  the webapp `<select>`. Deterministic only in this WP; the LLM is WP4.

**DoD.** A voice update → audio persisted + scrubbed + transcript set (LLM mocked); a degradation test
(LLM raises → capture persists, transcript empty, never scrubbed-before-persist); the v6 migration
round-trip test; the resolver ranks the right project first. Adversarial review (the egress is now
LIVE — confirm no raw audio is logged, N4; confirm the content-class/owner-egress path matches the
N5-narrowing ADR). **Commit.**

**Risk.** Egress is now real (audio → Vertex). Bind it to the R5 safety practices (EU region, no raw
content in logs, scrub-after-store). Hardware/latency: Vertex is cloud, so no local-compute concern
(R3 chose cloud), but a slow call must not block the worker loop — transcription runs after the scrub,
per-update, and a failure just defers to manual.

---

## WP4 — Increment 2: project inference + field extraction (Vertex), field-by-field validation

**Goal.** When the deterministic resolver is ambiguous, infer the project with the LLM; and extract
job-spec field VALUES the user validates field-by-field (the highest-stakes path — never auto-apply, R9).

**Tasks.**
- **Schema v6→v7:** add `captures.extracted_fields_json TEXT` + `captures.confidence REAL` (guarded
  ALTER; v7). `CaptureStore`: writer + `_row` decode (always-a-list/empty discipline like `media_paths`).
- **Schemas (`schema.py`):** a `GEMINI_INFER_SCHEMA` + `INFER_TOOL` (ranked project candidates +
  confidence, with an explicit "none of these") and a `GEMINI_CAPTURE_FIELDS_SCHEMA` +
  `CAPTURE_FIELDS_TOOL` for the 14 `jobspec.FIELDS` — mirror `specdraft.py`'s `GEMINI_SPEC_SCHEMA`
  exactly (OpenAPI subset, `nullable: True`).
- **Inference + extraction functions:** `infer_project(text, active, client, cfg)` → ranked candidates
  (compute ∝ uncertainty — call the LLM **only** when the deterministic resolver is ambiguous);
  `extract_fields(text, client, cfg)` → `{addr: value}` + confidence, post-processed by a defensive
  `_coerce`-style clamp (copy `classifier._coerce` — never trust the model). Both wrapped in
  `try/except llm.LLMError` → degrade to deterministic/empty (the capture survives).
- **Resolution branches (the brief's High/Partial/Low):** wire inference confidence into `_offer_projects`
  — High (≥0.75) pre-selects, Partial shows 2–3, Low → manual. Still **always-confirm** (R9).
- **Field-by-field validation UI (`captures_page.py` + `webapp.py`):** the Caixa de Capturas row shows
  the extracted fields as **editable, individually-confirmable** entries; confirming one POSTs
  `/api/projects/{pid}/field` (the existing endpoint) with provenance from the capture. **No bulk
  auto-apply** — each gate-affecting field is a deliberate human action.

**DoD.** Inference ranks the right project (mocked); extraction returns coerced field values (mocked);
the field-validation flow writes only confirmed fields via `/field`; degradation tests (LLM down →
capture persists, no fields applied); the v7 migration test. Adversarial review focused on the
**estimable-gate impact** (a wrong extracted value must never auto-apply) + hallucination posture
(FACT/INFERENCE/UNKNOWN). **Commit.**

**Risk.** Highest-stakes WP — extracted values feed the estimable gate. The mitigation is structural:
extraction only ever *suggests*; the human confirms each field. Pin that no path writes a field without
explicit confirmation.

---

## WP5 — Graduate ADR-019/-020/-021 (Proposed → Accepted)

**Goal.** Once the milestones they govern have shipped, promote the ADRs and fill their Trace.

**Tasks.** For each ADR: `Status: Proposed → Accepted`; replace the "(pending implementation)" Trace
with the real files/tests (e.g. ADR-019 → `intake.py`/`captures.py`/`captures_page.py`; ADR-020 → the
persist-then-scrub worker + `data-stores.md` WAL note; ADR-021 → `connect(migrate=False)` + the LAN/auth
posture). Drop the `*(Proposed)*` markers in the [registry](../03-decisions/index.md). Record the
**N5-narrowing** (cloud capture egress, signed R5) and the **N6-relaxation** decisions as their own
accepted ADRs if not yet written. (ADRs are immutable once Accepted — so graduate only after the
behaviour is real and pinned.)

**DoD.** Registry + ADR statuses consistent with shipped code; docs lint clean. **Commit.**

---

## M4 — The live validation session (OWNER action — the one piece not buildable here)

Not a WP — it needs a real bot + a phone. After WP1/WP2 (the loop is clickable):

1. BotFather → token → `.env` (`TELEGRAM_BOT_TOKEN`); set `intake.enabled=true` + the `allowlist` in
   `config/settings.json`.
2. `email2data serve` (once, to migrate) then `email2data intake-bot` (the worker refuses to migrate —
   WP done).
3. From the phone: send a text note + a photo (and, after WP3, a voice memo); pick the project.
4. Verify in **Caixa de Capturas**: the capture appears, **Telegram is scrubbed**, on **Aplicar** the
   note + photo land in the project timeline with provenance. **Time it** vs the desk path (H1); confirm
   nothing piled up (H3) and scrub+preserve held (H2).

---

*Build order: WP0 → WP1 → WP2 (→ owner M4 session) → WP3 → WP4 → WP5. Each WP is one commit, gated by a
fail-before/pass-after test + an adversarial review, on `feat/conversational-intake`.*
