#!/bin/sh
# Railway: ishga tushishdan oldin yangi yuklangan bazani (restore.db) o'rnatadi, keyin serverni yoqadi.
set -e
DB="${NAZORAT_DB:-/data/nazorat.db}"
DIR="$(dirname "$DB")"
mkdir -p "$DIR"
if [ -f "$DIR/restore.db" ]; then
    [ -f "$DB" ] && cp "$DB" "$DIR/nazorat_oldingi_$(date +%Y%m%d_%H%M%S).db"
    rm -f "$DB-wal" "$DB-shm"
    mv "$DIR/restore.db" "$DB"
    echo "[railway] restore.db bazaga o'rnatildi"
fi
exec python -m uvicorn app:app --host 0.0.0.0 --port "${PORT:-8000}" \
    --workers "${WEB_CONCURRENCY:-4}" --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 30
