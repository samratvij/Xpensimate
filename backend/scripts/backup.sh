#!/usr/bin/env bash
# Back up Xpensimate's own database and files (nothing else on this host).
#   backend/scripts/backup.sh              # run from anywhere; cron runs it nightly
# Keeps KEEP_DAYS days (default 30) in BACKUP_DIR (default backend/backups, private to this user).
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

dest=${BACKUP_DIR:-$PWD/backups}
keep_days=${KEEP_DAYS:-30}
stamp=$(date +%Y%m%d-%H%M)
mkdir -p "$dest"
chmod 700 "$dest"

# Custom-format dump: compressed, and restorable table by table with pg_restore.
docker compose exec -T db pg_dump -U "${POSTGRES_USER:-expense}" -d "${POSTGRES_DB:-expense}" -Fc \
  > "$dest/db-$stamp.dump.partial" </dev/null
mv "$dest/db-$stamp.dump.partial" "$dest/db-$stamp.dump"

# Receipt photos and the household's setup (household.toml). .env holds secrets: keep your own copy of it.
tar -czf "$dest/files-$stamp.tar.gz" --ignore-failed-read data/receipts household.toml 2>/dev/null || true
chmod 600 "$dest"/db-"$stamp".dump "$dest"/files-"$stamp".tar.gz 2>/dev/null || true

find "$dest" -maxdepth 1 \( -name 'db-*.dump' -o -name 'files-*.tar.gz' \) -mtime +"$keep_days" -delete
echo "$(date -Is) backup ok: $(du -h "$dest/db-$stamp.dump" | cut -f1) database, $(ls "$dest"/db-*.dump | wc -l) kept"
