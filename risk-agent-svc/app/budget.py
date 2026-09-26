"""GLM kullanım bütçesi / kotası.

Sınırlar (hepsi env ile ayarlanır): günlük token, saatlik LLM isteği, saatlik değerlendirme,
değerlendirme başına token. Bellek-içi ve tek süreç içindir; çok replika çalıştırılacaksa Redis gibi
paylaşımlı bir sayaç gerekir (README "Sınırlamalar").
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import Settings


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
    def __init__(self, s: Settings) -> None:
        self._s = s
        self._lock = asyncio.Lock()
        self._day = self._today()
        self._tokens_today = 0
        self._requests: deque[float] = deque()  # LLM istek zamanları (son 1 saat)
        self._assessments: deque[float] = deque()  # LLM'e giden değerlendirmeler (son 1 saat)

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _roll(self, now: float) -> None:
        if self._day != self._today():
            self._day, self._tokens_today = self._today(), 0
        for dq in (self._requests, self._assessments):
            while dq and now - dq[0] > 3600:
                dq.popleft()

    async def reserve_assessment(self) -> None:
        """Bir değerlendirmeyi LLM'e göndermeden önce çağrılır; kota doluysa BudgetExceeded."""
        async with self._lock:
            now = time.time()
            self._roll(now)
            if self._tokens_today >= self._s.budget_daily_tokens:
                raise BudgetExceeded(f"günlük token bütçesi doldu ({self._tokens_today}/{self._s.budget_daily_tokens})")
            if len(self._assessments) >= self._s.budget_assessments_per_hour:
                raise BudgetExceeded(f"saatlik değerlendirme kotası doldu ({self._s.budget_assessments_per_hour}/sa)")
            self._assessments.append(now)

    async def before_call(self, assessment_tokens: int) -> None:
        """Her LLM isteğinden önce çağrılır."""
        async with self._lock:
            now = time.time()
            self._roll(now)
            if assessment_tokens >= self._s.budget_tokens_per_assessment:
                raise BudgetExceeded(f"değerlendirme başına token sınırı aşıldı ({assessment_tokens}/{self._s.budget_tokens_per_assessment})")
            if self._tokens_today >= self._s.budget_daily_tokens:
                raise BudgetExceeded("günlük token bütçesi doldu")
            if len(self._requests) >= self._s.budget_requests_per_hour:
                raise BudgetExceeded(f"saatlik LLM istek kotası doldu ({self._s.budget_requests_per_hour}/sa)")
            self._requests.append(now)

    async def record(self, total_tokens: int) -> None:
        async with self._lock:
            self._roll(time.time())
            self._tokens_today += max(0, total_tokens)

    def status(self) -> dict:
        now = time.time()
        self._roll(now)
        s = self._s
        return {
            "day": self._day,
            "tokens_today": self._tokens_today,
            "daily_token_budget": s.budget_daily_tokens,
            "tokens_remaining": max(0, s.budget_daily_tokens - self._tokens_today),
            "requests_last_hour": len(self._requests),
            "requests_per_hour_limit": s.budget_requests_per_hour,
            "assessments_last_hour": len(self._assessments),
            "assessments_per_hour_limit": s.budget_assessments_per_hour,
            "tokens_per_assessment_limit": s.budget_tokens_per_assessment,
            "on_exceeded": s.on_budget_exceeded,
        }
