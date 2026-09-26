# web-ui — Vite build + nginx (unprivileged). Build bağlamı servis klasörüdür:
#   docker build -f docker/web-ui.Dockerfile -t uskoruma/web-ui ./web-ui
# nginx /api isteklerini GATEWAY_URL'e proxy'ler (varsayılan http://gateway:8000).
# (Bu dosya yazıldı; bu depoda build/run EDİLMEZ.)
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# UI yalnızca gateway'e konuşur; aynı origin altında /api
ARG VITE_API_BASE=/api
ARG VITE_TILE_URL=https://tile.openstreetmap.org/{z}/{x}/{y}.png
ARG VITE_POLL_MS=2000
ENV VITE_API_BASE=$VITE_API_BASE VITE_TILE_URL=$VITE_TILE_URL VITE_POLL_MS=$VITE_POLL_MS
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
ENV GATEWAY_URL=http://gateway:8000
# UI_GATEWAY_API_KEY: /api isteklerine sunucu tarafında eklenen gateway anahtarı (boş: enjeksiyon yok, UI anahtar sorar)
ENV UI_GATEWAY_API_KEY=
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --chmod=755 nginx/15-ui-api-key.envsh /docker-entrypoint.d/15-ui-api-key.envsh
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
