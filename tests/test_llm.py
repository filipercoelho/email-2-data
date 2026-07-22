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


@pytest.fixture(autouse=True)
def _clear_gemini_cache():
    """Isolate the module-global cache registry so a test that crashes can't pollute the next."""
    llm._GEMINI_CACHE.clear()
    yield
    llm._GEMINI_CACHE.clear()


def test_large_prefix_cached_once_and_reused():
    client = FakeClient()
    system = "PLAYBOOK " * 50                       # well over the (test) 10-char floor
    for _ in range(3):
        assert llm.call(client, _cfg(), system, "user", text=True) == "DRAFT"
    assert len(client.caches.created) == 1                                  # created ONCE, then reused
    assert all(getattr(c, "cached_content", None) for c in client.models.calls)        # every call hit it
    assert all(getattr(c, "system_instruction", None) is None for c in client.models.calls)


def test_small_prefix_is_not_cached():
    client = FakeClient()
    llm.call(client, _cfg(context_cache_min_chars=100_000), "short", "u", text=True)
    assert client.caches.created == []
    assert getattr(client.models.calls[0], "system_instruction", None) == "short"


def test_context_cache_can_be_disabled():
    client = FakeClient()
    llm.call(client, _cfg(context_cache=False), "x" * 50, "u", text=True)
    assert client.caches.created == []
    assert getattr(client.models.calls[0], "system_instruction", None) == "x" * 50


def test_cache_create_failure_falls_back_to_plain_path():
    client = FakeClient()

    def boom(*a, **k):
        raise RuntimeError("no caches API on this endpoint")
    client.caches.create = boom

    out = llm.call(client, _cfg(), "x" * 50, "u", text=True)
    assert out == "DRAFT"                                                   # the call still succeeded
    assert getattr(client.models.calls[0], "system_instruction", None) == "x" * 50


def test_expired_cache_is_evicted_and_retried_uncached():
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


# ── llm.with_tier: per-call model overrides ──────────────────────────────────────────────────────
#
# A tier lets ONE call (the on-demand re-extract) pay for a heavier model without repointing the rest
# of the process. ``settings`` is shared process state, so the copy is the whole point.

TIERS = {
    "light": {"model": "gemini-2.5-flash-lite", "max_tokens": 1024, "thinking_budget": 0},
    "standard": {"model": "gemini-2.5-flash", "max_tokens": 1024, "thinking_budget": 0},
    "heavy": {"model": "gemini-2.5-pro", "max_tokens": 8192, "thinking_budget": 4096},
}


def _settings():
    return {"llm": {"provider": "vertex_gemini", "model": "gemini-2.5-flash", "max_tokens": 1024,
                    "max_retries": 5, "tiers": TIERS}}


def test_with_tier_returns_a_copy_and_never_mutates_the_shared_settings():
    settings = _settings()
    cfg = settings["llm"]
    heavy = llm.with_tier(cfg, "heavy")
    assert heavy["model"] == "gemini-2.5-pro"
    assert settings["llm"]["model"] == "gemini-2.5-flash"       # the shared dict is untouched
    assert cfg["max_tokens"] == 1024 and settings["llm"]["tiers"] is TIERS
    assert heavy is not cfg
    heavy["model"] = "scribbled"                                # and the copy is not a shallow alias
    assert settings["llm"]["model"] == "gemini-2.5-flash"


def test_heavy_tier_applies_model_max_tokens_and_thinking_budget():
    heavy = llm.with_tier(_settings()["llm"], "heavy")
    assert heavy["model"] == "gemini-2.5-pro"
    assert heavy["max_tokens"] == 8192
    assert heavy["thinking_budget"] == 4096
    assert heavy["provider"] == "vertex_gemini"                 # non-tier keys carry over


def test_light_tier_downgrades_the_model():
    assert llm.with_tier(_settings()["llm"], "light")["model"] == "gemini-2.5-flash-lite"


@pytest.mark.parametrize("tier", [None, "", "nonexistent", "HEAVY"])
def test_unknown_or_absent_tier_is_a_no_op(tier):
    """An unknown tier must never be a surprise model switch — it keeps the configured default."""
    cfg = _settings()["llm"]
    assert llm.with_tier(cfg, tier)["model"] == "gemini-2.5-flash"
    assert llm.with_tier(cfg, tier)["max_tokens"] == 1024


def test_with_tier_on_a_config_without_tiers_is_a_no_op():
    assert llm.with_tier(_cfg(), "heavy")["model"] == "gemini-2.5-flash"


def test_cache_key_differs_per_tier_so_two_models_cannot_share_a_prefix():
    cfg = _settings()["llm"]
    system = "PLAYBOOK " * 50
    keys = {llm._gemini_cache_key(llm.with_tier(cfg, t), system) for t in ("light", "standard", "heavy")}
    assert len(keys) == 3
    assert llm._gemini_cache_key(llm.with_tier(cfg, "standard"), system) == llm._gemini_cache_key(cfg, system)


def test_thinking_budget_flows_from_cfg_and_defaults_to_zero():
    client = FakeClient()
    llm.call(client, _cfg(context_cache=False), "sys", "u", text=True)
    assert client.models.calls[0].thinking_config.thinking_budget == 0    # default when unset

    client = FakeClient()
    heavy = llm.with_tier(_settings()["llm"], "heavy")
    heavy.update(context_cache=False, max_retries=1)
    llm.call(client, heavy, "sys", "u", text=True)
    cfg_obj = client.models.calls[0]
    assert cfg_obj.thinking_config.thinking_budget == 4096
    assert cfg_obj.max_output_tokens == 8192


def test_tier_model_is_the_one_actually_sent_to_the_provider():
    client = FakeClient()
    models: list[str] = []
    client.models.generate_content = lambda *, model, contents, config: (
        models.append(model) or _Resp())
    heavy = llm.with_tier(_settings()["llm"], "heavy")
    heavy.update(context_cache=False, max_retries=1)
    llm.call(client, heavy, "sys", "u", text=True)
    assert models == ["gemini-2.5-pro"]
