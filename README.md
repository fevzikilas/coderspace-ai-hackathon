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
# UI:       http://localhost:8080   (API anahtarı: GATEWAY_API_KEYS, varsayılan  dev-key-change-me — UI ilk açılışta sorar)
# Gateway:  http://localhost:8000
python scripts/eval_events.py --api-key dev-key-change-me    # 40 olayın tamamını değerlendirir, beklenenle karşılaştırır
```

UI'da: soldaki **Olay seçici**den bir görüntü seç → **▶ değerlendir** (veya çift tıkla) → haritada aracın hesaplanan konumu ve geçmiş izi,
görüntüde bbox'lar, sağda risk kartı (LOW/MEDIUM/HIGH + gerekçe + kanıt dağılımı + araç çağrısı zaman çizelgesi). Başlıkta
**"14:10 itibarıyla değerlendirme"** görünür. Kendi görüntünüzü yüklemek için dosya adı bir `image_id` olmalıdır (ör. `img_000860.jpg`).

`GLM_API_KEY` yoksa risk ajanı kural tabanlı çalışır (aynı çıktı şeması); anahtarı verirseniz LLM tool-calling devreye girer.

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
   │ YOLO Stage 1   │   │ image_meta +   │ │ kural tabanlı  │ │ üs · bölgeler  │ │ GLM tool-calling         │
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
     risk-agent-svc ──► GLM API                           chat/completions (tool calling)
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
               ├─3 georeference   POST core-svc /georeference            → bbox MERKEZİ → GPS (köşelerden bilinear);
               │                                                           tespitleri reference_time'daki tracks.csv izlerine eşler
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

- **Şema (kullanıcı örnekleriyle aynı):** `image_meta.json` (köşe koordinatları, `capture_time`), `tracks.csv` (`track_id,time,lat,lon`, 5 dk adım),
  `zones.json` (tek üs + bölge merkezleri), `field_reports.json` (`time`, `source: official|third_party`, `text`).
- **Sentetik set** `data/synthetic/`: 8 bölge × 5 görüntü = **40 olay**, `scenarios.yaml`'dan deterministik üretilir (`scripts/gen_dataset.py`):
  kafile yaklaşması, tek araç yaklaşması, üs yakınında/uzağında bekleme, sivil trafik, uzaklaşan kafile, çelişkili raporlarla yavaş yaklaşma, üs sınırı ihlali.
  `ground_truth.json` gerçek bbox'ları tutar ve gerçek YOLO gelene kadar mock dedektörü besler; `expected.json` her olayın tasarım niyetidir.
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
| **core-svc** | 8002 | `POST /georeference` `{image_id, boxes[], (image_meta \| drone_meta \| katalogdaki image_id), reference_time, match_tracks, ingest}` → `{detections:[{class,conf,lat,lon,vehicle_id,match_distance_m}], reference_time, georef_method}` · `GET /tracks/{id}?window=2h&until=` · `POST /tracks/analyze` `{vehicle_id\|coords, base_location, reference_time, current_position}` → `{speed_mps, heading_deg, approaching, eta_min, distance_to_base_m, …}` · `GET /dataset/images[/{id}]` · `GET /dataset/tracks` · `GET /detections/{id}` · `POST /admin/demo/reset` (yalnızca DEMO_MODE) |
| **pattern-svc** | 8003 | `POST /pattern/classify` `{vehicle_ids, zone_id, base_location, reference_time}` → `{pattern, confidence, involved_vehicles, detail}` |
| **mock-data-svc** | 8004 | `GET /base` · `GET /zones[/{id}]` · `POST /zones/assign {points}` · `GET /reports/{zone}?as_of=&lat=&lon=` · `GET /intel/{zone}?as_of=` (**yer tutucu**: veri setinde istihbarat yok) · `GET /drones` (yalnızca demo) |
| **risk-agent-svc** | 8005 | `POST /assess` `{zone_id, detection_id, base_location}` → `risk_level, rationale, confidence, evidence_breakdown[], tool_calls_log[]` (+`reference_time`, `mode`, `policy_adjustments`, `usage`, …) · `GET /assessments` · `GET /budget` |
| **gateway** | 8000 | `POST /pipeline/run` (`image_id` \| `image_id+image_b64` \| `drone_id` demo) · `GET /events` · `GET /pipeline/runs[/{id}]` · `GET /dashboard/state` · `GET /images/{id}` · `/proxy/{svc}/…` · `GET /health` (hepsi ayrıca `/api/…` altında) |

Sözleşme kararları:
- **Georef:** köşe yolunda piksel→GPS **bilinear**, araç konumu = **bbox merkezi**. `drone_meta` yolu (iğne-deliği kamera, düz zemin; `alt` AGL m, `fov` yatay °, `sensor_w/h` piksel, `gimbal_pitch` 0=ufuk −90=nadir) geri uyumluluk için durur.
- `detection_id`, core-svc'nin bir görüntü karesi için ürettiği **tespit olayı** kimliğidir; risk-agent bağlamını (araçlar, `reference_time`) bundan çeker.
- `eta_min` = araç **üs sınırına** (`radius_m`) ulaşana dek tahmini süre (yaklaşmıyorsa `null`).
- Pattern önceliği: `CONVOY > DIRECT_APPROACH > LOITERING > RANDOM`; eşleşen **tüm** desenler `detail.matched_patterns`, araç başına etiket `detail.per_vehicle` içindedir.
- Zaman damgaları ISO-8601 UTC (`…Z`); veri seti saatleri UI'da UTC gösterilir (kaymasın diye).

## Risk motoru

`risk-agent-svc`, GLM'e (OpenAI uyumlu `chat/completions` + tool calling) **beş araç** verir:
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

**Bütçe / kota** (`app/budget.py`): günlük token, saatlik LLM isteği, saatlik değerlendirme, değerlendirme başına token. Aşılırsa
`ON_BUDGET_EXCEEDED=fallback` (varsayılan) kural motoruna düşer, `reject` ise HTTP 429 döner. Aynı `(zone, detection)` sonucu önbelleğe alınır.

**Kural tabanlı fallback:** `GLM_API_KEY` yoksa, bütçe aşılırsa veya LLM hata verirse aynı politika ve aynı çıktı şemasıyla `mode=rule-based` üretilir.

## Arayüz

`web-ui` (React + Vite + TypeScript + Leaflet), yalnızca gateway'e bağlanır; durum polling ile alınır.

- **Olay seçici:** 40 görüntü (küçük resimli), bölgeye göre filtre, önceki değerlendirmelerin risk rozeti; **dosya yükleme** (dosya adı = `image_id`).
- **Başlık:** `14:10 itibarıyla değerlendirme` · görüntü kimliği · bölge · üs.
- **Görüntü:** bbox overlay (sınıf + confidence + araç kimliği; izsiz nesne gri kesikli), `capture_time` rozeti.
- **Harita:** üs sınırı/uyarı halkası, görüntünün yer ayak izi, aracın **görüntüden hesaplanan konumu**, geçmiş izi (5 dk noktaları saat etiketli), yaklaşma vektörü.
- **Hareket:** yaklaşma göstergesi, hız/yön/mesafe/ETA, üsse mesafe–zaman grafiği (iz, capture_time'a kadar).
- **Saha raporları:** kaynak (resmî / üçüncü taraf), olay anına göre yaş, yalnızca capture_time öncesi; **soluk/çizgili** (düşük güven).
- **Risk kartı:** LOW/MEDIUM/HIGH rozeti + gerekçe + kanıt dağılımı (yığılmış çubuk) + araç çağrısı zaman çizelgesi (tıklayınca açılır).

## Servisleri bağımsız çalıştırma

Komutlar **örnektir**; her servis kendi klasöründen çalışır. Tek venv yeterlidir (`python3 -m venv .venv && . .venv/bin/activate`).
`DATA_DIR`, veri seti klasörüdür (ör. `$PWD/data/synthetic`); `DATASET_DATE` core ve mock-data'da aynı olmalıdır.

```bash
export DATA_DIR=$PWD/data/synthetic DATASET_DATE=2025-06-01

