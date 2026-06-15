"""Vertex context-cache reuse for the large stable playbook prefix.

The triage playbook is re-sent as ``system_instruction`` on every classification; caching it once and
reusing it across a sync's batch bills the prefix at the discounted cache rate. Caching is best-effort
— it must NEVER break a call (no caches API, prefix too small, expired cache → plain path).
"""

import pytest

pytest.importorskip("google.genai")  # the Gemini path imports google.genai types

import email2data.llm as llm  # noqa: E402


class _Resp:
    text = "DRAFT"


class FakeCaches:
    def __init__(self):
        self.created = []

    def create(self, *, model, config):
        self.created.append((model, config))
        name = f"cachedContents/{len(self.created)}"
        return type("C", (), {"name": name})()


class FakeModels:
    def __init__(self):
        self.calls = []           # the GenerateContentConfig objects passed

    def generate_content(self, *, model, contents, config):
        self.calls.append(config)
        return _Resp()


class FakeClient:
    def __init__(self):
        self.caches = FakeCaches()
        self.models = FakeModels()


def _cfg(**over):
    base = {"provider": "vertex_gemini", "model": "gemini-2.5-flash", "max_retries": 1,
            "context_cache_min_chars": 10}
    base.update(over)
    return base


def test_large_prefix_cached_once_and_reused():
    llm._GEMINI_CACHE.clear()
    client = FakeClient()
    system = "PLAYBOOK " * 50                       # well over the (test) 10-char floor
    for _ in range(3):
        assert llm.call(client, _cfg(), system, "user", text=True) == "DRAFT"
    assert len(client.caches.created) == 1                                  # created ONCE, then reused
    assert all(getattr(c, "cached_content", None) for c in client.models.calls)        # every call hit it
    assert all(getattr(c, "system_instruction", None) is None for c in client.models.calls)


def test_small_prefix_is_not_cached():
    llm._GEMINI_CACHE.clear()
    client = FakeClient()
    llm.call(client, _cfg(context_cache_min_chars=100_000), "short", "u", text=True)
    assert client.caches.created == []
    assert getattr(client.models.calls[0], "system_instruction", None) == "short"


def test_context_cache_can_be_disabled():
    llm._GEMINI_CACHE.clear()
    client = FakeClient()
    llm.call(client, _cfg(context_cache=False), "x" * 50, "u", text=True)
    assert client.caches.created == []
    assert getattr(client.models.calls[0], "system_instruction", None) == "x" * 50


def test_cache_create_failure_falls_back_to_plain_path():
    llm._GEMINI_CACHE.clear()
    client = FakeClient()

    def boom(*a, **k):
        raise RuntimeError("no caches API on this endpoint")
    client.caches.create = boom

    out = llm.call(client, _cfg(), "x" * 50, "u", text=True)
    assert out == "DRAFT"                                                   # the call still succeeded
    assert getattr(client.models.calls[0], "system_instruction", None) == "x" * 50


def test_expired_cache_is_evicted_and_retried_uncached():
    llm._GEMINI_CACHE.clear()
    client = FakeClient()
    calls = []

    def gen(*, model, contents, config):
        calls.append(config)
        if getattr(config, "cached_content", None):
            raise RuntimeError("CachedContent not found")                   # simulate a TTL expiry
        return _Resp()
    client.models.generate_content = gen

    system = "x" * 50
    out = llm.call(client, _cfg(max_retries=3), system, "u", text=True)
    assert out == "DRAFT"                                                   # recovered on the plain path
    assert getattr(calls[0], "cached_content", None)                       # first attempt used the cache
    assert getattr(calls[-1], "system_instruction", None) == system        # retry dropped it
    assert llm._gemini_cache_key(_cfg(), system) not in llm._GEMINI_CACHE  # and evicted the dead entry
