"""M2 — the sync Telegram Bot API client. Pins token validation, the ok/error/429 unwrap, the
inline-keyboard payload, and the bounded download — all without real network (urlopen is faked)."""

from __future__ import annotations

import pytest

from email2data import telegram as tg
from email2data.telegram import TelegramClient, TelegramError, TelegramRateLimit


class _FakeResp:
    def __init__(self, body: bytes = b"", status: int = 200, headers: dict | None = None) -> None:
        self._body = body
        self.status = status
        self.headers = headers or {}

    def read(self, n: int = -1) -> bytes:
        return self._body if n is None or n < 0 else self._body[:n]

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def test_malformed_token_is_rejected():
    with pytest.raises(ValueError):
        TelegramClient("not-a-token")
    TelegramClient("123456:AAbb_cc-dd")  # well-formed -> no raise


def test_unwrap_ok_returns_result():
    assert TelegramClient._unwrap("m", 200, {"ok": True, "result": [1, 2]}) == [1, 2]


def test_unwrap_error_raises_telegramerror():
    with pytest.raises(TelegramError):
        TelegramClient._unwrap("m", 400, {"ok": False, "error_code": 400, "description": "bad"})


def test_unwrap_429_raises_ratelimit_with_retry_after():
    with pytest.raises(TelegramRateLimit) as ei:
        TelegramClient._unwrap("m", 429, {"description": "slow", "parameters": {"retry_after": 9}})
    assert ei.value.retry_after == 9


def test_get_updates_none_result_is_empty(monkeypatch):
    c = TelegramClient("123:ABC")
    monkeypatch.setattr(c, "_call", lambda method, payload: None)
    assert c.get_updates(0) == []


def test_send_message_serializes_inline_keyboard(monkeypatch):
    c = TelegramClient("123:ABC")
    seen: dict = {}
    monkeypatch.setattr(
        c, "_call",
        lambda method, payload: seen.update(method=method, payload=payload) or {"message_id": 7})
    mid = c.send_message(
        42, "hi", reply_markup={"inline_keyboard": [[{"text": "a", "callback_data": "d"}]]})
    assert mid == 7
    assert seen["method"] == "sendMessage"
    assert seen["payload"]["chat_id"] == 42
    assert seen["payload"]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "d"


def test_download_file_rejects_oversize_by_content_length(monkeypatch):
    c = TelegramClient("123:ABC")
    resp = _FakeResp(b"x" * 10, headers={"content-length": "999999"})
    monkeypatch.setattr(tg.urllib.request, "urlopen", lambda req, timeout=None: resp)
    with pytest.raises(ValueError):
        c.download_file("path", max_bytes=100)


def test_download_file_rejects_oversize_mid_stream(monkeypatch):
    c = TelegramClient("123:ABC")
    resp = _FakeResp(b"x" * 200, headers={"content-length": "0"})  # lies about its size
    monkeypatch.setattr(tg.urllib.request, "urlopen", lambda req, timeout=None: resp)
    with pytest.raises(ValueError):
        c.download_file("path", max_bytes=100)


def test_download_file_ok(monkeypatch):
    c = TelegramClient("123:ABC")
    resp = _FakeResp(b"img", headers={"content-length": "3"})
    monkeypatch.setattr(tg.urllib.request, "urlopen", lambda req, timeout=None: resp)
    assert c.download_file("path", max_bytes=100) == b"img"
