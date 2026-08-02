#!/usr/bin/env bash
# Periodic commit+push of capture output while a campaign runs.
#
# The capture campaign (run_*_backfill_range.sh) deliberately does NOT commit --
# it captures and parses only. Without this, a season accumulates ~12.5k
# untracked files (raw + parsed json) and a full backfill ~175k, which makes
# the eventual push enormous and a single failure expensive. This commits in
# small increments instead, so the repo is always close to pushed.
#
#   ./scripts/run_autocommit.sh                 # every 10 min until stopped
#   INTERVAL=300 PUSH=0 ./scripts/run_autocommit.sh
#
# Knobs (env-only):
#   INTERVAL=600   seconds between passes
#   PUSH=1         push after each commit (0 = commit only)
#   SETTLE=1       only stage files older than this many MINUTES
#
# SETTLE is the important one: a bundle being written right now must not be
# staged half-flushed, so only files whose mtime has settled are added. That
# makes this safe to run CONCURRENTLY with an active capture.
#
# Ctrl-C safe: it holds no state; the next run picks up whatever is pending.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
case "$PWD" in *wbb*) LEAGUE=wbb ;; *) LEAGUE=mbb ;; esac
INTERVAL="${INTERVAL:-600}"
PUSH="${PUSH:-1}"
SETTLE="${SETTLE:-1}"

mkdir -p logs
LOG="logs/autocommit_$(date +%Y%m%d_%H%M%S).log"
say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }
say "autocommit: league=${LEAGUE} interval=${INTERVAL}s push=${PUSH} settle=${SETTLE}min"
say "watch: tail -f $(pwd)/${LOG}"

while :; do
  # Stage only SETTLED files so an in-flight write is never committed partially.
  find "${LEAGUE}/raw" "${LEAGUE}/json" -type f -mmin "+${SETTLE}" -print0 2>/dev/null \
    | xargs -0 -r git add --
  # schedule_master is rewritten by discovery; safe to take whole.
  [ -f "${LEAGUE}/schedule_master.parquet" ] && git add "${LEAGUE}/schedule_master.parquet"

  if git diff --cached --quiet; then
    say "nothing settled to commit"
  else
    n=$(git diff --cached --name-only | wc -l | tr -d ' ')
    # Per-season counts make the commit message useful in `git log` later.
    summary=""
    for d in "${LEAGUE}"/raw/*/; do
      s=$(basename "$d")
      c=$(git diff --cached --name-only -- "${LEAGUE}/raw/${s}" | wc -l | tr -d ' ')
      [ "$c" -gt 0 ] && summary="${summary}${s}:+${c} "
    done
    git commit -q -m "feat(data): capture progress -- ${summary:-incremental} (${n} files)" \
      && say "committed ${n} files  ${summary}"
    if [ "$PUSH" = "1" ]; then
      git -c http.version=HTTP/1.1 -c http.postBuffer=1048576000 push -q origin main \
        && say "pushed" || say "PUSH FAILED (commit is safe locally; next pass retries)"
    fi
  fi
  sleep "$INTERVAL"
done
