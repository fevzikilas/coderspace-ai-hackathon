#!/usr/bin/env bash
# Tüm Python servislerini CONTAINER'SIZ, yerel süreçler olarak başlatır (geliştirme/demo).
# Önkoşul (tek bir venv yeterli):
#   python3 -m venv .venv && . .venv/bin/activate
#   for s in detection-svc core-svc pattern-svc mock-data-svc risk-agent-svc gateway; do pip install -r $s/requirements.txt; done
# UI ayrı terminalde: cd web-ui && npm install && npm run dev   (http://localhost:5173)
# Durdurmak için Ctrl-C (alt süreçlerin hepsi kapatılır).
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python}"
HOST="${HOST:-127.0.0.1}"

export MOCK_MODE="${MOCK_MODE:-true}"
# Olay veri seti (image_meta.json, tracks.csv, zones.json, field_reports.json, images/, ground_truth.json).
# Gerçek veri için DATA_DIR'i o klasöre çevirin. DEMO_MODE=true: eski drone/kafile demo akışını da açar.
export DATA_DIR="${DATA_DIR:-$PWD/data/synthetic}"
export DATASET_DATE="${DATASET_DATE:-2025-06-01}"
export DEMO_MODE="${DEMO_MODE:-false}"
export DETECTION_SVC_URL="http://$HOST:8001" CORE_SVC_URL="http://$HOST:8002" PATTERN_SVC_URL="http://$HOST:8003"
export MOCK_DATA_SVC_URL="http://$HOST:8004" RISK_AGENT_SVC_URL="http://$HOST:8005"
# GLM_API_KEY yoksa risk-agent kural tabanlı modda çalışır; GATEWAY_API_KEYS boşsa gateway auth'suzdur (yalnızca yerel geliştirme).

start() { # dizin port
  (cd "$1" && exec "$PY" -m uvicorn app.main:app --host "$HOST" --port "$2") &
  echo "  • $1 -> http://$HOST:$2"
}

trap 'echo; echo "durduruluyor…"; kill 0' EXIT INT TERM
echo "Servisler başlatılıyor:"
start mock-data-svc 8004
start detection-svc 8001
start core-svc 8002
start pattern-svc 8003
start risk-agent-svc 8005
start gateway 8000
echo
echo "Hazır olunca (DATA_DIR=$DATA_DIR):"
echo "  curl -s http://$HOST:8000/events | python -m json.tool | head -40"
echo "  curl -s -X POST http://$HOST:8000/pipeline/run -H 'content-type: application/json' -d '{\"image_id\":\"img_000114\"}' | python -m json.tool | head -60"
echo "  python scripts/eval_events.py   # 40 olayın tümü"
wait
