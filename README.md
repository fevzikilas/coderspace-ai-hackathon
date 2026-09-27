# Üs Koruma Sistemi — Tehlike Alarm Sistemi

Üs çevresine yaklaşan araçların **tehlike durumunu analiz eden erken uyarı / alarm sistemi**. Drone görüntüsü, araç takip verisi
(`tracks.csv`) ve (güvenilmez) saha raporları birleştirilir; **gerekçeli** bir risk alarmı (`LOW` / `MEDIUM` / `HIGH`) üretilir.

> **Olay anı (event snapshot) mantığı:** her görüntü tek bir olaydır. Değerlendirme, görüntünün `capture_time`'ı anındaki bilgiyle
> yapılır; o andan sonraki iz noktaları ve saha raporları **kullanılmaz**. Sistem "canlı akan zaman" ile çalışmaz —
> `now()` yalnızca eski/demo akışında (`DEMO_MODE=true`) vardır.
>
> **Karar önceliği:** kendi tespit + hareket verisi **>** dış istihbarat / saha raporu. Bu öncelik yalnızca LLM promptunda değil,
> kod düzeyinde de zorlanır (bkz. [Risk motoru](#risk-motoru)).

---

## İçindekiler
1. [Hızlı başlangıç](#hızlı-başlangıç) · 2. [Mimari](#mimari) · 3. [Veri akışı](#veri-akışı) · 4. [Veri seti](#veri-seti-ve-zaman-mantığı)
5. [Servisler ve API](#servisler-ve-api) · 6. [Risk motoru](#risk-motoru) · 7. [Arayüz](#arayüz) · 8. [Bağımsız çalıştırma](#servisleri-bağımsız-çalıştırma)
9. [Docker / Kubernetes](#docker--kubernetes) · 10. [Yapılandırma](#yapılandırma) · 11. [Test](#test) · 12. [Doğrulama durumu](#doğrulama-durumu)
13. [Güvenlik](#güvenlik-notları) · 14. [Sınırlamalar](#sınırlamalar-ve-bilinen-noktalar) · 15. [Klasör yapısı](#klasör-yapısı)

---

## Hızlı başlangıç

```bash
docker compose up --build -d            # db + 6 servis + web-ui (ilk derleme birkaç dakika sürer)
docker compose ps
# Karşılama: http://localhost:8080/        (simülasyon; HERKESE AÇIK, sağ üstte 'Giriş')
# UI:       http://localhost:8080/panel   (giriş gerekli; anahtar SORMAZ: web-ui'nin nginx'i gateway anahtarını sunucu tarafında ekler; doğrudan :8000 için X-API-Key gerekir, varsayılan dev-key-change-me)
# Gateway:  http://localhost:8000
python scripts/eval_events.py --api-key dev-key-change-me    # 40 olayın tamamını değerlendirir, beklenenle karşılaştırır
```

UI'da: soldaki **Olay seçici**den bir görüntü seç → **▶ değerlendir** (veya çift tıkla) → haritada aracın hesaplanan konumu ve geçmiş izi,
görüntüde bbox'lar, sağda risk kartı (LOW/MEDIUM/HIGH + gerekçe + kanıt dağılımı + araç çağrısı zaman çizelgesi). Başlıkta
**"14:10 itibarıyla değerlendirme"** görünür. Kendi görüntünüzü yüklemek için dosya adı bir `image_id` olmalıdır (ör. `img_000860.jpg`).

LLM anahtarı yoksa risk ajanı kural tabanlı çalışır (aynı çıktı şeması). **Birincil LLM sağlayıcısı OpenRouter** (`nvidia/nemotron-3-ultra-550b-a55b:free`, `OPENROUTER_API_KEY`); GLM (organizatör gateway'i) ikincil olarak kod yolunda durur (`LLM_PROVIDER=glm`). Ayrıntı: "LLM sağlayıcısı" bölümü.

## Mimari

Servisler birbirine **yalnızca HTTP** ile bağlıdır; her biri kendi klasöründe, kendi `requirements.txt`/`package.json` ve `Dockerfile`'ı ile
bağımsız çalışır. UI yalnızca `gateway`'e konuşur.

```
                       ┌─────────────────────────────────────────────┐
     Tarayıcı ───────► │ web-ui   React + Vite + Leaflet             │  :8080 (nginx) · :5173 (dev)
                       │ olay seçici · bbox görüntü · harita · risk   │
                       └──────────────────────┬──────────────────────┘
                                              │  /api/*  ·  polling (2 sn)  ·  X-API-Key
                                              ▼
                       ┌─────────────────────────────────────────────┐
                       │ gateway (BFF)                        :8000  │  tek origin · tek auth katmanı
                       │ orkestrasyon · agregasyon · reverse-proxy   │  POST /pipeline/run · GET /events · /dashboard/state
                       └──┬────────┬─────────┬────────┬───────────┬──┘
                          │        │         │        │           │        (yalnızca HTTP)
            ┌─────────────┘        │         │        │           └─────────────┐
            ▼                      ▼         ▼        ▼                         ▼
   ┌────────────────┐   ┌────────────────┐ ┌────────────────┐ ┌────────────────┐ ┌──────────────────────────┐
   │ detection-svc  │   │ core-svc       │ │ pattern-svc    │ │ mock-data-svc  │ │ risk-agent-svc           │
   │ :8001          │   │ :8002          │ │ :8003          │ │ :8004          │ │ :8005                    │
   │ D-fine Stage 1   │   │ image_meta +   │ │ kural tabanlı  │ │ üs · bölgeler  │ │ LLM tool-calling         │
   │ / mock (ground │   │ tracks.csv     │ │ CONVOY         │ │ saha raporları │ │ + politika (taban/tavan) │
   │  truth bbox)   │   │ köşe-georef    │ │ DIRECT_APPROACH│ │ (as_of süzme)  │ │ + bütçe/kota             │
   └────────────────┘   │ iz eşleştirme  │ │ LOITERING      │ └────────────────┘ │ + kural tabanlı fallback │
                        │ hareket analizi│ │ RANDOM         │                    └──────────────────────────┘
                        └────────────────┘ └────────────────┘

   Servisler arası çağrılar (hepsi HTTP; oklar "çağırır" yönündedir):
     pattern-svc    ──► core-svc                          GET  /tracks/{id}?until=<olay anı>
     risk-agent-svc ──► core-svc                          GET  /detections/{id} · POST /tracks/analyze
     risk-agent-svc ──► pattern-svc                       POST /pattern/classify
     risk-agent-svc ──► mock-data-svc                     GET  /intel/{zone} · /reports/{zone}?as_of=<olay anı>
     risk-agent-svc ──► LLM API (OpenRouter)               chat/completions (tool calling)
     core-svc · risk-agent-svc ─ ─► db (PostgreSQL+PostGIS :5432)   yazma (write-through); core açılışta tracks.csv'yi senkronlar
     core-svc · mock-data-svc · detection-svc ◄── ./data (salt-okunur volume: image_meta.json, tracks.csv, zones.json, field_reports.json, images/)
```

## Veri akışı

`POST /pipeline/run {"image_id": "img_000860"}` (gateway) zinciri sırayla yürütür; `wait=false` verilirse `202 + run_id` döner ve ilerleme
`GET /dashboard/state` içindeki `latest_run.steps` ile izlenir (UI böyle çalışır).

```
UI ─► gateway ─┬─1 event_context  GET  core-svc /dataset/images/{id}     → capture_time, köşe koordinatları, ayak izi
               │                  GET  mock-data-svc /base · POST /zones/assign → korunan üs; görüntünün bölgesi (en yakın bölge merkezi)
               ├─2 detect         POST detection-svc /detect {image_id}  → boxes[] (+ image_width/height)
               ├─3 georeference   POST core-svc /georeference            → bbox MERKEZİ → GPS (resmî formül, aşağıda);
               │                                                           tespitleri YALNIZCA time == capture_time olan tracks.csv satırlarına,
               │                                                           mesafe kapısı içinde en yakın olana eşler
               ├─4 tracks         GET  core-svc /tracks/{id}?until=ref    ┐ her araç için iz (ref'e kadar) + hız/yön/yaklaşma/ETA;
               │                  POST core-svc /tracks/analyze          ┘ görüntüdeki güncel konum iz sonuna eklenir
               ├─5 pattern        POST pattern-svc /pattern/classify     → CONVOY | DIRECT_APPROACH | LOITERING | RANDOM
               └─6 assess         POST risk-agent-svc /assess            → risk_level, rationale, confidence,
                                     │                                     evidence_breakdown[], tool_calls_log[]
                                     ├─ GET  core-svc /detections/{id}    (bağlam: araçlar, reference_time)
                                     ├─ tool get_movement_analysis        → core-svc  /tracks/analyze
                                     ├─ tool get_pattern_classification   → pattern-svc /pattern/classify
                                     ├─ tool get_intel · get_reports      → mock-data-svc  (as_of = olay anı; DÜŞÜK GÜVEN)
                                     └─ tool get_drone_context            → yalnızca eski akış (olayda drone kaydı yok)
```

Bir adım hata verirse koşu `failed` olur, hata adımı işaretlenir, kalan adımlar `skipped` görünür. Görüntüde araç yoksa ya da tespit hiçbir izle
eşleşmezse `tracks/pattern/assess` atlanır ve koşu `succeeded` kalır (`result.message` açıklar). İzle eşleşmeyen nesneler (ör. park halinde sivil araç)
UI'da gri kesikli kutuyla "izsiz" görünür.

**Eski/demo akış** (`DEMO_MODE=true` + `drone_id`): drone kaydı → kamera modeliyle (`drone_meta`) georef → tracker'a yazma; zaman = şimdi.
UI'da yalnızca `DEMO_MODE` açıkken **⚡ Demo: Kafile** düğmesi görünür. Varsayılan KAPALIDIR.

## Veri seti ve zaman mantığı

Ayrıntı ve şemalar: [data/README.md](data/README.md).

- **Resmî veri (`data/REAL/`, `gorev_tanimi.pdf` ile birlikte):** 40 görüntü, 226 iz (her biri 25 noktalı, 2 saatlik; **her iz ait olduğu görüntünün çekim anında biter**),
  8 bölge + 1 üs, 137 saha raporu. Dosyalar **katı** doğrulanır (aşağıda) ve ilk denemede geçti. Tespit için ise henüz gerçek dedektör bağlı değil (bkz. Doğrulama durumu).
- **Katı okuma:** loader'lar artık toleranslı değildir. Sondaki virgül, eksik parantez, yinelenen anahtar, `NaN`, bilinmeyen alan, `"960"` gibi tür dönüşümü, hatalı saat/koordinat,
  yinelenen `(track_id, time)`, geriye giden zaman **reddedilir**: servis `dosya:alan` içeren `DatasetError` ile açılmaz. Bozuk bir dosyayı bilinçli onarmak için ayrı bir araç vardır: `python scripts/repair_dataset.py <girdi> <çıktı>` (girdiye dokunmaz, ne düzelttiğini listeler; çıktı yine katı loader'dan geçer).
- **Şema (kullanıcı örnekleriyle aynı):** `image_meta.json` (köşe koordinatları, `capture_time`), `tracks.csv` (`track_id,time,lat,lon`, 5 dk adım),
  `zones.json` (tek üs + bölge merkezleri), `field_reports.json` (`time`, `source: official|third_party`, `text`).
- **Sentetik set** `data/synthetic/`: 8 bölge × 5 görüntü = **40 olay**, `scenarios.yaml`'dan deterministik üretilir (`scripts/gen_dataset.py`):
  kafile yaklaşması, tek araç yaklaşması, üs yakınında/uzağında bekleme, sivil trafik, uzaklaşan kafile, çelişkili raporlarla yavaş yaklaşma, üs sınırı ihlali.
  `ground_truth.json` gerçek bbox'ları tutar ve gerçek D-fine gelene kadar mock dedektörü besler; `expected.json` her olayın tasarım niyetidir.
- **Zaman:** her adım `reference_time` (= `capture_time`) kullanır. İzler `until=ref`'e kadar kesilir, saha raporları `as_of=ref`'e kadar süzülür,
  "yaş" (`age_min`) olay anına göre hesaplanır, `stale` = `ref − son_nokta`. `"14:10"` gibi tarihsiz saatler tek bir sabit güne (`DATASET_DATE`) oturtulur.
- **Bölge ilişkisi:** tek üs vardır; görüntü, konumuna en yakın bölge merkezine atanır. Saha raporları bölgeye bağlı değildir:
  metindeki koordinat en yakın bölgeye, metinde geçen yol adı ("Kuzey yolunda…") o bölgeye bağlanır; ikisi de yoksa genel bilgidir.
- **5 dk'lık seyrek izler:** pattern-svc eşikleri örnekleme adımına **otomatik uyarlanır** (yoğun 15 sn'lik izlerde değişmez); durmuş aracın GPS titremesi yön sayılmaz.

## Servisler ve API

`-` ile yazılan yol ayraçları ilk spesifikasyondan `/` olarak yorumlandı. Ek alanlar sözleşmeyi **genişletir**, bozmaz.

| Servis | Port | Uçlar |
|---|---|---|
| **detection-svc** | 8001 | `POST /detect` `{image_b64\|image_url\|image_id, timestamp}` → `{boxes:[{class,conf,x1,y1,x2,y2}], image_id}` (+`image_width/height`, `mode`) · `GET /images/{image_id}` (yüklenen bayt veya `DATA_DIR/images`) · `GET /health` |
| **core-svc** | 8002 | `POST /georeference` `{image_id, boxes[], (image_meta \| drone_meta \| katalogdaki image_id), reference_time, match_tracks, ingest}` → `{detections:[{class,conf,lat,lon,vehicle_id,match_distance_m}], reference_time, georef_method}` · `GET /tracks/{id}?window=2h&until=` · `POST /tracks/analyze` `{vehicle_id\|coords, base_location, reference_time, current_position}` → `{speed_mps, heading_deg, approaching, eta_min, distance_to_base_m, …}` · `GET /dataset/images[/{id}]` · `GET /dataset/tracks` · `GET /detections/{id}` · `POST /admin/demo/reset` (yalnızca DEMO_MODE) · `POST /detections/{id}/verify-claims` (rapor iddialarını iz+tespitle nicel karşılaştırır) |
| **pattern-svc** | 8003 | `POST /pattern/classify` `{vehicle_ids, zone_id, base_location, reference_time}` → `{pattern, confidence, involved_vehicles, detail}` |
| **mock-data-svc** | 8004 | `GET /base` · `GET /zones[/{id}]` · `POST /zones/assign {points}` · `GET /reports/{zone}?as_of=&lat=&lon=` · `GET /intel/{zone}?as_of=` (**yer tutucu**: veri setinde istihbarat yok) · `GET /drones` (yalnızca demo) |
| **risk-agent-svc** | 8005 | `POST /assess` `{zone_id, detection_id, base_location}` → `risk_level, rationale, confidence, evidence_breakdown[], tool_calls_log[]` (+`reference_time`, `mode`, `policy_adjustments`, `usage`, …) · `GET /assessments` · `GET /budget` |
| **gateway** | 8000 | `POST /pipeline/run` (`image_id` \| `image_id+image_b64` \| `drone_id` demo) · `GET /events` · `GET /pipeline/runs[/{id}]` · `GET /dashboard/state` · `GET /images/{id}` · `/proxy/{svc}/…` · `GET /health` (hepsi ayrıca `/api/…` altında) |

Sözleşme kararları:
- **Georef (RESMÎ FORMÜL — değiştirmeyin):** `boylam = sol_üst.boylam + (x/genişlik_px)·(sağ_üst.boylam − sol_üst.boylam)`, `enlem = sol_üst.enlem + (y/yükseklik_px)·(sol_alt.enlem − sol_üst.enlem)`;
  iki eksen ayrı doğrusal, `sağ_alt` hesaba girmez (imzada bile yok; testle sabit). `x,y` sol üst = (0,0); araç konumu = **bbox merkezi** (`x1+w/2, y1+h/2`).
  **İz eşleştirme (iki aşamalı):** (1) tracks.csv'de `time == capture_time` satırlarını süz — başka anın noktası ya da satırı olmayan izin ekstrapolasyonu asla aday değildir; (2) bunlar arasında tespite en yakın olanı `MATCH_GATE_M` (15 m) içinde birebir açgözlü eşle. Eşleşmeyen tespit "iz bulunamadı"dır (hata değil). Yanıtta `match_method: exact`. `drone_meta` yolu (iğne-deliği kamera, düz zemin; `alt` AGL m, `fov` yatay °, `sensor_w/h` piksel, `gimbal_pitch` 0=ufuk −90=nadir) geri uyumluluk için durur.
- `detection_id`, core-svc'nin bir görüntü karesi için ürettiği **tespit olayı** kimliğidir; risk-agent bağlamını (araçlar, `reference_time`) bundan çeker.
- `eta_min` = araç **üs sınırına** (`radius_m`) ulaşana dek tahmini süre (yaklaşmıyorsa `null`).
- Pattern önceliği: `CONVOY > DIRECT_APPROACH > LOITERING > RANDOM`; eşleşen **tüm** desenler `detail.matched_patterns`, araç başına etiket `detail.per_vehicle` içindedir.
- Zaman damgaları ISO-8601 UTC (`…Z`); veri seti saatleri UI'da UTC gösterilir (kaymasın diye).

## Cross-event vehicle visual candidate evidence

Olay pipeline'ı, izle eşleşmiş her araç bbox'ından frozen ImageNet-pretrained **MobileNetV3-Small** özelliği çıkarır. Özellikler L2-normalize edilir; crop boyutu, alan oranı ve basit sharpness bilgisi ile birlikte detection-svc LRU cache'inde tutulur. Her olay tek snapshot olduğu için mevcut veriyle track başına tek crop vardır; yeni kamera veya super-resolution üretilmez.

Gateway yalnız **farklı event** gözlemlerini karşılaştırır. Pair'in candidate olabilmesi için:

- cosine similarity ≥ `VEHICLE_REID_MIN_SIMILARITY`,
- kaynak zamanı hedef zamandan önce ve gap ≤ `VEHICLE_REID_MAX_TEMPORAL_GAP_S`,
- iki koordinat da varsa düz-çizgi displacement / gap ≤ `VEHICLE_REID_MAX_IMPLIED_SPEED_MPS`,
- hedef track başına en çok `VEHICLE_REID_TOP_K`

olması gerekir. Koordinat kontrolü gerçek yol/topoloji erişilebilirliği değildir; sentetik/kaba koordinatlarda yalnız fiziksel olarak absürt pair'leri eler. Çıktı **daima** `POSSIBLE_SAME_VEHICLE` relation'lı görsel adaydır. Track ID kalıcı identity değildir; A→B ve B→C edge'leri A/B/C için global kimlik veya transitive cluster oluşturmaz.

Pipeline sonucuna geriye uyumlu iki alan eklenir:

```json
{
  "candidate_vehicle_links": [{
    "link_id": "vl-...",
    "source_event_id": "img-a",
    "source_track_id": "T17",
    "target_event_id": "img-b",
    "target_track_id": "T42",
    "relation": "POSSIBLE_SAME_VEHICLE",
    "appearance_similarity": 0.87,
    "temporal_gap_seconds": 420,
    "spatial_distance_m": 1300.0,
    "implied_speed_mps": 3.1,
    "feasibility": {"temporal": true, "spatial": true, "spatial_checked": true},
    "evidence": {"source_crop": {}, "target_crop": {}, "model": "torchvision/mobilenet_v3_small-imagenet1k-v1"}
  }],
  "vehicle_graph": {
    "relation_semantics": "candidate_edges_are_independent_not_identity_clusters",
    "nodes": [],
    "edges": []
  }
}
```

UI'da Leaflet üzerinde amber kesikli edge/node görünümü ve teknik detaylarda **Possible Vehicle Matches** sekmesi vardır. Edge seçimi similarity, Δt, displacement, implied straight-line speed ve iki crop'u gösterir. Risk ajanı bu linkleri yalnız bağlam olarak görür; prompt açıkça “candidate linkage ≠ confirmed identity” ve “tek başına risk yükseltmez” kuralını taşır. Mevcut deterministik risk policy değişmemiştir.

Değerlendirilmiş full run JSON'leri için ground-truth'suz diagnostic:

```bash
python scripts/vehicle_reid_diagnostic.py runs.json --output vehicle-reid-diagnostic.json
```

Bu rapor similarity dağılımı, track başına candidate sayısı ve accepted/rejected pair sayılarını verir. `data/REAL` görüntülerinde pixel bbox/track-crop eşlemesi bulunmadığından bu branch organizatör verisi için ReID accuracy/precision/recall veya güvenilir contact sheet iddiası üretmez.

## Risk motoru

`risk-agent-svc`, LLM'e (OpenAI uyumlu `chat/completions` + tool calling) **beş araç** verir:
`get_movement_analysis`, `get_intel`, `get_reports`, `get_drone_context`, `get_pattern_classification` (+ nihai cevap için `submit_assessment`).
Sistem promptu: detection+movement verisini öncelikli say, CONVOY'da kademeyi yükselt, `evidence_breakdown` doldurmadan cevap verme,
olay anından sonrasına ait bilgi olmadığını bil, saha raporlarında `official`/`third_party` ayrımını gör.

Prompta ek olarak **kod düzeyinde** güvenceler (`app/policy.py`), LLM ne derse desin geçerlidir:

| Güvence | Nasıl |
|---|---|
| Kendi veri > istihbarat | Hareket+patern'den deterministik bir **taban kademe** (`own_data_level`) hesaplanır. LLM kademesi `[taban, min(taban+1, HIGH)]` bandına oturtulur: **aşağı çekemez**; kendi veri `LOW` iken raporlar/istihbarat tek başına en fazla `MEDIUM` yapar |
| CONVOY → kademe yükselt | CONVOY varsa taban bir kademe yükselir (LOW→MEDIUM, MEDIUM→HIGH); CONVOY + yaklaşma ise HIGH. Yükseltme `policy_adjustments`'ta görünür |
| `evidence_breakdown` zorunlu | `submit_assessment` boş/geçersiz `evidence_breakdown` ile **reddedilir**; ağırlıklar toplamı 1'e normalize edilir |
| Güven düzeyi kaynaktan gelir | `trust` LLM'den değil kaynak sınıfından atanır (detection/movement/pattern = high, drone = medium, intel/reports = **low**); düşük güvenli kaynakların toplam ağırlığı ≤ %20 |
| Prompt injection | Rapor/istihbarat metinleri `untrusted_external_content` olarak işaretlenir; sistem promptu bu metinlerdeki talimatlara uymamayı emreder; ayrıca taban/tavan LLM'i etkisiz kılar |
| Araç argüman denetimi | Yalnızca tespitteki `vehicle_id`, değerlendirilen `zone_id` (ve varsa tespiti yapan `drone_id`) sorgulanabilir |

**İkincil sağlayıcı — GLM gateway (gorev_tanimi.pdf):** OpenAI uyumlu; model `glm-5.3-flash`, sohbet `…/v1/chat/completions`. `thinking` parametresi **gönderilmez** (hata verir); düşünme `reasoning_effort` (`low|high|max`, varsayılan `low`) ile ayarlanır.
`max_tokens` ≥ 1000 (düşünme de token'dan düşer; `finish_reason=length` hata sayılır). Cevap `message.content`; `reasoning_content` yalnızca debug logudur. Aynı anda en çok 4 istek (semafor);
429/5xx/ağ hatasında üstel bekleme + `Retry-After` (5 deneme). `400 "Budget has been exceeded"` → kural motoruna düşülür ve sonraki değerlendirmeler LLM'i denemez; `400 "key not allowed…"` → model adı hatası mesajı.

**Bütçe (GLM)** (`app/budget.py`): organizatör modeli **toplam 15 USD, sıfırlanmaz**. Gerçek harcama `GET {kök URL}/key/info` (`spend`/`max_budget`; kök URL `/v1` içermez, `GLM_ROOT_URL` ayrı tutulur) ile okunur;
kalan ≤ `BUDGET_RESERVE_USD` (1) olunca LLM kullanılmaz. Yerel sınırlar: 50 istek/dk, 400K token/dk (takım limitleri 60 / 500K), değerlendirme başına 60K token (sonsuz döngü koruması).
`ON_BUDGET_EXCEEDED=fallback` (varsayılan) kural motoruna düşer, `reject` HTTP 429 döner. Aynı `(zone, detection)` sonucu önbelleğe alınır.

**Kural tabanlı fallback:** LLM anahtarı yoksa, bütçe/günlük kota aşılırsa, 8→**12 tur** dolarsa veya LLM hata verirse aynı politika ve aynı çıktı şemasıyla `mode=rule-based` üretilir.

## Rapor ↔ tespit/iz niceliksel doğrulaması

Resmî görev tanımı: *raporun iddiasını (tip, hareket, sayı) kendi bulgularınızla karşılaştırın; çelişki varsa raporu değil tespitinizi esas alın.* Anahtar kelime eşleşmesi yoktur:

1. **mock-data-svc** (`app/claims.py`) rapor metnini yapılandırılmış iddiaya çevirir: tip (`car|van|truck|bus`), sayı, hareket (`stationary|toward_base|away|moving`), süre, kimlik/dostluk iddiası, renk, bölge düzeyi iddia
   (`zone_no_heavy`, `zone_activity`) veya bağlam (hava, telsiz, plan, geçmiş ihbar). Gerçek 137 raporun 45 şablonunun hepsi sınıflanır (72 araç iddiası · 16 bölge sakinliği · 6 ağır-araç-yok · 43 bağlam). `/reports/{zone}` öğeleri `id` + `claim` taşır.
2. **core-svc** (`app/verify.py`, `POST /detections/{id}/verify-claims`) iddiayı **çekim anına** göre karşılaştırır. Resmî örnekte 12:40 tarihli rapor 13:25'teki tespitle karşılaştırılır: rapor konumu aracın *görüntüdeki* konumudur, rapor saati yalnızca "önceden bildirildi" demektir
   (gerçek veride ölçüldü: 72 koordinatlı raporun 71'i rapor sonrası bir D-FINE tespitine ≤70 m, medyan 3 m; rapor saatindeki iz konumuna ise 20'si ≥200 m uzaktı).
   - Bir rapor **yalnızca konumu bu görüntünün ayak izi içindeyse** bu görüntüdeki aracı anlatır; dışındakiler `irrelevant` (başka çekimdeki araç). Sonuç: her koordinatlı rapor **tam bir olayda** değerlendirilir (tutarsız çift verdict yok).
   - Kontroller (`match|mismatch|unverifiable`): konum (≤100 m'de tespit/iz; taze rapor ≤30 dk için "araç yok" = çelişki), tip (tespit sınıfı), sayı (görüntü+iz; iddia gözlenenden azsa/fazlaysa uyumsuz), hareket (aracın çekim anında biten izi: duruyor = pencerede ≤60 m; yaklaşıyor/uzaklaşıyor/hareketli = iz analizi), yoğunluk.
   - **İzsiz araçta hareket verisi yoktur → hareket "doğrulanamadı".** Kimlik ("dost", "kimlik teyidi yapılmıştır") ve renk doğrulanamaz ve riski asla düşürmez. 150 dk'dan (2 saatlik iz penceresi) eski rapor doğrulanamaz. Gelecek verisi kullanılmaz.
   - Sonuç: `compatible | incompatible | unverifiable | irrelevant`.
3. **risk-agent-svc** `get_reports` aracı sonucu verir (uyumsuz→uyumlu→doğrulanamadı sıralı, ≤25 madde; ilgisizler yalnızca sayılır). Kural tabanlı mod ve LLM promptu `evidence_breakdown`'daki `reports` maddesine sayıları ve bir uyumsuz örneği (yalnızca çelişen kontrollerle) yazar; çıktıda `report_verification` bulunur. UI'da her rapor rozetle gösterilir.

**Gerçek veride (40 olay, D-FINE, CONF_THRESHOLD=0.4):** 72 koordinatlı raporun **39'u uyumsuz, 33'ü uyumlu** (resmî kaynaklarda 22/27, üçüncü taraflarda 17/6). Uyumsuzluk nedenleri (kontrol bazında): sayı 28, tip 17, hareket 13. Kimlik/dostluk iddiası taşıyan 18 rapordan 4'ü çelişkili,
14'ü uyumlu (tarif edilen araç gerçekten orada ve hareketi tutuyor; ama "dost" etiketi doğrulanamaz ve riski düşürmez). Kalibrasyon dersleri: (1) rapor saatindeki iz konumuyla karşılaştırmak hareketli araçlarda yanlıştı; (2) izsiz park halindeki araçlar izlerde görünmez, bu yüzden sayı ancak görüntü ayak izi içindeyken çürütücüdür.

### İzsiz (track'siz) tespitler
Tüm tespitler (izli+izsiz) risk-agent'a gider; **yalnızca izli araçlar hareket/patern analizine girer**. İzsiz nesneler için `data_gaps` çıktıya yazılır ve gerekçede/kanıtta **"hareket verisi YOK — risk kademesine katılmadı, LOW/zararsız varsayılmadı"** denir; LLM'e her tespit `iz_kaydi: true|false` ile gider.
Hiç izli araç yoksa gateway değerlendirmeyi çalıştırmaz ("izle eşleşen araç yok; risk değerlendirmesi yapılmadı"): LOW üretilmez. `AGENT_MAX_VEHICLES` sınırını aşan izli araçlar da `data_gaps.vehicles_over_limit` ile bildirilir.

## LLM sağlayıcısı (openrouter | glm)

**Birincil sağlayıcı: OpenRouter** (`nvidia/nemotron-3-ultra-550b-a55b:free`) — demo bununla yapılır. **GLM** (organizatörün gateway'i) **ikincil**: kod yolunda kalır, anahtar gelirse `LLM_PROVIDER=glm` ile açılır.
`.env` / `.env.example` / `docker-compose.yml` varsayılanı `openrouter`'dır; kodun kendi varsayılanı geriye uyumluluk için `glm` kalmıştır (yalnızca `GLM_API_KEY` verip `LLM_PROVIDER` vermeyen eski kullanım bozulmasın diye — compose/`.env` ile çalıştırırken fark etmez).
`LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` seçili sağlayıcının değerlerini geçersiz kılar. Anahtar yoksa ya da LLM/kota hata verirse ajan kural tabanlı moda düşer (yanıt üretmeye devam eder).

| | **openrouter (birincil)** | glm (ikincil) |
|---|---|---|
| Model | `nvidia/nemotron-3-ultra-550b-a55b:free` (`OPENROUTER_MODEL`) | `glm-5.3-flash` |
| `reasoning_effort` | **gönderilmez** (tanımayabilir; zorlamak için `LLM_SEND_REASONING_EFFORT=true`) | gönderilir (`low\|high\|max`) |
| Limit / bütçe | ücretsiz katman **20 istek/dk, 50 istek/gün**: günlük tavan `LLM_MAX_REQUESTS_PER_DAY` (varsayılan **40**, süreç belleğinde, yeniden başlatınca sıfırlanır), 15 istek/dk, 2 eşzamanlı | toplam 15 USD, `key/info` ile; 60 istek/dk, 4 eşzamanlı |
| Günlük kota 429'u | yeniden denenmez; kural motoruna düşülür | — |

**Ajan döngüsü:** `AGENT_MAX_ROUNDS=12` (eskiden 8) ve sistem promptunda "gerekli tüm araçları aynı turda, paralel çağır" satırı. Neden: bazı modeller araçları tur tur, tek tek çağırır ve 8 tur yetmez (ölçüldü: nemotron-3-super-120b iki demo olayında da tur aşımına düştü; nemotron-3-ultra `img_006673`'te ilk denemede düştü, satır eklenince 10 aracı toplu çağırıp **2 LLM isteğiyle** bitirdi).

**Canlı doğrulama (2026-09-26, gerçek OpenRouter API, gerçek veri + D-FINE, 2 olay):** `tool_calls` geçerli JSON döndürüyor. `img_004530` LLM modunda tamamlandı (HIGH, 4 istek); `img_006673` 12 tur + paralel satırla LLM modunda tamamlandı (HIGH = kendi veri seviyesi, `policy_adjustments` boş, düşük güvenli kaynak ağırlığı 0.15 ≤ 0.20, "planlı ikmal" iddiasını uyumsuz sayıp kendi hareket verisine dayandı, kimlik/dostluk iddiasının riski düşürmediğini yazdı).
Politika taban/tavanının LLM'i canlıda sınırlaması bu iki olayda **sınanmadı** (LLM zaten kendi seviyesini verdi); yalnızca sahte-LLM birim testlerinde doğrulandı. `nemotron-3-super`/`qwen`/`gemma` yeni prompt satırıyla denenmedi. GLM canlı **hiç denenmedi** (anahtar gelmedi).

**İlk temas testi:** `python scripts/llm_smoke.py --provider openrouter` **tek istekle** modelin `tool_calls` döndürüp döndürmediğini sınar (dönmüyorsa "UYGUN DEĞİL" der ve durur; anahtarı `.env`'den okur); `--e2e img_a img_b` en çok 2 gerçek olayı LLM moduyla dener.
**Kota uyarısı:** arayüzden her olay değerlendirmesi ~2-8 LLM isteği harcar; 40 olayı art arda koşturmayın. Günlük tavan süreç belleğindedir: risk-agent yeniden başlatılınca sayaç 0'a döner ve o gün önceden harcanan istekleri BİLMEZ. Tavan aşılınca ya da OpenRouter günlük kotayı reddedince ajan kural motoruna düşer, demo bozulmaz.
k8s manifestleri yalnızca GLM değişkenlerini içerir (OpenRouter için güncellenmedi).

## Arayüz

`web-ui` (React + Vite + TypeScript + Leaflet), yalnızca gateway'e bağlanır; durum polling ile alınır.

Düzen "asıl hikaye önce": risk kartı 5 saniyede okunur, teknik ayrıntı istenirse açılır.

1. **Olay şeridi (üstte):** 40 görüntü yatay kaydırılan küçük resimli kartlar (saat + önceki risk rozeti), bölge süzgeci, "değerlendir" ve **dosya yükleme** (dosya adı = `image_id`; çift tıklama = hemen değerlendir).
2. **Asıl hikaye:** solda **görüntü** (bbox overlay + araç sekmeleri), sağda **risk kartı**: seviye rozeti + güven, `13:25 itibarıyla`, tek bakışlık özet (kaç araç üsse yaklaşıyor, en yakını ve varış süresi, patern, izsiz nesne uyarısı, rapor uyumu) ve **kısa gerekçe** (5 satırda kesilir, "gerekçenin tamamını oku").
3. **Destekleyici (ikinci sıra, daha küçük):** **harita** (bölge pusula gülü + iz/konumlar; lejand kapalı gelir) ve **saha raporları** (uyumlu/uyumsuz rozetli, ilgisizler katlı).
4. **Teknik detaylar — varsayılan KATLI** ("Detayları göster" düğmesi risk kartında ve sayfa altında): ham kanıt yüzdeleri (`evidence_breakdown`), politika düzeltmeleri, veri boşluğu notu, araç çağrısı zaman çizelgesi (`tool_calls_log`), seçili aracın hareket ayrıntısı (yaklaşma göstergesi, hız/mesafe/ETA, mesafe–zaman grafiği) ve pipeline/log.

**Bölge adları:** `zones.json` adları ASCII'ye katlanmıştır ("Kuzeydogu Kavsagi"); arayüz bunları **yalnızca görüntüleme katmanında** Türkçe karakterli gösterir ("Kuzeydoğu Kavşağı", "Güneydoğu Yerleşimi", "Güney Kapısı Yaklaşımı" …) — `web-ui/src/zoneLabels.ts` sözlüğü, `zone_id` ya da ada göre; sözlükte olmayan ad aynen gösterilir. Veri dosyasına/API'ye dokunulmaz; rapor metinleri (veri) olduğu gibi kalır. Üs adı ("Merkez Us") henüz eşleştirilmedi.

**Kısa gerekçe:** risk kartı gerekçenin ilk 1–3 cümlesini (≈340 karaktere kadar) **cümle sınırında** gösterir; ajanın sona eklediği "(Veri boşluğu: …)" dipnotu kısa görünümden çıkar (aynı bilgi kart üstündeki rozette durur), tamamı "gerekçenin tamamını oku" ile açılır. Gerçek LLM gerekçeleriyle (436–626 karakter, 3–7 cümle) sınandı: cümle ortasında kesme, taşma ve düzen kayması yok.

**Araç renkleri:** her araç sabit bir renk alır ve AYNI renk haritadaki iz+noktalarda, görüntüdeki kutu kenarında ve araç sekmesinde kullanılır (izsiz nesneler gri kesikli). 10 renkli palet CIEDE2000 ile seçildi ve "en uzak nokta" sırasına dizildi; kırmızı/turuncu/amber (risk ve üs halkası renkleri) palete alınmadı (kırmızıya ve amber'e ΔE00 ≥ 22).
Kimlikler sıralanıp paletin sırayla k. rengini alır: aynı araç kümesi hep aynı atamayı verir ve ≤ 10 araçta hiçbir iki araç aynı rengi almaz. En ayrışan renkler önce kullanılır: en yakın çift ΔE00 4 araçta 35, 5'te 24, 6'da 16, 10'da 13 (6+ araçta bazı çiftler, ör. gökyüzü mavisi ↔ buz camgöbeği, daha benzer görünür).
Gerçek veride her iz tek bir olayın görüntüsüne ait olduğundan renk pratikte iz başına sabittir; aynı iz farklı bir araç kümesiyle görünürse sırası (dolayısıyla rengi) değişebilir.

**Bölge pusula gülü (harita):** `zones.json` bölgelerinin üsse göre yönlerinden, üssü merkez alıp uyarı yarıçapına (8 km) uzanan dilimler: 8 bölgede 8 × 45° (tarayıcıda ölçüldü: sınırlar 22.5°+45°k). Her dilim bölge adıyla etiketli, ince/yarı saydam nötr gri çizgili;
**yalnızca olayın ait olduğu bölgenin dilimi** beyaz dolgu + kalın kenarla vurgulanır (dilimler bilerek araç renklerinden farklı, nötr). Dilim sınırları komşu bölge yönlerinin ortasındadır, yani bölge sayısı/yönü değişirse gül kendini uyarlar. Bölge adları veri dosyasındaki gibi görünür (ASCII).

### Simülasyon = karşılama sayfası — `/`

Ana domain (`/`, eski `/simulation` adresi de çalışır) doğrudan bu sayfayı gösterir ve **girişsiz açıktır**; ayrı bir landing sayfası yoktur.
Operasyon paneli `/panel`'dedir ve giriş ister. Yönlendirme:

| Yol | Gösterilen | Erişim (auth-proxy :8080) |
|---|---|---|
| `/`, `/simulation` | simülasyon (SPA, `main.tsx` yol kontrolü) | herkese açık |
| `/assets/*`, `/sim-data/*` | derlenmiş paketler, önceden hesaplanmış simülasyon verisi | herkese açık (gizli bilgi yok: gateway anahtarı sunucu tarafında eklenir) |
| `/panel` | operasyon paneli (`App`) | giriş gerekli → yoksa 302 `/auth/login?next=/panel` |
| `/api/*` ve diğer her yol | gateway / SPA | giriş gerekli |
| `/auth/*` | auth-svc giriş/çıkış | açık (dakikada 10 deneme sınırı) |

Sağ üstteki küçük **Giriş** düğmesi sayfanın üstünde bir pencere açar ve auth-svc'nin mevcut JSON girişine (`POST /auth/login`) bağlıdır (yer tutucu değil):
başarılı girişte oturum çerezi yazılır ve `/panel`'e geçilir; pencere açıkken demo arkada oynamaya devam eder. Oturum zaten açıksa düğme "Operasyon paneli" bağlantısıdır.
Operasyonel panelden **tamamen ayrı** ve **canlı backend'e bağlı değil**: hiçbir `/api` isteği atmaz,
LLM kullanmaz. Gerçek 40 olayın DB'de **önceden hesaplanmış kural tabanlı** sonuçlarını (`web-ui/public/sim-data/events.json` + görüntüler) `capture_time` sırasıyla
oynatır. Her olay iki aşamalıdır (Normal hızda olay başına 7–8 s; "Hızlı" 3 kat): önce ~3 s **analiz** (araçlar tek tek kutulanır → hareket incelenir →
saha raporları karşılaştırılır → risk hesaplanır; kutular ve harita bu sırada nötr), sonra **sonuç**: Türkçe risk rozeti, sade tek cümle ve ortadaki kutuda
gerekçe metni. Gerekçe, aynı görüntü için DB'de aynı risk seviyesinde bir **LLM değerlendirmesi varsa onun metni** (şu an 10 olay), yoksa kural motorunun
gerekçesidir; kutu kaynağı yazar. Harita (Leaflet/OSM): üs ve 1.5 km sınırı, bölge merkezleri, görüntü alanı, araçlar + son 10 dk izleri, en yakın yaklaşan
araçtan üsse "~N dk" çizgisi. Oynat/duraklat, önceki/sonraki (← → Boşluk), olay şeridi; sona gelince baştan başlar.

Veriyi yenilemek (yalnızca Postgres + `data/REAL/zones.json`/`tracks.csv` okunur, LLM çağrısı yok; Pillow varsa görüntüler 1280 px'e küçültülür):

```bash
python scripts/export_simulation_data.py            # sonra web-ui imajını yeniden build edin
```

**Risk açıklandığında (hepsi best-effort):**
- **Ekran çerçevesi:** tüm ekranın kenarı risk rengine döner (kırmızı / turuncu / yeşil; analiz sırasında mavi); YÜKSEK RİSK'te birkaç kez nabız atar. Bilgi banner'da değil ortadaki kutudadır.
- **Ses:** YÜKSEK RİSK'te WebAudio alarmı ("Ses açık/kapalı" düğmesi). Tarayıcılar sesi ancak kullanıcı sayfaya bir kez dokunduktan/tıkladıktan sonra çalar.
- **Titreşim:** `navigator.vibrate()` — Android Chrome'da çalışır (bir kez etkileşim gerekir). **iOS'ta çalışmaz**: iOS'taki hiçbir tarayıcı Vibration API'yi desteklemez.
- **Flaş:** standart web API'si yok; yalnızca **Android**'de sayfa açılır açılmaz arka kamera izni istenir ve `torch` kısıtı denenir (bazı Android + Chrome
  kombinasyonlarında çalışır, **iOS Safari'de çalışmaz**; masaüstünde kamera hiç istenmez). Destek yoksa kamera kapatılır, "Flaş: yok" yazar, hata verilmez.
- **Bildirim izni** sayfa açılınca istenir (Safari/Firefox gibi yalnızca kullanıcı hareketiyle izin verenlerde ilk dokunuşta yeniden). Sistem bildirimi yalnızca
  sekme **arka plandayken** gösterilir (ön plandayken çerçeve + ses yeterli). Bildirim ve kamera **yalnızca güvenli bağlamda** (HTTPS — ör. Cloudflare tüneli — veya
  `localhost`) vardır; Android Chrome `new Notification()` desteklemediğinden (service worker gerekir) orada sistem bildirimi çıkmaz.

## Servisleri bağımsız çalıştırma

Komutlar **örnektir**; her servis kendi klasöründen çalışır. Tek venv yeterlidir (`python3 -m venv .venv && . .venv/bin/activate`).
`DATA_DIR`, veri seti klasörüdür (ör. `$PWD/data/synthetic`); `DATASET_DATE` core ve mock-data'da aynı olmalıdır.

```bash
export DATA_DIR=$PWD/data/synthetic DATASET_DATE=2025-06-01

cd mock-data-svc  && pip install -r requirements.txt && uvicorn app.main:app --port 8004
cd detection-svc  && pip install -r requirements.txt && MOCK_MODE=true uvicorn app.main:app --port 8001
#   Gerçek D-fine: pip install -r requirements-model.txt ; MOCK_MODE=false MODEL_PATH=models/stage1.pt uvicorn …
cd core-svc       && pip install -r requirements.txt && uvicorn app.main:app --port 8002        # DEMO_MODE=true: eski demo izleri
cd pattern-svc    && pip install -r requirements.txt && CORE_SVC_URL=http://localhost:8002 uvicorn app.main:app --port 8003
cd risk-agent-svc && pip install -r requirements.txt && LLM_PROVIDER=openrouter OPENROUTER_API_KEY=... CORE_SVC_URL=http://localhost:8002 \
  PATTERN_SVC_URL=http://localhost:8003 MOCK_DATA_SVC_URL=http://localhost:8004 uvicorn app.main:app --port 8005
cd gateway        && pip install -r requirements.txt && GATEWAY_API_KEYS=dev-key DETECTION_SVC_URL=http://localhost:8001 \
  CORE_SVC_URL=http://localhost:8002 PATTERN_SVC_URL=http://localhost:8003 MOCK_DATA_SVC_URL=http://localhost:8004 \
  RISK_AGENT_SVC_URL=http://localhost:8005 uvicorn app.main:app --port 8000
cd web-ui         && npm install && npm run dev        # http://localhost:5173 (/api → gateway)
```

Hepsi tek komutla (container'sız): `scripts/run-local.sh`.

```bash
curl -s localhost:8000/events | python -m json.tool | head -30
curl -s -X POST localhost:8000/pipeline/run -H 'content-type: application/json' -d '{"image_id":"img_000114"}' | python -m json.tool | head -60
```

## Docker / Kubernetes

- `docker/<servis>.Dockerfile` — her servis için; **build bağlamı servis klasörüdür**, ör. `docker build -f docker/core-svc.Dockerfile -t uskoruma/core-svc ./core-svc`.
  `detection-svc` için `--build-arg WITH_MODEL=true` CPU torch + ultralytics ekler (model ağırlığı imaja gömülmez; `/srv/models/stage1.pt` olarak bağlanır).
  `web-ui` çok aşamalı: Vite build → nginx-unprivileged (`/api` → `GATEWAY_URL`).
- `docker-compose.yml` — `db` + 6 servis + `web-ui`. `./data` salt-okunur `/data` olarak bağlanır; `DATA_DIR` varsayılanı `/data/synthetic`.
  Yalnızca `web-ui` (8080) ve `gateway` (8000) dışarı açılır. `db/init.sql` ilk açılışta yüklenir. **Çalıştırılıp doğrulandı** (bkz. Doğrulama durumu).
- `k8s/` — namespace, ConfigMap, `db` (StatefulSet+PVC), veri PVC'si (`uskoruma-data`), her servis için Deployment+Service, `web-ui` ve `gateway` için Ingress,
  `kustomization.yaml`. Secret **depoda yoktur** (`secret.example.yaml` şablon). Bu manifestler yazıldı, **k8s'e uygulanmadı**.

```bash
# k8s örnek (çalıştırılmadı)
kubectl apply -f k8s/namespace.yaml
kubectl -n uskoruma create secret generic uskoruma-secrets --from-literal=POSTGRES_PASSWORD='…' \
  --from-literal=DATABASE_URL='postgresql://uskoruma:…@db:5432/uskoruma' --from-literal=GLM_API_KEY='…' --from-literal=GATEWAY_API_KEYS='…'
kubectl apply -k k8s/       # ardından veri PVC'sini doldurun (k8s/data-pvc.yaml içindeki not)
```

  `k8s/db-init-configmap.yaml`, `db/init.sql`'den `scripts/gen-k8s-db-configmap.sh` ile üretilir (kaynak-doğru: `db/init.sql`).

## Yapılandırma

Tam liste `.env.example` içinde; başlıcalar:

| Değişken | Servis | Açıklama |
|---|---|---|
| `DATA_DIR`, `DATASET_DATE` | core, mock-data, detection | olay veri seti klasörü; `HH:MM` → epoch için sabit gün (**core ve mock-data'da aynı**) |
| `DEMO_MODE` (eski adı `SEED_DEMO`) | core | duvar saatine çapalı demo izleri + `/admin/demo/reset`; varsayılan **false** |
| `MATCH_GATE_M`, `MATCH_MAX_EXTRAP_S`, `ANALYSIS_WINDOW_S`, `STALE_AFTER_S` | core | tespit↔iz eşleştirme kapısı (15 m; yalnızca `time == capture_time` satırları), ızgara-dışı yedek yol için ekstrapolasyon sınırı, analiz penceresi (adım-duyarlı), bayatlık eşiği |
| `MOCK_MODE` (`MOCK_FALLBACK`, `MODEL_BACKEND`, `MODEL_PATH`, `CONF_THRESHOLD`, `VEHICLE_CLASSES`, `DFINE_REPO`) | detection | `true`: ground_truth.json'dan (yoksa rastgele) bbox; `false`: gerçek model (`MODEL_BACKEND=ultralytics\|dfine`). Yüklenemezse **servis açılmaz**; yalnızca açıkça `MOCK_FALLBACK=true` denirse mock'a düşer ve `/health` **`degraded` + `fallback_reason`** raporlar |
| `LOITER_RADIUS_M`, `APPROACH_MAX_DEV_DEG`, `APPROACH_MIN_SEGMENT_M`, `CONVOY_DIST_M`, … | pattern | tüm eşikler env ile ayarlanır (varsayılanlar: 200 m/2 sa, 15°, 500 m); seyrek izlerde otomatik uyarlanır |
| `REPORT_RADIUS_KM` | mock-data | olay konumuna yakınlık eşiği (3 km) |
| `LLM_PROVIDER` (`openrouter` birincil \| `glm`), `OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL`, `OPENROUTER_MODEL`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_MAX_REQUESTS_PER_DAY`, `LLM_SEND_REASONING_EFFORT`, `AGENT_MAX_ROUNDS` (12) | risk-agent | birincil sağlayıcı OpenRouter (bkz. "LLM sağlayıcısı") |
| `GLM_API_KEY`, `GLM_BASE_URL`, `GLM_ROOT_URL`, `GLM_MODEL`, `GLM_REASONING_EFFORT`, `GLM_MAX_TOKENS`, `GLM_RETRIES`, `GLM_MAX_CONCURRENT` | risk-agent | İKİNCİL: organizatör gateway'i (varsayılan `…railway.app/v1`, `glm-5.3-flash`, `low`, 4000, 5, 4). `GLM_ROOT_URL` boşsa base URL'den `/v1` atılarak türetilir (key/info için). **`GLM_THINKING` kaldırıldı** (yok sayılır, uyarı loglanır) |
| `BUDGET_TOTAL_USD`, `BUDGET_RESERVE_USD`, `BUDGET_REQUESTS_PER_MINUTE`, `BUDGET_TOKENS_PER_MINUTE`, `BUDGET_TOKENS_PER_ASSESSMENT`, `ON_BUDGET_EXCEEDED` | risk-agent | toplam (sıfırlanmayan) bütçe modeli; günlük/saatlik eşikler kaldırıldı |
| `GATEWAY_API_KEYS`, `CORS_ORIGINS`, `*_SVC_URL`, `PIPELINE_TIMEOUT_S` | gateway | auth, alt servis adresleri |
| `BASE_LAT/LON/RADIUS_M` | hepsi | yalnızca eski/demo akışı ve varsayılan yarıçap; olay akışında üs `zones.json`'dan gelir |
| `VITE_API_BASE`, `VITE_POLL_MS`, `VITE_TILE_URL` | web-ui | `/api`, 2000 ms, OSM (çevrimdışı için kendi tile sunucunuz) |

## Test

```bash
for s in mock-data-svc detection-svc core-svc pattern-svc risk-agent-svc gateway; do
  (cd $s && pip install -r requirements-dev.txt && python -m pytest -q)
done
(cd web-ui && npm install && npm run build)               # strict TypeScript + Vite build
python scripts/eval_events.py --api-key <anahtar>          # çalışan sistemde 40 olay ↔ expected.json
```

Kapsam (107 test): loader'lar (bozuk JSON/CSV onarımı, gece yarısı taşması), köşe-koordinat georef'i, iz eşleştirme, `reference_time` bazlı analiz ve gelecek verisi sızmaması,
tüm desen kuralları (yoğun **ve** seyrek iz), rapor↔bölge ilişkisi ve `as_of` süzmesi, politika (CONVOY yükseltmesi, taban/tavan), ajan döngüsü (sahte GLM: enjeksiyon,
boş evidence reddi, 401 fallback, bütçe aşımı, önbellek), gateway (olay/eski akış, kısmi arıza, auth, proxy).

## Doğrulama durumu

| Ne | Durum |
|---|---|
| 6 Python servisi + araçlar: birim/entegrasyon testleri | ✅ **268 test geçti**, 1 atlandı (core 95, pattern 21, mock-data 38, detection 32, risk-agent 62, gateway 15, scripts 5). Atlanan: gerçek D-FINE ağırlığıyla test (`DFINE_REPO`/`DFINE_TEST_WEIGHTS` ayarlıyken çalışır, yerelde geçti) |
| `docker compose up --build` | ✅ ilk denemede hatasız; 8 konteynerin hepsi `healthy` (db, mock-data, detection, core, pattern, risk-agent, gateway, web-ui) |
| `db/init.sql` gerçek **PostgreSQL 16 + PostGIS 3.4**'te | ✅ hatasız yüklendi; core açılışta 14 iz / 203 noktayı senkronladı (yeniden başlatmada tekrar yazmaz); 40 tespit olayı, 73 nesne (65 izli / 8 izsiz), 40 değerlendirme yazıldı; `evidence_breakdown` boş olamaz kısıtı ve PostGIS mesafe sorgusu doğrulandı |
| Uçtan uca 40 olay (konteynerlerde, `scripts/eval_events.py`) | ✅ 40/40 risk ve patern `expected.json` ile uyuşuyor (11 HIGH · 18 MEDIUM · 11 LOW). **Not:** beklenenler senaryo tasarımından türetildi; bu, sentetik verinin sistemle tutarlılığını gösterir, gerçek veri başarımını değil |
| web-ui `http://localhost:8080` (konteyner) gerçek tarayıcıda | ✅ (anahtarsız, taze oturum) 40 olay → değerlendirme → bbox, harita, risk kartı, saha raporları, çizelge açılıp kapanması. Dosya yükleme yolu yerel Vite sunucusunda tarayıcıda denendi (konteyner UI'da ayrıca denenmedi) |
| Eski akış (`DEMO_MODE=true`, `drone_id`) konteynerde | ✅ HIGH · CONVOY + DIRECT_APPROACH; olay akışıyla birlikte çalışır, veri seti izleri demo sıfırlamasından etkilenmez |
| LLM yolu (olay akışı), OpenAI uyumlu **sahte GLM** ile konteynerde | ✅ araç döngüsü, `get_drone_context` hatasının zarifçe atlanması, politika tavanı, düşük güven ağırlığı ≤ %20 |
| **Gerçek metadata ile kuru zincir** (`data/REAL`: 40 görüntü, 226 iz, 137 rapor; core+mock-data+pattern+risk-agent+gateway) | ✅ katı loader'lardan geçti; 40/40 olay hatasız. **Kutular sahtedir:** dedektör çıktısı yerine, çekim saatinde ayak izine düşen izlerden ±2 m gürültüyle türetildi (218 kutu, 198'i izle eşleşti, 20'si bilerek izsiz). Yalnızca zincirin çalıştığını kanıtlar, doğruluk kanıtlamaz |
| **Gerçek OpenRouter API** (`nvidia/nemotron-3-ultra-550b-a55b:free`, gerçek veri + D-FINE) | ✅ 2 olayda LLM modu tamamlandı (`img_004530`, `img_006673`; ikincisi 12 tur + paralel-çağrı satırıyla 2 LLM isteğinde); HIGH = kendi veri seviyesi. Politika taban/tavanı canlıda sınanmadı. Bugün ~35/50 ücretsiz istek harcandı |
| **Gerçek GLM API** | ⚠ Denenmedi (anahtar gelmedi); kod yolu sahte sunucu ve birim testlerle doğrulandı |
| **Gerçek dedektör (D-FINE-L) konteynerde, `MOCK_MODE=false`, 40 gerçek JPEG** | ✅ 423 kutu (conf ≥ 0.25; görüntü başına ort. 10.6): car 296 · van 68 · truck 50 · bus 9; ortalama confidence **0.613**; CPU'da ~2.3 sn/görüntü. Resmî formülle koordinata çevrilen kutular çekim saatindeki izlerle **medyan 0.2 m** (p90 1.0 m) örtüşüyor; ayak izi içindeki 206 izin **204'ü (%99)** bir tespitle bulundu. 423 tespitin 204'ü izle eşleşti (hepsi `exact`), 219'u izsiz (park halindeki araç ya da düşük güvenli gürültü; izsizlerin ort. conf'u 0.43). **Varsayılan `CONF_THRESHOLD` artık 0.4** (izsiz 219→89, izli araç recall %99→%96); 0.25 env ile seçilebilir |
| Gerçek veri + D-FINE + rapor doğrulaması, tam zincir (40 olay, gateway) | ✅ 40/40 hatasız, olay başına ~2.3–3 sn; risk 18 MEDIUM · 17 HIGH · 5 LOW. PostGIS'e yazıldı (226 araç, 5650 nokta, 43 olay, 468 nesne, 43 değerlendirme). **Beklenen etiket yoktur**: doğruluk ölçülemez |
| Kubernetes manifestleri | ⚠ YAML olarak ayrıştırıldı ve çapraz kontrol edildi; **k8s'e uygulanmadı** |

## Güvenlik notları

- **Auth:** yalnızca `gateway` dışarı açılır; `X-API-Key` veya `Authorization: Bearer` (sabit zamanlı karşılaştırma). `GATEWAY_API_KEYS` boşsa auth **kapalıdır** (uyarı loglanır).
  Diğer servislerin kendi auth'u yoktur; iç ağda tutulmalıdır (compose'ta portları yayınlanmaz). Ek sertleştirme: NetworkPolicy.
- **Ana sayfa anahtar sormaz:** web-ui'nin nginx'i (`web-ui/nginx/15-ui-api-key.envsh`) `/api` isteklerine `UI_GATEWAY_API_KEY` (varsayılan: `GATEWAY_API_KEYS`; virgüllü listede ilki) anahtarını **sunucu tarafında** ekler; tarayıcının gönderdiği anahtar ezilir. Anahtar build'e gömülmez.
  Gateway'in kendi doğrulaması açık kalır: doğrudan `:8000`'e anahtarsız istek 401. **Güvenlik notu:** `:8080`'e erişebilen herkes UI üzerinden tam API erişimi kazanır (LAN'a açıksanız ağ düzeyinde kısıtlayın). `UI_GATEWAY_API_KEY=` (boş) verilirse enjeksiyon kapanır ve UI, 401'de anahtar sorup `localStorage`'da saklar (eski davranış). Yalnızca `[A-Za-z0-9._~+=:@/-]` karakterli anahtarlar enjekte edilir. k8s manifestleri bu değişken için güncellenmedi (boş kalırsa UI eskisi gibi sorar).
- `POST /detect`: `image_id` yol gezinmesine karşı doğrulanır (`^[A-Za-z0-9_.-]+$`); `image_url` yalnızca http(s), özel/loopback adresler reddedilir.
- `/proxy/*`: yalnızca bilinen servisler, `..` reddedilir, `admin` uçları varsayılan kapalı.
- Container'lar root olmayan kullanıcıyla çalışır; k8s'te `readOnlyRootFilesystem` + `drop: ALL`. Veri klasörü **salt-okunur** bağlanır. Gizli değerler depoda yok.

## Sınırlamalar ve bilinen noktalar

- **Resmî veri için beklenen etiket yoktur:** sonuçların doğruluğu ölçülemedi; yalnızca hatasız çalıştığı, tespitlerin izlerle sub-metre örtüştüğü ve raporların anlamlı ayrıştığı görüldü.
- **Rapor doğrulaması sezgiseldir ve tespit kalitesine bağlıdır:** eşikler (100/250 m …) resmî veride ölçülerek seçildi; D-FINE sınıf hatası (ör. kamyon→araba) 'tip' uyumsuzluğu üretir; iz kaydı olmayan park halindeki araçlar yalnızca görüntü ayak izi içindeyse sayılabilir. LLM'e en çok 25 rapor (uyumsuz→uyumlu→doğrulanamadı) gider, ilgisizler sayılır.
- **Dedektör:** varsayılan yığın hâlâ mock'tur (sentetik set). Gerçek veri için `.env` (git'e girmez) `DATA_DIR=/data/REAL MOCK_MODE=false MODEL_BACKEND=dfine MODEL_PATH=/srv/models/dfine.pt DETECTION_WITH_MODEL=dfine` ayarlar; ağırlık (`dfine.pt`, 500 MB) imaja gömülmez, volume ile bağlanır. Modeli yüklemek CPU'da ~15-20 sn sürer. **k8s manifestleri bu D-FINE yapılandırması için güncellenmedi** (imaj arg'ı, volume).
- **İstihbarat kaynağı yok:** `get_intel` veri seti bölgeleri için boş liste döner (yer tutucu); eski demo zone'larında statik metin vardır.
- **Georef:** resmî formül kuşbakışı/ortorektifiye görüntü varsayar (üst kenar kuzey, sol kenar batı); lens bozulması/arazi yüksekliği modellenmez. Görüntü çekim anı iz ızgarasında (5 dk) değilse iz sonu ≤5 dk eski olabilir; görüntüden hesaplanan konum analize son nokta olarak eklenir (pattern-svc bunu görmez).
- **Rapor↔bölge ilişkisi sezgiseldir** (koordinat → en yakın bölge; metinde yol adı). Gerçek raporlar başka biçimde bölge belirtiyorsa `Catalog.reports_for` uyarlanmalıdır.
- **Durum bellek-içidir** (core izleri, gateway koşuları/logları, risk-agent bütçe sayaçları/önbelleği, detection görüntü önbelleği); bu yüzden k8s'te bu servisler **tek replika**dır.
  Postgres şu an **yazma** yönündedir (core: tracks.csv senkronu + tespit olayları; risk-agent: değerlendirmeler); açılışta geri okunmaz.
- Yüklenen görüntünün dosya adı bir `image_id` olmalıdır (zaman/köşe koordinatı image_meta.json'dan gelir).
- LLM çıktısı politika bandıyla sınırlandığı için nihai kademeyi tek başına belirlemez — bilinçli bir güvenlik tercihidir.

## Klasör yapısı

```
uskoruma/
├── detection-svc/     app/{main,detector,dataset,images,config,schemas}.py · tests/ · requirements*.txt · models/ (Stage 1 ağırlığı buraya)
├── core-svc/          app/{main,dataset,georef,store,analysis,demo,db,geo,timeutil,schemas,config}.py · app/loaders/{image_meta,tracks_csv,strict}.py · tests/
├── pattern-svc/       app/{main,classifier,geo,config}.py · tests/
├── mock-data-svc/     app/{main,dataset,data,config,geo,timeutil}.py · app/loaders/{zones,field_reports,strict}.py · tests/
├── risk-agent-svc/    app/{main,agent,tools,policy,rules,prompts,budget,glm_client,facts,store,db,config}.py · tests/
├── gateway/           app/{main,pipeline,dashboard,state,services,auth,config}.py · tests/
├── web-ui/            src/{App,api,types,theme,geo,usePolling}.ts(x) · src/components/* · nginx/default.conf.template
├── data/              README.md · (kullanıcı şema örnekleri) · synthetic/ (scenarios.yaml, image_meta.json, tracks.csv, zones.json, field_reports.json, images/, ground_truth.json, expected.json)
├── db/                init.sql            (PostgreSQL + PostGIS: drones, vehicles, tracks, detections, detection_objects, assessments)
├── docker/            *.Dockerfile
├── k8s/               *.yaml · kustomization.yaml   (yazıldı, uygulanmadı)
├── scripts/           gen_dataset.py · eval_events.py · run-local.sh · gen-k8s-db-configmap.sh · requirements.txt
├── docker-compose.yml · .env.example · README.md
```
