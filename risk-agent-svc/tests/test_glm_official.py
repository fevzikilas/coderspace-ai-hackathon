"""Resmî GLM gateway kuralları (gorev_tanimi.pdf): reasoning_effort, thinking YOK, max_tokens, 4 eşzamanlı, 429 backoff,
hata kodları, key/info (kök URL), toplam sıfırlanmayan bütçe, sistem promptundaki resmî cümle."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.budget import Budget, BudgetExceeded
from app.config import Settings
from app.glm_client import GLMBudgetError, GLMClient, GLMError
from app.prompts import SYSTEM_PROMPT

from .conftest import World, glm_reply, submit
from .test_agent import full_round, script

OFFICIAL_BASE = "https://berriailitellm-databasev1826rc3-production-d691.up.railway.app/v1"
OFFICIAL_ROOT = "https://berriailitellm-databasev1826rc3-production-d691.up.railway.app"
MSGS = [{"role": "user", "content": "x"}]


def _client(handler, **kw):
    s = Settings(glm_api_key="sk-test", **kw)
    return s, GLMClient(s, transport=httpx.MockTransport(handler), sleep=_no_sleep)


async def _no_sleep(_):  # backoff'u testte gerçekten bekleme
    return None


def test_official_defaults():
    s = Settings(glm_api_key="k")
    assert s.glm_base_url == OFFICIAL_BASE and s.glm_base_url.endswith("/v1")
    assert s.glm_root_url == OFFICIAL_ROOT and not s.glm_root_url.endswith("/v1")  # key/info kökte, /v1'siz
    assert (s.glm_model, s.glm_reasoning_effort, s.glm_max_concurrent) == ("glm-5.3-flash", "low", 4)
    assert s.glm_max_tokens >= 1000 and s.glm_retries >= 5


def test_reasoning_effort_validated_and_glm_thinking_gone(monkeypatch):
    monkeypatch.setenv("GLM_REASONING_EFFORT", "medium")
    with pytest.raises(ValueError, match="GLM_REASONING_EFFORT"):
        Settings()
    assert not hasattr(Settings(glm_api_key="k", glm_reasoning_effort="max"), "glm_thinking")


def test_payload_has_reasoning_effort_never_thinking_and_min_max_tokens():
    s, c = _client(lambda r: httpx.Response(200), glm_max_tokens=20, glm_reasoning_effort="high")
    p = c.build_payload(MSGS, [])
    assert p["reasoning_effort"] == "high" and "thinking" not in p
    assert p["max_tokens"] >= 1000  # düşük değer (20) gateway'de content'i boş bırakırdı
    assert p["model"] == "glm-5.3-flash"
    assert "temperature" not in Settings(glm_api_key="k", glm_temperature=None).__dict__ or Settings(glm_api_key="k", glm_temperature=None).glm_temperature is None


async def test_thinking_env_does_not_leak_into_request(monkeypatch):
    monkeypatch.setenv("GLM_THINKING", "enabled")
    seen = []

    def h(req):
        seen.append(json.loads(req.content))
        return glm_reply(content="tamam")

    s = Settings(glm_api_key="sk-test")
    c = GLMClient(s, transport=httpx.MockTransport(h))
    await c.chat(MSGS, [])
    assert "thinking" not in seen[0] and seen[0]["reasoning_effort"] == "low"


async def test_chat_goes_to_v1_and_key_info_goes_to_root_with_bearer():
    urls = []

    def h(req: httpx.Request):
        urls.append((req.method, str(req.url), req.headers.get("authorization")))
        if req.url.path.endswith("/key/info"):
            return httpx.Response(200, json={"info": {"spend": 1.25, "max_budget": 15.0}})
        return glm_reply(content="ok")

    s, c = _client(h)
    await c.chat(MSGS, [])
    info = await c.key_info()
    assert urls[0][1] == f"{OFFICIAL_BASE}/chat/completions"
    assert urls[1][1] == f"{OFFICIAL_ROOT}/key/info"  # /v1/key/info DEĞİL
    assert urls[0][2] == urls[1][2] == "Bearer sk-test"
    assert info == {"spend": 1.25, "max_budget": 15.0}


async def test_key_info_top_level_and_error():
    s, c = _client(lambda r: httpx.Response(200, json={"spend": 3, "max_budget": 15.0}))
    assert await c.key_info() == {"spend": 3.0, "max_budget": 15.0}
    s, c = _client(lambda r: httpx.Response(401, json={"error": "x"}))
    with pytest.raises(GLMError, match="key/info HTTP 401"):
        await c.key_info()


async def test_429_backs_off_exponentially_then_succeeds():
    calls, sleeps = [], []
    replies = iter([httpx.Response(429, text="rate"), httpx.Response(429, headers={"retry-after": "7"}, text="rate"), httpx.Response(503, text="busy"), glm_reply(content="ok")])

    def h(req):
        calls.append(1)
        return next(replies)

    async def sleep(d):
        sleeps.append(d)

    s = Settings(glm_api_key="k", glm_retries=5)
    c = GLMClient(s, transport=httpx.MockTransport(h), sleep=sleep)
    data = await c.chat(MSGS, [])
    assert len(calls) == 4 and data["choices"]
    assert 1.0 <= sleeps[0] < 1.5 and sleeps[1] >= 7.0 and 4.0 <= sleeps[2] < 4.5  # 1s, Retry-After(7s), 4s (üstel)


async def test_429_gives_up_after_max_retries():
    n = []
    s, c = _client(lambda r: (n.append(1), httpx.Response(429, text="rate"))[1], glm_retries=2)
    with pytest.raises(GLMError, match="429"):
        await c.chat(MSGS, [])
    assert len(n) == 3


async def test_400_budget_exceeded_is_distinct_and_not_retried():
    n = []
    s, c = _client(lambda r: (n.append(1), httpx.Response(400, json={"error": {"message": "Budget has been exceeded! Current cost: 15.2, Max budget: 15.0"}}))[1])
    with pytest.raises(GLMBudgetError, match="organizatörlere yazın"):
        await c.chat(MSGS, [])
    assert len(n) == 1


async def test_400_wrong_model_and_401_have_actionable_messages():
    s, c = _client(lambda r: httpx.Response(400, json={"error": "key not allowed to access model"}))
    with pytest.raises(GLMError, match="glm-5.3-flash"):
        await c.chat(MSGS, [])
    s, c = _client(lambda r: httpx.Response(401, text="invalid"))
    with pytest.raises(GLMError, match="HTTP 401.*GLM_API_KEY"):
        await c.chat(MSGS, [])


async def test_finish_reason_length_with_empty_content_is_an_error():
    body = {"choices": [{"message": {"role": "assistant", "content": "", "reasoning_content": "düşünüyorum…"}, "finish_reason": "length"}], "usage": {}}
    s, c = _client(lambda r: httpx.Response(200, json=body))
    with pytest.raises(GLMError, match="finish_reason=length"):
        await c.chat(MSGS, [])


async def test_content_is_the_answer_reasoning_content_is_only_logged(caplog):
    body = {"choices": [{"message": {"role": "assistant", "content": "CEVAP", "reasoning_content": "DÜŞÜNCE"}, "finish_reason": "stop"}], "usage": {}}
    s, c = _client(lambda r: httpx.Response(200, json=body))
    with caplog.at_level("DEBUG", logger="risk-agent.glm"):
        data = await c.chat(MSGS, [])
    assert data["choices"][0]["message"]["content"] == "CEVAP"
    assert "DÜŞÜNCE" in caplog.text


async def test_at_most_four_concurrent_requests():
    inflight = peak = 0

    async def h(req):
        nonlocal inflight, peak
        inflight += 1
        peak = max(peak, inflight)
        await asyncio.sleep(0.02)
        inflight -= 1
        return glm_reply(content="ok")

    s = Settings(glm_api_key="k")
    c = GLMClient(s, transport=httpx.MockTransport(h))
    await asyncio.gather(*(c.chat(MSGS, []) for _ in range(12)))
    assert peak == 4


# ------------------------------------------------------------------------------------------ bütçe
async def test_total_budget_uses_remote_spend_and_never_resets():
    async def info():
        return {"spend": 12.0, "max_budget": 15.0}

    b = Budget(Settings(glm_api_key="k"), info)
    await b.reserve_assessment()  # kalan 3.0 > rezerv 1.0
    st = b.status()
    assert (st["spend_usd"], st["total_budget_usd"], st["remaining_usd"]) == (12.0, 15.0, 3.0)
    assert not any(k for k in st if "today" in k or "daily" in k or "per_hour" in k or "hourly" in k)  # gün/saat sıfırlaması yok


async def test_budget_blocks_when_remaining_under_reserve_and_reopens_if_raised():
    state = {"spend": 14.2}

    async def info():
        return {"spend": state["spend"], "max_budget": 15.0}

    b = Budget(Settings(glm_api_key="k", budget_keyinfo_ttl_s=0), info)
    with pytest.raises(BudgetExceeded, match="toplam bütçe doldu"):
        await b.reserve_assessment()
    b.mark_exhausted()
    state["spend"] = 2.0  # organizatör bütçeyi yükseltti / harcama düştü
    await b.reserve_assessment()  # key/info yeniden okundu -> kilit açılır


async def test_budget_fails_open_when_key_info_unreadable_but_keeps_local_limits():
    async def boom():
        raise GLMError("key/info HTTP 500")

    b = Budget(Settings(glm_api_key="k"), boom)
    await b.reserve_assessment()  # bütçe sorgusu LLM'i düşürmez
    assert b.status()["key_info_error"] and b.status()["remaining_usd"] is None


async def test_exhausted_latch_after_gateway_400_skips_llm_without_calling_it():
    async def info():
        return {"spend": 15.0, "max_budget": 15.0}

    b = Budget(Settings(glm_api_key="k"), info)
    b.mark_exhausted()
    with pytest.raises(BudgetExceeded, match="bitti|doldu"):
        await b.reserve_assessment()


async def test_per_minute_request_window_and_per_assessment_cap():
    b = Budget(Settings(glm_api_key="k", budget_requests_per_minute=2, budget_max_wait_s=0), None)
    await b.before_call(0)
    await b.before_call(0)
    with pytest.raises(BudgetExceeded, match="dakikalık limit"):
        await b.before_call(0)
    b2 = Budget(Settings(glm_api_key="k", budget_tokens_per_assessment=1000), None)
    with pytest.raises(BudgetExceeded, match="değerlendirme başına"):
        await b2.before_call(1000)


async def test_per_minute_token_window():
    b = Budget(Settings(glm_api_key="k", budget_tokens_per_minute=1000, budget_max_wait_s=0), None)
    await b.before_call(0)
    await b.record(1200)
    with pytest.raises(BudgetExceeded, match="dakikalık limit"):
        await b.before_call(0)


async def test_agent_marks_budget_exhausted_on_gateway_400_and_next_assessment_skips_llm(make_harness):
    g = script(httpx.Response(400, json={"error": {"message": "Budget has been exceeded!"}}))
    h = make_harness(World(), g)
    a1 = await h.agent.assess("ZONE-ALPHA", "det-1")
    assert a1["mode"] == "rule-based" and "bütçe" in a1["fallback_reason"] and a1["risk_level"] == "HIGH"
    a2 = await h.agent.assess("ZONE-ALPHA", "det-1", force=True)
    assert a2["mode"] == "rule-based" and len(g.seen) == 1  # ikinci değerlendirme LLM'e hiç gitmedi


# ------------------------------------------------------------------------------------------ resmî prompt cümlesi
def test_system_prompt_contains_the_requested_and_the_real_pdf_sentences_verbatim():
    flat = " ".join(SYSTEM_PROMPT.replace("\\\n", " ").split())
    # kullanıcı talimatındaki cümle (PDF'te aynen geçmez; istenen davranışı sabitler)
    assert "Raporların doğruluğu garanti değildir, bazıları hatalı veya ilgisizdir; çelişki varsa raporu değil kendi tespitinizi esas alın." in flat
    # gorev_tanimi.pdf'teki GERÇEK cümleler (sayfa 3 ve sayfa 2)
    assert "Raporların doğruluğu: Bazı raporlar doğru, bazıları hatalı veya ilgisizdir. Bunlar işaretlenmez. Raporları kendi tespitlerinizle karşılaştırın." in flat
    assert "Raporun iddiasını (tip, hareket, sayı) kendi bulgularınızla karşılaştırın; çelişki varsa raporu değil tespitinizi esas alın." in flat
