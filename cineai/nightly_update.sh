#!/usr/bin/env bash
#
# Nightly RAG refresh for SmartMovieSearch.
#
# Discovers Roger Ebert reviews archived since the last run, scrapes the new
# ones, and ingests only those into Milvus. Idempotent: --skip-existing plus
# amp/-slug normalization mean re-runs never create duplicates.
#
# Note: movie/TV *facts* (ratings, cast, release dates, trending) are served live
# by the TMDB agent and need no ingestion — this job only refreshes the prose
# (review) layer that RAG retrieves over.
#
# Scheduled via cron (see crontab). Run manually:  ./nightly_update.sh
#
set -euo pipefail

cd "$(dirname "$0")"                       # → cineai/ (where docker-compose.yml lives)

LOG_DIR="backend/data/nightly-logs"
mkdir -p "$LOG_DIR"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$LOG_DIR/nightly-$TS.log"

# Single-instance lock so a long scrape can't overlap the next night's run.
exec 9>"$LOG_DIR/.lock"
if ! flock -n 9; then
    echo "$(date -u): another nightly run is in progress — exiting" >>"$LOG"
    exit 0
fi

exec >>"$LOG" 2>&1
echo "════════ nightly RAG update @ $TS ════════"

# 1. Discover + scrape reviews archived in the last year (refreshes CDX cache).
echo "── scrape ─────────────────────────────────"
docker compose exec -T backend python3 scripts/scrape_ebert.py --refresh-recent 1 --limit 800

# 2. Ingest only the new reviews (skip-existing + amp dedup keep it clean).
echo "── ingest ─────────────────────────────────"
docker compose exec -T backend python3 scripts/ingest_ebert.py --skip-existing

echo "════════ done @ $(date -u +%Y%m%dT%H%M%SZ) ════════"

# Keep the log dir tidy.
find "$LOG_DIR" -name 'nightly-*.log' -mtime +30 -delete 2>/dev/null || true
