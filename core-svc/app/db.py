"""Opsiyonel Postgres/PostGIS kalıcılığı (write-through, best-effort).

`DATABASE_URL` yoksa hiçbir şey yapmaz. Okumalar bellekten sunulur; DB, geçmiş/denetim kaydıdır.
Herhangi bir DB hatası servisi ASLA düşürmez (yalnızca loglanır). Şema: db/init.sql.
"""
from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger("core-svc.db")

Point = tuple[float, float, float]  # (ts_epoch, lat, lon)


def _j(value: Any) -> str | None:
    return None if value is None else json.dumps(value)


class Persistence:
    def __init__(self, url: str | None) -> None:
        self.url = url
        self.enabled = bool(url)

    def save_event(self, event: dict[str, Any]) -> None:
        """Bir tespit olayını (detections + detection_objects) yazar; `ingested` ise vehicles + tracks da."""
        if not self.enabled:
            return
        try:
            import psycopg  # noqa: PLC0415  (opsiyonel bağımlılık)

            with psycopg.connect(self.url, connect_timeout=3) as conn, conn.cursor() as cur:
                ts = event["ts"]
                cur.execute(
                    "INSERT INTO detections (detection_id, image_id, drone_id, zone_id, ts, drone_meta, image_meta) "
                    "VALUES (%s, %s, %s, %s, to_timestamp(%s), %s::jsonb, %s::jsonb) ON CONFLICT (detection_id) DO NOTHING",
                    (event["detection_id"], event["image_id"], event.get("drone_id"), event.get("zone_id"), ts, _j(event.get("drone_meta")), _j(event.get("image"))),
                )
                for det in event["detections"]:
                    vid = det.get("vehicle_id")
                    if vid and event.get("ingested"):
                        cur.execute(
                            "INSERT INTO vehicles (id, class, first_seen, last_seen) "
                            "VALUES (%s, %s, to_timestamp(%s), to_timestamp(%s)) "
                            "ON CONFLICT (id) DO UPDATE SET last_seen = EXCLUDED.last_seen",
                            (vid, det["class"], ts, ts),
                        )
                        cur.execute(
                            "INSERT INTO tracks (vehicle_id, ts, lat, lon, geom, detection_id) "
                            "VALUES (%s, to_timestamp(%s), %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s) "
                            "ON CONFLICT (vehicle_id, ts) DO NOTHING",
                            (vid, ts, det["lat"], det["lon"], det["lon"], det["lat"], event["detection_id"]),
                        )
                    cur.execute(
                        "INSERT INTO detection_objects (detection_id, box_index, class, conf, bbox, lat, lon, geom, vehicle_id) "
                        "VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, "
                        "(SELECT id FROM vehicles WHERE id = %s))",
                        (
                            event["detection_id"],
                            det["box_index"],
                            det["class"],
                            det["conf"],
                            _j(det.get("bbox")),
                            det["lat"],
                            det["lon"],
                            det["lon"],
                            det["lat"],
                            vid,
                        ),
                    )
        except Exception as exc:  # noqa: BLE001 — kalıcılık asla isteği bozmamalı
            log.warning("DB yazımı başarısız (istek etkilenmedi): %s", exc)

    def sync_catalog(self, tracks: dict[str, list[Point]]) -> None:
        """tracks.csv izlerini vehicles + tracks tablosuna idempotent yazar (açılışta bir kez)."""
        if not self.enabled:
            return
        try:
            import psycopg  # noqa: PLC0415

            n_rows = 0
            with psycopg.connect(self.url, connect_timeout=3) as conn, conn.cursor() as cur:
                for tid, pts in tracks.items():
                    if not pts:
                        continue
                    cur.execute(
                        "INSERT INTO vehicles (id, class, first_seen, last_seen) "
                        "VALUES (%s, 'unknown', to_timestamp(%s), to_timestamp(%s)) "
                        "ON CONFLICT (id) DO UPDATE SET "
                        "first_seen = LEAST(vehicles.first_seen, EXCLUDED.first_seen), "
                        "last_seen = GREATEST(vehicles.last_seen, EXCLUDED.last_seen)",
                        (tid, pts[0][0], pts[-1][0]),
                    )
                    cur.executemany(
                        "INSERT INTO tracks (vehicle_id, ts, lat, lon, geom) "
                        "VALUES (%s, to_timestamp(%s), %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography) "
                        "ON CONFLICT (vehicle_id, ts) DO NOTHING",
                        [(tid, ts, lat, lon, lon, lat) for ts, lat, lon in pts],
                    )
                    n_rows += len(pts)
            log.info("DB: %d iz / %d nokta senkronlandı", len(tracks), n_rows)
        except Exception as exc:  # noqa: BLE001
            log.warning("DB katalog senkronu başarısız: %s", exc)
