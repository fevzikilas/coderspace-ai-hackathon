"""Sağlayıcıdan bağımsız LLM yapılandırması (glm | openrouter): env çözümü, reasoning_effort yalnızca GLM'e, günlük kota."""
import httpx
import pytest

from app.budget import Budget, BudgetExceeded
from app.config import Settings
from app.glm_client import GLMBudgetError, GLMClient

MSGS = [{"role": "user", "content": "x"}]
LLM_ENV = ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY", "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", "OPENROUTER_MODEL", "GLM_API_KEY", "GLM_BASE_URL", "GLM_MODEL",
           "LLM_SEND_REASONING_EFFORT", "LLM_MAX_REQUESTS_PER_DAY", "BUDGET_REQUESTS_PER_MINUTE", "GLM_MAX_CONCURRENT")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in LLM_ENV:
        monkeypatch.delenv(k, raising=False)


def test_default_provider_is_glm_with_official_values(monkeypatch):
    monkeypatch.setenv("GLM_API_KEY", "sk-glm")
    s = Settings()
    assert (s.llm_provider, s.glm_model, s.glm_api_key, s.send_reasoning_effort) == ("glm", "glm-5.3-flash", "sk-glm", True)
    assert s.glm_base_url.endswith("/v1") and s.llm_max_requests_per_day == 0 and s.budget_requests_per_minute == 50 and s.glm_max_concurrent == 4


def test_openrouter_provider_defaults_and_safe_limits(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
    s = Settings()
    assert (s.llm_provider, s.glm_base_url, s.glm_model, s.glm_api_key) == ("openrouter", "https://openrouter.ai/api/v1", "nvidia/nemotron-3-ultra-550b-a55b:free", "sk-or")
    assert s.send_reasoning_effort is False              # OpenRouter modeli reasoning_effort'u tanımayabilir
    assert s.llm_max_requests_per_day == 40 and s.budget_requests_per_minute == 15 and s.glm_max_concurrent == 2   # ücretsiz katman: 20/dk, 50/gün


def test_generic_llm_variables_override_the_provider_values(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("LLM_BASE_URL", "https://openrouter.ai/api/v1/")
    monkeypatch.setenv("LLM_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")
    monkeypatch.setenv("LLM_API_KEY", "sk-generic")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-ignored")
    s = Settings()
    assert (s.glm_base_url, s.glm_model, s.glm_api_key) == ("https://openrouter.ai/api/v1", "nvidia/nemotron-3-ultra-550b-a55b:free", "sk-generic")


def test_unknown_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "azure")
    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        Settings()


def test_reasoning_effort_is_sent_only_to_glm():
    glm = GLMClient(Settings(glm_api_key="k", llm_provider="glm", send_reasoning_effort=True), transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    orx = GLMClient(Settings(glm_api_key="k", llm_provider="openrouter", send_reasoning_effort=False), transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    assert glm.build_payload(MSGS, [])["reasoning_effort"] == "low"
    p = orx.build_payload(MSGS, [])
    assert "reasoning_effort" not in p and "thinking" not in p and p["tool_choice"] == "auto"


def test_reasoning_effort_can_be_forced_for_other_providers(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("LLM_SEND_REASONING_EFFORT", "true")
    assert Settings().send_reasoning_effort is True


async def test_daily_quota_429_is_not_retried_and_is_a_budget_error():
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(429, json={"error": {"message": "Rate limit exceeded: free-models-per-day. Add credits to unlock more"}})

    c = GLMClient(Settings(glm_api_key="k", glm_retries=5), transport=httpx.MockTransport(h))
    with pytest.raises(GLMBudgetError, match="günlük istek kotası"):
        await c.chat(MSGS, [])
    assert len(calls) == 1  # yeniden deneme yok: istek hakkı boşa harcanmaz


async def test_per_minute_429_is_still_retried():
    replies = iter([httpx.Response(429, text="Rate limit exceeded: 20 requests per minute"), httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}], "usage": {}})])

    async def no_sleep(_):
        return None

    c = GLMClient(Settings(glm_api_key="k", glm_retries=2), transport=httpx.MockTransport(lambda r: next(replies)), sleep=no_sleep)
    assert (await c.chat(MSGS, []))["choices"]


async def test_daily_request_cap_falls_back_instead_of_burning_the_free_quota():
    b = Budget(Settings(glm_api_key="k", llm_provider="openrouter", llm_max_requests_per_day=3, budget_requests_per_minute=100))
    for _ in range(3):
        await b.before_call(0)
    with pytest.raises(BudgetExceeded, match="günlük LLM istek tavanı"):
        await b.before_call(0)
    with pytest.raises(BudgetExceeded, match="günlük LLM istek tavanı"):
        await b.reserve_assessment()
    st = b.status()
    assert st["requests_today"] == 3 and st["max_requests_per_day"] == 3


async def test_no_daily_cap_for_glm():
    b = Budget(Settings(glm_api_key="k", budget_requests_per_minute=1000))
    for _ in range(60):
        await b.before_call(0)
    assert "max_requests_per_day" not in b.status() and "requests_today" not in b.status()
