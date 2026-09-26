-- =====================================================================================
-- Üs Koruma Sistemi — Tehlike Alarm Sistemi : PostgreSQL + PostGIS şeması
--
-- Kullanım: docker-entrypoint-initdb.d altına mount edilir (docker-compose / k8s ConfigMap).
-- Manuel: psql "$DATABASE_URL" -f db/init.sql
--
-- Yazan servisler (hepsi write-through / best-effort; DB düşse de servisler çalışır):
--   core-svc       -> vehicles, tracks, detections, detection_objects
--   risk-agent-svc -> assessments
-- drones tablosu mock-data-svc kaydının başlangıç anlık görüntüsüdür (demo seed).
-- Koordinatlar KURGUSALDIR. Kaynak-doğru dosya budur; k8s/db.yaml'daki ConfigMap
-- `scripts/gen-k8s-db-configmap.sh` ile bu dosyadan üretilir.
-- =====================================================================================

CREATE EXTENSION IF NOT EXISTS postgis;

-- ---------------------------------------------------------------------------- drones
CREATE TABLE IF NOT EXISTS drones (
    id               TEXT PRIMARY KEY,
    callsign         TEXT,
    status           TEXT NOT NULL CHECK (status IN ('ACTIVE', 'STANDBY', 'RTB', 'OFFLINE')),
    mode             TEXT,
    lat              DOUBLE PRECISION NOT NULL CHECK (lat BETWEEN -90 AND 90),
    lon              DOUBLE PRECISION NOT NULL CHECK (lon BETWEEN -180 AND 180),
    alt_m            REAL,                -- yerden yükseklik (AGL)
    heading_deg      REAL,                -- kuzeyden saat yönünde
    gimbal_pitch_deg REAL,                -- 0 = ufuk, -90 = nadir
    fov_deg          REAL,                -- yatay görüş açısı
    sensor_w         INTEGER,             -- görüntü genişliği (px)
    sensor_h         INTEGER,             -- görüntü yüksekliği (px)
    battery_pct      INTEGER CHECK (battery_pct BETWEEN 0 AND 100),
    geom             geography(Point, 4326),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS drones_geom_gix ON drones USING GIST (geom);

-- ---------------------------------------------------------------------------- vehicles
CREATE TABLE IF NOT EXISTS vehicles (
    id         TEXT PRIMARY KEY,          -- örn. V-101 (demo), V-1000+ (tracker'ın yarattığı)
    class      TEXT,
    first_seen TIMESTAMPTZ,
    last_seen  TIMESTAMPTZ,
    is_demo    BOOLEAN NOT NULL DEFAULT FALSE
);

-- ---------------------------------------------------------------------------- tracks
CREATE TABLE IF NOT EXISTS tracks (
    id           BIGSERIAL PRIMARY KEY,
    vehicle_id   TEXT NOT NULL REFERENCES vehicles (id) ON DELETE CASCADE,
    ts           TIMESTAMPTZ NOT NULL,
    lat          DOUBLE PRECISION NOT NULL,
    lon          DOUBLE PRECISION NOT NULL,
    geom         geography(Point, 4326) NOT NULL,
    detection_id TEXT                      -- noktayı üreten tespit olayı (varsa)
);
CREATE INDEX IF NOT EXISTS tracks_vehicle_ts_idx ON tracks (vehicle_id, ts DESC);
-- aynı araç aynı anda tek nokta: tracks.csv senkronu ve tekrar eden yazımlar idempotent olsun
CREATE UNIQUE INDEX IF NOT EXISTS tracks_vehicle_ts_uidx ON tracks (vehicle_id, ts);
CREATE INDEX IF NOT EXISTS tracks_geom_gix ON tracks USING GIST (geom);

-- ---------------------------------------------------------------------------- detections
-- Bir görüntü karesi = bir tespit olayı (risk-agent'a verilen detection_id). `ts` = olayın referans zamanı
-- (görüntünün capture_time'ı); kaydın yazıldığı an `created_at`.
CREATE TABLE IF NOT EXISTS detections (
    detection_id TEXT PRIMARY KEY,
    image_id     TEXT NOT NULL,
    drone_id     TEXT,                     -- bilinçli olarak FK yok: kayıt dışı drone da yazılabilsin (olay akışında NULL)
    zone_id      TEXT,
    ts           TIMESTAMPTZ NOT NULL,
    drone_meta   JSONB,                    -- eski/demo akışı: lat, lon, alt, heading, gimbal_pitch, fov, sensor_w, sensor_h
    image_meta   JSONB,                    -- olay akışı: {width_px, height_px, corner_coordinates}
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS detections_ts_idx ON detections (ts DESC);
CREATE INDEX IF NOT EXISTS detections_drone_idx ON detections (drone_id);

-- Olaydaki her nesne (bbox + georeferanslı konum + atanan araç)
CREATE TABLE IF NOT EXISTS detection_objects (
    id           BIGSERIAL PRIMARY KEY,
    detection_id TEXT NOT NULL REFERENCES detections (detection_id) ON DELETE CASCADE,
    box_index    INTEGER NOT NULL,
    class        TEXT NOT NULL,
    conf         REAL CHECK (conf BETWEEN 0 AND 1),
    bbox         JSONB,                    -- {x1,y1,x2,y2} piksel
    lat          DOUBLE PRECISION NOT NULL,
    lon          DOUBLE PRECISION NOT NULL,
    geom         geography(Point, 4326) NOT NULL,
    vehicle_id   TEXT REFERENCES vehicles (id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS detection_objects_det_idx ON detection_objects (detection_id);
CREATE INDEX IF NOT EXISTS detection_objects_geom_gix ON detection_objects USING GIST (geom);

-- ---------------------------------------------------------------------------- assessments
CREATE TABLE IF NOT EXISTS assessments (
    id                 TEXT PRIMARY KEY,
    detection_id       TEXT NOT NULL,      -- FK yok: core-svc yazımı asenkron, sıralama garanti değil
    zone_id            TEXT NOT NULL,
    risk_level         TEXT NOT NULL CHECK (risk_level IN ('LOW', 'MEDIUM', 'HIGH')),
    confidence         REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    rationale          TEXT NOT NULL,
    -- açıklanabilirlik: boş olamaz  [{source, weight, summary, trust, effect}]
    evidence_breakdown JSONB NOT NULL
        CHECK (jsonb_typeof(evidence_breakdown) = 'array' AND jsonb_array_length(evidence_breakdown) > 0),
    tool_calls_log     JSONB NOT NULL DEFAULT '[]'::jsonb,
    reference_time     TIMESTAMPTZ,        -- değerlendirme anı (capture_time); created_at yazıldığı an
    mode               TEXT NOT NULL CHECK (mode IN ('llm', 'rule-based')),
    model              TEXT,
    policy_adjustments JSONB NOT NULL DEFAULT '[]'::jsonb,
    usage              JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS assessments_zone_created_idx ON assessments (zone_id, created_at DESC);
CREATE INDEX IF NOT EXISTS assessments_detection_idx ON assessments (detection_id);
CREATE INDEX IF NOT EXISTS assessments_level_idx ON assessments (risk_level);

-- Bölge başına son değerlendirme
CREATE OR REPLACE VIEW v_latest_assessments AS
SELECT DISTINCT ON (zone_id) *
FROM assessments
ORDER BY zone_id, created_at DESC;

-- ---------------------------------------------------------------------------- seed: drone kaydı
-- mock-data-svc/app/data.py _DRONE_SPECS'in t=0 anlık görüntüsü (üretim: list_drones(0, animate=False))
INSERT INTO drones (id, callsign, status, mode, lat, lon, alt_m, heading_deg, gimbal_pitch_deg, fov_deg, sensor_w, sensor_h, battery_pct)
VALUES
    ('DRN-01', 'KARTAL-1', 'ACTIVE', 'ORBIT', 39.923027, 32.761158, 140.0, 0.0, -60.0, 84.0, 1920, 1080, 78),
    ('DRN-02', 'KARTAL-2', 'ACTIVE', 'ORBIT', 39.918733, 32.784921, 150.0, 285.5, -55.0, 84.0, 1920, 1080, 71),
    ('DRN-03', 'ŞAHİN-3', 'ACTIVE', 'STATION', 39.881912, 32.773585, 120.0, 315.0, -90.0, 84.0, 1920, 1080, 64),
    ('DRN-04', 'ŞAHİN-4', 'STANDBY', 'STATIC', 39.8985, 32.7462, 0.0, 0.0, -45.0, 84.0, 1920, 1080, 100),
    ('DRN-05', 'KARTAL-5', 'ACTIVE', 'ORBIT', 39.881064, 32.72705, 130.0, 222.5, -60.0, 84.0, 1920, 1080, 58),
    ('DRN-06', 'BAYKUŞ-6', 'ACTIVE', 'ORBIT', 39.91763, 32.715686, 110.0, 319.9, -70.0, 84.0, 1920, 1080, 83),
    ('DRN-07', 'BAYKUŞ-7', 'RTB', 'STATIC', 39.9075, 32.759, 90.0, 225.0, -30.0, 84.0, 1920, 1080, 17),
    ('DRN-08', 'ŞAHİN-8', 'OFFLINE', 'STATIC', 39.8992, 32.7511, 0.0, 0.0, -90.0, 84.0, 1920, 1080, 0)

ON CONFLICT (id) DO NOTHING;

UPDATE drones SET geom = ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography WHERE geom IS NULL;
