#!/usr/bin/env bash
#
# Nightly backup of the *irreplaceable* smartmoviesearch state:
#   - backend/.env          API keys + preview/admin password (can't be regenerated)
#   - backend/data/         scraped Ebert JSONL (~90MB, hours to re-scrape), URL cache,
#                           IP blacklist
#
# The Milvus vector DB is *derived* data — it is NOT backed up here; restore it by
# re-running  scripts/ingest_ebert.py  against the backed-up JSONL (cheap + scripted).
#
# Keeps the last RETAIN backups. Scheduled via cron; run manually:  ./backup.sh
#
set -euo pipefail
cd "$(dirname "$0")"                       # → cineai/

BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/smartmoviesearch}"
RETAIN="${RETAIN:-14}"
mkdir -p "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$BACKUP_DIR/sms-$STAMP.tgz"

# .env and data/ live under backend/; tar them relative to backend/ so restore is simple.
tar czf "$OUT" -C backend .env data

# Prune old backups (keep newest $RETAIN)
ls -1t "$BACKUP_DIR"/sms-*.tgz 2>/dev/null | tail -n +"$((RETAIN + 1))" | xargs -r rm -f

echo "$(date -u +%FT%TZ): backed up -> $OUT ($(du -h "$OUT" | cut -f1)); $(ls -1 "$BACKUP_DIR"/sms-*.tgz | wc -l) kept"
