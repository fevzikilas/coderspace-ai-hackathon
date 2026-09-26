"""GLM (Zhipu AI / Z.ai) chat-completions istemcisi — OpenAI uyumlu tool calling."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .config import Settings

log = logging.getLogger("risk-agent.glm")

_RETRY_STATUS = {429, 500, 502, 503, 504}


class GLMError(Exception):
    """GLM çağrısı başarısız."""


class GLMClient:
    def __init__(self, s: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        if not s.glm_api_key:
            raise ValueError("GLM_API_KEY yok")
        self._s = s
        self._http = httpx.AsyncClient(
            base_url=s.glm_base_url,
            timeout=s.glm_timeout_s,
            headers={"Authorization": f"Bearer {s.glm_api_key}", "Content-Type": "application/json"},
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._s.glm_model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": self._s.glm_temperature,
            "max_tokens": self._s.glm_max_tokens,
            "stream": False,
        }
        if self._s.glm_thinking in {"enabled", "disabled"}:
            payload["thinking"] = {"type": self._s.glm_thinking}

        last_err = "bilinmeyen hata"
        for attempt in range(self._s.glm_retries + 1):
            try:
                r = await self._http.post("/chat/completions", json=payload)
            except httpx.HTTPError as exc:
                last_err = f"ağ hatası: {exc.__class__.__name__}: {exc}"
            else:
                if r.status_code == 200:
                    data = r.json()
                    if data.get("choices"):
                        return data
                    last_err = f"boş yanıt: {str(data)[:200]}"
                elif r.status_code in _RETRY_STATUS:
                    last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                else:
                    # 4xx (kimlik doğrulama, geçersiz istek): tekrar denemek anlamsız
                    raise GLMError(f"HTTP {r.status_code}: {r.text[:300]}")
            if attempt < self._s.glm_retries:
                delay = 1.5 * (2**attempt)
                log.warning("GLM çağrısı başarısız (%s); %.1f sn sonra tekrar", last_err, delay)
                await asyncio.sleep(delay)
        raise GLMError(last_err)
