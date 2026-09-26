"""Alt servislere HTTP erişimi (tek noktadan, hata eşlemeli)."""
from __future__ import annotations

from typing import Any

import httpx

from .config import Settings


class ServiceError(Exception):
    def __init__(self, service: str, message: str, status: int | None = None) -> None:
        super().__init__(f"{service}: {message}")
        self.service = service
        self.message = message
        self.status = status


class Services:
    NAMES = ("detection", "core", "pattern", "mock", "risk")

    def __init__(self, s: Settings, http: httpx.AsyncClient | None = None) -> None:
        self._s = s
        self.urls = {
            "detection": s.detection_url,
            "core": s.core_url,
            "pattern": s.pattern_url,
            "mock": s.mock_url,
            "risk": s.risk_url,
        }
        self.http = http or httpx.AsyncClient(timeout=s.http_timeout_s)
        self._owns = http is None

    async def aclose(self) -> None:
        if self._owns:
            await self.http.aclose()

    async def raw(self, service: str, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            return await self.http.request(method, f"{self.urls[service]}{path}", **kw)
        except httpx.TimeoutException as exc:
            raise ServiceError(service, f"zaman aşımı ({path})") from exc
        except httpx.HTTPError as exc:
            raise ServiceError(service, f"erişilemiyor ({exc.__class__.__name__})") from exc

    async def call(self, service: str, method: str, path: str, *, timeout: float | None = None, **kw: Any) -> Any:
        if timeout is not None:
            kw["timeout"] = timeout
        r = await self.raw(service, method, path, **kw)
        if r.status_code >= 400:
            detail: Any = r.text[:300]
            try:
                detail = r.json().get("detail", detail)
            except Exception:  # noqa: BLE001
                pass
            raise ServiceError(service, f"{method} {path} -> {r.status_code}: {detail}", status=r.status_code)
        return r.json()
