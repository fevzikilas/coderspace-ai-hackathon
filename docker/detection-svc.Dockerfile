# detection-svc — bağımsız çalışır. Build bağlamı servis klasörüdür:
#   docker build -f docker/detection-svc.Dockerfile -t uskoruma/detection-svc ./detection-svc
# Gerçek model için WITH_MODEL (model ağırlığı imaja GÖMÜLMEZ: /srv/models altına volume ile bağlanır, MOCK_MODE=false verilir):
#   --build-arg WITH_MODEL=true    ultralytics YOLO (CPU torch + ultralytics)         → MODEL_BACKEND=ultralytics
#   --build-arg WITH_MODEL=dfine   resmî D-FINE (CPU torch + repo, sabit commit)      → MODEL_BACKEND=dfine, MODEL_PATH=/srv/models/dfine.pt
FROM python:3.12-slim

ARG WITH_MODEL=false
ARG WITH_REID=true
# Resmî D-FINE reposu (Apache-2.0), sabitlenmiş commit
ARG DFINE_COMMIT=956d1709314c2c6a4df6f34de232054578a7449f
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TORCH_HOME=/srv/.cache/torch

WORKDIR /srv
COPY requirements.txt requirements-model.txt requirements-dfine.txt ./
# 1) torch (en ağır katman; bağımlılık değişince yeniden indirilmesin diye ayrı)
RUN set -eu; \
    if [ "$WITH_MODEL" = "true" ] || [ "$WITH_MODEL" = "dfine" ] || [ "$WITH_REID" = "true" ]; then \
      pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu ; \
    fi; \
    if [ "$WITH_MODEL" = "true" ]; then \
      apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/* \
    fi
# 2) geri kalan bağımlılıklar (+ dfine: resmî repo, sabit commit)
RUN set -eu; \
    if [ "$WITH_MODEL" = "true" ]; then \
      pip install -r requirements-model.txt ; \
    elif [ "$WITH_MODEL" = "dfine" ]; then \
      pip install -r requirements-dfine.txt \
      && python -c "import io, tarfile, urllib.request as u; tarfile.open(fileobj=io.BytesIO(u.urlopen('https://github.com/Peterande/D-FINE/archive/${DFINE_COMMIT}.tar.gz', timeout=120).read())).extractall('/opt')" \
      && mv /opt/D-FINE-${DFINE_COMMIT} /opt/D-FINE ; \
    else \
      pip install -r requirements.txt ; \
    fi
RUN set -eu; \
    if [ "$WITH_REID" = "true" ]; then \
      mkdir -p "$TORCH_HOME" \
      && python -c "from torchvision.models import MobileNet_V3_Small_Weights, mobilenet_v3_small; mobilenet_v3_small(weights=MobileNet_V3_Small_Weights.DEFAULT)" ; \
    fi
COPY app ./app

RUN useradd --system --uid 10001 --no-create-home app && mkdir -p /srv/models && chown -R app /srv
USER app

ENV MOCK_MODE=true \
    MODEL_PATH=/srv/models/stage1.pt \
    DFINE_REPO=/opt/D-FINE \
    HF_HOME=/tmp/hf \
    TRANSFORMERS_OFFLINE=1 \
    MPLCONFIGDIR=/tmp/mpl

EXPOSE 8001
HEALTHCHECK --interval=15s --timeout=3s --start-period=90s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
