# ADR-032 — Composer output language (PT/EN/FR/ES) + translate-received-emails-to-English

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-07-21 |

## Context

Lindo Serviço deals with foreign clients (e.g. the `sourcing.vbo@amadeus.com` PO in the field). Two
language needs surfaced:

1. **Compose in EN/FR/ES, not only Portuguese.** The client-email composer (ADR-013/-027/-031) only
   ever produced pt-PT. A quote/rejection/etc. to an English-, French- or Spanish-speaking client had
   to be hand-translated after copying.
2. **Read incoming mail in English.** A reading aid: turn a received PT/FR/ES email body into English
   on demand, so a non-native reader can triage it.

Both are translation, which is inherently an LLM task — there is no deterministic translator. The
question was how to add them without breaking the invariants: no LLM in the deterministic base
(ADR-013), the polish checked (ADR-027/-031), zero-hallucination on money (ADR-031), and the egress /
never-log rules (ADR-012, ADR-020, VISION §6).

## Decision

### A. Composer output language — the polish translates; the number guard survives it

- New `clientdraft.LANGUAGES` (`pt`/`en`/`fr`/`es`), `DEFAULT_LANGUAGE="pt"`. The deterministic base
  draft is **always Portuguese** — ADR-013 is untouched; the base is the "what to say".
- Choosing a non-PT language makes the existing **Melhorar com IA** pass a *translate + polish* pass:
  `build_polish_message`/`polish_draft` take a `language` and, when it is not PT, prepend an
  `IDIOMA DE SAÍDA` directive ("write the final email in <language>; translate faithfully; keep the
  VALORES A MANTER exactly — never translate or reformat a number"). PT adds nothing, so the PT path
  is byte-identical to before.
- **The money/number/date guard is language-independent, so it still runs verbatim across a
  translation** (`extract_values`/`missing_values`, ADR-031) — this is exactly what protects a
  translated quote from a localized/rounded price. **The verbatim *question* check cannot survive
  translation** (a translated question is a different string), so for a non-PT result the endpoint
  **skips** `missing_questions` and marks the response `translated: true`; the UI shows a
  **"traduzido — rever"** banner and makes no question-coverage claim it did not verify. Honesty over a
  false guarantee.
- `POST …/draft/polish` accepts `lang` (validated ∈ `LANGUAGES`; invalid → 400 before any model call),
  returns `lang` + `translated`, and passes `language` to `polish_draft` only when non-PT (so the PT
  path calls it with its historical argument set). UI: a language `<select id="_ailang">` in the AI bar
  (view-only until the button); the button reads "✨ Traduzir e melhorar" when non-PT.

### B. Translate received emails to English — a display-only reading aid

- New `translate.py` (through `llm.py`, ADR-012): `translate_to_english(text, playbook, client, cfg)` —
  `llm.call(..., text=True, temperature=0.0)` for a faithful translation; raises `LLMError` on empty
  (never echoes the untranslated text as a success). Editable `config/translation_playbook.md` (hard
  rules: translate faithfully; keep names/numbers/prices/dates/URLs/emails exactly; output only the
  translation), re-read per request, with a fallback that carries the same rules.
- `POST /api/translate` — `{message_id, text}` → `{text, cached}`. Button-only (nothing on page load).
  In-memory `_translate_cache` keyed by `(message_id, sha256(text))` — a re-click costs 0 tokens; cold
  on restart (it caches LLM output, not precious state — persisting derived personal data would be a
  new store we deliberately don't create). To-English only, so language is not in the key. Guards:
  empty text → 400; no `settings.json` → 503; `LLMError` → 502; other → 503.
- UI: one **traduzir (EN)** button in the shared `cockpit_ui.msgHTML` renderer covers **Fila, Projetos
  → Origem, and Para ti** at once. A single document-level **capture-phase** delegated handler
  (`translateMsg` in `_SHELL_UTILS`, wired in `_SHELL_EVENTS`, present on every cockpit page) reads the
  message's visible body, POSTs it, and shows the English in a sibling slot; re-clicking toggles
  original ⇄ translation with no second call. Capture-phase + `stopPropagation` so the click never also
  fires the ancestor row/detail handlers.

### Egress & privacy (unchanged posture)

Sending a received body to Vertex to translate it is the **same egress the triage and spec passes
already perform** (`classifier.py`, `specdraft.py`/`specbuild.py` send full raw bodies to the same
same Vertex project through `llm.py`). The hard rules are honoured: route through `llm.py`
(ADR-012), **never *log*** the body or the translation (ids/counts only, per the audit privacy rule),
treat the translation as personal data (shown on screen, never sent, never stored).

## Consequences

- Covered by `tests/test_clientdraft.py` (language registry; the `IDIOMA DE SAÍDA` directive; PT
  byte-identical; `polish_draft` forwards `language`), `tests/test_translate.py` (faithful call at
  temperature 0; raises on empty; playbook fallback), and `tests/test_webapp.py` (non-PT polish marks
  `translated` and still catches an altered number; PT default unchanged + invalid lang → 400;
  `/api/translate` translates, caches at 0 tokens, guards empty/no-settings/`LLMError`; every cockpit
  page ships the button + handler; the composer ships `#_ailang` + the translated banner).
- **A translated composer email is review-required by design.** We enforce the numbers verbatim but not
  the sentences, and say so — a false "questions kept" claim across a translation would be worse than an
  honest "rever".
- **The base stays PT and deterministic.** A user who wants a non-PT email must run the AI pass; there
  is no deterministic non-PT draft (translation is not deterministic). This is stated in the UI hint.
- **No new stored data.** The translate cache is in-memory and cold on restart; nothing is persisted.
- **Extends ADR-013/-027/-031 and reuses ADR-012/-020; supersedes nothing.** If a future decision
  persists translations, adds a non-PT deterministic template set, or lets the polish reformat a
  number, it must supersede this ADR explicitly.
