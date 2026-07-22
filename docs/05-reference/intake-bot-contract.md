# Intake bot — the conversational contract

| Field | Value |
| --- | --- |
| Status | Reference — mirrors `src/email2data/intake.py`. Update in the same commit as a change that invalidates it. |
| Governs | The Telegram surface of the conversational intake ([ADR-019](../03-decisions/adr-019-conversational-intake-capture-adapter.md), [ADR-020](../03-decisions/adr-020-capture-egress-and-data-handling.md)) |
| Pinned by | `tests/test_intake_bot.py` |

The Telegram chat is the **only** surface the staffer sees from the phone, and a capture is
**scrubbed from Telegram once stored** (ADR-020 §2 persist-then-scrub). What the bot says back is
therefore load-bearing: after the scrub the chat is the sole record of what was understood, and the
stored capture is the sole copy of the content. Wording here is a contract, not decoration.

## Commands

| Command | Behaviour |
| --- | --- |
| `/ajuda` (also `/start`, `/help`) | Sends the help text: the accepted kinds + the "confirmas sempre na app" guarantee. |

Registered with Telegram via `setMyCommands` at worker start, so `/ajuda` appears in the client's
`/` autocomplete instead of being hidden knowledge. Registration is **best-effort** — a failure is
logged and polling continues (discoverability is not function).

**The allowlist is checked before the command.** The help text describes the internal workflow, so an
unauthorised sender gets only the rejection — never the help body (default-deny, ADR-019 §6).

## Accepted message kinds

| Telegram field | `content_class` | Stored as | Notes |
| --- | --- | --- | --- |
| `text` | `conversation` | `raw_text` | |
| `voice` / `audio` | `conversation` | audio file + `transcript` | Transcribed best-effort via Vertex (Increment 1). |
| `photo` | `artifact` | `photo.jpg` | Largest size taken. |
| `document` | `artifact` | original filename | PDF/DXF drawings **and any photo sent "as file"** — Telegram classifies those as `document`, not `photo`. |
| `caption` (on any of the above) | — | `raw_text` | |

Anything else (video, sticker, location, contact) and a fully empty message get `_E_EMPTY`, which
names every accepted kind. **`edited_message` is not handled** — editing a sent note is a no-op.

**`document.file_name` is attacker-controlled.** It is reduced to a basename, both separator styles
are neutralised, dot-dot runs are collapsed, and the resolved path is re-checked against the captures
root before writing. Pinned by `test_document_filename_cannot_escape_the_captures_dir`.

## Reply sequence

```text
(any accepted message)
  → 📥 Recebido. A guardar…            (T1 ack; sent BEFORE the download/persist)
  → [download] → [PERSIST] → [SCRUB]   (never reordered — ADR-020 §2)
  → [transcribe if audio] → [extract fields]
  → «🎙 Ouvi: …» + A que projeto pertence?   (T1 edited into the pick-list)
```

### Voice always reports what was heard

Because the audio is gone from Telegram by this point, a voice capture **always** states its
transcription outcome — the staffer's only chance to catch a bad transcription while they can still
re-say it:

| Outcome | Reply prefix |
| --- | --- |
| Transcribed | `🎙 Ouvi: «…»` — clipped to `_HEARD_MAX` (400) chars for a phone screen. The **full** transcript is always stored regardless of the clip. |
| Not transcribed (LLM down / absent) | `🎙 Guardei o áudio mas não consegui transcrevê-lo — confirma na Caixa de Capturas.` |

The reply is sent with `parse_mode=None`: a transcript is model output and project titles are email
subjects — neither is trusted Markdown.

> **Accepted trade-off (2026-07-19).** The echo puts the transcribed text *back* into Telegram, which
> is in tension with ADR-020 §2's intent that the scrub leaves capture content out of Telegram's
> cloud. It was accepted deliberately: without it a staffer cannot tell a good transcription from a
> failed one, and the audio they would need in order to check has already been deleted — a silent bad
> transcription is the worse failure. The echo is clipped, sits in the staffer's own private chat, and
> is deletable there. Revisit if intake ever widens beyond single-user (it would belong in a new ADR,
> since ADR-020 is Accepted and immutable).

### Errors are per-kind

`_E_DOWNLOAD_PHOTO` / `_E_DOWNLOAD_AUDIO` / `_E_DOWNLOAD_DOC` are distinct. A failed voice memo must
not report an "imagem" the staffer never sent. A download failure happens **before** persist, so
nothing is stored and **Telegram is not scrubbed** — the message stays in the chat to retry.

## Pick-list buttons

Label format — `_button_label()`:

```text
{title clipped to 30} · {client domain, else DD/MM} ({project_id})
```

Titles are raw email subjects: long, and frequently duplicated (two `Troféu KIA`, two
`Pedido de orçamento - troféu croissant`). Two identical buttons are un-pickable on a phone, so a
human-meaningful discriminator — the client's domain, falling back to the creation date — is always
appended, with the id kept for cross-referencing the app.

- At most `_MAX_PICK` (8) buttons; beyond that the reply says `(+N outros …)` — **never a silent cap**.
- A `▫️ Outro (resolver na app)` row is always appended — the staffer is never trapped.
- Ordering is deterministic-first (`capture_resolve`), escalating to the model only when ambiguous
  (ADR-001). Ordering is a **hint**: tapping is still required, nothing auto-applies (ADR-019 §5 / R9).

## Known gaps (not defects — unbuilt)

- No `/desfazer`: a mis-tapped project is corrected in the app, not from the chat.
- `edited_message` is ignored.
- The bot never reports which job-spec fields were extracted; they surface only in Caixa de Capturas.
- `admin_chat_id` is `null` by default, so new-sender notifications go nowhere (the id is logged).
