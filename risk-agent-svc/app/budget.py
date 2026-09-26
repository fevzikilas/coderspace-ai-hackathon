"""GLM kullanım bütçesi / kotası — organizatör modeli: TOPLAM bütçe (15 USD, yarışma boyunca SIFIRLANMAZ).

Gerçek harcama, gateway'in `GET {kök URL}/key/info` yanıtındaki `spend` / `max_budget` alanlarından okunur (kök URL `/v1`
içermez; bkz. GLMClient.key_info). Kalan tutar `BUDGET_RESERVE_USD` rezervinin altına inince LLM kullanılmaz (ajan kural motoruna düşer).
Yerel sayaçlar yalnızca ek güvenliktir ve gün/saat bazında SIFIRLANMAZ; dakikalık pencereler ise takım limitlerine uyar
(60 istek/dk, 500K token/dk). Ayrıca tek bir değerlendirmenin toplam token tavanı, sonsuz döngüye giren bir ajanı keser.

Bellek-içi ve tek süreç içindir; çok replika çalıştırılacaksa paylaşımlı sayaç gerekir (README "Sınırlamalar"); ancak
gerçek harcama zaten uzaktan (key/info) okunduğu için bütçe koruması replikalar arasında da geçerlidir.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .config import Settings

log = logging.getLogger("risk-agent.budget")

KeyInfoFn = Callable[[], Awaitable[dict[str, float | None]]]


class BudgetExceeded(Exception):
    """Bütçe/kota aşıldı."""


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    llm_calls: int = 0

    def add(self, usage: dict | None) -> None:
        self.llm_calls += 1
        if usage:
            self.prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
            self.completion_tokens += int(usage.get("completion_tokens", 0) or 0)
            self.total_tokens += int(usage.get("total_tokens", 0) or 0)

    def as_dict(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "llm_calls": self.llm_calls,
        }


class Budget:
    def __init__(self, s: Settings, key_info: KeyInfoFn | None = None, sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep) -> None:
        self._s = s
        self._key_info = key_info
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._requests: deque[float] = deque()  # son 60 sn'deki LLM istek zamanları
        self._tokens: deque[tuple[float, int]] = deque()  # son 60 sn'deki (zaman, token)
        self._day = self._today()
        self._requests_today = 0  # UTC gün başında sıfırlanan istek sayacı (LLM_MAX_REQUESTS_PER_DAY)
        self.total_tokens = 0  # süreç boyunca toplam (sıfırlanmaz)
        self.total_requests = 0
        # uzak bütçe (key/info) önbelleği
        self._spend: float | None = None
        self._max_budget: float | None = None
        self._info_at = 0.0
        self._info_error: str | None = None
        self._exhausted = False  # 400 "Budget has been exceeded" görüldü: key/info yeniden okunana dek LLM yok

    # ------------------------------------------------------------------ uzak bütçe
    def _cap(self) -> float:
        return self._max_budget if self._max_budget is not None else self._s.budget_total_usd

    def remaining_usd(self) -> float | None:
        return None if self._spend is None else round(self._cap() - self._spend, 4)

    async def refresh(self, force: bool = False) -> None:
        """key/info'yu okur (TTL'li). Okunamazsa hata kaydedilir ve YEREL sınırlarla devam edilir (LLM engellenmez)."""
        if self._key_info is None:
            return
        now = time.time()
        if not force and self._info_at and now - self._info_at < self._s.budget_keyinfo_ttl_s:
            return
        try:
            info = await self._key_info()
        except Exception as exc:  # noqa: BLE001 — ağ/HTTP/biçim: bütçe sorgusu LLM çağrısını düşürmemeli
            self._info_error = f"{exc.__class__.__name__}: {exc}"
            self._info_at = now  # hata durumunda da TTL kadar tekrar sorma
            log.warning("key/info okunamadı (%s); yerel sınırlarla devam", self._info_error)
            return
        self._info_error = None
        self._info_at = now
        if info.get("spend") is not None:
            self._spend = float(info["spend"])  # type: ignore[arg-type]
        if info.get("max_budget") is not None:
            self._max_budget = float(info["max_budget"])  # type: ignore[arg-type]
        if self._exhausted and (rem := self.remaining_usd()) is not None and rem > self._s.budget_reserve_usd:
            self._exhausted = False  # bütçe yeniden yükseltilmiş

    @staticmethod
    def _today() -> str:
        return time.strftime("%Y-%m-%d", time.gmtime())

    def _check_daily_cap(self) -> None:
        cap = self._s.llm_max_requests_per_day
        if self._day != self._today():
            self._day, self._requests_today = self._today(), 0
        if cap > 0 and self._requests_today >= cap:
            raise BudgetExceeded(f"günlük LLM istek tavanı doldu ({self._requests_today}/{cap}, LLM_MAX_REQUESTS_PER_DAY); kural motoruna düşülüyor")

    def mark_exhausted(self) -> None:
        """Gateway 400 "Budget has been exceeded" döndü: sonraki değerlendirmeler LLM'i hiç denemez."""
        self._exhausted = True

    # ------------------------------------------------------------------ kapılar
    async def reserve_assessment(self) -> None:
        """Bir değerlendirmeyi LLM'e göndermeden önce: toplam bütçede yeterli pay var mı? Yoksa BudgetExceeded."""
        self._check_daily_cap()
        await self.refresh(force=self._exhausted)
        if self._exhausted:
            raise BudgetExceeded("organizatör bütçesi bitti (400 'Budget has been exceeded'); organizatörlere yazın")
        rem = self.remaining_usd()
        if rem is not None and rem <= self._s.budget_reserve_usd:
            raise BudgetExceeded(f"toplam bütçe doldu: harcanan {self._spend:.2f} / {self._cap():.2f} USD, kalan {rem:.2f} ≤ rezerv {self._s.budget_reserve_usd:.2f}")

    async def before_call(self, assessment_tokens: int) -> None:
        """Her LLM isteğinden önce: değerlendirme başına token tavanı ve dakikalık pencereler (dolarsa kısa süre beklenir)."""
        if assessment_tokens >= self._s.budget_tokens_per_assessment:
            raise BudgetExceeded(f"değerlendirme başına token sınırı aşıldı ({assessment_tokens}/{self._s.budget_tokens_per_assessment})")
        self._check_daily_cap()
        deadline = time.time() + self._s.budget_max_wait_s
        while True:
            async with self._lock:
                now = time.time()
                self._roll(now)
                used = sum(t for _, t in self._tokens)
                wait = 0.0
                if len(self._requests) >= self._s.budget_requests_per_minute:
                    wait = max(wait, self._requests[0] + 60.0 - now)
                if used >= self._s.budget_tokens_per_minute and self._tokens:
                    wait = max(wait, self._tokens[0][0] + 60.0 - now)
                if wait <= 0:
                    self._requests.append(now)
                    self.total_requests += 1
                    self._requests_today += 1
                    return
            if time.time() + wait > deadline:
                raise BudgetExceeded(f"dakikalık limit doldu (istek {len(self._requests)}/{self._s.budget_requests_per_minute}, token {used}/{self._s.budget_tokens_per_minute}); {wait:.0f} sn beklenmesi gerekir")
            await self._sleep(min(wait, 5.0) + 0.05)

    async def record(self, total_tokens: int) -> None:
        async with self._lock:
            now = time.time()
            self._roll(now)
            n = max(0, total_tokens)
            self._tokens.append((now, n))
            self.total_tokens += n

    def _roll(self, now: float) -> None:
        while self._requests and now - self._requests[0] >= 60.0:
            self._requests.popleft()
        while self._tokens and now - self._tokens[0][0] >= 60.0:
            self._tokens.popleft()

    def status(self) -> dict:
        now = time.time()
        self._roll(now)
        s = self._s
        return {
            "total_budget_usd": round(self._cap(), 2),
            "spend_usd": None if self._spend is None else round(self._spend, 4),
            "remaining_usd": self.remaining_usd(),
            "reserve_usd": s.budget_reserve_usd,
            "exhausted": self._exhausted,
            "key_info_at": self._info_at or None,
            "key_info_error": self._info_error,
            **({"requests_today": self._requests_today, "max_requests_per_day": self._s.llm_max_requests_per_day} if self._s.llm_max_requests_per_day else {}),  # yalnızca günlük tavan varsa (openrouter)
            "requests_last_minute": len(self._requests),
            "requests_per_minute_limit": s.budget_requests_per_minute,
            "tokens_last_minute": sum(t for _, t in self._tokens),
            "tokens_per_minute_limit": s.budget_tokens_per_minute,
            "tokens_per_assessment_limit": s.budget_tokens_per_assessment,
            "total_tokens_since_start": self.total_tokens,
            "total_requests_since_start": self.total_requests,
            "on_exceeded": s.on_budget_exceeded,
        }
