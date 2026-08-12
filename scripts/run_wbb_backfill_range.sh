#!/usr/bin/env bash
# Multi-season NCAA WBB backfill campaign: seasons NEWEST-first, chunked capture
# per season until complete, resumable at every level. Wraps run_wbb_backfill.sh
# (discover -> capture -> parse per season).
#
# Transport: capture rides the canary-validated Decodo US-residential sticky
# sessions (NCAA_VENDOR=decodo_patchright by default; creds in the gitignored
# canary_vendors.toml — the sticky session id is re-minted on every chunk, so
# each chunk gets a fresh IP and the ~70min/~1400-bundle session ceiling never
# binds). Discovery (team pages, ~350/season) stays on the sdv-py
# ProxyBonanza browser transport inside run_01_schedules.sh.
#
# SAFE RATE (measured 2026-07): 1-2 capture workers, serial preferred; chunk
# ~1400 bundles per session. A ban/soft-ban hard-stops the chunk; this script
# cools down and retries (bounded), then moves on.
#
# Usage (run in YOUR terminal):
#   ./scripts/run_wbb_backfill_range.sh                 # 2025 down to 2010
#   ./scripts/run_wbb_backfill_range.sh 2025 2020       # 2025 down to 2020
#   CHUNK=1000 COOLDOWN_S=900 ./scripts/run_wbb_backfill_range.sh
#
# Knobs (env-only — retune without code changes):
#   VENDOR=decodo_patchright  canary_vendors.toml transport for capture
#   CHUNK=1400                new bundles per capture session (fresh sticky IP each)
#   WORKERS=1                 capture processes (2 = measured ceiling; never more)
#   COOLDOWN_S=300            pause between successful chunks
#   BAN_COOLDOWN_S=1800       pause after a ban/soft-ban hard-stop
#   MAX_ROUNDS=12             chunk attempts per season before moving on
#
# Watch live:   tail -f logs/backfill_range_<ts>.log
# Ctrl-C:       safe anytime — per-game JSON on disk IS the checkpoint; re-run
#               resumes exactly where it stopped (captured contests are skipped).
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
ROOT="$(pwd)"
SDV_PY="${SDV_PY:-C:/Users/saiem/Documents/GitHub-Data/sdv-dev/sdv-py}"
# .venv layout is OS-dependent: Linux/droplet = .venv/bin, Windows = .venv/Scripts
if [ -x "${SDV_PY}/.venv/bin/python" ]; then PY="${PY:-${SDV_PY}/.venv/bin/python}"
else PY="${PY:-${SDV_PY}/.venv/Scripts/python.exe}"; fi
export SDV_PY PY

START="${1:-2025}"   # newest season to backfill (ending year; 2025 = 2024-25)
END="${2:-2010}"     # oldest (crosswalk floor is 2009-10 -> season 2010)
VENDOR="${VENDOR:-decodo_patchright}"
CHUNK="${CHUNK:-1400}"
WORKERS="${WORKERS:-1}"
COOLDOWN_S="${COOLDOWN_S:-300}"
BAN_COOLDOWN_S="${BAN_COOLDOWN_S:-1800}"
MAX_ROUNDS="${MAX_ROUNDS:-12}"

mkdir -p logs
LOG="logs/backfill_range_$(date +%Y%m%d_%H%M%S).log"
say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }
say "campaign: seasons ${START}..${END} (desc), vendor=${VENDOR}, chunk=${CHUNK}, workers=${WORKERS}"
say "log -> ${LOG}   (watch: tail -f ${LOG})"

remaining() { # rows in schedule_master for season $1 minus captured raw bundles on disk
  "$PY" -c "
import polars as pl, pathlib
season = '$1'
m = pl.read_parquet('wbb/schedule_master.parquet')
total = m.filter(pl.col('season') == season).height
have = len(list(pathlib.Path('wbb/raw', season).glob('*'))) if pathlib.Path('wbb/raw', season).is_dir() else 0
print(max(total - have, 0) if total else -1)
" 2>/dev/null || echo -1
}

for season in $(seq "$START" -1 "$END"); do
  say "=== season ${season} ==="
  round=0
  while :; do
    round=$((round + 1))
    if [ "$round" -gt "$MAX_ROUNDS" ]; then
      say "season ${season}: MAX_ROUNDS=${MAX_ROUNDS} reached with $(remaining "$season") remaining -- moving on (re-run later to finish)"
      break
    fi
    before="$(remaining "$season")"
    NCAA_VENDOR="$VENDOR" CHUNK="$CHUNK" WORKERS="$WORKERS" \
      ./scripts/run_wbb_backfill.sh "$season" 2>&1 | tee -a "$LOG"
    rc=${PIPESTATUS[0]}
    left="$(remaining "$season")"
    # A clean round that captured NOTHING means the remainder is un-capturable,
    # not merely un-captured: every season carries a few pageless/cancelled
    # contests that sit in schedule_master but have no game page and never
    # will. Without this, each season burns MAX_ROUNDS x COOLDOWN_S chasing
    # them -- ~1 idle hour per season, ~13 across a full backfill. Compared
    # before-vs-after so it is caught in ONE round. rc!=0 is excluded: a ban
    # hard-stop also makes no progress but MUST cool down and retry, not skip.
    if [ "$rc" -eq 0 ] && [ "$left" != "-1" ] && [ "$left" = "$before" ] && [ "$left" != "0" ]; then
      say "season ${season}: round ${round} captured nothing (${left} left, un-capturable) -- treating as done"
      break
    fi
    if [ "$left" = "0" ]; then
      say "season ${season}: COMPLETE (round ${round})"
      break
    elif [ "$left" = "-1" ]; then
      # Discovery produced no rows (a bm-verify hard-stop mid-sweep kills the
      # whole season's discover by design). RETRY with the ban cooldown rather
      # than skipping the season -- a skipped season silently never backfills.
      say "season ${season}: no schedule_master rows after discover (round ${round}) -- cooling ${BAN_COOLDOWN_S}s then retrying discover"
      sleep "$BAN_COOLDOWN_S"
      continue
    fi
    if [ "$rc" -ne 0 ]; then
      say "season ${season}: chunk ${round} hard-stopped (rc=${rc}), ${left} remaining -- cooling ${BAN_COOLDOWN_S}s"
      sleep "$BAN_COOLDOWN_S"
    else
      say "season ${season}: chunk ${round} done, ${left} remaining -- cooling ${COOLDOWN_S}s (fresh sticky session next)"
      sleep "$COOLDOWN_S"
    fi
  done
done
say "campaign finished ${START}..${END}"
echo "EXIT=$?" | tee -a "$LOG"
