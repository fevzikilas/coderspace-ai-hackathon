# detection-svc — bağımsız çalışır. Build bağlamı servis klasörüdür:
#   docker build -f docker/detection-svc.Dockerfile -t uskoruma/detection-svc ./detection-svc
#   # Gerçek YOLO ile (CPU torch + ultralytics; imaj çok daha büyük):
#   docker build -f docker/detection-svc.Dockerfile --build-arg WITH_MODEL=true -t uskoruma/detection-svc:model ./detection-svc
# Model ağırlığı imaja gömülmez: /srv/models/stage1.pt olarak volume ile bağlayın ve MOCK_MODE=false verin.
# (Bu dosya yazıldı; bu depoda build/run EDİLMEZ.)
FROM python:3.12-slim

ARG WITH_MODEL=false
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv
COPY requirements.txt requirements-model.txt ./
RUN if [ "$WITH_MODEL" = "true" ]; then \
      apt-get update \
      && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
      && rm -rf /var/lib/apt/lists/* \
      && pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu \
      && pip install -r requirements-model.txt ; \
    else \
      pip install -r requirements.txt ; \
    fi
COPY app ./app

RUN useradd --system --uid 10001 --no-create-home app && mkdir -p /srv/models && chown -R app /srv
USER app

ENV MOCK_MODE=true \
    MODEL_PATH=/srv/models/stage1.pt

EXPOSE 8001
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
