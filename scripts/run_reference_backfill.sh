#!/usr/bin/env bash
# Reference-dataset backfill: schedules + rosters + teams for EVERY season,
# newest-first, committing and pushing per season.
#
# Reference data is cheap relative to pbp: ~2 pages per team-season (schedule
# page + roster page, ~730/season) versus 3 pages per GAME (~18,700/season).
# So the whole 17-season reference corpus costs roughly what ONE season of
# pbp capture does -- which is why it runs first.
#
#   ./scripts/run_reference_backfill.sh                 # 2026 down to 2010
#   ./scripts/run_reference_backfill.sh 2026 2020       # a sub-range
#   WORKERS=12 PUSH=0 ./scripts/run_reference_backfill.sh
#
# Knobs (env-only):
#   WORKERS=12   shard processes per stage
#   PUSH=1       commit AND push per season (0 = commit only)
#   VENDOR=decodo_patchright
#
# Resumable at every level: a team whose schedule/roster HTML is already
# committed is re-parsed OFFLINE (no fetch), so a re-run costs nothing for
# work already done and Ctrl-C is always safe.
#
# Watch live:  tail -f logs/reference_backfill_<ts>.log
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8

START="${1:-2026}"
END="${2:-2010}"
WORKERS="${WORKERS:-12}"
PUSH="${PUSH:-1}"
VENDOR="${VENDOR:-decodo_patchright}"
case "$PWD" in *wbb*) LEAGUE=wbb ;; *) LEAGUE=mbb ;; esac
SDV_PY="C:/Users/saiem/Documents/GitHub-Data/sdv-dev/sdv-py"
PY="${SDV_PY}/.venv/Scripts/python.exe"
export PYTHONPATH="${SDV_PY};$(pwd)/python"

mkdir -p logs
LOG="logs/reference_backfill_$(date +%Y%m%d_%H%M%S).log"
say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }
say "reference backfill: ${LEAGUE} seasons ${START}..${END}, workers=${WORKERS}, push=${PUSH}"
say "watch: tail -f $(pwd)/${LOG}"

for season in $(seq "$START" -1 "$END"); do
  say "=== ${season}: schedules ==="
  # A season swept BEFORE the schedules tree existed has .discover checkpoints
  # but no HTML; discovery short-circuits on the checkpoint and would never
  # persist the dataset row. Dropping the checkpoint costs nothing -- the
  # contest ids are already committed in schedule_master.
  if [ -d "${LEAGUE}/.discover/${season}" ] && [ -z "$(ls -A "${LEAGUE}/schedules/html/${season}" 2>/dev/null)" ]; then
    say "  dropping stale .discover/${season} (checkpoints without HTML)"
    rm -rf "${LEAGUE}/.discover/${season}"
  fi
  NCAA_VENDOR="$VENDOR" DISCOVER_WORKERS="$WORKERS" \
    ./scripts/run_01_schedules.sh --season "$season" >> "$LOG" 2>&1 \
    || say "  discover ${season} returned non-zero (tolerated; partial is fine)"

  say "=== ${season}: rosters (${WORKERS} shards) ==="
  # run_04_rosters.sh has no fan-out of its own -- shard here. Serial would be
  # ~365 pages x ~11s = an hour per season; 12 disjoint shards on disjoint
  # sticky ports cut that to minutes.
  pids=()
  for i in $(seq 0 $((WORKERS - 1))); do
    NCAA_VENDOR="$VENDOR" "$PY" python/ncaa_wbb_04_rosters_scrape.py \
      --season "$season" --league "$LEAGUE" --shard "${i}/${WORKERS}" >> "$LOG" 2>&1 &
    pids+=($!)
  done
  for p in "${pids[@]}"; do wait "$p" || say "  a rosters shard exited non-zero (tolerated)"; done

  say "=== ${season}: compile teams/schedules/rosters ==="
  ./scripts/run_05_datasets.sh --season "$season" --overwrite >> "$LOG" 2>&1 \
    || say "  datasets ${season} returned non-zero"

  sched=$(ls "${LEAGUE}/schedules/html/${season}" 2>/dev/null | wc -l | tr -d ' ')
  rost=$(ls "${LEAGUE}/rosters/html/${season}" 2>/dev/null | wc -l | tr -d ' ')
  say "${season}: schedules_html=${sched} rosters_html=${rost}"

  git add "${LEAGUE}/schedules" "${LEAGUE}/rosters" "${LEAGUE}/teams" \
          "${LEAGUE}/schedule_master.parquet" "${LEAGUE}/team_rosters" 2>/dev/null
  if git diff --cached --quiet; then
    say "${season}: nothing new to commit"
  else
    git commit -q -m "feat(reference): ${season} schedules + rosters + teams (${sched} schedule pages, ${rost} roster pages)" \
      && say "${season}: committed"
    if [ "$PUSH" = "1" ]; then
      git -c http.version=HTTP/1.1 -c http.postBuffer=1048576000 push -q origin main \
        && say "${season}: pushed" || say "${season}: PUSH FAILED (commit is safe locally)"
    fi
  fi
done
say "reference backfill finished ${START}..${END}"
echo "EXIT=$?" | tee -a "$LOG"
