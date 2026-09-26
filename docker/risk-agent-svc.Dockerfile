# risk-agent-svc — bağımsız çalışır. Build bağlamı servis klasörüdür:
#   docker build -f docker/risk-agent-svc.Dockerfile -t uskoruma/risk-agent-svc ./risk-agent-svc
# (Bu dosya yazıldı; bu depoda build/run EDİLMEZ.)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY app ./app

RUN useradd --system --uid 10001 --no-create-home app && chown -R app /srv
USER app

EXPOSE 8005
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8005/health', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8005"]
