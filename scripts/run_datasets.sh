#!/usr/bin/env bash
# User-run launcher: build the committed schedules / teams / rosters trees.
#
#   ./scripts/run_datasets.sh --season 2026                  # all three
#   ./scripts/run_datasets.sh --season 2026 --what teams     # just teams
#   ./scripts/run_datasets.sh --season 2026 --overwrite      # after a parser fix
#
# FULLY OFFLINE -- no proxy creds, no network, safe to run any time. The per-team
# html + json come from the fetches run_discover.sh / run_rosters.sh ALREADY
# make; this stage only re-derives json from committed html and compiles the
# season parquet.
#
#   wbb/schedules/html/{season}/{team_id}.html   <- from run_discover.sh
#   wbb/schedules/json/{season}/{team_id}.json   <- from run_discover.sh
#   wbb/schedules/parquet/{season}.parquet       <- compiled HERE (one file)
#   wbb/rosters/html/{season}/{team_id}.html     <- from run_rosters.sh
#   wbb/rosters/json/{season}/{team_id}.json     <- from run_rosters.sh
#   wbb/rosters/parquet/{season}.parquet         <- compiled HERE (one file)
#   wbb/teams/{html,json,parquet}/{season}.*     <- built HERE from the crosswalk
#
# Deliberately NOT sharded: the season parquet is a single output file per kind,
# so concurrent --shard workers would race each other into a truncated dataset.
# Run this once, after the sharded discover/roster sweeps have finished.
#
# Watch live:   tail -f logs/datasets_<ts>.log
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1   # -> ncaa-wbb-hoops-raw repo root
ROOT="$(pwd)"
SDV_PY="C:/Users/saiem/Documents/GitHub-Data/sdv-dev/sdv-py"
PY="${SDV_PY}/.venv/Scripts/python.exe"
export PYTHONPATH="${SDV_PY};${ROOT}/python"
export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8

mkdir -p logs
LOG="logs/datasets_$(date +%Y%m%d_%H%M%S).log"
echo "log -> ${LOG}  (watch: tail -f ${LOG})"

"$PY" python/ncaa_datasets.py "$@" 2>&1 | tee -a "${LOG}"
rc=${PIPESTATUS[0]}
echo "EXIT=${rc}" | tee -a "${LOG}"
# Propagate the python exit code -- `$?` after a pipe is TEE's status, and a
# bare trailing `echo` would mask a failure as success (it did: the 2026-07-13
# backfill reported rc=0).
exit "${rc}"
