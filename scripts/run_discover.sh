#!/usr/bin/env bash
# User-run launcher: discover a season's contest_ids -> wbb/schedule_master.parquet.
# Sources ProxyBonanza creds from ~/.Renviron (discovery is a browser-transport
# scrape of team pages, same safe-rate rules as capture -- see README.md).
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1   # -> ncaa-wbb-hoops-raw repo root
ROOT="$(pwd)"
SDV_PY="C:/Users/saiem/Documents/GitHub-Data/sdv-dev/sdv-py"

RENV="${HOME}/.Renviron"
[ -f "$RENV" ] || RENV="${HOME}/Documents/.Renviron"
getcred() { grep -E "^$1=" "$RENV" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d '\r'; }

export SDV_PY_PROXYBONANZA_KEY="$(getcred PROXYBONANZA_API_KEY)"
export SDV_PY_PROXYBONANZA_PKG="$(getcred PROXY_PKG)"
if [ -z "${SDV_PY_PROXYBONANZA_KEY}" ] || [ -z "${SDV_PY_PROXYBONANZA_PKG}" ]; then
  echo "ERROR: proxy creds not found in ${RENV} (need PROXYBONANZA_API_KEY + PROXY_PKG)" >&2
  exit 2
fi
echo "proxy creds loaded from ${RENV} (values hidden)"

export PYTHONPATH="${SDV_PY}:${ROOT}/python"
export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8

mkdir -p logs
LOG="logs/discover_$(date +%Y%m%d_%H%M%S).log"
echo "log -> ${LOG}  (watch: tail -f ${LOG})"
"${SDV_PY}/.venv/Scripts/python.exe" python/ncaa_discover.py "$@" 2>&1 | tee -a "${LOG}"
rc=${PIPESTATUS[0]}
echo "EXIT=${rc}" | tee -a "${LOG}"
# Propagate the python exit code -- a bare trailing `echo` would mask a
# ban hard-stop as success (it did: the 2026-07-13 backfill reported rc=0).
exit "${rc}"
