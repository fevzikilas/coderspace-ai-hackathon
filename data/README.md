# data/ — olay (event snapshot) veri seti

Sistem "canlı akan zaman" ile değil **olay anı** ile çalışır: her görüntü tek bir olaydır; değerlendirme, görüntünün
`capture_time`'ı (= `reference_time`) anındaki bilgiyle yapılır. O andan sonraki iz noktaları, saha raporları vb.
**hiçbir yerde kullanılmaz**. Duvar saati (`now()`) yalnızca eski/demo akışında (`DEMO_MODE=true`) kullanılır.

```
data/
├── REAL/            ← RESMÎ veri (gorev_tanimi.pdf, image_meta.json, zones.json, tracks.csv, field_reports.json, images/ x40)
├── synthetic/       ← sentetik 40 olaylık set (scenarios.yaml'dan üretilir; ground_truth.json + expected.json içerir)
└── image_meta.json  zones.json  tracks.csv  field_reports.json   ← ilk verilen ŞEMA ÖRNEKLERİ (bozuk JSON; dokunulmaz, YÜKLENMEZ)
```

`DATA_DIR` bir veri klasörünü gösterir (compose varsayılanı `/data/synthetic`; resmî veri için `DATA_DIR=/data/REAL`).
Yeniden üretim (sentetik): `pip install -r scripts/requirements.txt && python scripts/gen_dataset.py`.

## Resmî veri (`data/REAL/`) — ölçülen özellikler

| | |
|---|---|
| Görüntü | 40 adet (16 × 960×540, 19 × 1360×765, 5 × 1920×1080), çekim saatleri 10:10–15:50, hepsi 5 dk ızgarasında ve benzersiz; ayak izi 107–369 m genişlik |
| Köşeler | 40'ının tamamı eksene paralel (üst kenar kuzey, sol kenar batı) → resmî formül ile eski tam-bilinear arasında **0.0000 m** fark |
| `tracks.csv` | 5650 satır, **226 track_id**, her biri 25 nokta (2 saat, 5 dk adım); 226 iznin **hepsi** bir görüntünün çekim saatinde biter (görüntü başına 3–10 iz); bunların 206'sı ayak izinin içinde, 20'si dışında |
| İz hızları | son 30 dk ortalaması: 48 iz duruyor (<0.5 m/s), 4 iz 0.5–0.8, 174 iz ≥0.8 m/s; en hızlısı 4.8 m/s. İz sonlarının üsse uzaklığı 1554–5509 m (üs sınırı içinde iz yok) |
| `zones.json` | 1 üs (`Merkez Us`) + 8 bölge merkezi (üsse ~3.2 km, 45° aralıklı) |
| `field_reports.json` | **137** rapor (98 official, 39 third_party). Konum: 72'si koordinat, 43'ü bölge adı, **22'si konumsuz** (3 benzersiz genel metin: hava durumu, lojistik konvoyu ikmali, planlı tatbikat). Resmî kalıp `\d+\.\d+N\s+\d+\.\d+E` ile bizim çıkarım arasında **0 fark**; 8 bölge adıyla tam eşleşme = bizim toleranslı kalıp (0 kaçırılan, 0 fazla) |
| Doğruluk etiketi | **yok**: `official` yalnızca kaynak türüdür; her iki türde de doğru ve yanıltıcı raporlar vardır (ör. "planli ikmal aracidir, kimlik teyidi yapilmistir"). Politika: çelişkide kendi tespit+hareket verisi esastır |

## Dosya şemaları (KATI: bilinmeyen alan, tür dönüşümü, geçersiz değer → hata)

`image_meta.json` — anahtar `image_id` (`[A-Za-z0-9_.-]{1,80}`):
```json
{
  "img_003839": {
    "width_px": 960,
    "height_px": 540,
    "capture_time": "13:25",
    "corner_coordinates": {
      "top_left": [39.93778, 32.847904],
      "top_right": [39.93778, 32.849658],
      "bottom_left": [39.937024, 32.847904],
      "bottom_right": [39.937024, 32.849658]
    }
  }
}
```

