"""Conversational-intake Telegram worker (ADR-019/-020/-021).

Outbound-only long-poll worker: it polls api.telegram.org, persists each capture via CaptureStore (the
store seam, never the HTTP API — so 8042 stays closed, N6/ADR-021), and only AFTER a durable commit
deletes the source from Telegram (ADR-020 §2 persist-then-scrub). A capture is never dropped (N2): an
unmatched one stays pending in the queue for the user to resolve in the webapp — nothing is applied
automatically (ADR-019 §5 / R9). The project pick-list uses Telegram-native inline buttons, so no
navigable link leaves the chat (R6).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from . import capture_resolve as _resolve, llm, project as _project
from .captures import CONTENT_ARTIFACT, CONTENT_CONVERSATION, CaptureStore
from .project import ProjectStore
from .telegram import TelegramClient, TelegramError, TelegramRateLimit

logger = logging.getLogger(__name__)


class TransientPersistError(Exception):
    """A capture's durable persist failed transiently (e.g. SQLite 'database is locked'). The worker
    must NOT advance the offset past this update and must NOT scrub Telegram — re-polling re-delivers it
    and the idempotent add() retries to a durable commit (ADR-020 persist-then-scrub: keep -> retry)."""


_LONG_POLL = 25
_MAX_BACKOFF = 60
_MAX_PICK = 8  # inline buttons shown; beyond this we say so (no silent cap)

# The transcription system prompt (Increment 1). The audio rides as a multimodal part; the system
# carries the only instruction. pt-PT, text-only out (the model returns just the transcript).
_TRANSCRIBE_SYSTEM = "Transcreve o áudio em pt-PT; devolve só o texto."

# pt-PT user-facing strings (project convention; code/comments stay English).
_HELP = ("Envia uma nota, uma foto ou uma mensagem de voz e eu guardo-a para associares a uma obra. "
         "Confirmas sempre na app — nada é aplicado automaticamente.")
_F1_UNAUTHORIZED = "Não estás autorizado a usar este canal."
_T1_ACK = "📥 Recebido. A guardar…"
_E_EMPTY = "Envia texto ou uma foto para eu guardar."
_E_DOWNLOAD = "Não consegui descarregar a imagem. Tenta enviar outra vez."
_E_PERSIST = "Erro temporário a guardar. Reenvia, por favor."
_T2_PICK = "A que projeto pertence?"
_T2_NO_PROJECTS = ("Guardado. Não há projetos ativos — abre a Caixa de Capturas na app "
                   "para o associares.")
_T2_TRUNCATED = "\n(+{extra} outros — usa a Caixa de Capturas na app se não estiver na lista.)"
_T2_PICKED = "✅ Associado a {title}. Valida na Caixa de Capturas."
_T2_SKIPPED = "Deixei na Caixa de Capturas para validares na app."


def _admin_new_user(name: str, uid: int, username: str | None) -> str:
    tail = f" (@{username})" if username else ""
    return f"Novo remetente não autorizado: {name}{tail} — id {uid}."


class IntakeBot:
    """Per-update handler: allowlist -> T1 ack -> persist -> scrub -> pick-list, plus the callback that
    records the user's project pick. Telegram I/O goes through the injected client; persistence through
    CaptureStore/ProjectStore — all three are easy to fake in tests."""

    def __init__(self, *, client: TelegramClient, captures: CaptureStore, projects: ProjectStore,
                 captures_dir: str | Path, admin_chat_id: int | None = None,
                 delete_after_scrub: bool = True, llm_client: Any = None,
                 llm_cfg: dict[str, Any] | None = None,
                 resolve_aliases: dict[str, str] | None = None,
                 resolve_gazetteer: dict[str, str] | None = None) -> None:
        self._client = client
        self._captures = captures
        self._projects = projects
        self._captures_dir = Path(captures_dir)
        self._admin_chat_id = admin_chat_id
        self._delete = delete_after_scrub
        # Increment 1: transcription via the shared Vertex/Gemini dispatch (R3). Lazy + optional — when
        # no client is wired (LLM unconfigured) the bot degrades to "stored, not transcribed".
        self._llm_client = llm_client
        self._llm_cfg = llm_cfg or {}
        # Deterministic resolve (R2 seed): alias table (capture_playbook) + gazetteer, loaded once.
        self._aliases = resolve_aliases or {}
        self._gazetteer = resolve_gazetteer or {}

    def handle(self, update: dict[str, Any]) -> None:
        """Dispatch one update. Swallows ordinary errors so one bad update can't break the poll loop,
        but RE-RAISES TransientPersistError so the loop holds the offset and retries the persist rather
        than advancing past an un-stored capture."""
        try:
            if "callback_query" in update:
                self._handle_callback(update["callback_query"])
            elif "message" in update:
                self._handle_message(update["message"])
        except TransientPersistError:
            raise
        except Exception:
            logger.exception("intake_handle_failed", extra={"update_id": update.get("update_id")})

    # -- message -> capture -----------------------------------------------------------------------

    def _handle_message(self, message: dict[str, Any]) -> None:
        chat = message.get("chat") or {}
        if chat.get("type") != "private":
            return  # single-user: private chats only
        chat_id = chat.get("id")
        from_user = message.get("from") or {}
        sender_id = from_user.get("id")
        message_id = message.get("message_id")
        if chat_id is None or sender_id is None or message_id is None:
            return

        text = (message.get("text") or "").strip()
        if text in ("/start", "/ajuda", "/help"):
            self._client.send_message(chat_id, _HELP)
            return

        if not self._captures.is_allowed(sender_id):
            self._reject(chat_id, sender_id, from_user)
            return

        photos = message.get("photo") or []
        voice = message.get("voice") or message.get("audio")   # a voice memo or an audio file
        body_text = text or (message.get("caption") or "").strip()
        if not body_text and not photos and not voice:
            self._client.send_message(chat_id, _E_EMPTY)
            return

        t1_id = self._client.send_message(chat_id, _T1_ACK)

        media_paths: list[str] = []
        content_class = CONTENT_CONVERSATION
        audio_rel: str | None = None
        if photos:
            content_class = CONTENT_ARTIFACT
            try:
                media_paths.append(self._download_photo(photos, chat_id, message_id))
            except Exception:
                logger.exception("intake_download_failed", extra={"chat_id": chat_id})
                self._client.edit_message_text(chat_id, t1_id, _E_DOWNLOAD)
                return  # before persist -> nothing to scrub; Telegram keeps the message
        elif voice:
            # A voice memo / audio file: the staffer's own words (ADR-019 §3) — content_class stays
            # CONVERSATION. Download it (it is PRECIOUS: the sole copy once Telegram is scrubbed).
            content_class = CONTENT_CONVERSATION
            try:
                audio_rel = self._download_audio(voice, chat_id, message_id)
                media_paths.append(audio_rel)
            except Exception:
                logger.exception("intake_download_failed", extra={"chat_id": chat_id})
                self._client.edit_message_text(chat_id, t1_id, _E_DOWNLOAD)
                return  # before persist -> nothing to scrub; Telegram keeps the message

        user = self._captures.get_user(sender_id) or {}
        asserted_by = user.get("roster_owner") or user.get("display_name") or ""

        # PERSIST (durably committed inside add(), transcript=NULL). Only AFTER this may we scrub.
        try:
            cid, _created = self._captures.add(
                telegram_message_id=message_id, telegram_chat_id=chat_id,
                content_class=content_class, raw_text=body_text, media_paths=media_paths,
                channel="manual", asserted_by=asserted_by)
        except sqlite3.OperationalError as exc:
            logger.warning("intake_persist_locked", extra={"chat_id": chat_id})
            self._client.edit_message_text(chat_id, t1_id, _E_PERSIST)
            # persist FAILED -> never scrub (Telegram keeps the sole copy) AND hold the offset: signal
            # the loop to re-deliver + retry this update rather than drop it (N2 never-dropped).
            raise TransientPersistError("capture persist failed (database locked)") from exc

        self._scrub(chat_id, message_id, cid)
        # Transcribe AFTER the scrub (the persist-then-scrub order is never broken): best-effort, so a
        # slow/failed transcription leaves the capture intact (audio preserved, transcript empty) and
        # surfaced for manual handling — the capture is PRECIOUS; inference is not (ADR-020).
        if audio_rel:
            self._transcribe(cid, audio_rel, voice or {})
        self._offer_projects(chat_id, t1_id, cid)

    def _reject(self, chat_id: int, sender_id: int, from_user: dict[str, Any]) -> None:
        self._client.send_message(chat_id, _F1_UNAUTHORIZED)
        logger.info("intake_unauthorized", extra={"telegram_user_id": sender_id})
        if self._admin_chat_id is None:
            return
        try:
            name = " ".join(filter(None, [from_user.get("first_name"),
                                          from_user.get("last_name")])) or str(sender_id)
            self._client.send_message(
                self._admin_chat_id, _admin_new_user(name, sender_id, from_user.get("username")),
                parse_mode=None)  # name/username are untrusted — never parse them as Markdown
        except Exception:
            logger.warning("intake_admin_notify_failed", extra={"telegram_user_id": sender_id})

    def _download_photo(self, photos: list[dict[str, Any]], chat_id: int, message_id: int) -> str:
        best = photos[-1]  # Telegram lists photo sizes ascending; the last is the largest
        meta = self._client.get_file(best["file_id"])
        data = self._client.download_file(meta["file_path"])
        rel = f"c-{chat_id}-{message_id}/photo.jpg"
        dest = self._captures_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return rel

    def _download_audio(self, audio: dict[str, Any], chat_id: int, message_id: int) -> str:
        """Download a voice memo / audio file to the precious captures dir. The extension follows the
        Telegram file path (voice is .oga/.ogg-Opus), defaulting to .ogg so the transcription mime is sane."""
        meta = self._client.get_file(audio["file_id"])
        data = self._client.download_file(meta["file_path"])
        ext = Path(meta.get("file_path", "")).suffix.lstrip(".") or "ogg"
        rel = f"c-{chat_id}-{message_id}/voice.{ext}"
        dest = self._captures_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return rel

    def _transcribe(self, cid: str, audio_rel: str, audio: dict[str, Any]) -> None:
        """Best-effort pt-PT transcription via the shared Vertex/Gemini dispatch (R3), stored on the
        capture. Degrades silently when no LLM is wired or the call fails — the capture survives either
        way (ADR-020 preserve-at-core). The raw audio is NEVER logged (N4 / CLAUDE.md secrets rule)."""
        if self._llm_client is None or not self._llm_cfg:
            return  # no LLM configured -> "stored, not transcribed"
        try:
            data = (self._captures_dir / audio_rel).read_bytes()
            mime = audio.get("mime_type") or "audio/ogg"
            text = llm.call(self._llm_client, self._llm_cfg, system=_TRANSCRIBE_SYSTEM, user="",
                            text=True, images=[{"mime": mime, "data": data}])
            if isinstance(text, str) and text.strip():
                self._captures.set_transcript(cid, text.strip())
        except llm.LLMError:
            logger.info("intake_transcribe_failed", extra={"capture_id": cid})  # never log raw audio
        except Exception:
            logger.info("intake_transcribe_error", extra={"capture_id": cid})

    def _scrub(self, chat_id: int, message_id: int, cid: str) -> None:
        """Delete the source from Telegram — strictly AFTER the durable persist above (ADR-020 §2). A
        failed delete is non-fatal: the capture is safe; the message just lingers in Telegram."""
        if not self._delete:
            return
        try:
            self._client.delete_message(chat_id, message_id)
            self._captures.mark_scrubbed(cid)
        except Exception:
            logger.info("intake_scrub_failed", extra={"chat_id": chat_id, "capture_id": cid})

    def _offer_projects(self, chat_id: int, t1_id: int, cid: str) -> None:
        active = [p for p in self._projects.list() if p["stage"] not in _project.TERMINAL_STAGES]
        # Deterministic resolve (R2 seed): rank the active projects by how strongly the capture's
        # text/transcript names them, so the likeliest obra is the FIRST button. No auto-apply — these
        # are still buttons the staffer taps (ADR-019 §5). A no-signal capture keeps the default order.
        cap = self._captures.get(cid) or {}
        hay = " ".join(filter(None, [cap.get("raw_text"), cap.get("transcript")]))
        if hay.strip():
            active = _resolve.rank_projects(hay, active, aliases=self._aliases,
                                            gazetteer=self._gazetteer)
        shown = active[:_MAX_PICK]
        rows: list[list[dict[str, str]]] = [
            [{"text": f"{p['title']} ({p['project_id']})",
              "callback_data": f"pick:{cid}:{p['project_id']}"}] for p in shown]
        if not rows:
            self._client.edit_message_text(chat_id, t1_id, _T2_NO_PROJECTS)
            return
        rows.append([{"text": "▫️ Outro (resolver na app)", "callback_data": f"skip:{cid}"}])
        text = _T2_PICK
        if len(active) > len(shown):
            text += _T2_TRUNCATED.format(extra=len(active) - len(shown))
        self._client.edit_message_text(chat_id, t1_id, text, reply_markup={"inline_keyboard": rows})

    # -- callback -> project pick -----------------------------------------------------------------

    def _handle_callback(self, cb: dict[str, Any]) -> None:
        cb_id = cb.get("id")
        if cb_id:
            try:
                self._client.answer_callback_query(cb_id)
            except Exception:
                logger.info("intake_answer_cb_failed")
        sender_id = (cb.get("from") or {}).get("id")
        if sender_id is None or not self._captures.is_allowed(sender_id):
            return  # only allowlisted users drive resolution
        data = cb.get("data") or ""
        msg = cb.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        msg_id = msg.get("message_id")
        if chat_id is None or msg_id is None:
            return
        if data.startswith("pick:"):
            _, cid, pid = data.split(":", 2)
            self._captures.set_project(cid, pid)
            proj = self._projects.get(pid)
            title = proj["title"] if proj else pid
            # plain text (parse_mode=None): a project title with Markdown specials (* _ ` [) must not
            # break the confirmation edit AFTER set_project has already committed.
            self._client.edit_message_text(
                chat_id, msg_id, _T2_PICKED.format(title=title), parse_mode=None)
        elif data.startswith("skip:"):
            self._client.edit_message_text(chat_id, msg_id, _T2_SKIPPED)


# -- offset (a regenerable cursor — a file, never the precious DB) ---------------------------------

def _read_offset(path: Path, bot_name: str) -> int | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    val = data.get(bot_name) if isinstance(data, dict) else None
    return int(val) if isinstance(val, int) else None


def _write_offset(path: Path, bot_name: str, offset: int) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data[bot_name] = offset
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)  # atomic replace


def _initial_offset(client: TelegramClient, path: Path, bot_name: str) -> int:
    """Resume from the persisted offset, else SKIP the backlog: start from Telegram's current tail so a
    fresh bot never replays old messages (materials-costing pattern). The chosen offset is persisted
    immediately so the skip survives a restart before the first real poll."""
    persisted = _read_offset(path, bot_name)
    if persisted is not None:
        return persisted
    try:
        tail = client.get_updates(offset=-1, timeout=0)
        offset = int(tail[-1]["update_id"]) + 1 if tail else 0
    except Exception:
        offset = 0
    _write_offset(path, bot_name, offset)
    return offset


def poll_forever(*, client: TelegramClient, bot: IntakeBot, bot_name: str, offset_path: Path,
                 shutdown: Any = None) -> None:
    """Outbound long-poll loop. Crash-proof: honor rate-limit ``retry_after`` literally, back off
    exponentially on API/transport errors, and a bare-except catch-all keeps the loop alive on any
    unexpected error. The offset advances per-update only AFTER the handler returns, so a crash
    mid-batch replays only the unhandled tail (idempotent, ADR-020). A TransientPersistError holds the
    offset on the failed update — it is re-delivered and retried until the capture durably commits, so
    a locked DB never drops a capture."""
    backoff = 0
    offset = _initial_offset(client, offset_path, bot_name)
    logger.info("intake_started", extra={"bot_name": bot_name, "offset": offset})
    while shutdown is None or not shutdown.is_set():
        try:
            updates = client.get_updates(
                offset, timeout=_LONG_POLL, allowed_updates=["message", "callback_query"])
            backoff = 0
            for upd in updates:
                if shutdown is not None and shutdown.is_set():
                    break
                bot.handle(upd)
                offset = int(upd["update_id"]) + 1
                _write_offset(offset_path, bot_name, offset)
        except TransientPersistError:
            # The capture for the current update did not commit; do NOT advance the offset — re-poll
            # re-delivers it and the idempotent add() retries (persist-then-scrub: keep -> retry).
            backoff = min(_MAX_BACKOFF, max(1, backoff * 2 or 1))
            logger.warning("intake_persist_retry", extra={"backoff": backoff})
            time.sleep(backoff)
        except TelegramRateLimit as exc:
            logger.warning("intake_rate_limited", extra={"retry_after": exc.retry_after})
            time.sleep(exc.retry_after)
        except (TelegramError, OSError) as exc:
            backoff = min(_MAX_BACKOFF, max(1, backoff * 2 or 1))
            logger.warning("intake_poll_error", extra={"backoff": backoff, "error": str(exc)})
            time.sleep(backoff)
        except Exception:
            backoff = min(_MAX_BACKOFF, max(1, backoff * 2 or 1))
            logger.exception("intake_poll_unexpected", extra={"backoff": backoff})
            time.sleep(backoff)
    logger.info("intake_stopped", extra={"bot_name": bot_name})
