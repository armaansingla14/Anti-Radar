#!/bin/sh
# ANTI RADAR backend entrypoint (Docker / Render).
#   1. validate configuration (clean error, no traceback)
#   2. alembic upgrade head   (uses DATABASE_URL_DIRECT when set — Neon's non-pooled host)
#   3. optional seed          SEED_ON_START=snapshot|none   (default: none; snapshot only seeds an EMPTY db)
#   4. uvicorn on $PORT       (default 8000)
set -eu

cd "$(dirname "$0")"

python -m radar.config_check

echo "start: alembic upgrade head"
alembic upgrade head

case "${SEED_ON_START:-none}" in
  snapshot)
    echo "start: seeding the verified real-data snapshot (only if the database is empty)"
    if [ "${DEMO_SYNTHETIC:-${DEMO_MODE:-false}}" = "true" ]; then
      radar seed-demo --if-empty
    else
      radar seed-demo --if-empty --no-fictional
    fi
    ;;
  none|"") ;;
  *) echo "start: unknown SEED_ON_START='${SEED_ON_START}' (expected snapshot|none)" >&2; exit 2 ;;
esac

# Render free has 512 MB: one worker. Proxy headers so request URLs/schemes are right behind Render's proxy.
exec uvicorn radar.api.main:app --host 0.0.0.0 --port "${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-1}" --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 75
