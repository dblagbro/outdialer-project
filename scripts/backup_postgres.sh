#!/bin/sh
set -eu

mkdir -p backups
timestamp="$(date +%Y%m%d-%H%M%S)"
docker compose exec -T postgres pg_dump -U "${POSTGRES_USER:-outdialer}" -d "${POSTGRES_DB:-outdialer}" > "backups/outdialer-${timestamp}.sql"
echo "Wrote backups/outdialer-${timestamp}.sql"
