#!/usr/bin/env bash
# Restore Xpensimate's database from a backup made by backup.sh. REPLACES the current data.
#   backend/scripts/restore.sh backups/db-20261104-0330.dump --yes
# Receipt photos and household.toml are in the matching files-*.tar.gz: tar -xzf it inside backend/.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
dump=${1:?usage: restore.sh <db-*.dump> --yes}
[ "${2:-}" = "--yes" ] || { echo "This replaces the current Xpensimate data with $dump. Re-run with --yes."; exit 1; }
[ -s "$dump" ] || { echo "No such backup: $dump"; exit 1; }

docker compose --profile gmail stop api gmail-worker </dev/null
docker compose exec -T db pg_restore -U "${POSTGRES_USER:-expense}" -d "${POSTGRES_DB:-expense}" \
  --clean --if-exists --no-owner < "$dump"
docker compose --profile gmail up -d api gmail-worker </dev/null
echo "Restored from $dump"