`zones.json`:
```json
{
  "base": {"name": "Merkez Us", "lat": 39.92184, "lon": 32.85306},
  "zones": [{"name": "Kuzey Yolu", "center": [39.950586, 32.85306]}]
}
```
(`base` içinde isteğe bağlı `radius_m`, `alert_radius_m`. `zone_id` addan türetilir: `Kuzey Yolu` → `kuzey-yolu`; aynı slug'a düşen iki ad reddedilir.)

`field_reports.json` (boş liste geçerlidir; yalnızca bu üç alan):
```json
[
  {"time": "13:05", "source": "official", "text": "39.9374N 32.8483E civarinda 1 kamyon goruldu, yukleri tespit edilemedi."},
  {"time": "10:05", "source": "third_party", "text": "Kuzeybati Yolu bolgesinde trafik akisi normal seyrediyor."}
]
```

`tracks.csv` — başlık BİREBİR `track_id,time,lat,lon`; `time` `HH:MM`; iz içinde saat artan sırada, (track_id, time) tekrarı yok:
```csv
track_id,time,lat,lon
T0001,10:15,39.988691,32.880750
T0001,10:20,39.978233,32.885015
```

`ground_truth.json` (yalnızca mock dedektörü besler; isteğe bağlı): `{"img_000860": {"width_px": 960, "height_px": 540, "boxes": [{"class": "car", "conf": 0.9, "x1": 10, "y1": 20, "x2": 40, "y2": 60, "track": "T0001"}]}}` (`track` isteğe bağlı etiket; kutular görüntü içinde).

## Yöntem (resmî spesifikasyon)

- **Piksel → koordinat:** `boylam = sol_üst.boylam + (x/genişlik_px)·(sağ_üst.boylam − sol_üst.boylam)`, `enlem = sol_üst.enlem + (y/yükseklik_px)·(sol_alt.enlem − sol_üst.enlem)`. `sağ_alt` kullanılmaz. Araç konumu = kutu merkezi.
- **İz eşleştirme:** önce `time == capture_time` satırları süzülür, sonra tespite en yakın olan `MATCH_GATE_M` içinde eşlenir; eşleşmeyen "iz bulunamadı" (hata değil).
- **Veri tek havuzdur, görüntüye bağlı değildir:** izler ve raporlar çekim saati + konumdan süzülür (bölge ataması: en yakın bölge merkezi; raporda koordinat → en yakın bölge, raporda bölge adı → o bölge, ikisi de yoksa genel).
- **Okuma KATIdır** (`loaders/strict.py`): bozuk dosya servisi düşürür ve `dosya:alan` içeren hata verir. Bozuk bir dosyayı bilinçli onarmak için: `python scripts/repair_dataset.py <girdi> <çıktı>`.

## Saat mantığı

`"14:10"` gibi saatler tarihsizdir; epoch'a çevirmek için tek bir sabit gün kullanılır (`DATASET_DATE`, varsayılan `2025-06-01`,
**tüm servislerde aynı**). Yalnızca sıralama/fark için kullanılır. Bir izde saat geriye sararsa (gece yarısı) gün +1 kabul edilir.
ISO-8601 zamanlar da kabul edilir (tarihli veri için `DATASET_DATE` gerekmez).

## Resmî veriyle çalıştırma

1. Yerel `.env` (git'e girmez): `DATA_DIR=/data/REAL MOCK_MODE=false MODEL_BACKEND=dfine MODEL_PATH=/srv/models/dfine.pt DETECTION_WITH_MODEL=dfine`, sonra `docker compose up --build -d`.
   Servisler dosyaları KATI doğrular; resmî dosyalar ilk denemede geçti. Ağırlık `detection-svc/models/dfine.pt` (imaja gömülmez, volume). Sentetik/mock'a dönmek için `.env`'i silin.
2. **Tespit:** `data/REAL` içinde `ground_truth.json` YOKTUR; mock dedektör bu klasörde anlamsız kutular üretir, bu yüzden gerçek veri için `MOCK_MODE=false` gerekir. Model yüklenemezse servis açılmaz
   (`MOCK_FALLBACK=true` verilirse mock'a düşer ve `/health` `degraded` olur). Ölçülen (conf ≥ 0.25): 40 görüntüde 423 kutu, ort. conf 0.613; varsayılan `CONF_THRESHOLD=0.4`: 287 kutu (198 izli, 89 izsiz), ort. conf 0.755; çıkarım ~2.3 sn/görüntü (CPU).
3. Kabul: sentetik sette `python scripts/eval_events.py` (40/40). Resmî veride beklenen etiket yoktur (gizli).
4. Veri seti değiştirirken Postgres'i de temizleyin (iz kimlikleri çakışır): `TRUNCATE assessments, detection_objects, detections, tracks, vehicles RESTART IDENTITY CASCADE;` ve core-svc'yi yeniden başlatın.
