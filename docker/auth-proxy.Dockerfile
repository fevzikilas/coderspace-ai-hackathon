# auth-proxy — web-ui (8080) ve gateway (8000) önündeki oturum kapısı (nginx auth_request → auth-svc).
#   docker build -f docker/auth-proxy.Dockerfile -t uskoruma/auth-proxy ./auth-proxy
FROM nginxinc/nginx-unprivileged:1.27-alpine
USER root
RUN rm -f /etc/nginx/conf.d/default.conf && mkdir -p /etc/nginx/snippets
COPY nginx/auth-proxy.conf /etc/nginx/conf.d/auth-proxy.conf
COPY nginx/auth-gate.inc nginx/proxy-headers.inc /etc/nginx/snippets/
USER 101
EXPOSE 8080 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
