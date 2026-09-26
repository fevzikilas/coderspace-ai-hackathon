"""Opsiyonel Postgres kalıcılığı (write-through, best-effort). Şema: db/init.sql `assessments`."""
from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger("risk-agent.db")


class Persistence:
    def __init__(self, url: str | None) -> None:
        self.url = url
        self.enabled = bool(url)

    def save_assessment(self, a: dict[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            import psycopg  # noqa: PLC0415  (opsiyonel bağımlılık)

            with psycopg.connect(self.url, connect_timeout=3) as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO assessments (id, detection_id, zone_id, risk_level, confidence, rationale, "
                    "evidence_breakdown, tool_calls_log, mode, model, policy_adjustments, usage, created_at, reference_time) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s::jsonb,%s::jsonb,%s::timestamptz,%s::timestamptz) "
                    "ON CONFLICT (id) DO NOTHING",
                    (
                        a["assessment_id"], a["detection_id"], a["zone_id"], a["risk_level"], a["confidence"], a["rationale"],
                        json.dumps(a["evidence_breakdown"]), json.dumps(a["tool_calls_log"]), a["mode"], a.get("model"),
                        json.dumps(a["policy_adjustments"]), json.dumps(a["usage"]), a["created_at"], a.get("reference_time"),
                    ),
                )
        except Exception as exc:  # noqa: BLE001 — kalıcılık asla isteği bozmamalı
            log.warning("DB yazımı başarısız (istek etkilenmedi): %s", exc)