cd mock-data-svc  && pip install -r requirements.txt && uvicorn app.main:app --port 8004
cd detection-svc  && pip install -r requirements.txt && MOCK_MODE=true uvicorn app.main:app --port 8001
#   Gerçek YOLO: pip install -r requirements-model.txt ; MOCK_MODE=false MODEL_PATH=models/stage1.pt uvicorn …
cd core-svc       && pip install -r requirements.txt && uvicorn app.main:app --port 8002        # DEMO_MODE=true: eski demo izleri
cd pattern-svc    && pip install -r requirements.txt && CORE_SVC_URL=http://localhost:8002 uvicorn app.main:app --port 8003
cd risk-agent-svc && pip install -r requirements.txt && GLM_API_KEY=... CORE_SVC_URL=http://localhost:8002 \
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
| `MATCH_GATE_M`, `MATCH_MAX_EXTRAP_S`, `ANALYSIS_WINDOW_S`, `STALE_AFTER_S` | core | tespit↔iz eşleştirme kapısı (60 m), analiz penceresi (adım-duyarlı), bayatlık eşiği |
| `MOCK_MODE` (`MOCK_FALLBACK`, `MODEL_PATH`, `CONF_THRESHOLD`, `VEHICLE_CLASSES`) | detection | `true`: ground_truth.json'dan (yoksa rastgele) bbox; `false`: YOLO, yüklenemezse mock'a düşer |
| `LOITER_RADIUS_M`, `APPROACH_MAX_DEV_DEG`, `APPROACH_MIN_SEGMENT_M`, `CONVOY_DIST_M`, … | pattern | tüm eşikler env ile ayarlanır (varsayılanlar: 200 m/2 sa, 15°, 500 m); seyrek izlerde otomatik uyarlanır |
| `REPORT_RADIUS_KM` | mock-data | olay konumuna yakınlık eşiği (3 km) |
| `GLM_API_KEY`, `GLM_BASE_URL`, `GLM_MODEL`, `GLM_THINKING` | risk-agent | varsayılan `https://open.bigmodel.cn/api/paas/v4`, `glm-4.5`; uluslararası Z.ai için genellikle `https://api.z.ai/api/paas/v4` (hesabınızdaki değeri doğrulayın) |
| `BUDGET_*`, `ON_BUDGET_EXCEEDED` | risk-agent | bütçe/kota |
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
| 6 Python servisi: birim/entegrasyon testleri (temiz venv) | ✅ 107 test geçti (mock-data 12, detection 9, core 29, pattern 21, risk-agent 21, gateway 15) |
| `docker compose up --build` | ✅ ilk denemede hatasız; 8 konteynerin hepsi `healthy` (db, mock-data, detection, core, pattern, risk-agent, gateway, web-ui) |
| `db/init.sql` gerçek **PostgreSQL 16 + PostGIS 3.4**'te | ✅ hatasız yüklendi; core açılışta 14 iz / 203 noktayı senkronladı (yeniden başlatmada tekrar yazmaz); 40 tespit olayı, 73 nesne (65 izli / 8 izsiz), 40 değerlendirme yazıldı; `evidence_breakdown` boş olamaz kısıtı ve PostGIS mesafe sorgusu doğrulandı |
| Uçtan uca 40 olay (konteynerlerde, `scripts/eval_events.py`) | ✅ 40/40 risk ve patern `expected.json` ile uyuşuyor (11 HIGH · 18 MEDIUM · 11 LOW). **Not:** beklenenler senaryo tasarımından türetildi; bu, sentetik verinin sistemle tutarlılığını gösterir, gerçek veri başarımını değil |
| web-ui `http://localhost:8080` (konteyner) gerçek tarayıcıda | ✅ API anahtarı kapısı → 40 olay → değerlendirme → bbox, harita, risk kartı, saha raporları, çizelge açılıp kapanması. Dosya yükleme yolu yerel Vite sunucusunda tarayıcıda denendi (konteyner UI'da ayrıca denenmedi) |
| Eski akış (`DEMO_MODE=true`, `drone_id`) konteynerde | ✅ HIGH · CONVOY + DIRECT_APPROACH; olay akışıyla birlikte çalışır, veri seti izleri demo sıfırlamasından etkilenmez |
| LLM yolu (olay akışı), OpenAI uyumlu **sahte GLM** ile konteynerde | ✅ araç döngüsü, `get_drone_context` hatasının zarifçe atlanması, politika tavanı, düşük güven ağırlığı ≤ %20 |
| **Gerçek GLM API** | ⚠ Denenmedi (anahtar yok). `GLM_MODEL`/`GLM_BASE_URL` hesabınıza göre doğrulanmalı |
| **Gerçek YOLO modeli** | ⚠ Denenmedi (`.pt` ağırlığı yok); mock dedektör `ground_truth.json` ile beslenir |
| **Gerçek (organizatör) veri seti** | ⚠ Henüz yok; şemalar kullanıcının örnek dosyalarından türetildi |
| Kubernetes manifestleri | ⚠ YAML olarak ayrıştırıldı ve çapraz kontrol edildi; **k8s'e uygulanmadı** |

## Güvenlik notları

- **Auth:** yalnızca `gateway` dışarı açılır; `X-API-Key` veya `Authorization: Bearer` (sabit zamanlı karşılaştırma). `GATEWAY_API_KEYS` boşsa auth **kapalıdır** (uyarı loglanır).
  Diğer servislerin kendi auth'u yoktur; iç ağda tutulmalıdır (compose'ta portları yayınlanmaz). Ek sertleştirme: NetworkPolicy.
