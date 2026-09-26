#!/usr/bin/env bash
# db/init.sql (kaynak-doğru) -> k8s/db-init-configmap.yaml. init.sql değişince yeniden çalıştırın.
set -euo pipefail
cd "$(dirname "$0")/.."
out=k8s/db-init-configmap.yaml
{
  echo "# ÜRETİLDİ: scripts/gen-k8s-db-configmap.sh (kaynak: db/init.sql) — elle DÜZENLEMEYİN"
  echo "apiVersion: v1"
  echo "kind: ConfigMap"
  echo "metadata:"
  echo "  name: uskoruma-db-init"
  echo "  namespace: uskoruma"
  echo "  labels:"
  echo "    app.kubernetes.io/part-of: uskoruma"
  echo "data:"
  echo "  init.sql: |"
  sed 's/^/    /' db/init.sql
} > "$out"
echo "yazıldı: $out"
