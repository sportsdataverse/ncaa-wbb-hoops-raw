#!/usr/bin/env bash
# User-run launcher: probe each proxy vendor in canary_vendors.toml against the
# same small bm-verify canary, and write a scorecard you pick a vendor from.
# Cheap + gentle (10 games x 2 pages per vendor). No proxy creds in .Renviron
# needed -- creds come from canary_vendors.toml.
#
#   cp canary_vendors.toml.example canary_vendors.toml   # then fill in trial creds
#   ./scripts/run_canary.sh                               # all vendors
#   ./scripts/run_canary.sh --games 5                     # quick 5-game pass
#   watch:  tail -f logs/canary_*.log
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1   # -> ncaa-wbb-hoops-raw repo root
ROOT="$(pwd)"
SDV_PY="C:/Users/saiem/Documents/GitHub-Data/sdv-dev/sdv-py"
PY="${SDV_PY}/.venv/Scripts/python.exe"

if [ ! -f "canary_vendors.toml" ]; then
  echo "ERROR: canary_vendors.toml not found." >&2
  echo "  cp canary_vendors.toml.example canary_vendors.toml   # then fill in trial creds" >&2
  exit 2
fi

export PYTHONPATH="${SDV_PY}:${ROOT}/python"
export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8

mkdir -p logs
LOG="logs/canary_$(date +%Y%m%d_%H%M%S).log"
echo "log -> ${LOG}  (watch: tail -f ${LOG})"
"${PY}" python/ncaa_canary.py "$@" 2>&1 | tee -a "${LOG}"
rc=${PIPESTATUS[0]}
echo "EXIT=${rc}" | tee -a "${LOG}"
exit "${rc}"
