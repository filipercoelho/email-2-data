"""Minimal sync Telegram Bot API client for the conversational-intake worker (ADR-019/-021).

Outbound-only (long-poll ``getUpdates``), stdlib ``urllib`` — no new dependency and no inbound port, so
the loopback/LAN posture (N6/ADR-021) is untouched: the bot reaches api.telegram.org, nothing reaches
in. Ports the materials-costing client's robustness (token validation + masking, 429/``retry_after``
handling, bounded download) and ADDS the inline-keyboard + callback-query support the project pick-list
needs (which that client lacks).
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org"
_TOKEN_PATTERN = re.compile(r"^\d+:[A-Za-z0-9_-]+$")
_MAX_FILE_BYTES_DEFAULT = 20 * 1024 * 1024


class TelegramError(RuntimeError):
    """The Bot API returned ``ok=false`` (or a non-JSON error body)."""

    def __init__(self, method: str, error_code: int, description: str) -> None:
        super().__init__(f"{method}: {error_code} {description}")
        self.method = method
        self.error_code = error_code
        self.description = description


class TelegramRateLimit(TelegramError):
    """HTTP 429; ``retry_after`` is Telegram's suggested delay in seconds."""

    def __init__(self, method: str, description: str, retry_after: int) -> None:
        super().__init__(method, 429, description)
        self.retry_after = retry_after


def _mask_token(token: str) -> str:
    return f"{token[:5]}…{token[-3:]}" if len(token) > 8 else "…"


class TelegramClient:
    """Sync Bot API client over ``urllib``. API failures raise ``TelegramError`` / ``TelegramRateLimit``;
    transport failures raise ``OSError`` (incl. ``urllib.error.URLError`` / socket timeout). The poller
    backs off on both. The bot token is validated on construction and never logged in full."""

    def __init__(self, bot_token: str, *, request_timeout: float = 35.0) -> None:
        if not _TOKEN_PATTERN.match(bot_token or ""):
            raise ValueError("malformed Telegram bot token")
        self._masked = _mask_token(bot_token)
        self._api_url = f"{_API_BASE}/bot{bot_token}"
        self._file_url = f"{_API_BASE}/file/bot{bot_token}"
        self._timeout = request_timeout

    # -- transport --------------------------------------------------------------------------------

    def _call(self, method: str, payload: dict[str, Any]) -> Any:
        """POST a JSON body to one Bot API method (Telegram accepts POST for every method, including
        getUpdates). Returns the unwrapped ``result``; raises on ``ok=false`` / 429."""
        req = urllib.request.Request(
            f"{self._api_url}/{method}", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            return self._unwrap(method, resp.status, body)
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8", "replace"))
            except ValueError:
                body = {}
            return self._unwrap(method, exc.code, body)

    @staticmethod
    def _unwrap(method: str, status: int, body: dict[str, Any]) -> Any:
        if status == 429:
            retry_after = int(body.get("parameters", {}).get("retry_after", 1))
            raise TelegramRateLimit(method, body.get("description", "rate limited"), retry_after)
        if not body.get("ok"):
            raise TelegramError(
                method, int(body.get("error_code", status)),
                str(body.get("description", "unknown error")))
        return body.get("result")

    # -- methods ----------------------------------------------------------------------------------

    def get_updates(self, offset: int, *, timeout: int = 25,
                    allowed_updates: list[str] | None = None) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"offset": offset, "timeout": timeout}
        if allowed_updates is not None:
            payload["allowed_updates"] = allowed_updates
        return self._call("getUpdates", payload) or []

    def get_file(self, file_id: str) -> dict[str, Any]:
        return self._call("getFile", {"file_id": file_id})

    def download_file(self, file_path: str, *, max_bytes: int = _MAX_FILE_BYTES_DEFAULT) -> bytes:
        """Download a file from the ``/file/bot<token>`` host, bounded so a hostile/oversize file can't
        exhaust memory (content-length pre-check + a hard read cap)."""
        req = urllib.request.Request(f"{self._file_url}/{file_path}", method="GET")
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            length = int(resp.headers.get("content-length", "0") or 0)
            if length > max_bytes:
                raise ValueError(f"file too large: {length} > {max_bytes}")
            data = resp.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError(f"file too large mid-stream: > {max_bytes}")
        return data

    def send_message(self, chat_id: int, text: str, *, reply_markup: dict[str, Any] | None = None,
                     parse_mode: str | None = "Markdown") -> int:
        payload: dict[str, Any] = {
            "chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        if parse_mode is not None:  # parse_mode=None -> plain text (no entity parsing / injection)
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return int(self._call("sendMessage", payload)["message_id"])

    def edit_message_text(self, chat_id: int, message_id: int, text: str, *,
                          reply_markup: dict[str, Any] | None = None,
                          parse_mode: str | None = "Markdown") -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id, "message_id": message_id, "text": text,
            "disable_web_page_preview": True}
        if parse_mode is not None:  # parse_mode=None -> plain text (no entity parsing / injection)
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self._call("editMessageText", payload)

    def delete_message(self, chat_id: int, message_id: int) -> None:
        self._call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

    def answer_callback_query(self, callback_query_id: str, *, text: str = "") -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        self._call("answerCallbackQuery", payload)
