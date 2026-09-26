"""GLM-5.3-Flash istemcisi — organizatörün OpenAI uyumlu gateway'i (gorev_tanimi.pdf), tool calling.

Resmî kurallar (koda yansıyanlar):
  • `thinking` parametresi ASLA gönderilmez (hata verir); düşünme `reasoning_effort` (low|high|max) ile ayarlanır.
  • `max_tokens` düşünmeyi de kapsar: en az 1000 gönderilir; yine de `finish_reason="length"` ile içerik boş dönerse hata verilir.
  • Cevap `message.content`; düşünce `message.reasoning_content` (yalnızca log/debug için).
  • Aynı anda en çok 4 istek (semafor); 429/5xx/ağ hatasında üstel bekleme ile yeniden deneme.
  • 400 "Budget has been exceeded" → `GLMBudgetError` (15 USD bitti; organizatöre haber verilir, ajan kural motoruna düşer).
  • 400 "key not allowed to access model" → model adı yanlış (tam olarak `glm-5.3-flash`).
  • Bütçe: `GET {kök URL}/key/info` (kök URL `/v1` OLMADAN; sohbet uçları `/v1` ile).
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import httpx

from .config import Settings

log = logging.getLogger("risk-agent.glm")

_RETRY_STATUS = {429, 500, 502, 503, 504}
MIN_MAX_TOKENS = 1000
_MAX_BACKOFF_S = 30.0


class GLMError(Exception):
    """GLM çağrısı başarısız."""


class GLMBudgetError(GLMError):
    """Organizatör bütçesi (15 USD) bitti — 400 "Budget has been exceeded"."""


def _is_daily_quota(text: str) -> bool:
    low = text.lower()
    return "per-day" in low or "per day" in low or "daily" in low


def _backoff_s(attempt: int, retry_after: str | None) -> float:
    """Üstel bekleme (1, 2, 4, 8, 16 … sn, tavan 30) + küçük jitter; sunucu `Retry-After` verdiyse ondan az beklenmez."""
    delay = min(_MAX_BACKOFF_S, 1.0 * (2**attempt)) + random.uniform(0, 0.25)
    if retry_after:
        try:
            delay = max(delay, min(60.0, float(retry_after)))
        except ValueError:
            pass
    return delay


def _find_number(data: Any, key: str, depth: int = 3) -> float | None:
    """`key/info` yanıtında `key` alanını üst düzeyde ya da iç içe (`{"info": {...}}`) arar."""
    if isinstance(data, dict):
        v = data.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
        if depth > 0:
            for child in data.values():
                if isinstance(child, dict):
                    found = _find_number(child, key, depth - 1)
                    if found is not None:
                        return found
    return None


class GLMClient:
    def __init__(self, s: Settings, transport: httpx.AsyncBaseTransport | None = None, sleep: Any = asyncio.sleep) -> None:
        if not s.glm_api_key:
            raise ValueError("GLM_API_KEY yok")
        self._s = s
        self._sleep = sleep
        self._gate = asyncio.Semaphore(max(1, s.glm_max_concurrent))
        self._http = httpx.AsyncClient(
            base_url=s.glm_base_url,
            timeout=s.glm_timeout_s,
            headers={"Authorization": f"Bearer {s.glm_api_key}", "Content-Type": "application/json"},
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ bütçe: GET {kök}/key/info
    async def key_info(self) -> dict[str, float | None]:
        """{"spend": ..., "max_budget": ...} (USD). Kök URL `/v1` içermez; sohbet base URL'sinden AYRI tutulur."""
        r = await self._http.get(f"{self._s.glm_root_url}/key/info")
        if r.status_code != 200:
            raise GLMError(f"key/info HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        return {"spend": _find_number(data, "spend"), "max_budget": _find_number(data, "max_budget")}

    # ------------------------------------------------------------------ sohbet
    def build_payload(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._s.glm_model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "max_tokens": max(MIN_MAX_TOKENS, self._s.glm_max_tokens),
            "stream": False,
        }
        if self._s.send_reasoning_effort:  # YALNIZCA GLM: diğer sağlayıcılar tanımayıp hata verebilir
            payload["reasoning_effort"] = self._s.glm_reasoning_effort
        if self._s.glm_temperature is not None:
            payload["temperature"] = self._s.glm_temperature
        return payload  # NOT: "thinking" anahtarı kasıtlı olarak yok

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        payload = self.build_payload(messages, tools)
        last_err = "bilinmeyen hata"
        for attempt in range(self._s.glm_retries + 1):
            retry_after: str | None = None
            try:
                async with self._gate:  # takım limiti: aynı anda en çok 4 istek
                    r = await self._http.post("/chat/completions", json=payload)
            except httpx.HTTPError as exc:
                last_err = f"ağ hatası: {exc.__class__.__name__}: {exc}"
            else:
                if r.status_code == 200:
                    data = r.json()
                    if data.get("choices"):
                        return self._checked(data)
                    last_err = f"boş yanıt: {str(data)[:200]}"
                elif r.status_code == 429 and _is_daily_quota(r.text):
                    # Günlük kota (ör. openrouter ücretsiz katman "free-models-per-day"): beklemek/yeniden denemek anlamsız
                    raise GLMBudgetError(f"HTTP 429: günlük istek kotası doldu — {r.text[:200]}")
                elif r.status_code in _RETRY_STATUS:
                    last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                    retry_after = r.headers.get("retry-after")
                else:
                    self._raise_client_error(r)
            if attempt < self._s.glm_retries:
                delay = _backoff_s(attempt, retry_after)
                log.warning("GLM çağrısı başarısız (%s); %.1f sn sonra tekrar (%d/%d)", last_err, delay, attempt + 1, self._s.glm_retries)
                await self._sleep(delay)
        raise GLMError(last_err)

    @staticmethod
    def _raise_client_error(r: httpx.Response) -> None:
        """4xx: tekrar denemek anlamsız (429 hariç, o yukarıda ele alındı)."""
        text = r.text[:300]
        low = text.lower()
        if r.status_code == 400 and "budget has been exceeded" in low:
            raise GLMBudgetError(f"HTTP 400: {text} — 15 USD bütçe bitti, organizatörlere yazın")
        if r.status_code == 400 and "not allowed to access model" in low:
            raise GLMError(f"HTTP 400: {text} — model adı tam olarak 'glm-5.3-flash' olmalı (GLM_MODEL)")
        if r.status_code == 401:
            raise GLMError(f"HTTP 401: {text} — API key geçersiz ya da süresi dolmuş (GLM_API_KEY)")
        raise GLMError(f"HTTP {r.status_code}: {text}")

    def _checked(self, data: dict[str, Any]) -> dict[str, Any]:
        choice = data["choices"][0]
        msg = choice.get("message") or {}
        if msg.get("reasoning_content"):
            log.debug("GLM reasoning_content: %s", str(msg["reasoning_content"])[:2000])  # yalnızca debug; cevap = content
        if choice.get("finish_reason") == "length" and not msg.get("tool_calls"):
            raise GLMError(f"finish_reason=length: max_tokens={self._s.glm_max_tokens} düşünme + cevap için yetmedi (content boş/kesik); GLM_MAX_TOKENS artırın")
        return data
