#!/usr/bin/env bash
# One-command NCAA WBB backfill: discover -> capture -> parse, resumable.
#
# Chains the per-stage launchers (run_discover/run_capture/run_03_parse.sh) in order.
# RESUMABLE: capture skips already-captured contests; if it hard-stops on a ban,
# wait a while and just re-run this script -- it picks up where it left off.
# Parse is fully offline and safe to run on a partial capture.
#
# SAFE RATE: with canary-vendor sticky residential sessions (NCAA_VENDOR=...)
# each worker rides a DISJOINT proxy offset, so per-IP pacing binds, not process
# count -- WORKERS is capped at 16. The old cap of 2 was measured on the shared
# datacenter pool, where 4 workers piled onto one IP and earned a ban.
# SESSION CEILING (measured 2026-07-13 on the MBB scraper, same transport):
# a browser session captures cleanly for ~70min/~1400 bundles, then bm-verify
# stops clearing; the run degraded to ZERO yield for a full hour and earned a
# hard 403 at 2402/6300. So CHUNK it: capture ~1500, cool down, re-run. The
# capture loop now also self-aborts on a soft-ban (25 consecutive challenge
# failures) instead of hammering.
#
# SEASON CEILING: MAX_SEASON below must track the bundled WBB crosswalk
# (sportsdataverse/wbb/data/ncaa_teamids_wbb.csv), which now covers
# 2009-10..2025-26. An out-of-range season is refused below with an accurate
# cause instead of letting discover_season() raise its generic "crosswalk
# drift" ValueError. When sdv-py extends the crosswalk another season, bump
# MAX_SEASON here (the 2026-08-01 campaign lost its first round to a stale
# guard refusing a season the crosswalk already covered).
#
# Usage (run in YOUR terminal, on a residential IP -- stats.ncaa.org bans datacenter IPs):
#   ./scripts/run_wbb_backfill.sh 2025                      # 1 worker, unlimited
#   CHUNK=1500 ./scripts/run_wbb_backfill.sh 2025           # stop after 1500 new bundles (recommended)
#   NCAA_VENDOR=decodo_patchright WORKERS=8 CHUNK=1500 ./scripts/run_wbb_backfill.sh 2025
#
# Watch live:  tail -f logs/backfill_<season>_<ts>.log   (path is printed on start;
#              per-stage logs under logs/ are also printed as each stage starts)
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1          # -> ncaa-wbb-hoops-raw repo root
ROOT="$(pwd)"
SDV_PY="C:/Users/saiem/Documents/GitHub-Data/sdv-dev/sdv-py"
PY="${SDV_PY}/.venv/Scripts/python.exe"

SEASON="${1:?usage: run_wbb_backfill.sh <season>  (ending year, e.g. 2025)}"
MIN_SEASON=2010
MAX_SEASON=2026
case "$SEASON" in
  ''|*[!0-9]*)
    echo "REFUSING SEASON='${SEASON}' -- must be a plain integer ending year (e.g. 2025)." >&2
    exit 2 ;;
esac
if [ "$SEASON" -lt "$MIN_SEASON" ]; then
  echo "REFUSING season=${SEASON} -- the bundled WBB crosswalk (sportsdataverse/wbb/data/ncaa_teamids_wbb.csv)" >&2
  echo "  starts at season ${MIN_SEASON} (2009-10); there is no earlier row." >&2
  exit 2
fi
if [ "$SEASON" -gt "$MAX_SEASON" ]; then
  echo "REFUSING season=${SEASON} -- the bundled WBB crosswalk (sportsdataverse/wbb/data/ncaa_teamids_wbb.csv)" >&2
  echo "  only covers seasons through ${MAX_SEASON}; there is no later row yet." >&2
  echo "  This is a crosswalk coverage gap, not the 'team-ids format drift' that discover_season()" >&2
  echo "  would otherwise report. Extending the crosswalk is a separate sdv-py change; once it" >&2
  echo "  lands, bump MAX_SEASON in this script." >&2
  exit 2
fi

