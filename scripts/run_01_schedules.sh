#!/usr/bin/env bash
# User-run launcher: discover a season's contest_ids -> wbb/schedule_master.parquet.
#
# Also persists the schedules dataset tree from the SAME team-page fetches this
# sweep already makes (zero extra HTTP):
#   wbb/schedules/html/{season}/{team_id}.html   -- the raw page
#   wbb/schedules/json/{season}/{team_id}.json   -- team/opponent ids AND names
# The compiled wbb/schedules/parquet/{season}.parquet is built separately by
# ./scripts/run_05_datasets.sh (one non-sharded pass -- shards would race it).
#
# Fans out DISCOVER_WORKERS shard processes (default 12) over disjoint team
# slices, then runs ONE shard-less merge pass that reads every shard's per-team
# checkpoints (so it fetches nothing) and writes schedule_master.parquet.
# Pass an explicit --shard i/N to run exactly one shard instead.
#
# Transport: NCAA_VENDOR (e.g. decodo_patchright) routes through
# canary_vendors.toml -- team pages sit behind the same bm-verify as game
# pages, and the ProxyBonanza datacenter pool no longer clears it. The
# ProxyBonanza creds below are only the legacy no-vendor fallback.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1   # -> ncaa-wbb-hoops-raw repo root
ROOT="$(pwd)"
SDV_PY="${SDV_PY:-C:/Users/saiem/Documents/GitHub-Data/sdv-dev/sdv-py}"
DISCOVER_WORKERS="${DISCOVER_WORKERS:-12}"

if [ -z "${NCAA_VENDOR:-}" ]; then
  RENV="${HOME}/.Renviron"
  [ -f "$RENV" ] || RENV="${HOME}/Documents/.Renviron"
  getcred() { grep -E "^$1=" "$RENV" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d '\r'; }
  export SDV_PY_PROXYBONANZA_KEY="$(getcred PROXYBONANZA_API_KEY)"
  export SDV_PY_PROXYBONANZA_PKG="$(getcred PROXY_PKG)"
  if [ -z "${SDV_PY_PROXYBONANZA_KEY}" ] || [ -z "${SDV_PY_PROXYBONANZA_PKG}" ]; then
    echo "ERROR: no NCAA_VENDOR set and no proxy creds in ${RENV}" >&2
    exit 2
  fi
  echo "proxy creds loaded from ${RENV} (values hidden)"
fi

export PYTHONPATH="${SDV_PY}:${ROOT}/python"
export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8

mkdir -p logs
LOG="logs/discover_$(date +%Y%m%d_%H%M%S).log"
echo "log -> ${LOG}  (watch: tail -f ${LOG})"

# .venv layout is OS-dependent: Linux/droplet = .venv/bin, Windows = .venv/Scripts
if [ -x "${SDV_PY}/.venv/bin/python" ]; then PY="${PY:-${SDV_PY}/.venv/bin/python}"
else PY="${PY:-${SDV_PY}/.venv/Scripts/python.exe}"; fi
rc=0
if [[ " $* " == *" --shard "* ]]; then
  "$PY" python/ncaa_wbb_01_schedules_scrape.py "$@" 2>&1 | tee -a "${LOG}"
  rc=${PIPESTATUS[0]}
else
  pids=()
  for i in $(seq 0 $((DISCOVER_WORKERS - 1))); do
    "$PY" python/ncaa_wbb_01_schedules_scrape.py "$@" --shard "${i}/${DISCOVER_WORKERS}" >> "${LOG}" 2>&1 &
    pids+=($!)
  done
  # A shard that hard-stops on a ban is tolerated here: its teams stay
  # uncheckpointed and the merge pass re-fetches them (each game also appears
  # on a second team's page, so the loss is near-zero either way).
  for p in "${pids[@]}"; do wait "$p" || echo "a discover shard exited non-zero" >> "${LOG}"; done
  echo "=== merge pass (reads all shard checkpoints, writes schedule_master) ===" | tee -a "${LOG}"
  "$PY" python/ncaa_wbb_01_schedules_scrape.py "$@" 2>&1 | tee -a "${LOG}"
  rc=${PIPESTATUS[0]}
fi
echo "EXIT=${rc}" | tee -a "${LOG}"
# Propagate the python exit code -- a bare trailing `echo` would mask a
# ban hard-stop as success (it did: the 2026-07-13 backfill reported rc=0).
exit "${rc}"
