#!/usr/bin/env bash
# Capture one season's team rosters (with stats.ncaa.org player ids).
#
#   NCAA_VENDOR=decodo_patchright ./scripts/run_04_rosters.sh --season 2025
#   ./scripts/run_04_rosters.sh --season 2025 --limit-teams 3    # smoke
#
# Also persists the rosters dataset tree from the SAME fetch (zero extra HTTP):
#   wbb/rosters/html/{season}/{team_id}.html   -- the raw page
#   wbb/rosters/json/{season}/{team_id}.json   -- player_id + clean_name + player
# The compiled wbb/rosters/parquet/{season}.parquet is built separately by
# ./scripts/run_05_datasets.sh (one non-sharded pass -- shards would race it).
#
# Resumable: existing wbb/team_rosters/{season}/{team_id}.json are skipped, and
# a team whose rosters html is already committed is re-parsed offline.
# Watch live:   tail -f logs/rosters_<ts>.log
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8
SDV_PY="${SDV_PY:-C:/Users/saiem/Documents/GitHub-Data/sdv-dev/sdv-py}"
# .venv layout is OS-dependent: Linux/droplet = .venv/bin, Windows = .venv/Scripts
# (same branch as run_01_schedules.sh/run_02_games.sh). PYTHONPATH separator is
# also OS-dependent (`:` vs `;`); a hardcoded `;` silently no-ops on Linux since
# bash just treats it as part of one path entry, and the import then falls
# through to whatever `sportsdataverse` happens to be on the ambient PATH.
if [ -x "${SDV_PY}/.venv/bin/python" ]; then
  PY="${PY:-${SDV_PY}/.venv/bin/python}"
  export PYTHONPATH="${SDV_PY}:$(pwd)/python"
else
  PY="${PY:-${SDV_PY}/.venv/Scripts/python.exe}"
  export PYTHONPATH="${SDV_PY};$(pwd)/python"
fi
mkdir -p logs
LOG="logs/rosters_$(date +%Y%m%d_%H%M%S).log"
echo "log -> ${LOG}   (watch: tail -f ${LOG})"
"$PY" python/ncaa_wbb_04_rosters_scrape.py "$@" 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
echo "EXIT=${rc}" | tee -a "$LOG"
# Propagate the python exit code -- `$?` after a pipe is TEE's status, and a
# bare trailing `echo` would mask a ban hard-stop as success (it did: the
# 2026-07-13 backfill reported rc=0).
exit "${rc}"
