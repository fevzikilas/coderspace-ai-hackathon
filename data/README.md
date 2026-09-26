# data/ — olay (event snapshot) veri seti

Sistem "canlı akan zaman" ile değil **olay anı** ile çalışır: her görüntü tek bir olaydır; değerlendirme, görüntünün
`capture_time`'ı (= `reference_time`) anındaki bilgiyle yapılır. O andan sonraki iz noktaları, saha raporları vb.
**hiçbir yerde kullanılmaz**. Duvar saati (`now()`) yalnızca eski/demo akışında (`DEMO_MODE=true`) kullanılır.

```
data/
├── image_meta.json  zones.json  tracks.csv  field_reports.json   ← kullanıcının verdiği ŞEMA ÖRNEKLERİ (dokunulmaz)
└── synthetic/                                                     ← sentetik 40 olaylık veri seti (DATA_DIR varsayılanı)
    ├── scenarios.yaml      KAYNAK TANIM (8 bölge x 5 olay; hareket senaryoları, raporlar, beklenen sonuçlar)
    ├── image_meta.json  zones.json  tracks.csv  field_reports.json   ← şema örnekleriyle aynı biçim
    ├── images/img_XXXXXX.jpg   960x540 sentetik hava görüntüleri
    ├── ground_truth.json       görüntüdeki araçların GERÇEK bbox'ları (gerçek YOLO gelene kadar mock dedektörü besler)
    └── expected.json           olay başına tasarım niyeti (desen + risk) — scripts/eval_events.py ile karşılaştırılır
```

Yeniden üretim (deterministik): `pip install -r scripts/requirements.txt && python scripts/gen_dataset.py`.

## Dosya şemaları

| Dosya | Biçim | Not |
|---|---|---|
| `image_meta.json` | `{"img_000860": {"width_px": 960, "height_px": 540, "capture_time": "14:10", "corner_coordinates": {"top_left": [lat, lon], "top_right": [...], "bottom_left": [...], "bottom_right": [...]}}}` | Köşeler eksene paralel dikdörtgen (top_left/top_right aynı enlem, sol köşeler aynı boylam) varsayılır; piksel→GPS **bilinear** interpolasyonla, bbox **merkezinden** hesaplanır. Dış `{}` eksik olsa da okunur. |
| `tracks.csv` | `track_id,time,lat,lon` (örn. `T0001,10:15,39.988691,32.880750`) | ~5 dk adım. Dosya capture_time'dan SONRAKİ noktaları da içerebilir; sistem bunları keser. |
| `zones.json` | `{"base": {"name","lat","lon"[,"radius_m","alert_radius_m"]}, "zones": [{"name","center":[lat,lon]}]}` | **Tek üs**; "bölge"ler onun etrafındaki yol/sektör merkezleridir. `zone_id` addan türetilir (`Kuzey Yolu` → `kuzey-yolu`). Üs yarıçapı yoksa `BASE_RADIUS_M` (1500 m). Bir görüntü, konumuna en yakın bölge merkezine atanır. |
| `field_reports.json` | `[{"time": "13:05", "source": "official" \| "third_party", "text": "..."}]` | Bölge alanı YOK. İlişki: metinde koordinat varsa (`39.9374N 32.8483E`) en yakın bölge / olay yakınlığı; koordinat yoksa metinde geçen yol adı (`Kuzey yolunda …`); ikisi de yoksa genel bilgi. `source` düşük güven içinde ayrı tutulur (resmî / üçüncü taraf). |

Dosyalar **toleranslı** okunur (sondaki virgül, dizisiz nesne akışı, string içinde satır sonu, dış `{}` eksikliği onarılır);
biçim hatalı bir dosya servisi düşürmez, `/health` → `dataset.errors` içinde raporlanır.

## Saat mantığı

`"14:10"` gibi saatler tarihsizdir; epoch'a çevirmek için tek bir sabit gün kullanılır (`DATASET_DATE`, varsayılan `2025-06-01`,
**tüm servislerde aynı**). Yalnızca sıralama/fark için kullanılır. Bir izde saat geriye sararsa (gece yarısı) gün +1 kabul edilir.
ISO-8601 zamanlar da kabul edilir (tarihli veri için `DATASET_DATE` gerekmez).

## Gerçek veriye geçiş

1. Dosyaları `data/` altına (veya başka bir klasöre) koyun: `image_meta.json`, `tracks.csv`, `zones.json`, `field_reports.json`, `images/<image_id>.jpg`.
2. `DATA_DIR`'i o klasöre çevirin (compose: `DATA_DIR=/data`; yerel: `DATA_DIR=$PWD/data`).
3. Gerçek Stage 1 modeli için `detection-svc/models/stage1.pt` + `MOCK_MODE=false`; yoksa mock, `ground_truth.json` varsa onu, yoksa rastgele kutular üretir.
   (`ground_truth.json` biçimi: `{"img_000860": {"width_px": 960, "height_px": 540, "boxes": [{"class","conf","x1","y1","x2","y2"}]}}`.)
4. Kabul için: `python scripts/eval_events.py --expected <kendi expected.json'unuz>`.