WORKERS="${WORKERS:-1}"
# The old ceiling of 2 was measured on a SHARED datacenter proxy pool, where
# every worker piled onto the same handful of IPs. With canary-vendor sticky
# residential sessions (NCAA_VENDOR=...), _vendor_fetcher rotates each worker's
# proxy list to a disjoint offset, so what binds is per-IP pacing, not process
# count. 16 is the cap; raise it only with a fresh measurement.
case "$WORKERS" in
  ''|*[!0-9]*) WORKERS=0 ;;
esac
if [ "$WORKERS" -lt 1 ] || [ "$WORKERS" -gt 16 ]; then
  echo "REFUSING WORKERS='${WORKERS}' -- must be 1..16 (each worker rides its own" >&2
  echo "  disjoint sticky proxy session; more than 16 has not been measured)." >&2
  exit 2
fi

mkdir -p logs
TS="$(date +%Y%m%d_%H%M%S)"
LOG="logs/backfill_${SEASON}_${TS}.log"
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

say "NCAA WBB backfill: season=${SEASON} workers=${WORKERS}"
say "watch this run:  tail -f ${ROOT}/${LOG}"

# --- 1) discover: only if this season has no rows yet (avoid re-scraping team pages) ---
need_discover() {
  [ -f wbb/schedule_master.parquet ] || return 0
  local n
  n="$("$PY" -c "import polars as pl; print(pl.read_parquet('wbb/schedule_master.parquet').filter(pl.col('season')==str(${SEASON})).height)" 2>/dev/null || echo 0)"
  [ "${n:-0}" -eq 0 ]
}
if need_discover; then
  say "=== discover ${SEASON} (season not in schedule_master yet) ==="
  ./scripts/run_01_schedules.sh --season "$SEASON" || { say "discover FAILED -- stopping (fix creds/network, then re-run)"; exit 1; }
else
  say "=== skip discover (season ${SEASON} already in schedule_master; delete wbb/schedule_master.parquet to force) ==="
fi

# --- 2) capture (resumable, ban-hard-stops). 1 shard, or WORKERS disjoint shards in parallel. ---
CAP_ARGS=(--season "$SEASON")
if [ -n "${CHUNK:-}" ]; then
  CAP_ARGS+=(--max-contests "$CHUNK")
  say "=== capture ${SEASON}: ${WORKERS} worker(s), chunk=${CHUNK} new bundles per worker ==="
else
  say "=== capture ${SEASON} with ${WORKERS} worker(s) (no chunk limit) ==="
fi
rc=0
if [ "$WORKERS" -eq 1 ]; then
  ./scripts/run_02_games.sh "${CAP_ARGS[@]}" --shard 0/1 || rc=$?
else
  pids=()
  for i in $(seq 0 $((WORKERS-1))); do
    ./scripts/run_02_games.sh "${CAP_ARGS[@]}" --shard "${i}/${WORKERS}" &
    pids+=($!)
  done
  for p in "${pids[@]}"; do wait "$p" || rc=$?; done
fi
if [ "$rc" -ne 0 ]; then
  say "capture stopped (rc=${rc}) -- a ban/soft-ban hard-stop or Ctrl-C (see the capture log)."
  say "  This is RESUMABLE: cool down (a ban clears in minutes-hours), then re-run this"
  say "  script -- already-captured contests are skipped."
fi

# --- 3) parse (offline; safe on a partial capture) -> wbb/json/{contest_id}.json ---
say "=== parse captured bundles -> wbb/json/ ==="
./scripts/run_03_parse.sh --league wbb || { say "parse FAILED"; exit 1; }

# --- summary + next step ---
CAP="$(find wbb/raw -name '*.json.gz' 2>/dev/null | wc -l | tr -d ' ')"
JSON="$(ls wbb/json 2>/dev/null | wc -l | tr -d ' ')"
say "DONE: captured_bundles=${CAP} parsed_json=${JSON} (capture rc=${rc})"
if [ "$rc" -eq 0 ]; then
  say "next -> build the -data parquet (Phase 2, not yet built as of this branch):"
  say "  cd ${ROOT}/../ncaa-wbb-hoops-data && python -m ncaa_wbb_data_build build --dataset all --season ${SEASON}"
else
  say "capture INCOMPLETE (rc=${rc}) -- re-run this script to continue before building."
fi
echo "EXIT=${rc}" | tee -a "$LOG"
exit "$rc"
