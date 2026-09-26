"""Demo veri seti: zone tanımları, istihbarat, saha raporları ve drone kaydı.

Tüm koordinatlar KURGUSALDIR. Demo senaryosu (ZONE-ALPHA):
  - Üsse güneydoğudan (bearing 135°) 3 araçlık bir kafile doğrudan yaklaşıyor
    (CONVOY + DIRECT_APPROACH).
  - Kuzeybatıda tek bir pick-up saatlerdir aynı noktada duruyor (LOITERING).
  - Güneybatıda sivil bir araç rastgele dolaşıyor (RANDOM).
Araç hareket verisi core-svc tarafında üretilir; burada yalnızca istihbarat/rapor ve
drone kaydı vardır. DRN-03'ün konumu, core-svc demo senaryosundaki kafile ile
detection-svc mock sahnesiyle birebir uyumludur (bkz. README "Demo senaryosu").
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

R_EARTH = 6371008.8

BASE: dict[str, Any] = {
    "name": "Üs Alfa",
    "lat": 39.9000,
    "lon": 32.7500,
    "radius_m": 1500,
    "alert_radius_m": 8000,
}

ZONES: dict[str, dict[str, Any]] = {
    "ZONE-ALPHA": {
        "zone_id": "ZONE-ALPHA",
        "name": "Güneydoğu yaklaşma koridoru (demo: kafile)",
        "base": BASE,
    },
    "ZONE-BRAVO": {
        "zone_id": "ZONE-BRAVO",
        "name": "Kuzey sektör (sakin)",
        "base": BASE,
    },
    "ZONE-CHARLIE": {
        "zone_id": "ZONE-CHARLIE",
        "name": "Batı tarım sahası (çelişkili raporlar)",
        "base": BASE,
    },
}

# (yaş_dakika, kaynak, metin) — güven her zaman "low"
_INTEL: dict[str, list[tuple[int, str, str]]] = {
    "ZONE-ALPHA": [
        (
            38,
            "SIGINT-parça",
            "Kimliği belirsiz bir frekansta kısa telsiz trafiği; 'kapıya yirmi dakika' benzeri bir ifade "
            "duyulmuş olabilir. Kayıt kalitesi düşük, çözümleme güvenilir değil.",
        ),
        (
            95,
            "HUMINT-anon-17",
            "Anonim kaynak, güneydoğu yolunda 3-4 araçlık yüklü bir kafile hareketi planlandığını "
            "söylüyor. Kaynağın geçmiş doğruluk oranı düşük.",
        ),
        (
            140,
            "OSINT-sosyal-medya",
            "Bir kullanıcı sabah saatlerinde köy yolunda 'askeri görünümlü' pick-up ve kamyon gördüğünü "
            "paylaşmış. Fotoğraf doğrulanamadı, konum etiketi yok.",
        ),
        (
            210,
            "HUMINT-yerel",
            "Yerel muhtar, bugün köyde düğün olduğunu ve yollarda misafir araçlarının yoğun olabileceğini "
            "belirtiyor (yanlış pozitif kaynağı olabilir).",
        ),
        (
            2880,
            "arşiv-derleme",
            "Geçen hafta aynı güzergâhta tarımsal sevkiyat yapan kamyonlar görülmüştü.",
        ),
    ],
    "ZONE-BRAVO": [
        (
            120,
            "OSINT-haber",
            "Bölgede olağan tarımsal faaliyet sürüyor; olağandışı bir hareketlilik bildirilmedi.",
        ),
        (
            600,
            "HUMINT-yerel",
            "Köy kahvesinde bölgeye yabancı araç girmediği konuşuluyor.",
        ),
    ],
    "ZONE-CHARLIE": [
        (
            55,
            "HUMINT-anon-04",
            "Batı yolunda silah taşındığı iddiası; başka hiçbir kaynakla örtüşmüyor.",
        ),
        (
            75,
            "OSINT-sosyal-medya",
            "Aynı bölgede traktör konvoyu görüntüsü paylaşılmış; tarım sezonu açılışı olabilir.",
        ),
        (
            180,
            "SIGINT-parça",
            "Kısa süreli GSM aktivitesi artışı; bölgede pazar günü olduğu için olağan olabilir.",
        ),
    ],
}

# (yaş_dakika, bildiren, metin)
_REPORTS: dict[str, list[tuple[int, str, str]]] = {
    "ZONE-ALPHA": [
        (
            9,
            "Devriye Timi-2 (Çvş. K.)",
            "Güneydoğu yolunda 3 araç art arda, aralarında yaklaşık 50 metre mesafe ile üsse doğru "
            "ilerliyor. Hızları düşük değil; farları yanık.",
        ),
        (
            34,
            "Gözetleme Kulesi-4",
            "Kuzeybatıda tek bir pick-up uzun süredir aynı noktada duruyor. Sürücü araçtan inmedi.",
        ),
        (
            72,
            "Sivil ihbar hattı",
            "Bir çiftçi, yolda traktörün arızalandığını ve yolun yer yer tıkalı olduğunu bildirdi.",
        ),
        (
            160,
            "Nöbetçi Kapı-2",
            "Şüpheli bir hareket gözlenmedi, olağan sivil trafik.",
        ),
    ],
    "ZONE-BRAVO": [
        (
            25,
            "Devriye Timi-1",
            "Sektör sakin. Olağan çiftçi trafiği dışında hareket yok.",
        ),
        (
            140,
            "Gözetleme Kulesi-1",
            "Görüş açık, bölgede hareketli hedef bulunmuyor.",
        ),
    ],
    "ZONE-CHARLIE": [
        (
            20,
            "Devriye Timi-3",
            "Batı yolunda iki traktör ve bir kamyonet görüldü; araçlar tarım alanına girdi.",
        ),
        (
            48,
            "Sivil ihbar hattı",
            "Arayan kişi 'şüpheli bir minibüs' gördüğünü söyledi ancak plaka veya konum veremedi.",
        ),
    ],
}


def _dest(lat: float, lon: float, bearing_deg: float, dist_m: float) -> tuple[float, float]:
    b = math.radians(bearing_deg)
    d = dist_m / R_EARTH
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), math.degrees(l2)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def list_zones() -> list[dict[str, Any]]:
    return list(ZONES.values())


def get_zone(zone_id: str) -> dict[str, Any] | None:
    return ZONES.get(zone_id.upper())


def get_intel(zone_id: str, now: datetime | None = None) -> list[dict[str, Any]] | None:
    zid = zone_id.upper()
    if zid not in _INTEL:
        return None
    now = now or datetime.now(timezone.utc)
    return [
        {"text": text, "source": source, "ts": _iso(now - timedelta(minutes=age)), "confidence": "low"}
        for age, source, text in _INTEL[zid]
    ]


def get_reports(zone_id: str, now: datetime | None = None) -> list[dict[str, Any]] | None:
    zid = zone_id.upper()
    if zid not in _REPORTS:
        return None
    now = now or datetime.now(timezone.utc)
    return [
        {"text": text, "reporter": reporter, "ts": _iso(now - timedelta(minutes=age))}
        for age, reporter, text in _REPORTS[zid]
    ]


# --------------------------------------------------------------------------- drone kaydı
_COMMON = {"fov": 84.0, "sensor_w": 1920, "sensor_h": 1080}

# mode: STATION (sabit hover), ORBIT (üs çevresinde devriye), STATIC (yerde / sabit)
# center: üsse göre (bearing_deg, mesafe_m)
_DRONE_SPECS: list[dict[str, Any]] = [
    dict(id="DRN-01", callsign="KARTAL-1", status="ACTIVE", mode="ORBIT", center=(10, 2600), radius=500,
         period=480, phase=0.0, alt=140.0, gimbal=-60.0, battery=78, **_COMMON),
    dict(id="DRN-02", callsign="KARTAL-2", status="ACTIVE", mode="ORBIT", center=(60, 3300), radius=450,
         period=420, phase=1.3, alt=150.0, gimbal=-55.0, battery=71, **_COMMON),
    # DEMO DRONE: kafilenin (V-102) tam üstünde, kuzeye (315°) bakan nadir görüntü.
    dict(id="DRN-03", callsign="ŞAHİN-3", status="ACTIVE", mode="STATION", pos=(39.881912, 32.773585),
         heading=315.0, alt=120.0, gimbal=-90.0, battery=64, **_COMMON),
    dict(id="DRN-04", callsign="ŞAHİN-4", status="STANDBY", mode="STATIC", pos=(39.8985, 32.7462),
         heading=0.0, alt=0.0, gimbal=-45.0, battery=100, **_COMMON),
    dict(id="DRN-05", callsign="KARTAL-5", status="ACTIVE", mode="ORBIT", center=(215, 2900), radius=400,
         period=390, phase=2.4, alt=130.0, gimbal=-60.0, battery=58, **_COMMON),
    dict(id="DRN-06", callsign="BAYKUŞ-6", status="ACTIVE", mode="ORBIT", center=(300, 3600), radius=250,
         period=300, phase=0.7, alt=110.0, gimbal=-70.0, battery=83, **_COMMON),
    dict(id="DRN-07", callsign="BAYKUŞ-7", status="RTB", mode="STATIC", pos=(39.9075, 32.7590),
         heading=225.0, alt=90.0, gimbal=-30.0, battery=17, **_COMMON),
    dict(id="DRN-08", callsign="ŞAHİN-8", status="OFFLINE", mode="STATIC", pos=(39.8992, 32.7511),
         heading=0.0, alt=0.0, gimbal=-90.0, battery=0, **_COMMON),
]


def _drone_state(spec: dict[str, Any], t: float) -> dict[str, Any]:
    if spec["mode"] == "ORBIT":
        clat, clon = _dest(BASE["lat"], BASE["lon"], spec["center"][0], spec["center"][1])
        theta = 2 * math.pi * (t % spec["period"]) / spec["period"] + spec["phase"]
        east = spec["radius"] * math.cos(theta)
        north = spec["radius"] * math.sin(theta)
        lat = clat + math.degrees(north / R_EARTH)
        lon = clon + math.degrees(east / (R_EARTH * math.cos(math.radians(clat))))
        # saat yönünün tersine dönüş: teğet yönü
        heading = math.degrees(math.atan2(-math.sin(theta), math.cos(theta))) % 360.0
    else:
        lat, lon = spec["pos"]
        heading = spec["heading"]
    return {
        "id": spec["id"],
        "callsign": spec["callsign"],
        "lat": round(lat, 6),
        "lon": round(lon, 6),
        "heading": round(heading, 1),
        "fov": spec["fov"],
        "status": spec["status"],
        "mode": spec["mode"],
        "alt": spec["alt"],
        "gimbal_pitch": spec["gimbal"],
        "sensor_w": spec["sensor_w"],
        "sensor_h": spec["sensor_h"],
        "battery_pct": spec["battery"],
    }


def list_drones(t: float, animate: bool = True) -> list[dict[str, Any]]:
    """`animate=False` ORBIT drone'larını t=0 konumunda dondurur (deterministik testler için)."""
    return [_drone_state(s, t if animate else 0.0) for s in _DRONE_SPECS]
