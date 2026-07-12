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
# Output is age-ENCRYPTED (it contains backend/.env secrets). Requires an age recipient
# via BACKUP_AGE_RECIPIENT (env, or a BACKUP_AGE_RECIPIENT= line in backend/.env);
# refuses to write plaintext. Restore:
#   age -d -i <key-file> sms-<stamp>.tgz.age | tar xzf - -C <dest>
#
# Keeps the last RETAIN backups. Scheduled via cron; run manually:  ./backup.sh
#
set -euo pipefail
cd "$(dirname "$0")"                       # → cineai/

BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/smartmoviesearch}"
RETAIN="${RETAIN:-14}"
mkdir -p "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$BACKUP_DIR/sms-$STAMP.tgz.age"

# age recipient (PUBLIC key). Refuse plaintext — the archive holds backend/.env secrets.
RECIPIENT="${BACKUP_AGE_RECIPIENT:-}"
if [ -z "$RECIPIENT" ] && [ -f backend/.env ]; then
  RECIPIENT="$(grep -E '^BACKUP_AGE_RECIPIENT=' backend/.env | cut -d= -f2- | tr -d '"' || true)"
fi
if [ -z "$RECIPIENT" ]; then
  echo "[sms-backup] ERROR: BACKUP_AGE_RECIPIENT unset (env or backend/.env) — refusing to write plaintext." >&2
  exit 1
fi
command -v age >/dev/null 2>&1 || { echo "[sms-backup] ERROR: age not installed (apt install age)." >&2; exit 1; }

# .env and data/ live under backend/; tar relative to backend/ (simple restore), then encrypt.
tar czf - -C backend .env data | age -r "$RECIPIENT" > "$OUT"

# Prune old backups (keep newest $RETAIN)
ls -1t "$BACKUP_DIR"/sms-*.tgz.age 2>/dev/null | tail -n +"$((RETAIN + 1))" | xargs -r rm -f

echo "$(date -u +%FT%TZ): backed up -> $OUT ($(du -h "$OUT" | cut -f1)); $(ls -1 "$BACKUP_DIR"/sms-*.tgz.age | wc -l) kept"