- UI, API anahtarını build'e **gömmez**; 401 alınca sorar ve `localStorage`'da saklar.
- `POST /detect`: `image_id` yol gezinmesine karşı doğrulanır (`^[A-Za-z0-9_.-]+$`); `image_url` yalnızca http(s), özel/loopback adresler reddedilir.
- `/proxy/*`: yalnızca bilinen servisler, `..` reddedilir, `admin` uçları varsayılan kapalı.
- Container'lar root olmayan kullanıcıyla çalışır; k8s'te `readOnlyRootFilesystem` + `drop: ALL`. Veri klasörü **salt-okunur** bağlanır. Gizli değerler depoda yok.

## Sınırlamalar ve bilinen noktalar

- **Sentetik veri:** organizatörün final veri seti henüz yok; şemalar kullanıcı örneklerinden türetildi. Gerçek dosyalar farklıysa yalnızca `loaders/` katmanı değişir.
- **Stage 1 YOLO modeli yok:** mock dedektör `ground_truth.json`'daki bbox'ları döndürür (yani tespit "mükemmel"dir). Gerçek model kodu (`ultralytics`) yazıldı ama denenmedi.
- **İstihbarat kaynağı yok:** `get_intel` veri seti bölgeleri için boş liste döner (yer tutucu); eski demo zone'larında statik metin vardır.
- **Georef:** köşe yolu eksene paralel dikdörtgen varsayar (bilinear); lens bozulması/arazi yüksekliği modellenmez. Görüntü çekim anı iz ızgarasında (5 dk) değilse iz sonu ≤5 dk eski olabilir; görüntüden hesaplanan konum analize son nokta olarak eklenir (pattern-svc bunu görmez).
- **Rapor↔bölge ilişkisi sezgiseldir** (koordinat → en yakın bölge; metinde yol adı). Gerçek raporlar başka biçimde bölge belirtiyorsa `Catalog.reports_for` uyarlanmalıdır.
- **Durum bellek-içidir** (core izleri, gateway koşuları/logları, risk-agent bütçe sayaçları/önbelleği, detection görüntü önbelleği); bu yüzden k8s'te bu servisler **tek replika**dır.
  Postgres şu an **yazma** yönündedir (core: tracks.csv senkronu + tespit olayları; risk-agent: değerlendirmeler); açılışta geri okunmaz.
- Yüklenen görüntünün dosya adı bir `image_id` olmalıdır (zaman/köşe koordinatı image_meta.json'dan gelir).
- LLM çıktısı politika bandıyla sınırlandığı için nihai kademeyi tek başına belirlemez — bilinçli bir güvenlik tercihidir.

## Klasör yapısı

```
uskoruma/
├── detection-svc/     app/{main,detector,dataset,images,config,schemas}.py · tests/ · requirements*.txt · models/ (Stage 1 ağırlığı buraya)
├── core-svc/          app/{main,dataset,georef,store,analysis,demo,db,geo,timeutil,schemas,config}.py · app/loaders/{image_meta,tracks_csv,jsonlenient}.py · tests/
├── pattern-svc/       app/{main,classifier,geo,config}.py · tests/
├── mock-data-svc/     app/{main,dataset,data,config,geo,timeutil}.py · app/loaders/{zones,field_reports,jsonlenient}.py · tests/
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
