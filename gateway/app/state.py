"""Gateway'in bellek-içi durumu: pipeline koşuları, log paneli, patern taraması, TTL önbelleği."""
from __future__ import annotations

import time
from collections import OrderedDict, deque
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from .config import Settings


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class GatewayState:
    def __init__(self, s: Settings) -> None:
        self._s = s
        self.runs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.logs: deque[dict[str, Any]] = deque(maxlen=s.max_logs)
        # Son zone taraması: vehicle_id -> {"pattern", "matched"} (harita etiketleri için)
        self.sweep: dict[str, dict[str, Any]] = {}
        self.sweep_at: str | None = None
        self._cache: dict[str, tuple[float, Any]] = {}
        self._cache_errors: dict[str, str] = {}

    # ------------------------------------------------------------------ log paneli
    def log(self, level: str, message: str, run_id: str | None = None, step: str | None = None) -> None:
        self.logs.append({"ts": now_iso(), "level": level, "run_id": run_id, "step": step, "message": message})

    # ------------------------------------------------------------------ koşular
    def add_run(self, run: dict[str, Any]) -> None:
        self.runs[run["run_id"]] = run
        self.runs.move_to_end(run["run_id"])
        while len(self.runs) > self._s.max_runs:
            self.runs.popitem(last=False)

    def latest_run(self) -> dict[str, Any] | None:
        return next(reversed(self.runs.values()), None)

    def latest_finished_result(self) -> dict[str, Any] | None:
        for run in reversed(self.runs.values()):
            if run["status"] == "succeeded":
                return run
        return None

    # ------------------------------------------------------------------ TTL önbelleği (son-iyi-değer destekli)
    async def cached(self, key: str, ttl: float, fetch: Callable[[], Awaitable[Any]]) -> Any:
        """TTL içinde önbellekten verir; yenilemede hata olursa son iyi değeri döndürür ve hatayı kaydeder."""
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
        try:
            value = await fetch()
        except Exception as exc:  # noqa: BLE001 — hata paneli için kaydedilir
            self._cache_errors[key] = str(exc)
            if hit:
                return hit[1]
            raise
        self._cache[key] = (now, value)
        self._cache_errors.pop(key, None)
        return value

    def cache_error(self, key: str) -> str | None:
        return self._cache_errors.get(key)
