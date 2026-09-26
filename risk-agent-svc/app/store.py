from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any


class AssessmentStore:
    """Son N değerlendirmeyi bellekte tutar; (zone, detection) ile idempotent önbellek işlevi görür."""

    def __init__(self, size: int) -> None:
        self._size = max(1, size)
        self._items: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._by_detection: dict[str, str] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(zone_id: str, detection_id: str) -> str:
        return f"{zone_id.upper()}|{detection_id}"

    def put(self, a: dict[str, Any]) -> None:
        with self._lock:
            self._items[a["assessment_id"]] = a
            self._items.move_to_end(a["assessment_id"])
            self._by_detection[self._key(a["zone_id"], a["detection_id"])] = a["assessment_id"]
            while len(self._items) > self._size:
                _, old = self._items.popitem(last=False)
                self._by_detection.pop(self._key(old["zone_id"], old["detection_id"]), None)

    def get(self, assessment_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._items.get(assessment_id)

    def find(self, zone_id: str, detection_id: str) -> dict[str, Any] | None:
        with self._lock:
            aid = self._by_detection.get(self._key(zone_id, detection_id))
            return self._items.get(aid) if aid else None

    def list(self, zone_id: str | None, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            items = reversed(self._items.values())
            return [a for a in items if zone_id is None or a["zone_id"] == zone_id.upper()][:limit]
