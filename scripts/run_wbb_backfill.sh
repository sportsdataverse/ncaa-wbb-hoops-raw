#!/usr/bin/env bash
# One-command NCAA WBB backfill: discover -> capture -> parse, resumable.
#
# Chains the per-stage launchers (run_discover/run_capture/run_parse.sh) in order.
# RESUMABLE: capture skips already-captured contests; if it hard-stops on a ban,
# wait a while and just re-run this script -- it picks up where it left off.
# Parse is fully offline and safe to run on a partial capture.
#
# SAFE RATE (measured): 1-2 capture workers OK, 4 => ban. WORKERS is capped at 2.
# SESSION CEILING (measured 2026-07-13 on the MBB scraper, same transport):
# a browser session captures cleanly for ~70min/~1400 bundles, then bm-verify
# stops clearing; the run degraded to ZERO yield for a full hour and earned a
# hard 403 at 2402/6300. So CHUNK it: capture ~1500, cool down, re-run. The
# capture loop now also self-aborts on a soft-ban (25 consecutive challenge
# failures) instead of hammering.
#
# SEASON CEILING: the bundled WBB crosswalk (sportsdataverse/wbb/data/
# ncaa_teamids_wbb.csv) covers 2009-10..2024-25 ONLY -- there is no 2025-26
# row yet (unlike MBB's crosswalk, which does cover 2025-26). Season 2026 is
# refused below with an accurate cause instead of letting discover_season()
# raise its generic "crosswalk drift" ValueError. Extending the crosswalk to
# 2025-26 is a separate sdv-py change, out of scope here.
#
# Usage (run in YOUR terminal, on a residential IP -- stats.ncaa.org bans datacenter IPs):
#   ./scripts/run_wbb_backfill.sh 2025                      # 1 worker, unlimited
#   CHUNK=1500 ./scripts/run_wbb_backfill.sh 2025           # stop after 1500 new bundles (recommended)
#   WORKERS=2 CHUNK=1500 ./scripts/run_wbb_backfill.sh 2025 # 2 workers (measured ceiling)
#
# Watch live:  tail -f logs/backfill_<season>_<ts>.log   (path is printed on start;
#              per-stage logs under logs/ are also printed as each stage starts)
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1          # -> ncaa-wbb-hoops-raw repo root
ROOT="$(pwd)"
SDV_PY="C:/Users/saiem/Documents/GitHub-Data/sdv-dev/sdv-py"
PY="${SDV_PY}/.venv/Scripts/python.exe"

SEASON="${1:?usage: run_wbb_backfill.sh <season>  (ending year, e.g. 2025)}"
MAX_SEASON=2025
if [ "$SEASON" -gt "$MAX_SEASON" ]; then
  echo "REFUSING season=${SEASON} -- the bundled WBB crosswalk (sportsdataverse/wbb/data/ncaa_teamids_wbb.csv)" >&2
  echo "  only covers seasons through ${MAX_SEASON} (i.e. 2024-25); there is no 2025-26 row yet." >&2
  echo "  This is a crosswalk coverage gap, not the 'team-ids format drift' that discover_season()" >&2
  echo "  would otherwise report. Extending the crosswalk to 2025-26 is a separate sdv-py change." >&2
  exit 2
fi

WORKERS="${WORKERS:-1}"
case "$WORKERS" in 1|2) ;; *)
  echo "REFUSING WORKERS='$WORKERS' -- measured safe ceiling is 2 (4 workers => ban). Use 1 or 2." >&2
  exit 2 ;;
esac

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
  ./scripts/run_discover.sh --season "$SEASON" || { say "discover FAILED -- stopping (fix creds/network, then re-run)"; exit 1; }
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
  ./scripts/run_capture.sh "${CAP_ARGS[@]}" --shard 0/1 || rc=$?
else
  pids=()
  for i in $(seq 0 $((WORKERS-1))); do
    ./scripts/run_capture.sh "${CAP_ARGS[@]}" --shard "${i}/${WORKERS}" &
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
./scripts/run_parse.sh --league wbb || { say "parse FAILED"; exit 1; }

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
