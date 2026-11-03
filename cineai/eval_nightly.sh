#!/usr/bin/env bash
#
# Nightly evals for SmartMovieSearch — the "is the unattended system still right?" job.
#
# Runs T1 routing + T2 retrieval + T4 canaries (+T5 cost/latency) + T6 MCP + T7 red team against the live
# backend container, and the weekly T3 LLM-judge tier on Sundays. Results land in
# evals/results/ (latest.json is what the daily ops digest, the box dashboard and
# /admin read). On a REGRESSION against evals/baseline.json it emails the admin
# immediately via send_email.py → server-management notify-admin.sh; a green run is
# silent here (the daily digest carries it).
#
# Scheduled via cron at 04:20 UTC — before the 04:50 ops digest. Run manually:
#   ./eval_nightly.sh            # nightly tiers
#   ./eval_nightly.sh weekly     # force the weekly tier too
# Then `--promote`: a newer model in the tier is evaluated before it may go live.
# Cost: ~$0.01/night on Haiku 5.5; the weekly judge ~$0.2; a promotion attempt ~$0.25.
#
set -uo pipefail

cd "$(dirname "$0")"                       # → cineai/ (where docker-compose.yml lives)

LOG_DIR="evals/results/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/eval-$(date -u +%Y%m%dT%H%M%SZ).log"
find "$LOG_DIR" -name 'eval-*.log' -mtime +30 -delete 2>/dev/null

exec 9>"$LOG_DIR/.lock"
flock -n 9 || { echo "$(date -u): eval run already in progress — exiting" >>"$LOG"; exit 0; }

TIERS="nightly"
if [[ "${1:-}" == "weekly" || "$(date -u +%u)" == "7" ]]; then
    TIERS="t1,t2,t3,t4,t6,t7"
fi

docker compose exec -T backend python3 /app/evals/run_evals.py --tier "$TIERS" >"$LOG" 2>&1
rc=$?

if [[ $rc -ne 0 ]]; then
    # Exit 1 = a tier regressed; anything else = the harness itself broke. Both page.
    if [[ $rc -eq 1 ]]; then subj="🔴 SmartMovieSearch evals: regression"; else subj="🔴 SmartMovieSearch evals: run failed (exit $rc)"; fi
    {
        echo "Nightly evals ($TIERS) exited $rc."
        echo
        grep -E "^  (✓|🔴|•) t[0-9]|REGRESSION|newly|Traceback|Error" "$LOG" | head -40
        echo
        echo "Full log on the server: cineai/$LOG"
        echo "Admin page: https://smartmoviesearch.com/admin"
    } | APP=smartmoviesearch SEVERITY=error ./send_email.py "$subj"
fi

# Model promotion: when Anthropic lists a newer model in the tier than the approved one,
# evaluate it (separate process; live traffic stays on the approved model) and approve it
# only if every tier passes and it costs no more per question. Emails either way.
PLOG="$LOG_DIR/promote-$(date -u +%Y%m%dT%H%M%SZ).log"
docker compose exec -T backend python3 /app/evals/run_evals.py --promote >"$PLOG" 2>&1
prc=$?
if grep -q "^PROMOTED" "$PLOG"; then
    { grep -A12 "^PROMOTED" "$PLOG"; echo; echo "The app switched on its next call (no restart)."
      echo "Undo: set MODEL_PIN=<previous model> in .env and recreate the backend."; } \
        | APP=smartmoviesearch SEVERITY=info ./send_email.py "SmartMovieSearch: new model promoted after evals"
elif grep -q "^BLOCKED" "$PLOG" || [[ $prc -gt 1 ]]; then
    { grep -A12 "^BLOCKED" "$PLOG" || tail -20 "$PLOG"; echo; echo "Live traffic stays on the approved model."
      echo "Full log on the server: cineai/$PLOG"; } \
        | APP=smartmoviesearch SEVERITY=warn ./send_email.py "SmartMovieSearch: new model NOT promoted"
fi
exit $rc
