#!/usr/bin/env bash
# Capture one season's team rosters (with stats.ncaa.org player ids).
#
#   NCAA_VENDOR=decodo_patchright ./scripts/run_rosters.sh --season 2025
#   ./scripts/run_rosters.sh --season 2025 --limit-teams 3    # smoke
#
# Resumable: existing wbb/team_rosters/{season}/{team_id}.json are skipped.
# Watch live:   tail -f logs/rosters_<ts>.log
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8
SDV_PY="C:/Users/saiem/Documents/GitHub-Data/sdv-dev/sdv-py"
PY="${SDV_PY}/.venv/Scripts/python.exe"
export PYTHONPATH="${SDV_PY};$(pwd)/python"
mkdir -p logs
LOG="logs/rosters_$(date +%Y%m%d_%H%M%S).log"
echo "log -> ${LOG}   (watch: tail -f ${LOG})"
"$PY" python/ncaa_rosters.py "$@" 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
echo "EXIT=${rc}" | tee -a "$LOG"
# Propagate the python exit code -- `$?` after a pipe is TEE's status, and a
# bare trailing `echo` would mask a ban hard-stop as success (it did: the
# 2026-07-13 backfill reported rc=0).
exit "${rc}"
